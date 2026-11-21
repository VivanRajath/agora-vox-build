import asyncio

import pytest

from backend import github, jobs, publish, store, vox_tools


@pytest.fixture
def project():
    meta = store.create()
    store.update(meta["id"], current=1, brief={"business": "Sweet Crumbs"})
    return meta["id"]


@pytest.fixture
def gh(monkeypatch):
    """GitHub with one existing repo (ana/taken) and a recorder for publish runs."""
    state = {"existing": {"taken", "taken-site"}, "runs": []}

    async def login(token):
        return "ana"

    async def repo_exists(token, owner, name):
        return name in state["existing"]

    def run(project_id, coro):
        state["runs"].append(coro)
        coro.close()

    monkeypatch.setattr(vox_tools, "_owner_token", lambda pid: "gh-token")
    monkeypatch.setattr(github, "login", login)
    monkeypatch.setattr(github, "repo_exists", repo_exists)
    monkeypatch.setattr(jobs, "run", run)
    return state


def call(pid, **args):
    return asyncio.run(vox_tools.publish_site(pid, args))


def test_without_github_it_asks_then_publishes_locally(project, monkeypatch):
    monkeypatch.setattr(vox_tools, "_owner_token", lambda pid: "")
    result = call(project, confirmed=True)  # a yes before being asked isn't enough
    assert result["status"] == "needs_approval" and "localhost" in result["message"]


def test_confirmed_without_asking_first_is_not_enough(project, gh):
    result = call(project, confirmed=True)
    assert result["status"] == "needs_approval"
    assert "sweet-crumbs" in result["message"] and "ana.github.io/sweet-crumbs" in result["message"]
    assert gh["runs"] == []


def test_publish_after_approval(project, gh):
    assert call(project, confirmed=False)["status"] == "needs_approval"
    assert gh["runs"] == []
    result = call(project, confirmed=True)
    assert result["status"] == "in_progress"
    assert len(gh["runs"]) == 1
    assert store.load_meta(project).get("publish_approval") is None


def test_approval_is_for_one_repo(project, gh):
    call(project, confirmed=False, repo_name="sweet-crumbs")
    assert call(project, confirmed=True, repo_name="other-name")["status"] == "needs_approval"
    assert gh["runs"] == []


def test_name_taken_offers_free_alternatives(project, gh):
    result = call(project, confirmed=False, repo_name="taken")
    assert result["status"] == "name_taken"
    assert "taken-site" not in result["options"] and len(result["options"]) == 2
    assert call(project, confirmed=True, repo_name="taken")["status"] == "name_taken"
    assert gh["runs"] == []


def test_republish_to_own_repo_is_allowed(project, gh):
    store.update(project, published={"url": "https://ana.github.io/taken/", "repo": "ana/taken", "version": 1})
    result = call(project, confirmed=False)
    assert result["status"] == "needs_approval" and "update your live site" in result["message"]
    assert call(project, confirmed=True)["status"] == "in_progress"


def test_publish_run_pushes_source_and_pages(project, monkeypatch, tmp_path):
    built = tmp_path / "built"
    built.mkdir()
    (built / "index.html").write_text("<html></html>")
    calls = []

    async def ensure_preview(pid, n):
        return built

    async def push(token, full, files, **kw):
        calls.append(("push", full, sorted(files)))

    async def push_branch(token, full, branch, files, message):
        calls.append(("branch", branch, sorted(files)))

    async def enable_pages(token, full, branch="gh-pages"):
        return "https://ana.github.io/sweet-crumbs/"

    async def wait_live(*args):
        calls.append(("live",))

    monkeypatch.setattr(publish.engine, "ensure_preview", ensure_preview)
    monkeypatch.setattr(github, "push", push)
    monkeypatch.setattr(github, "push_branch", push_branch)
    monkeypatch.setattr(github, "enable_pages", enable_pages)
    monkeypatch.setattr(publish, "_wait_live", wait_live)
    result = asyncio.run(publish.run(project, "tok", "ana", "sweet-crumbs"))
    assert result["ok"]
    assert ("branch", "gh-pages", [".nojekyll", "index.html"]) in calls
    meta = store.load_meta(project)
    assert meta["published"]["url"] == "https://ana.github.io/sweet-crumbs/"
    assert meta["publish"]["state"] == "live"


def test_publish_run_retries_transient_errors_once(project, monkeypatch, tmp_path):
    attempts = []

    async def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise github.GitHubError("GitHub: Server Error (502)", 502)
        return "ok"

    assert asyncio.run(publish._retry(flaky)) == "ok" and len(attempts) == 2

    async def denied():
        raise github.GitHubError("GitHub: Not Found (404)", 404)

    with pytest.raises(github.GitHubError):
        asyncio.run(publish._retry(denied))


def test_typed_flow_plans_then_builds(project):
    pid = store.create()["id"]
    assert vox_tools.typed_tool(pid, "a bakery in Indiranagar")[0] == "plan_site"
    store.update(pid, draft={"description": "a bakery", "brief": {}, "spoken": "plan"})
    assert vox_tools.typed_tool(pid, "go ahead")[0] == "build_site"
    # A change is added to the waiting plan, not re-planned from scratch.
    assert vox_tools.typed_tool(pid, "add a cakes section") == ("revise_plan", {"change": "add a cakes section"})
    assert vox_tools.typed_tool(pid, "publish")[0] == "publish_site"


def test_put_it_online_serves_the_site_at_its_name(project, monkeypatch):
    import asyncio
    from fastapi.testclient import TestClient
    from backend import local_site, vox_tools
    from backend.main import app
    from backend.editor import engine
    pid = store.create()["id"]
    built = asyncio.run(engine.ensure_preview(pid, 0))
    store.update(pid, current=1, brief={"business": "Aurora Branding"})

    async def ready(project_id, n):
        return built

    monkeypatch.setattr(engine, "ensure_preview", ready)
    monkeypatch.setattr(vox_tools, "_owner_token", lambda pid: "")
    meta = store.load_meta(pid)
    site = local_site.publish(app, pid, built, "http://localhost:8000")
    assert site["slug"].startswith("aurora-branding") and site["url"] == f"http://localhost:8000/{site['slug']}/"
    c = TestClient(app)
    assert c.get(f"/{site['slug']}/").status_code == 200
    assert c.get(f"/{site['slug']}", follow_redirects=False).headers["location"] == f"/{site['slug']}/"
    assert c.get("/api/config").status_code == 200  # the app's own paths still work
    # Voice: without GitHub, "put it online" asks first, then publishes to the same address.
    first = asyncio.run(vox_tools.run(pid, "publish_site", {"confirmed": False}))
    assert first["status"] == "needs_approval" and site["url"] in first["message"]
    done = asyncio.run(vox_tools.run(pid, "publish_site", {"confirmed": True, "repo_name": site["slug"]}))
    assert done["status"] == "done" and site["url"] in done["message"]
