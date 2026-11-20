"""Puts a site live on GitHub Pages.

  1. source project  -> the repo's default branch (created public if needed)
  2. built site      -> an orphan gh-pages branch (relative asset paths, so /<repo>/ works)
  3. Pages enabled from gh-pages via the REST API
  4. poll the Pages build, then the URL itself, until it answers 200

Progress is kept in meta["publish"] and streamed as "publish" events; the final
URL is saved in meta["published"] and announced with a "published" event.
"""
import asyncio
import logging
import re
import time

import httpx

from . import events, github, store
from .editor import engine

log = logging.getLogger("vox-build.publish")

LIVE_TIMEOUT = 240  # seconds to wait for GitHub Pages to serve the site
POLL_EVERY = 5

STATE_SPEECH = {
    "pushing": "I'm uploading the site to GitHub",
    "deploying": "GitHub Pages is building the site, usually under a minute",
    "checking": "the site is almost live, I'm checking the address",
}


def repo_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60].strip("-") or "my-website"


def valid_repo_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name or "")) and name not in (".", "..")


async def suggestions(token: str, owner: str, name: str, city: str = "") -> list[str]:
    """Two free alternatives to a taken repo name."""
    candidates = [f"{name}-{repo_slug(city)}" if city else "", f"{name}-site", f"{name}-online", f"{name}-web", f"{name}-{time.gmtime().tm_year}"]
    free = []
    for candidate in dict.fromkeys(c for c in candidates if c and c != name):
        if not await github.repo_exists(token, owner, candidate):
            free.append(candidate)
        if len(free) == 2:
            break
    return free


def _set(project_id: str, **state) -> None:
    store.update(project_id, publish=state)
    events.publish(project_id, {"type": "publish", **state})


async def _retry(step):
    """Runs a GitHub step, retrying a transient failure once."""
    try:
        return await step()
    except (github.GitHubError, httpx.HTTPError) as e:
        if isinstance(e, github.GitHubError) and not e.transient:
            raise
        log.info("transient GitHub error, retrying: %s", e)
        await asyncio.sleep(2)
        return await step()


async def run(project_id: str, token: str, owner: str, repo: str) -> dict:
    """The whole publish; returns {ok, message} and never raises."""
    meta = store.load_meta(project_id)
    n = meta["current"]
    business = (meta.get("brief") or {}).get("business") or "My website"
    version = next((v for v in meta["versions"] if v["n"] == n), {})
    full = f"{owner}/{repo}"
    try:
        _set(project_id, state="pushing", repo=full, version=n)
        built = await engine.ensure_preview(project_id, n)
        if built is None:
            raise github.GitHubError("the site couldn't be built for publishing")
        source = store.export_files(project_id)
        await _retry(lambda: github.push(
            token, full, source, message=f"Vox Build v{n}: {version.get('summary') or 'Update site'}",
            private=False, description=f"{business} website, built with Vox Build",
        ))
        site = {p.relative_to(built).as_posix(): p.read_bytes() for p in sorted(built.rglob("*")) if p.is_file()}
        site[".nojekyll"] = b""
        await _retry(lambda: github.push_branch(token, full, "gh-pages", site, f"Publish v{n}"))

        _set(project_id, state="deploying", repo=full, version=n)
        url = await _retry(lambda: github.enable_pages(token, full)) or f"https://{owner.lower()}.github.io/{repo}/"
        await _wait_live(project_id, token, full, url, n)
    except (github.GitHubError, httpx.HTTPError) as e:
        reason = str(e).removeprefix("GitHub: ")
        log.warning("publish of %s failed: %s", project_id, reason)
        _set(project_id, state="failed", repo=full, version=n, reason=reason)
        return {"ok": False, "message": f"Publishing didn't work: {reason}"}

    published = {"url": url, "repo": full, "version": n, "ts": time.time()}
    store.update(project_id, published=published, github={"repo": full, "url": f"https://github.com/{full}"})
    _set(project_id, state="live", repo=full, version=n, url=url)
    events.publish(project_id, {"type": "published", **published})
    return {"ok": True, "message": f"Your site is live at {url}"}


async def _wait_live(project_id: str, token: str, full: str, url: str, n: int) -> None:
    deadline = time.monotonic() + LIVE_TIMEOUT
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as http:
        while time.monotonic() < deadline:
            status = await _retry(lambda: github.pages_status(token, full))
            if status == "errored":
                raise github.GitHubError("GitHub Pages couldn't build the site")
            if status == "built":
                _set(project_id, state="checking", repo=full, version=n, url=url)
                try:
                    if (await http.get(url, params={"v": n})).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
            await asyncio.sleep(POLL_EVERY)
    raise github.GitHubError("GitHub Pages is taking longer than usual. It should be live within a few minutes")
