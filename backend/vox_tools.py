"""Vox's tools: one registry used by the /llm endpoint (voice) and the typed fallback.

Each tool has a JSON schema the conversation model sees, a short filler line Vox
says while it runs, and an async handler returning {status, message}.
status is done | in_progress | failed (plus tool-specific states).
"""
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import events, jobs, store
from .editor import engine

log = logging.getLogger("vox-build.tools")

Handler = Callable[[str, dict], Awaitable[dict]]


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
# Site tools
# ---------------------------------------------------------------------------

@tool(
    "generate_site",
    "Build a brand-new website from a description. Use when the page is still blank or the user wants to start over.",
    {"description": {"type": "string", "description": "One rich paragraph in English: business name, what it does, city, audience, style, colours, sections."}},
    ["description"],
    filler="Great, I'm getting the team on it.",
)
async def generate_site(project_id: str, args: dict) -> dict:
    description = str(args.get("description", "")).strip()
    if not description:
        return {"status": "failed", "message": "I need a short description of the website first."}
    return await jobs.answer_voice(project_id, engine.generate(project_id, description))


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
        return await jobs.answer_voice(project_id, engine.generate(project_id, instruction))
    return await jobs.answer_voice(project_id, engine.edit(project_id, instruction))


@tool("undo_change", "Undo the last change to the website.", filler="Undoing that.")
async def undo_change(project_id: str, args: dict) -> dict:
    result = await engine.undo(project_id)
    return {"status": "done" if result["ok"] else "failed", "message": result["message"]}


@tool("describe_site", "Describe what is on the website now: title, sections in order, the selected element.", filler="Let me look.")
async def describe_site(project_id: str, args: dict) -> dict:
    return {"status": "done", "message": engine.describe(project_id)}


STAGE_SPEECH = {
    "research": "the researcher is planning the design",
    "team": "Font Knight is choosing fonts and the copywriter is writing the text",
    "architect": "the architect is building the page",
    "critic": "the design critic is polishing it",
    "build": "the site is being compiled",
    "jnr": "the developer is making the change",
    "snr": "the senior developer is making the change",
}


@tool("check_progress", "How the latest background build or edit is going. Call it when a tool said in_progress, or the user asks if it's done.", filler="Let me check.")
async def check_progress(project_id: str, args: dict) -> dict:
    job = events.job(project_id)
    if not job:
        return {"status": "idle", "message": "Nothing is being built right now."}
    if job["state"] == "working":
        stage = STAGE_SPEECH.get(job.get("stage"), "it is still working")
        return {"status": "in_progress", "message": f"Still working: {stage}. It should be ready in a few seconds."}
    if job["state"] == "failed":
        return {"status": "failed", "message": f"The last change didn't work: {job.get('reason', 'unknown error')}"}
    return {"status": "done", "message": f"Finished. {job.get('summary', '')}"}


# ---------------------------------------------------------------------------
# Typed fallback
# ---------------------------------------------------------------------------

UNDO_WORDS = ("undo", "undo that", "go back")


def typed_tool(project_id: str, text: str) -> tuple[str, dict]:
    """Maps a typed command straight to a tool, without the conversation model."""
    if text.lower().strip(" .!") in UNDO_WORDS:
        return "undo_change", {}
    if store.load_meta(project_id)["current"] == 0:
        return "generate_site", {"description": text}
    return "edit_site", {"instruction": text}
