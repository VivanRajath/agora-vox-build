"""Vox's system prompt: the persona in agents/vox/SOUL.md plus live project state.

Agora's history only keeps spoken text, not tool results, so every turn re-states
what the project looks like now (versions, latest job, draft plan, publication).
"""
import html as html_lib
import re
from functools import lru_cache

from . import events, steering, store, users, vox_tools
from .config import ROOT_DIR


@lru_cache(maxsize=1)
def soul() -> str:
    path = ROOT_DIR / "agents" / "vox" / "SOUL.md"
    return path.read_text(encoding="utf-8") if path.is_file() else "You are Vox, a friendly voice web designer."


MODE_RULES = {
    "oneshot": """Mode: ONE-SHOT. The user describes the website in one go: no interview, no plan. As soon as you know what
the site is for, call create_site with the request expanded into one rich English paragraph (what it is, the theme in detail:
mood, colours, imagery, type feel; fitting sections; every name, place and detail they said) and `heard`, a short version to
read back. It returns a check-back question: read it out exactly. On yes, call create_site again with confirmed: true. If the
user corrects you ("I said Avengers, not Aranj"), call create_site again with the corrected request (not confirmed).""",
    "business": """Mode: BUSINESS. Interview the owner like a friendly web designer before anything is planned.
Ask ONE short question per turn, in this order, skipping anything already answered:
1) the business name and what it does, 2) who the customers are, 3) what visitors should do on the site (book, buy,
call, visit, enquire), 4) the look and feel or theme, and any colours, 5) must-have sections or features,
6) location and contact details or opening hours (optional; skip if they don't know).
Acknowledge each answer in a few words ("Families with kids, got it."). When you have 1 to 4, or the user says "that's all"
or "just build it", call submit_brief with everything you learned, in English. It returns a plan: read it out and ask
"Shall I build it, or change anything?". On yes, call build_site. If they want a change to that plan (photos of X, another
section, a colour), call revise_plan with just the change: everything else in the plan stays. Only call submit_brief again if
they describe a completely different business.""",
}


def greeting(meta: dict) -> str:
    business = (meta.get("brief") or {}).get("business")
    if meta.get("current", 0) > 0 and business:
        return f"Hi again! What would you like to change on the {business} website?"
    if vox_tools.mode(meta) == "oneshot":
        return "Hi, I'm Vox. Describe the website you want in one go, and I'll build it straight away."
    return "Hi, I'm Vox. Let's plan your website together. What's your business called, and what does it do?"


def state(project_id: str) -> str:
    meta = store.load_meta(project_id)
    brief = meta.get("brief") or {}
    lines = ["Current project state (updated every turn, trust it over your memory):"]
    if meta["current"] == 0:
        lines.append("- The website is still blank.")
        lines.append(MODE_RULES[vox_tools.mode(meta)])
    else:
        version = next((v for v in meta["versions"] if v["n"] == meta["current"]), {})
        lines.append(f"- The website for {brief.get('business') or 'the user'} is at version {meta['current']}: {version.get('summary', '')}")
    if meta.get("draft"):
        lines.append(
            f"- A plan is waiting for approval: {meta['draft'].get('spoken', '')}. Changes to it go to revise_plan. If the user agrees in ANY language "
            "(yes, ಹೌದು, हाँ, go ahead...), call build_site NOW; never ask again."
        )
    if meta.get("oneshot_pending") and meta["current"] == 0:
        lines.append(
            f"- You already read back this one-shot request and asked to build it: {meta['oneshot_pending'].get('heard', '')}. "
            "If the user agrees in ANY language or words (yes, ok, start, go ahead, ಹೌದು, ಸರಿ, ಶುರು ಮಾಡಿ, हाँ, ठीक है...), "
            "call create_site with confirmed: true NOW. Never ask the same question twice. Only if they change the "
            "request, call create_site again with the new request."
        )
    job = events.job(project_id)
    if job:
        detail = job.get("summary") or job.get("reason") or job.get("stage") or ""
        lines.append(f"- Latest job ({job.get('instruction', '')[:80]}): {job['state']} {detail}".rstrip())
    if meta.get("selection"):
        f = selection_facts(meta["selection"])
        where = f" in the {f['section']} section" if f.get("section") else ""
        content = f' It reads: "{f["text"]}".' if f.get("text") else ""
        lines.append(
            f"- The user has clicked {f.get('kind', 'an element')}{where} in the preview, so 'this' means that element.{content} "
            "If they ask what it is or to read it out, read that back to them."
        )
    publishing = meta.get("publish") or {}
    if publishing.get("state") in ("pushing", "deploying", "checking"):
        lines.append(f"- Publishing to GitHub Pages is in progress ({publishing['state']}); use publication_status.")
    elif publishing.get("state") == "failed":
        lines.append(f"- The last publish failed: {publishing.get('reason', '')}")
    if (meta.get("publish_approval") or {}).get("repo"):
        lines.append(f"- You asked the user to approve publishing to {meta['publish_approval']['repo']}; waiting for a clear yes.")
    published = meta.get("published") or {}
    if published.get("url"):
        stale = published.get("version") != meta["current"]
        lines.append(f"- Published at {published['url']} (version {published.get('version', '?')})" + (", with unpublished changes since." if stale else "."))
    owner = users.get(meta.get("owner"))
    lines.append(f"- GitHub is {'connected' if users.github_token(owner) else 'not connected'}.")
    return "\n".join(lines)


KINDS = {
    "img": "the photo", "picture": "the photo", "h1": "the main heading", "h2": "the heading", "h3": "the heading",
    "h4": "the heading", "p": "the text", "a": "the link", "button": "the button", "section": "the section",
    "nav": "the menu", "header": "the header", "footer": "the footer", "li": "the list item", "form": "the form",
    "input": "the input field", "span": "the text", "blockquote": "the quote", "figure": "the picture", "ul": "the list",
}


def _words_for(name: str) -> str:
    """'CharacterBio' or 'character-bio' -> 'character bio'."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", name or "").replace("-", " ").lower().strip()


def selection_facts(selection: dict | None) -> dict:
    """What the clicked element is, in words: kind, its text (or a photo's alt), and its section."""
    if not selection:
        return {}
    html = selection.get("html", "")
    tag = (re.match(r"\s*<([a-zA-Z0-9]+)", html) or [None, ""])[1].lower()
    alt = re.search(r'alt="([^"]*)"', html)
    text = html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()
    text = re.sub(r"\s+([?.!,:;])", r"\1", text)
    if tag == "img":
        text = html_lib.unescape(alt.group(1)) if alt else ""
    section = _words_for(selection.get("vox_id", "")) or _words_for((re.search(r"section#([\w-]+)", selection.get("path", "")) or [None, ""])[1])
    return {"kind": KINDS.get(tag, "that element"), "text": text[:300], "section": section}


def selection_speech(selection: dict | None) -> str:
    """What Vox says the moment the user clicks something."""
    f = selection_facts(selection)
    if not f:
        return ""
    where = f" in the {f['section']} section" if f["section"] else ""
    if f["kind"] == "the photo":
        what = f"That's the photo{': ' + f['text'] if f['text'] else ''}{where}."
    elif f["text"]:
        said = f["text"][:160].rstrip()
        what = f"That's {f['kind']}{where}. It says: {said}{'' if said[-1:] in '.?!' else '.'}"
    else:
        what = f"That's {f['kind']}{where}."
    return f"{what} What would you like to change?"


def system(project_id: str) -> str:
    return f"{soul()}\n\n{state(project_id)}\n\n{steering.for_vox(store.load_meta(project_id))}"
