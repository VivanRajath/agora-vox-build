"""Background jobs shared by the browser API, the typed fallback and Vox's tools."""
import asyncio

from . import config, llm, store, users

_background: set[asyncio.Task] = set()

# Projects whose job outlived the voice turn ("working on it"): Vox announces the result when it lands.
announce: set[str] = set()


def spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def owner_settings(project_id: str) -> dict | None:
    """The project owner's AI settings (their keys and models), or None for the server's .env."""
    return users.ai_settings(users.get(store.load_meta(project_id).get("owner")))


def run(project_id: str, coro) -> asyncio.Task:
    """Runs an edit job with the project owner's AI settings."""
    settings = owner_settings(project_id)

    async def with_settings():
        llm.use_settings(settings)
        return await coro

    return spawn(with_settings())


async def answer_voice(project_id: str, coro) -> dict:
    """Waits briefly so quick edits can be confirmed in the same breath;
    otherwise lets the job finish in the background (the preview updates via SSE)."""
    task = run(project_id, coro)
    try:
        result = await asyncio.wait_for(asyncio.shield(task), config.TOOL_SYNC_WAIT)
    except asyncio.TimeoutError:
        announce.add(project_id)
        return {
            "status": "in_progress",
            "message": "Working on it. It will show up on screen in a few seconds.",
            "note": "NOT finished yet. Say it's on its way; never say it's done. You'll announce the result automatically when it lands.",
        }
    return {"status": "done" if result["ok"] else "failed", "message": result["message"]}
