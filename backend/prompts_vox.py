"""Vox's system prompt: the persona in agents/vox/SOUL.md plus live project state.

Agora's history only keeps spoken text, not tool results, so every turn re-states
what the project looks like now (versions, latest job, draft plan, publication).
"""
from functools import lru_cache

from . import events, store, users
from .config import ROOT_DIR


@lru_cache(maxsize=1)
def soul() -> str:
    path = ROOT_DIR / "agents" / "vox" / "SOUL.md"
    return path.read_text(encoding="utf-8") if path.is_file() else "You are Vox, a friendly voice web designer."


def greeting(meta: dict) -> str:
    business = (meta.get("brief") or {}).get("business")
    if meta.get("current", 0) > 0 and business:
        return f"Hi again! What would you like to change on the {business} website?"
    return "Hi, I'm Vox. Tell me about your business, and I'll plan a website for it."


def state(project_id: str) -> str:
    meta = store.load_meta(project_id)
    brief = meta.get("brief") or {}
    lines = ["Current project state (updated every turn, trust it over your memory):"]
    if meta["current"] == 0:
        lines.append("- The website is still blank.")
    else:
        version = next((v for v in meta["versions"] if v["n"] == meta["current"]), {})
        lines.append(f"- The website for {brief.get('business') or 'the user'} is at version {meta['current']}: {version.get('summary', '')}")
    if meta.get("draft"):
        lines.append(f"- A plan is waiting for approval: {meta['draft'].get('spoken', '')}")
    job = events.job(project_id)
    if job:
        detail = job.get("summary") or job.get("reason") or job.get("stage") or ""
        lines.append(f"- Latest job ({job.get('instruction', '')[:80]}): {job['state']} {detail}".rstrip())
    if meta.get("selection"):
        lines.append(f"- The user has clicked {meta['selection'].get('label', 'an element')} in the preview, so 'this' means that element.")
    published = meta.get("published") or {}
    if published.get("url"):
        lines.append(f"- Published at {published['url']} (version {published.get('version', '?')}).")
    owner = users.get(meta.get("owner"))
    lines.append(f"- GitHub is {'connected' if users.github_token(owner) else 'not connected'}.")
    return "\n".join(lines)


def system(project_id: str) -> str:
    return f"{soul()}\n\n{state(project_id)}"
