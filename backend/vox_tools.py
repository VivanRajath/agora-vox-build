"""Vox's tools: one registry used by the /llm endpoint (voice) and the typed fallback.

Each tool has a JSON schema the conversation model sees, a short filler line Vox
says while it runs, and an async handler returning {status, message}.

The track flow: plan_site -> (user approves or changes) -> build_site ->
edit_site / undo_change ... -> publish_site (needs_approval) -> user says yes ->
publish_site(confirmed) -> publication_status -> live URL.
"""
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import auth, events, github, jobs, publish, store, users
from .editor import engine

log = logging.getLogger("vox-build.tools")

Handler = Callable[[str, dict], Awaitable[dict]]
APPROVAL_TTL = 15 * 60


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Handler
    filler: str = "One moment."

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None, filler: str = "One moment."):
    def register(fn: Handler) -> Handler:
        schema = {"type": "object", "properties": properties or {}, "required": required or []}
        TOOLS[name] = Tool(name, description, schema, fn, filler)
        return fn
    return register


def specs() -> list[dict]:
    return [t.spec() for t in TOOLS.values()]


async def run(project_id: str, name: str, args: dict | None = None) -> dict:
    """Runs one tool and reports it on the project's event stream."""
    args = args or {}
    entry = TOOLS.get(name)
    if not entry:
        return {"status": "failed", "message": f"There is no tool called {name}."}
    events.publish(project_id, {"type": "tool", "name": name, "status": "started", "args": _short(args)})
    try:
        result = await entry.handler(project_id, args)
    except Exception as e:  # a tool crash must never leave the call silent
        log.exception("tool %s failed", name)
        result = {"status": "failed", "message": f"Something went wrong: {e}"}
    events.publish(project_id, {"type": "tool", "name": name, "status": result.get("status"), "message": result.get("message", "")})
    return result


def _short(args: dict) -> dict:
    return {k: (v[:160] if isinstance(v, str) else v) for k, v in args.items()}


# ---------------------------------------------------------------------------
# Plan and build
# ---------------------------------------------------------------------------

@tool(
    "plan_site",
    "Plan a new website before building it: the researcher returns business type, style, sections and palette. "
    "Always the first step for a new site. Read the plan to the user and ask whether to build it or change anything.",
    {"description": {"type": "string", "description": "One rich paragraph in English: business name, what it does, city, audience, style, colours, sections the user mentioned."}},
    ["description"],
    filler="Great, let me plan that.",
)
async def plan_site(project_id: str, args: dict) -> dict:
    description = str(args.get("description", "")).strip()
    if not description:
        return {"status": "failed", "message": "I need a short description of the business first."}
    result = await jobs.answer_voice(project_id, engine.plan(project_id, description))
    if result["status"] == "done":
        result = {"status": "needs_approval", "message": result["message"] + " Shall I build it, or change anything?"}
    return result


@tool(
    "build_site",
    "Build the website from the plan the user approved. Pass the user's requested changes to the plan, if any.",
    {"changes": {"type": "string", "description": "Changes to the plan in English (e.g. 'add a section for custom birthday cakes'), or empty."}},
    filler="Building it now. The whole team is on it.",
)
async def build_site(project_id: str, args: dict) -> dict:
    draft = store.load_meta(project_id).get("draft")
    if not draft:
        return {"status": "failed", "message": "There's no approved plan yet. Describe the business and I'll plan it first."}
    changes = str(args.get("changes") or "").strip()
    if changes:
        # The researcher re-plans with the changes, so the brief lists the new sections too.
        coro = engine.generate(project_id, f"{draft['description']} Requested changes: {changes}")
    else:
        coro = engine.generate(project_id, draft["description"], brief=draft["brief"])
    return await jobs.answer_voice(project_id, coro)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@tool(
    "edit_site",
    "Change the existing website. Write one self-contained English instruction. Keep the word 'this' if the user pointed at an element on screen.",
    {"instruction": {"type": "string", "description": "The change, in clear English."}},
    ["instruction"],
    filler="On it.",
)
async def edit_site(project_id: str, args: dict) -> dict:
    instruction = str(args.get("instruction", "")).strip()
    if not instruction:
        return {"status": "failed", "message": "What would you like me to change?"}
    if store.load_meta(project_id)["current"] == 0:
        return {"status": "failed", "message": "The page is still blank. Use plan_site to plan the website first."}
    return await jobs.answer_voice(project_id, engine.edit(project_id, instruction))


