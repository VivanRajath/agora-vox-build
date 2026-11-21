"""'Put it online' on this machine: the current version is published at /<website-name>/.

The name comes from the site ("Aurora Branding" -> /aurora-branding/), stays the same when the site is
republished, and gets -2, -3... if another project already uses it. The published copy is a snapshot:
later edits don't show there until the user publishes again.
"""
import json
import re
import shutil
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
from starlette.routing import Mount, Route

from . import config, events, store

SITES_DIR = config.DATA_DIR.parent / "sites"
INDEX = SITES_DIR / "index.json"
# Paths the app itself uses; a site can't take these names.
RESERVED = {"api", "auth", "preview", "llm", "tools", "assets", "denoiser", "favicon.svg", "index.html", "preview-inject.js", "sites"}
BASE_URL = config.LOCAL_SITE_BASE
APP = None  # the FastAPI app, set by mount_all at startup, for publishes from Vox's tools


def _index() -> dict[str, str]:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_index(index: dict[str, str]) -> None:
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(index, indent=1), encoding="utf-8")


def slug_for(project_id: str) -> str:
    """The site's web name: kept from an earlier publish, else from the business name, made unique."""
    meta = store.load_meta(project_id)
    known = (meta.get("local_site") or {}).get("slug")
    if known:
        return known
    name = (meta.get("brief") or {}).get("business") or (meta.get("draft") or {}).get("brief", {}).get("business") or "my website"
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48].strip("-") or "my-website"
    if base in RESERVED:
        base += "-site"
    index, slug, n = _index(), base, 2
    while index.get(slug) not in (None, project_id):
        slug, n = f"{base}-{n}", n + 1
    return slug


def mount(app, slug: str) -> None:
    """Serves SITES_DIR/<slug> at /<slug>/, ahead of the app's own catch-all frontend mount."""
    path = f"/{slug}"
    if any(isinstance(r, Mount) and r.path == path for r in app.router.routes):
        return
    app.router.routes.insert(0, Mount(path, app=StaticFiles(directory=SITES_DIR / slug, html=True), name=f"site-{slug}"))
    # /aurora-branding -> /aurora-branding/ (the site's relative links need the slash)
    app.router.routes.insert(0, Route(path, endpoint=lambda request: RedirectResponse(path + "/"), name=f"site-{slug}-slash"))


def mount_all(app) -> None:
    global APP
    APP = app
    for slug in _index():
        if (SITES_DIR / slug / "index.html").is_file():
            mount(app, slug)


def publish(app, project_id: str, built: Path, base_url: str | None = None) -> dict:
    """Copies the built current version to /<slug>/ and records it. Returns {slug, url, version}."""
    meta = store.load_meta(project_id)
    slug = slug_for(project_id)
    target = SITES_DIR / slug
    staging = SITES_DIR / f".{slug}.tmp"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(built, staging)
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    index = _index()
    index[slug] = project_id
    _save_index(index)
    mount(app or APP, slug)
    url = f"{(base_url or BASE_URL).rstrip('/')}/{slug}/"
    info = {"slug": slug, "url": url, "version": meta["current"]}
    store.update(project_id, local_site=info)
    events.publish(project_id, {"type": "published", "url": url, "repo": "", "version": meta["current"], "local": True})
    return info
