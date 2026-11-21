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


# The job running for each project, so the user can stop it ("stop", the Stop button).
current: dict[str, asyncio.Task] = {}


def run(project_id: str, coro) -> asyncio.Task:
    """Runs an edit job with the project owner's AI settings."""
    settings = owner_settings(project_id)

    async def with_settings():
        llm.use_settings(settings)
        try:
            return await coro
        except asyncio.CancelledError:
            from . import events
            events.publish(project_id, {"type": "job_failed", "instruction": "", "reason": "Stopped, as you asked."})
            announce.discard(project_id)
            return {"ok": False, "message": "Stopped."}

    task = spawn(with_settings())
    current[project_id] = task
    task.add_done_callback(lambda t: current.pop(project_id, None) if current.get(project_id) is t else None)
    return task


def stop(project_id: str) -> bool:
    """Cancels the project's running job. True if there was one."""
    task = current.get(project_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


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
