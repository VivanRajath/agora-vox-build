import asyncio
import json

from fastapi.testclient import TestClient

from backend import jobs, llm, prompts_vox, steering, store, voice, vox_llm, vox_tools
from backend.main import app

client = TestClient(app)


def test_rules_reach_vox_and_the_builders():
    meta = store.create()
    asyncio.run(vox_tools.run(meta["id"], "steer", {"action": "add_rule", "rule": "Always use a dark theme"}))
    asyncio.run(vox_tools.run(meta["id"], "steer", {"action": "suggestions_off"}))
    m = store.load_meta(meta["id"])
    assert "Always use a dark theme" in prompts_vox.system(meta["id"])
    assert "don't offer ideas" in prompts_vox.system(meta["id"])
    assert "Always use a dark theme" in steering.for_builders(m)
    asyncio.run(vox_tools.run(meta["id"], "steer", {"action": "remove_rule", "rule": "the dark theme one"}))
    assert steering.get(store.load_meta(meta["id"]))["rules"] == []


def test_steering_api_round_trip():
    meta = store.create()
    r = client.post(f"/api/projects/{meta['id']}/steering", json={"rules": ["No photos of people"], "ask_first": False})
    assert r.status_code == 200 and r.json()["ask_first"] is False and r.json()["rules"] == ["No photos of people"]
    assert client.get(f"/api/projects/{meta['id']}/steering").json()["rules"] == ["No photos of people"]


def test_ask_first_off_builds_one_shot_without_read_back(monkeypatch):
    from backend.editor import engine
    meta = store.create()
    store.update(meta["id"], mode="oneshot", steering={"ask_first": False})
    built = []

    async def fake_generate(pid, description, brief=None):
        built.append(description)
        return {"ok": True, "message": "Built."}

    monkeypatch.setattr(engine, "generate", fake_generate)
    vox_tools.turn_text.set("a bakery site")
    result = asyncio.run(vox_tools.run(meta["id"], "create_site", {"description": "A bakery site.", "heard": "a bakery site"}))
    assert result["status"] == "done" and built == ["A bakery site."]


def test_stop_cancels_the_running_job():
    meta = store.create()

    async def scenario():
        async def slow():
            await asyncio.sleep(30)
            return {"ok": True, "message": "never"}
        task = jobs.run(meta["id"], slow())
        await asyncio.sleep(0)
        assert jobs.stop(meta["id"])
        return await task

    assert asyncio.run(scenario())["message"] == "Stopped."


def test_only_the_newest_turn_speaks(monkeypatch):
    meta = store.create()

    async def fake_chat(*a, **k):
        return llm.Reply("Okay, dark it is.")

    monkeypatch.setattr(llm, "chat", fake_chat)

    async def scenario():
        older = [{"role": "user", "content": "Colors."}]
        newer = [{"role": "user", "content": "Colors. Make the whole site dark."}]

        async def collect(msgs):
            return "".join([x async for x in vox_llm.converse(meta["id"], msgs)])

        first = asyncio.create_task(collect(older))  # sounds unfinished, so it waits for more
        await asyncio.sleep(0.2)
        second = await collect(newer)
        return await first, second

    first, second = asyncio.run(scenario())
    assert first == "" and second.strip() == "Okay, dark it is."
