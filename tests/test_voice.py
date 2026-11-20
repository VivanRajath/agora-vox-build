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