@tool("undo_change", "Undo the last change to the website.", filler="Undoing that.")
async def undo_change(project_id: str, args: dict) -> dict:
    result = await engine.undo(project_id)
    return {"status": "done" if result["ok"] else "failed", "message": result["message"]}


@tool("describe_site", "Describe what is on the website now: name, sections in order, the selected element.", filler="Let me look.")
async def describe_site(project_id: str, args: dict) -> dict:
    return {"status": "done", "message": engine.describe(project_id)}


STAGE_SPEECH = {
    "research": "the researcher is planning the design",
    "team": "Font Knight is choosing fonts, the copywriter is writing and the researcher is finding photos",
    "architect": "the architect is building the site",
    "critic": "the design critic is polishing it",
    "repair": "the senior developer is fixing a build error",
    "jnr": "the developer is making the change",
    "snr": "the senior developer is making the change",
}


@tool("check_progress", "How the latest background plan, build or edit is going. Call it when a tool said in_progress, or the user asks if it's done.", filler="Let me check.")
async def check_progress(project_id: str, args: dict) -> dict:
    job = events.job(project_id)
    if not job:
        return {"status": "idle", "message": "Nothing is being built right now."}
    if job["state"] == "working":
        stage = STAGE_SPEECH.get(job.get("stage"), "it is still working")
        return {"status": "in_progress", "message": f"Still working: {stage}. It should be ready in a few seconds."}
    if job["state"] == "failed":
        return {"status": "failed", "message": f"The last job didn't work: {job.get('reason', 'unknown error')}"}
    if job["state"] == "planned":
        return {"status": "needs_approval", "message": f"The plan is ready. {job.get('summary', '')} Shall I build it, or change anything?"}
    return {"status": "done", "message": f"Finished. {job.get('summary', '')}"}


# ---------------------------------------------------------------------------
# Publish (human in the loop, enforced here)
# ---------------------------------------------------------------------------

def _owner_token(project_id: str) -> str:
    return users.github_token(users.get(store.load_meta(project_id).get("owner")))


def _ours(meta: dict, full: str) -> bool:
    """A repo this project already published or pushed to may be updated."""
    known = {(meta.get("published") or {}).get("repo"), (meta.get("github") or {}).get("repo")}
    return full.lower() in {k.lower() for k in known if k}


