"""Vox Build backend.

  /api/*      browser API (projects, typed commands, SSE, account + AI settings,
              zip download, GitHub push)
  /api/voice  start/stop an Agora Conversational AI agent (voice.py)
  /llm/*      OpenAI-compatible endpoint Agora calls each turn (vox_llm.py)
  /auth/*     Google / GitHub sign-in
  /preview/*  serves generated sites to the preview iframe
"""
import io
import ipaddress
import logging
import mimetypes
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, events, github, jobs, llm, local_site, steering, store, users, voice, vox_llm, vox_tools
from .editor import engine, images

log = logging.getLogger("vox-build")
# Windows may not know .wasm; the browser needs the right type to compile the noise-suppression module.
mimetypes.add_type("application/wasm", ".wasm")
# uvicorn only configures its own loggers; show ours too (tool calls, builds, publishing).
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
app = FastAPI(title="Vox Build")
if config.FRONTEND_ORIGINS:
    # The app runs on another site (Vercel) and calls this backend directly: allow it, with cookies, and
    # send the session cookie on those cross-site requests (SameSite=None needs HTTPS).
    _exact = [o for o in config.FRONTEND_ORIGINS if "*" not in o]
    _wild = [re.escape(o).replace(r"\*", "[a-z0-9-]*") for o in config.FRONTEND_ORIGINS if "*" in o]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_exact,
        allow_origin_regex="^(" + "|".join(_wild) + ")$" if _wild else None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, session_cookie="vox-build", max_age=30 * 86400, same_site="none", https_only=True)
else:
    app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, session_cookie="vox-build", max_age=30 * 86400, same_site="lax")
app.include_router(auth.router)
app.include_router(voice.router)
app.include_router(vox_llm.router)

INJECT_JS = Path(__file__).with_name("preview_inject.js")
NO_STORE = {"Cache-Control": "no-store"}
# Generated pages are untrusted (anyone with a project link can edit them) and
# share this origin with the session cookie. Sandboxing without allow-same-origin
# gives them an opaque origin, in the iframe and when opened directly, so their
# scripts can't call /api as the signed-in viewer (e.g. push to their GitHub).
PREVIEW_SANDBOX = "sandbox allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals allow-downloads"

def _require_project(project_id: str) -> dict:
    try:
        return store.load_meta(project_id)
    except store.ProjectNotFound:
        raise HTTPException(404, "Project not found")


def _claim(request: Request, meta: dict) -> None:
    """A user (or this browser's guest account) who works on an unowned project
    becomes its owner, so voice and typed edits run on their AI settings."""
    user = auth.current_user(request)
    if user and not meta.get("owner"):
        store.update(meta["id"], owner=user["id"])


# ---------------------------------------------------------------------------
# Browser API
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    return {
        "voice_enabled": config.voice_enabled(),
        "llm_provider": config.LLM_PROVIDER,
        "auth": auth.enabled(),
        "default_models": config.DEFAULT_MODELS,
    }


@app.post("/api/projects")
async def create_project(request: Request):
    meta = store.create()
    # A guest account per browser, so keys added in Settings apply to its projects.
    auth.current_or_guest(request)
    _claim(request, meta)
    return store.load_meta(meta["id"])


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    return _require_project(project_id)


@app.post("/api/projects/{project_id}/command")
async def typed_command(project_id: str, request: Request):
    """Typed fallback for the voice flow. Runs in the background; results arrive over SSE."""
    meta = _require_project(project_id)
    text = str((await request.json()).get("text", "")).strip()
    if not text:
        raise HTTPException(400, "Empty command")
    _claim(request, meta)
    name, args = vox_tools.typed_tool(project_id, text)
    jobs.spawn(vox_tools.run(project_id, name, args))
    return JSONResponse({"accepted": True}, status_code=202)


@app.post("/api/projects/{project_id}/publish")
async def publish_site(project_id: str, request: Request):
    """The approval card's buttons. Same tool and same server-side approval check as voice."""
    meta = _require_project(project_id)
    _claim(request, meta)
    body = await request.json()
    args = {"confirmed": body.get("confirmed") is True}
    if body.get("repo_name"):
        args["repo_name"] = str(body["repo_name"])
    return await vox_tools.run(project_id, "publish_site", args)


@app.post("/api/projects/{project_id}/undo")
async def undo(project_id: str):
    _require_project(project_id)
    return await engine.undo(project_id)


@app.post("/api/projects/{project_id}/restore/{n}")
async def restore(project_id: str, n: int):
    _require_project(project_id)
    try:
        return await engine.restore(project_id, n)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/projects/{project_id}/select")
