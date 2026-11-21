
import asyncio
import base64
import re

import httpx

API = "https://api.github.com"
REPO_RE = re.compile(r"^(?:([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/)?([A-Za-z0-9._-]{1,100})$")


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status

    @property
    def transient(self) -> bool:
        return self.status >= 500 or self.status in (0, 429)


def parse_repo(value: str) -> tuple[str | None, str]:
    m = REPO_RE.match(value.strip())
    if not m or m.group(2) in (".", ".."):
        raise GitHubError("Repository names can use letters, numbers, '.', '-' and '_'")
    return m.group(1), m.group(2)


def _check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        try:
            message = r.json().get("message", "")
        except ValueError:
            message = r.text[:200]
        if r.status_code == 401:
            message = "GitHub access expired. Connect GitHub again."
        raise GitHubError(f"GitHub: {message} ({r.status_code})", r.status_code)
    return r.json() if r.content else {}


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API, headers=_headers(token), timeout=60)


async def login(token: str) -> str:
    async with client(token) as gh:
        return _check(await gh.get("/user"))["login"]


async def repo_exists(token: str, owner: str, name: str) -> bool:
    async with client(token) as gh:
        r = await gh.get(f"/repos/{owner}/{name}")
    if r.status_code == 404:
        return False
    _check(r)
    return True


async def push_branch(token: str, full: str, branch: str, files: dict[str, bytes], message: str) -> str:
    """Makes `branch` contain exactly `files` as one new commit (creating the branch if needed,
    as an orphan so it shares no history with main). Returns the commit sha."""
    async with client(token) as gh:
        parent = await _head(gh, full, branch)
        limit = asyncio.Semaphore(6)

        async def blob(path: str, data: bytes) -> dict:
            async with limit:
                sha = _check(await gh.post(f"/repos/{full}/git/blobs", json={"content": base64.b64encode(data).decode(), "encoding": "base64"}))["sha"]
            return {"path": path, "mode": "100644", "type": "blob", "sha": sha}

        tree = await asyncio.gather(*(blob(p, d) for p, d in files.items()))
        tree_sha = _check(await gh.post(f"/repos/{full}/git/trees", json={"tree": tree}))["sha"]
        commit = _check(await gh.post(f"/repos/{full}/git/commits", json={"message": message, "tree": tree_sha, "parents": [parent] if parent else []}))
        if parent:
            _check(await gh.patch(f"/repos/{full}/git/refs/heads/{branch}", json={"sha": commit["sha"], "force": True}))
        else:
            _check(await gh.post(f"/repos/{full}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]}))
    return commit["sha"]


async def enable_pages(token: str, full: str, branch: str = "gh-pages") -> str:
    """Serves `branch` with GitHub Pages; returns the site URL."""
    source = {"source": {"branch": branch, "path": "/"}}
    async with client(token) as gh:
        r = await gh.post(f"/repos/{full}/pages", json=source)
        if r.status_code == 409:  # already enabled: make sure it serves the right branch
            _check(await gh.put(f"/repos/{full}/pages", json=source))
            r = await gh.get(f"/repos/{full}/pages")
        info = _check(r)
    return info.get("html_url") or ""


async def pages_status(token: str, full: str) -> str:
    """Latest Pages build: built, building, queued, errored, or unknown."""
    async with client(token) as gh:
        r = await gh.get(f"/repos/{full}/pages/builds/latest")
    if r.status_code == 404:
        return "queued"
    return _check(r).get("status") or "unknown"


async def push(token: str, repo: str, files: dict[str, bytes], message: str, private: bool, description: str) -> dict:
    owner, name = parse_repo(repo)
    async with client(token) as gh:
        login = _check(await gh.get("/user"))["login"]
        owner = owner or login
        r = await gh.get(f"/repos/{owner}/{name}")
        if r.status_code == 404:
            create_path = "/user/repos" if owner.lower() == login.lower() else f"/orgs/{owner}/repos"
            info = _check(await gh.post(create_path, json={"name": name, "private": private, "description": description, "auto_init": True}))
        else:
            info = _check(r)
        if not info.get("permissions", {}).get("push", True):
            raise GitHubError(f"You don't have push access to {info['full_name']}")
        full, branch = info["full_name"], info.get("default_branch") or "main"

        parent = await _head(gh, full, branch)
        if parent is None:
            # Empty repo: the Git Data API needs one commit to exist first.
            seed = base64.b64encode(f"# {name}\n".encode()).decode()
            _check(await gh.put(f"/repos/{full}/contents/README.md", json={"message": "Initial commit", "content": seed, "branch": branch}))
            parent = await _head(gh, full, branch, wait=True)

        limit = asyncio.Semaphore(6)

        async def blob(path: str, data: bytes) -> dict:
            async with limit:
                sha = _check(await gh.post(f"/repos/{full}/git/blobs", json={"content": base64.b64encode(data).decode(), "encoding": "base64"}))["sha"]
            return {"path": path, "mode": "100644", "type": "blob", "sha": sha}

        tree = await asyncio.gather(*(blob(p, d) for p, d in files.items()))
        tree_sha = _check(await gh.post(f"/repos/{full}/git/trees", json={"tree": tree}))["sha"]
        commit = _check(await gh.post(f"/repos/{full}/git/commits", json={"message": message, "tree": tree_sha, "parents": [parent]}))
        _check(await gh.patch(f"/repos/{full}/git/refs/heads/{branch}", json={"sha": commit["sha"]}))
    return {"repo": full, "url": info["html_url"], "branch": branch, "commit": commit["sha"], "commit_url": commit.get("html_url")}


async def _head(gh: httpx.AsyncClient, full: str, branch: str, wait: bool = False) -> str | None:
    # A just-created repo can take a moment before its branch is readable.
    for attempt in range(4):
        r = await gh.get(f"/repos/{full}/git/ref/heads/{branch}")
        if r.status_code == 200:
            return r.json()["object"]["sha"]
        if r.status_code == 409 and not wait:
            return None  # "Git Repository is empty"
        if r.status_code not in (404, 409):
            _check(r)
        await asyncio.sleep(1 + attempt)
    if wait:
        raise GitHubError("GitHub didn't create the branch in time, try again")
    return None