@tool(
    "publish_site",
    "Put the website online on GitHub Pages. Call it first WITHOUT confirmed to get what will happen, read that to the user "
    "and ask. Only after the user clearly says yes, call it again with confirmed: true and the same repo_name.",
    {
        "repo_name": {"type": "string", "description": "GitHub repository name (lowercase, hyphens), e.g. sweet-crumbs. Optional the first time."},
        "confirmed": {"type": "boolean", "description": "true only if the user explicitly said yes to the summary you just read out."},
    },
    ["confirmed"],
    filler="Let me get that ready.",
)
async def publish_site(project_id: str, args: dict) -> dict:
    meta = store.load_meta(project_id)
    if meta["current"] == 0:
        return {"status": "failed", "message": "There's nothing to publish yet. Let's build the site first."}
    token = _owner_token(project_id)
    if not token:
        events.publish(project_id, {"type": "needs_github"})
        how = "Please connect GitHub using the button on screen" if auth.enabled().get("github") else "GitHub sign-in isn't set up on this server"
        return {"status": "needs_github", "message": f"{how}, then ask me again. Or download the ZIP from the top bar."}
    if (meta.get("publish") or {}).get("state") in ("pushing", "deploying", "checking"):
        return {"status": "in_progress", "message": "It's already being published. Ask me for the publication status."}

    published = meta.get("published") or {}
    default = published.get("repo", "").split("/")[-1] or publish.repo_slug((meta.get("brief") or {}).get("business", ""))
    name = publish.repo_slug(str(args.get("repo_name") or "")) if args.get("repo_name") else default
    if not publish.valid_repo_name(name):
        return {"status": "failed", "message": "That repository name won't work. Try letters, numbers and hyphens."}
    owner = await github.login(token)
    full = f"{owner}/{name}"

    if not _ours(meta, full) and await github.repo_exists(token, owner, name):
        city = ((meta.get("brief") or {}).get("location") or "")
        options = await publish.suggestions(token, owner, name, city)
        store.update(project_id, publish_approval=None)
        events.publish(project_id, {"type": "approval", "action": "publish", "state": "name_taken", "repo": full, "options": options})
        return {
            "status": "name_taken",
            "message": f"You already have a repository called {name}. I could use {' or '.join(options) or 'another name'}. Which one?",
            "options": options,
        }

    url = f"https://{owner.lower()}.github.io/{name}/"
    approval = meta.get("publish_approval") or {}
    approved = args.get("confirmed") is True and approval.get("repo") == full and time.time() - approval.get("ts", 0) < APPROVAL_TTL
    if not approved:
        # First call (or a confirmation that doesn't match what the user was told): ask.
        store.update(project_id, publish_approval={"repo": full, "ts": time.time()})
        if _ours(meta, full) and published:
            summary = f"I'll update your live site at {url} with the latest version."
        else:
            summary = f"I'll create a public GitHub repository called {name} under your account {owner}, and put the site live at {url}."
        events.publish(project_id, {"type": "approval", "action": "publish", "state": "needs_approval", "repo": full, "url": url, "message": summary})
        return {"status": "needs_approval", "message": summary + " Shall I go ahead?", "repo_name": name}

    store.update(project_id, publish_approval=None)
    events.publish(project_id, {"type": "approval", "action": "publish", "state": "approved", "repo": full})
    jobs.run(project_id, publish.run(project_id, token, owner, name))
    return {"status": "in_progress", "message": f"Publishing now. GitHub usually takes under a minute; I'll tell you when {url} is live."}


@tool("publication_status", "Whether the site is live yet after publish_site, and its address.", filler="Checking.")
async def publication_status(project_id: str, args: dict) -> dict:
    meta = store.load_meta(project_id)
    state = meta.get("publish") or {}
    published = meta.get("published") or {}
    if state.get("state") in publish.STATE_SPEECH:
        return {"status": "in_progress", "message": f"Not yet: {publish.STATE_SPEECH[state['state']]}."}
    if state.get("state") == "failed":
        return {"status": "failed", "message": f"Publishing didn't work: {state.get('reason', 'unknown error')}"}
    if published.get("url"):
        stale = published.get("version") != meta["current"]
        note = " It shows an older version; say republish to update it." if stale else ""
        return {"status": "done", "message": f"Your site is live at {published['url']}.{note}", "url": published["url"]}
    return {"status": "idle", "message": "The site isn't published yet."}


# ---------------------------------------------------------------------------
# Typed fallback
# ---------------------------------------------------------------------------

UNDO_WORDS = ("undo", "undo that", "go back")
GO_AHEAD = ("build it", "go ahead", "yes", "yes build it", "looks good", "build", "build the site", "approve", "ok build it")
PUBLISH_WORDS = ("publish", "put it online", "go live", "republish", "publish it", "deploy")


def typed_tool(project_id: str, text: str) -> tuple[str, dict]:
    """Maps a typed command straight to a tool, without the conversation model."""
    words = text.lower().strip(" .!")
    meta = store.load_meta(project_id)
    if words in UNDO_WORDS:
        return "undo_change", {}
    if words in PUBLISH_WORDS:
        return "publish_site", {"confirmed": False}
    if meta.get("draft"):
        if words in GO_AHEAD:
            return "build_site", {}
        return "plan_site", {"description": f"{meta['draft']['description']} {text}"}
    if meta["current"] == 0:
        return "plan_site", {"description": text}
    return "edit_site", {"instruction": text}