async def select(project_id: str, request: Request):
    _require_project(project_id)
    selection = (await request.json()).get("selection")
    store.set_selection(project_id, selection)
    events.publish(project_id, {"type": "selection", "selection": selection})
    return {"ok": True}


@app.post("/api/projects/{project_id}/layout")
async def page_layout(project_id: str, request: Request):
    """Where each photo sits in the preview (section, left/right, background), reported by the page itself."""
    _require_project(project_id)
    items = (await request.json()).get("images") or []
    images.remember_layout(project_id, items if isinstance(items, list) else [])
    return {"ok": True}


@app.get("/api/projects/{project_id}/steering")
async def get_steering(project_id: str):
    return steering.get(_require_project(project_id))


@app.post("/api/projects/{project_id}/steering")
async def set_steering(project_id: str, request: Request):
    """The Steer panel: standing rules for every agent, and how Vox behaves."""
    _require_project(project_id)
    return steering.save(project_id, await request.json())


@app.post("/api/projects/{project_id}/stop")
async def stop_job(project_id: str):
    _require_project(project_id)
    return {"stopped": jobs.stop(project_id)}


@app.post("/api/projects/{project_id}/mode")
async def set_mode(project_id: str, request: Request):
    """The One-shot / Business switch. Vox picks it up on its next turn (the state is re-read every turn)."""
    _require_project(project_id)
    result = await vox_tools.run(project_id, "set_mode", {"mode": (await request.json()).get("mode", "")})
    if result["status"] != "done":
        raise HTTPException(400, result["message"])
    return store.load_meta(project_id)


@app.get("/api/projects/{project_id}/events")
async def project_events(project_id: str):
    _require_project(project_id)
    return StreamingResponse(
        events.stream(project_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/projects/{project_id}/download")
async def download(project_id: str):
    meta = _require_project(project_id)
    buf = io.BytesIO()
    files = store.export_files(project_id)
    files.setdefault("README.md", _readme((meta.get("brief") or {}).get("business") or "My website").encode())
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    name = _slug((meta.get("brief") or {}).get("business", "")) or f"site-{project_id}"
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"', **NO_STORE},
    )


@app.post("/api/projects/{project_id}/github")
async def push_to_github(project_id: str, request: Request):
    meta = _require_project(project_id)
    user = auth.require_user(request)
    token = users.github_token(user)
    if not token:
        raise HTTPException(403, "Connect your GitHub account first")
    body = await request.json()
    files = store.export_files(project_id)
    business = (meta.get("brief") or {}).get("business") or "My website"
    files.setdefault("README.md", _readme(business).encode())
    version = next((v for v in meta["versions"] if v["n"] == meta["current"]), {})
    try:
        result = await github.push(
            token,
            str(body.get("repo", "")),
            files,
            message=f"Vox Build v{meta['current']}: {version.get('summary') or 'Update site'}",
            private=bool(body.get("private", False)),
            description=f"{business} website, built with Vox Build",
        )
    except github.GitHubError as e:
        raise HTTPException(400, str(e))
    store.update(project_id, github={"repo": result["repo"], "url": result["url"]})
    return result


def _readme(business: str) -> str:
    return (
        f"# {business}\n\nBuilt by voice with [Vox Build](https://github.com/VivanRajath/vox-build): "
        "a Vite + React + Tailwind CSS project.\n\n"
        "```bash\nnpm install\nnpm run dev      # http://localhost:5173\nnpm run build    # static site in dist/\n```\n\n"
        "Text, prices, hours and links live in `src/data/site.js`; each section is a component in `src/components/`.\n"
    )


def _slug(text: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in text.lower()).split())[:60]


# ---------------------------------------------------------------------------
# Account + AI settings
# ---------------------------------------------------------------------------

@app.get("/api/me")
async def me(request: Request):
    return {"user": users.public(auth.current_user(request))}


def _check_base_url(provider: str, base_url: str) -> str:
    if provider != "custom":
        return ""
    url = urlparse(base_url.strip())
    if url.scheme != "https" or not url.hostname:
        raise HTTPException(400, "Base URL must be an https:// URL")
    host = url.hostname.lower()
    try:
        private = not ipaddress.ip_address(host).is_global
    except ValueError:
        private = host == "localhost" or host.endswith((".local", ".internal", ".localhost"))
    if private:
        # The server makes these requests, so it must not be pointed at its own network.
        raise HTTPException(400, "Base URL must be a public address")
    return base_url.strip().rstrip("/")


def _settings_view(user: dict) -> dict:
    return {
        **users.ai_view(user),
        "user": users.public(user),
        # Which providers the server's .env already has keys for (never the keys).
        "server": {
            "providers": {p: len(keys) for p, keys in config.ENV_KEYS.items() if keys},
            "default_provider": config.LLM_PROVIDER,
            "pexels": bool(config.PEXELS_API_KEY),
        },
        "default_models": config.DEFAULT_MODELS,
    }


