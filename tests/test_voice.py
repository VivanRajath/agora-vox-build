import json

import pytest
from fastapi.testclient import TestClient

from backend import llm, store, voice, vox_llm, vox_tools
from backend.agora_token.AccessToken2 import AccessToken, ServiceRtc
from backend.agora_token.RtcTokenBuilder2 import Role_Publisher, RtcTokenBuilder
from backend.main import app

client = TestClient(app)


# --- per-session LLM token -------------------------------------------------

def test_llm_token_roundtrip():
    token = voice.make_llm_token("abc123def456")
    assert voice.verify_llm_token(token, "abc123def456")


def test_llm_token_rejects_other_project_tamper_and_expiry():
    token = voice.make_llm_token("abc123def456")
    assert not voice.verify_llm_token(token, "ffffffffffff")
    assert not voice.verify_llm_token(token[:-2] + "xx", "abc123def456")
    assert not voice.verify_llm_token(token.replace("vox1.abc123def456", "vox1.ffffffffffff"), "ffffffffffff")
    expired = voice.make_llm_token("abc123def456", ttl=10, now=1000)
    assert not voice.verify_llm_token(expired, "abc123def456", now=2000)
    assert not voice.verify_llm_token("", "abc123def456")


# --- Agora token (vendored builder) ------------------------------------------

def test_rtc_token_is_verifiable():
    app_id, cert = "a" * 32, "b" * 32
    token = RtcTokenBuilder.build_token_with_uid(app_id, cert, "vox-chan", 1234, Role_Publisher, 600, 600)
    assert token.startswith("007")
    parsed = AccessToken()
    assert parsed.from_string(token)
    assert parsed.verify_signature(cert)
    assert parsed.get_services(ServiceRtc.kServiceType)


def test_join_body_shape(monkeypatch):
    meta = store.create()
    monkeypatch.setattr(voice.config, "PUBLIC_BASE_URL", "https://example.test")
    body = voice.join_body(meta["id"], "chan", 2000001, 1001, "agent-token", "llm-token")
    props = body["properties"]
    assert props["remote_rtc_uids"] == ["1001"] and props["agent_rtc_uid"] == "2000001"
    assert props["llm"]["url"] == f"https://example.test/llm/{meta['id']}/chat/completions"
    assert props["llm"]["api_key"] == "llm-token"
    assert props["tts"]["vendor"] == "murf"
    assert props["asr"]["vendor"] in ("deepgram", "ares")
    if props["asr"]["vendor"] == "deepgram":
        assert props["asr"]["credential_mode"] == "managed" and props["asr"]["params"]["model"] == "nova-3"
    assert "turn_detection" not in props  # Agora's defaults unless END_OF_SPEECH / INTERRUPT_MS are set
    assert {"api_key", "voiceId", "locale", "model", "sample_rate", "base_url"} <= set(props["tts"]["params"])


# --- /llm endpoint -----------------------------------------------------------

def _events(text: str) -> list:
    lines = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    return [json.loads(line) for line in lines[:-1]]


def test_llm_endpoint_requires_token():
    meta = store.create()
    r = client.post(f"/llm/{meta['id']}/chat/completions", json={"messages": [], "stream": True})
    assert r.status_code == 401