@app.get("/api/settings")
async def get_settings(request: Request):
    return _settings_view(auth.current_or_guest(request))


@app.post("/api/settings/keys")
async def add_key(request: Request):
    """Adds an API key after checking it works (listing models doubles as the check)."""
    user = auth.current_or_guest(request)
    body = await request.json()
    provider = str(body.get("provider", ""))
    if provider not in users.PROVIDERS:
        raise HTTPException(400, "Unknown provider")
    api_key = str(body.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(400, "Paste an API key")
    base_url = _check_base_url(provider, str(body.get("base_url", "")))
    try:
        models = await llm.list_models(provider, api_key, base_url)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    entry = users.add_key(user, provider, api_key, base_url, str(body.get("label", "")))
    return {**_settings_view(user), "added": entry["id"], "models": models}


@app.delete("/api/settings/keys/{key_id}")
async def remove_key(key_id: str, request: Request):
    user = auth.current_or_guest(request)
    if not users.remove_key(user, key_id):
        raise HTTPException(404, "Key not found")
    return _settings_view(user)


@app.post("/api/settings/models/list")
async def list_models(request: Request):
    """Models for a provider, using the user's first key for it or the server's."""
    user = auth.current_or_guest(request)
    provider = str((await request.json()).get("provider", ""))
    key = next((k for k in users.ai_view(user)["keys"] if k["provider"] == provider), None)
    creds = users.key_credentials(user, key["id"]) if key else None
    if not creds and config.ENV_KEYS.get(provider):
        creds = (provider, config.ENV_KEYS[provider][0], "")
    if not creds:
        raise HTTPException(400, "Add a key for this provider first")
    try:
        return {"models": await llm.list_models(*creds)}
    except llm.LLMError as e:
        raise HTTPException(400, str(e))


@app.put("/api/settings/models")
async def save_models(request: Request):
    user = auth.current_or_guest(request)
    models = (await request.json()).get("models") or {}
    for tier in users.TIERS:
        pick = models.get(tier) or {}
        if pick.get("provider") == "custom" and not str(pick.get("model") or "").strip():
            raise HTTPException(400, "Custom providers need a model name")
    users.set_models(user, models)
    return _settings_view(user)


@app.put("/api/settings/pexels")
async def save_pexels(request: Request):
    user = auth.current_or_guest(request)
    api_key = str((await request.json()).get("api_key") or "").strip()
    if api_key:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://api.pexels.com/v1/search", params={"query": "coffee", "per_page": 1}, headers={"Authorization": api_key})
        if r.status_code in (401, 403):
            raise HTTPException(400, "Pexels rejected that key")
    users.set_pexels(user, api_key or None)
    return _settings_view(user)


# ---------------------------------------------------------------------------
# Preview + frontend
# ---------------------------------------------------------------------------

@app.get("/preview-inject.js")
async def preview_inject():
    return FileResponse(INJECT_JS, media_type="application/javascript", headers=NO_STORE)


@app.get("/preview/{project_id}/{path:path}")
async def preview(project_id: str, path: str = ""):
    """The built site of the current version, built on demand if it's missing (v0, or after a restart)."""
    meta = _require_project(project_id)
    built = await engine.ensure_preview(project_id, meta["current"])
    if built is None:
        raise HTTPException(503, "This version couldn't be built")
    site = built.resolve()
    target = (site / (path or "index.html")).resolve()
    if target != site and site not in target.parents:
        raise HTTPException(404)
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        raise HTTPException(404)
    if target.suffix == ".html":
        html = target.read_text(encoding="utf-8")
        tag = '<script src="/preview-inject.js"></script>'
        idx = html.lower().rfind("</body>")
        html = html[:idx] + tag + html[idx:] if idx != -1 else html + tag
        return Response(html, media_type="text/html", headers={**NO_STORE, "Content-Security-Policy": PREVIEW_SANDBOX})
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type, headers=NO_STORE)


@app.post("/api/projects/{project_id}/publish-local")
async def publish_local(project_id: str, request: Request):
    """The 'Put it online' button: the current version at /<website-name>/ on this server."""
    meta = _require_project(project_id)
    if meta["current"] == 0:
        raise HTTPException(400, "There's nothing to put online yet. Build the site first.")
    built = await engine.ensure_preview(project_id, meta["current"])
    if built is None:
        raise HTTPException(503, "This version couldn't be built")
    return local_site.publish(app, project_id, built, str(request.base_url))


if config.FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIST, html=True), name="frontend")
local_site.mount_all(app)  # sites published earlier, ahead of the frontend mount