def test_llm_endpoint_streams_tool_loop(monkeypatch):
    meta = store.create()
    calls = []

    async def fake_chat(system, messages, tier, tools=None, max_tokens=0, json_mode=False):
        calls.append(messages)
        if len(calls) == 1:
            return llm.Reply("Sure, checking the page.", [llm.ToolCall("t1", "describe_site", {})])
        assert messages[-1]["role"] == "tool"
        return llm.Reply("It's a **blank** page at https://ana.github.io/bake/ for now.")

    monkeypatch.setattr(llm, "chat", fake_chat)
    token = voice.make_llm_token(meta["id"])
    r = client.post(
        f"/llm/{meta['id']}/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"stream": True, "messages": [{"role": "system", "content": "x"}, {"role": "user", "content": "what's on my page?"}]},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    chunks = _events(r.text)
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert "Sure, checking the page." in text
    assert "**" not in text and "https://" not in text
    assert "ana dot github dot io slash bake" in text


def test_llm_endpoint_accepts_header_token_and_non_stream(monkeypatch):
    meta = store.create()

    async def fake_chat(*args, **kwargs):
        return llm.Reply("Hello there.")

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post(
        f"/llm/{meta['id']}/chat/completions",
        headers={"X-Vox-Token": voice.make_llm_token(meta["id"])},
        json={"stream": False, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hello there."


def test_speakable():
    assert vox_llm.speakable("- **Done** — see `x`") == "Done, see x"


@pytest.mark.parametrize("text,tool", [("undo", "undo_change"), ("Undo that.", "undo_change")])
def test_typed_undo(text, tool):
    meta = store.create()
    assert vox_tools.typed_tool(meta["id"], text)[0] == tool


def test_anthropic_history_conversion():
    msgs = llm._anthropic_messages([
        {"role": "assistant", "content": "Hi, I'm Vox."},
        {"role": "user", "content": "plan a bakery"},
        {"role": "assistant", "content": "Sure.", "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "plan_site", "arguments": '{"description": "bakery"}'}}]},
        {"role": "tool", "tool_call_id": "t1", "content": '{"status": "needs_approval"}'},
    ])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user"]
    assert msgs[3]["content"][1] == {"type": "tool_use", "id": "t1", "name": "plan_site", "input": {"description": "bakery"}}
    assert msgs[4]["content"][0]["type"] == "tool_result"


@pytest.mark.parametrize("text,ok", [
    ("Yes.", True), ("yes go ahead", True), ("Sounds good, build it!", True), ("okay let's do it", True),
    ("yes but add a gallery", False), ("no", False), ("make it blue", False), ("what colours?", False),
])
def test_is_approval(text, ok):
    assert vox_llm.is_approval(text) is ok


def test_yes_builds_the_draft_without_the_model(monkeypatch):
    meta = store.create()
    store.update(meta["id"], draft={"description": "a cafe", "brief": {}, "spoken": "plan"})
    ran = []

    async def fake_run(pid, name, args):
        ran.append((name, args))
        return {"status": "in_progress", "message": "Building the site now."}

    async def no_model(*a, **k):
        raise AssertionError("the model should not be called for a plain yes")

    monkeypatch.setattr(vox_tools, "run", fake_run)
    monkeypatch.setattr(llm, "chat", no_model)
    r = client.post(
        f"/llm/{meta['id']}/chat/completions",
        headers={"Authorization": f"Bearer {voice.make_llm_token(meta['id'])}"},
        json={"stream": True, "messages": [{"role": "assistant", "content": "Shall I build it?"}, {"role": "user", "content": "Yes, go ahead."}]},
    )
    assert ran == [("build_site", {})]
    assert "Building the site now." in "".join(c["choices"][0]["delta"].get("content", "") for c in _events(r.text))


def test_plan_is_spoken_verbatim(monkeypatch):
    meta = store.create()
    rounds = []

    async def fake_chat(*a, **k):
        rounds.append(1)
        return llm.Reply("", [llm.ToolCall("t1", "plan_site", {"description": "a cafe"})])

    async def fake_run(pid, name, args):
        return {"status": "needs_approval", "message": "Sections: hero, menu, visit. Shall I build it?"}

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(vox_tools, "run", fake_run)
    r = client.post(
        f"/llm/{meta['id']}/chat/completions",
        headers={"Authorization": f"Bearer {voice.make_llm_token(meta['id'])}"},
        json={"stream": True, "messages": [{"role": "user", "content": "a website for my cafe"}]},
    )
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in _events(r.text))
    assert "Sections: hero, menu, visit. Shall I build it?" in text and len(rounds) == 1


def test_speakable_strips_reasoning():
    assert vox_llm.speakable("<think>plan the call</think>Building it now. </think") == "Building it now."


# --- one-shot and business modes ----------------------------------------------

def _tool_names(pid):
    return {s["name"] for s in vox_tools.specs(pid)}


def test_mode_decides_which_tools_vox_sees():
    meta = store.create()
    assert vox_tools.mode(meta) == "business"
    names = _tool_names(meta["id"])
    assert "submit_brief" in names and "create_site" not in names and "plan_site" not in names
    store.update(meta["id"], mode="oneshot")
    names = _tool_names(meta["id"])
    assert "create_site" in names and "submit_brief" not in names and "build_site" not in names


def test_set_mode_endpoint_and_greeting():
    from backend import prompts_vox
    meta = store.create()
    r = client.post(f"/api/projects/{meta['id']}/mode", json={"mode": "oneshot"})
    assert r.status_code == 200 and r.json()["mode"] == "oneshot"
    assert "in one go" in prompts_vox.greeting(store.load_meta(meta["id"]))
    assert "ONE-SHOT" in prompts_vox.state(meta["id"])
    assert client.post(f"/api/projects/{meta['id']}/mode", json={"mode": "nope"}).status_code == 400


def test_typed_request_follows_mode():
    meta = store.create()
    assert vox_tools.typed_tool(meta["id"], "a bakery site")[0] == "plan_site"
    store.update(meta["id"], mode="oneshot")
    assert vox_tools.typed_tool(meta["id"], "a bakery site") == ("create_site", {"description": "a bakery site", "heard": "a bakery site"})


def test_create_site_generates_straight_away(monkeypatch):
    import asyncio
    from backend.editor import engine
    meta = store.create()
    seen = {}

    async def fake_generate(pid, description, brief=None):
        seen["description"], seen["brief"] = description, brief
        return {"ok": True, "message": "Built it."}

    monkeypatch.setattr(engine, "generate", fake_generate)
    result = asyncio.run(vox_tools.run(meta["id"], "create_site", {"description": "a comic-book themed movie theatre"}))
    assert seen == {"description": "a comic-book themed movie theatre", "brief": None}
    assert result["status"] == "done"


def test_submit_brief_needs_the_key_answers_then_plans(monkeypatch):
    import asyncio
    meta = store.create()
    missing = asyncio.run(vox_tools.run(meta["id"], "submit_brief", {"business_name": "Wild Trails Zoo", "what_it_does": "a zoo"}))
    assert missing["status"] == "failed" and "target audience" in missing["message"]

    planned = {}

    async def fake_plan(pid, args):
        planned["description"] = args["description"]
        return {"status": "needs_approval", "message": "Plan. Shall I build it?"}

    monkeypatch.setattr(vox_tools, "plan_site", fake_plan)
    answers = {"business_name": "Wild Trails Zoo", "what_it_does": "a zoo", "target_audience": "families",
               "goal": "buy tickets", "theme": "jungle adventure", "colours": "none"}
    result = asyncio.run(vox_tools.run(meta["id"], "submit_brief", answers))
    assert result["status"] == "needs_approval"
    assert "- Target customers: families" in planned["description"] and "Colours" not in planned["description"]
    assert store.load_meta(meta["id"])["interview"]["goal"] == "buy tickets"


def test_empty_turn_is_not_answered_again(monkeypatch):
    meta = store.create()

    async def no_model(*a, **k):
        raise AssertionError("an empty turn must not re-answer the previous request")

    monkeypatch.setattr(llm, "chat", no_model)
    r = client.post(
        f"/llm/{meta['id']}/chat/completions",
        headers={"Authorization": f"Bearer {voice.make_llm_token(meta['id'])}"},
        json={"stream": True, "messages": [
            {"role": "user", "content": "build a zoo site"}, {"role": "assistant", "content": "Building it."},
            {"role": "user", "content": ""},
        ]},
    )
    assert r.status_code == 200
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in _events(r.text)) == ""


def test_background_results_are_announced_once():
    from backend import jobs
    meta = store.create()
    pid = meta["id"]
    assert voice._announcement(pid, {"type": "job_done", "version": {"summary": "Built it."}}) is None  # turn already said it
    jobs.announce.add(pid)
    text = voice._announcement(pid, {"type": "job_done", "version": {"summary": "Built the zoo site."}})
    assert "Built the zoo site." in text and pid not in jobs.announce
    assert "live at" in voice._announcement(pid, {"type": "published", "url": "https://ana.github.io/zoo/"})


def test_claimed_action_without_tool_gets_one_more_round(monkeypatch):
    meta = store.create()
    store.update(meta["id"], current=1)
    rounds = []

    async def fake_chat(system, messages, tier, tools=None, max_tokens=0, json_mode=False):
        rounds.append(messages[-1]["content"])
        if len(rounds) == 1:
            return llm.Reply("Got it, finding a new photo now.")
        if messages[-1]["role"] == "tool":
            return llm.Reply("It will show up in a few seconds.")
        return llm.Reply("", [llm.ToolCall("t1", "change_image", {"target": "this", "new_subject": "a superhero"})])

    ran = []

    async def fake_run(pid, name, args):
        ran.append(name)
        return {"status": "in_progress", "message": "Working on it."}

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(vox_tools, "run", fake_run)
    client.post(
        f"/llm/{meta['id']}/chat/completions",
        headers={"Authorization": f"Bearer {voice.make_llm_token(meta['id'])}"},
        json={"stream": True, "messages": [{"role": "user", "content": "use a superhero picture here"}]},
    )
    assert ran == ["change_image"] and vox_llm.NUDGE in rounds[1]
    assert not vox_llm.claims_action("Which photo do you mean?")


def test_change_image_trusts_the_users_words_over_a_bare_this(monkeypatch):
    import asyncio
    from backend.editor import engine
    meta = store.create()
    store.update(meta["id"], current=1)
    seen = []

    async def fake(pid, target, subject):
        seen.append(target)
        return {"ok": True, "message": "Swapped."}

    monkeypatch.setattr(engine, "change_image", fake)
    for said in ("Can you change the parkour photo?", "change this image"):
        vox_tools.turn_text.set(said)
        asyncio.run(vox_tools.TOOLS["change_image"].handler(meta["id"], {"target": "this"}))
    assert seen == ["Can you change the parkour photo?", "this"]


def test_clicking_an_element_is_described_and_announced():
    from backend import prompts_vox
    sel = {"path": "section#hero > h1", "html": '<h1 class="x">Who is <span>Spider-Man</span>?</h1>', "vox_id": "Hero"}
    speech = prompts_vox.selection_speech(sel)
    assert speech == "That's the main heading in the hero section. It says: Who is Spider-Man? What would you like to change?"
    assert voice._announcement("p", {"type": "selection", "selection": sel}) == speech
    assert voice._announcement("p", {"type": "selection", "selection": None}) is None
    meta = store.create()
    store.set_selection(meta["id"], sel)
    assert 'It reads: "Who is Spider-Man?"' in prompts_vox.state(meta["id"])


def test_goodbyes_are_not_claimed_actions():
    assert not vox_llm.claims_action("Got it. If you need anything else, just let me know. Have a great day!")
    assert vox_llm.claims_action("Sure, swapping that photo now.")


def test_spoken_one_shot_is_read_back_before_building(monkeypatch):
    import asyncio
    from backend.editor import engine
    meta = store.create()
    store.update(meta["id"], mode="oneshot")
    built = []

    async def fake_generate(pid, description, brief=None):
        built.append(description)
        return {"ok": True, "message": "Built it."}

    monkeypatch.setattr(engine, "generate", fake_generate)
    vox_tools.turn_text.set("build an avengers doomsday site")
    first = asyncio.run(vox_tools.run(meta["id"], "create_site", {"description": "An Avengers Doomsday fan site...", "heard": "an Avengers Doomsday fan site", "confirmed": True}))
    assert first["status"] == "needs_approval" and "Avengers Doomsday" in first["message"] and built == []
    # "yes" goes through the server shortcut, which builds exactly what was read back
    assert vox_llm.approved_action(meta["id"], "Yes, build it")[0] == "create_site"
    name, args = vox_llm.approved_action(meta["id"], "yes")
    asyncio.run(vox_tools.run(meta["id"], name, args))
    assert built == ["An Avengers Doomsday fan site..."]
    assert not store.load_meta(meta["id"]).get("oneshot_pending")
