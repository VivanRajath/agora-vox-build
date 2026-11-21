"""Voice sessions on Agora Conversational AI.

POST /api/voice/start  mints RTC tokens, starts an Agora agent in a fresh channel and
                       returns what the browser needs to join it (never any keys)
POST /api/voice/stop   asks Agora to remove the agent

The agent listens (Agora ASR), thinks through our /llm endpoint (vox_llm.py) and
speaks with Murf Falcon. Agora authenticates to /llm with a per-session token
signed here with TOOL_SECRET.
"""
import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
import time

import httpx
from fastapi import APIRouter, HTTPException, Request

from . import auth, config, events, jobs, prompts_vox, store
from .agora_token.RtcTokenBuilder2 import Role_Publisher, RtcTokenBuilder

log = logging.getLogger("vox-build.voice")
router = APIRouter()

TOKEN_TTL = 4 * 3600        # LLM session token and RTC tokens
IDLE_TIMEOUT = 60           # seconds the agent stays after the user leaves

# agent_id -> project_id for agents this server started, so /stop only touches our own.
_agents: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Per-session LLM token: "vox1.<project_id>.<expires>.<signature>"
# ---------------------------------------------------------------------------

def _sign(payload: str) -> str:
    digest = hmac.new(config.TOOL_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def make_llm_token(project_id: str, ttl: int = TOKEN_TTL, now: float | None = None) -> str:
    payload = f"vox1.{project_id}.{int((now or time.time()) + ttl)}"
    return f"{payload}.{_sign(payload)}"


def verify_llm_token(token: str, project_id: str, now: float | None = None) -> bool:
    parts = (token or "").split(".")
    if len(parts) != 4 or parts[0] != "vox1" or parts[1] != project_id:
        return False
    try:
        expires = int(parts[2])
    except ValueError:
        return False
    if expires < (now or time.time()):
        return False
    return hmac.compare_digest(parts[3], _sign(".".join(parts[:3])))


# ---------------------------------------------------------------------------
# Agora REST
# ---------------------------------------------------------------------------

def _auth_header() -> dict:
    raw = f"{config.AGORA_CUSTOMER_ID}:{config.AGORA_CUSTOMER_SECRET}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def _agora_error(r: httpx.Response) -> str:
    """A short reason for the UI; the full (truncated) body goes to the log."""
    log.warning("Agora %s %s: %s", r.request.url.path, r.status_code, r.text[:500])
    try:
        body = r.json()
    except ValueError:
        body = {}
    reason = body.get("reason") or body.get("detail") or ""
    if r.status_code in (401, 403):
        return "Agora rejected the credentials. Check AGORA_CUSTOMER_ID and AGORA_CUSTOMER_SECRET, and that Conversational AI is enabled for this project."
    if r.status_code == 429:
        return "Agora's concurrent agent limit is reached. Try again in a minute."
    return f"Agora couldn't start the voice agent ({r.status_code}{': ' + str(reason)[:120] if reason else ''})."


def join_body(project_id: str, channel: str, agent_uid: int, user_uid: int, agent_token: str, llm_token: str) -> dict:
    meta = store.load_meta(project_id)
    body = {
        "name": f"{channel}-agent",
        "properties": {
            "channel": channel,
            "token": agent_token,
            "agent_rtc_uid": str(agent_uid),
            "remote_rtc_uids": [str(user_uid)],
            "idle_timeout": IDLE_TIMEOUT,
            "llm": {
                "url": f"{config.PUBLIC_BASE_URL}/llm/{project_id}/chat/completions",
                "api_key": llm_token,
                # How Agora sends api_key isn't documented, so the token also rides in a header of our own.
                "headers": {"X-Vox-Token": llm_token},
                "system_messages": [{"role": "system", "content": prompts_vox.soul()}],
                "greeting_message": prompts_vox.greeting(meta),
                "failure_message": "Sorry, I lost my train of thought. Could you say that again?",
                "max_history": 20,
                "params": {"model": "vox"},
            },
            "asr": _asr(),
            "tts": {
                "vendor": "murf",
                "params": {
                    "api_key": config.MURF_API_KEY,
                    "base_url": config.MURF_WS_URL,
                    "voiceId": config.MURF_VOICE_ID,
                    "locale": config.MURF_LOCALE,
                    "model": config.MURF_MODEL,
                    "sample_rate": 24000,
                },
            },
        },
    }
    body["properties"].update(_turn_detection(), **_noise())
    return body


def _asr() -> dict:
    """Speech recognition. Deepgram nova-3 under Agora's managed credentials (no Deepgram key, billed
    to the Agora account) hears accents and names better than ARES; ARES is the fallback."""
    if config.ASR_VENDOR == "deepgram":
        return {
            "credential_mode": "managed",
            "vendor": "deepgram",
            "params": {"url": "wss://api.deepgram.com/v1/listen", "model": config.ASR_MODEL, "language": config.ASR_LANGUAGE},
        }
    return {"vendor": "ares", "language": config.ASR_LANGUAGE}


def _noise() -> dict:
    """Selective Attention Locking (beta): the agent locks onto the person who speaks first, clearly,
    and blocks most background voices and noise. No voiceprint needed in this mode."""
    if not config.NOISE_LOCK:
        return {}
    return {"advanced_features": {"enable_sal": True}, "sal": {"sal_mode": "locking"}}


def _turn_detection() -> dict:
    """Only when configured: with these set, calls went silent, so Agora's defaults are the default.
    Field names from the join reference (turn_detection.config; the flat fields are deprecated)."""
    cfg = {}
    if config.INTERRUPT_MS:
        cfg["start_of_speech"] = {"mode": "vad", "vad_config": {"interrupt_duration_ms": config.INTERRUPT_MS, "speaking_interrupt_duration_ms": config.INTERRUPT_MS}}
    if config.END_OF_SPEECH:
        cfg["end_of_speech"] = {"mode": config.END_OF_SPEECH}
    return {"turn_detection": {"mode": "default", "config": cfg}} if cfg else {}


async def _watch(agent_id: str, project_id: str) -> None:
    """Logs what Agora's agent has heard during a call, so a silent call can be told apart from
    a mic problem (nothing heard) or an ASR problem (heard, but no turn reached /llm)."""
    url = f"{config.AGORA_API_BASE}/projects/{config.AGORA_APP_ID}/agents/{agent_id}"
    seen = -1
    for _ in range(24):  # up to 6 minutes
        await asyncio.sleep(15)
        if agent_id not in _agents:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                status = (await client.get(url, headers=_auth_header())).json().get("status")
                r = await client.get(url + "/history", headers=_auth_header())
        except httpx.HTTPError as e:
            log.info("agent %s: watch failed: %s", agent_id, e)
            continue
        contents = (r.json() if r.status_code == 200 else {}).get("contents") or []
        if len(contents) != seen:
            seen = len(contents)
            last_user = next((c.get("content") for c in reversed(contents) if c.get("role") == "user"), None)
            log.info("agent %s [%s] status=%s history=%d last_user=%r", agent_id, project_id, status, seen, last_user)
        if status in ("STOPPED", "FAILED"):
            return


# ---------------------------------------------------------------------------
# Announcements: Vox only answers when the user speaks, so results that land later
# (a build that outlived the turn, publishing) are spoken with Agora's speak endpoint.
# ---------------------------------------------------------------------------

def _announcement(project_id: str, event: dict) -> str | None:
    kind = event.get("type")
    if kind == "selection" and event.get("selection"):
        return prompts_vox.selection_speech(event["selection"])
    if kind in ("job_done", "job_failed"):
        if project_id not in jobs.announce:
            return None  # the voice turn already said the result
        jobs.announce.discard(project_id)
        if kind == "job_failed":
            return f"That didn't work: {event.get('reason', 'something went wrong')}. Want me to try again?"
        summary = (event.get("version") or {}).get("summary", "")
        return f"Done! {summary} Take a look. What would you like to change?"
    if kind == "published" and event.get("url"):
        return f"Your site is live at {event['url']}. The QR code is on screen."
    if kind == "publish" and event.get("state") == "failed":
        return f"Publishing didn't work: {event.get('reason', '')}. Want me to try again?"
    return None


def _on_event(project_id: str, event: dict) -> None:
    agent_id = next((a for a, pid in _agents.items() if pid == project_id), None)
    if not agent_id:
        if event.get("type") in ("job_done", "job_failed"):
            jobs.announce.discard(project_id)
        return
    text = _announcement(project_id, event)
    if text:
        # A click is the user acting now, so it cuts in; results wait for Vox to finish its sentence.
        priority = "INTERRUPT" if event.get("type") == "selection" else "APPEND"
        asyncio.get_running_loop().create_task(speak(agent_id, text, priority))


async def speak(agent_id: str, text: str, priority: str = "APPEND") -> None:
    from .vox_llm import speakable  # vox_llm imports this module
    text = speakable(text)
    data = text.encode("utf-8")[:500].decode("utf-8", "ignore")  # Agora's limit is 512 bytes
    url = f"{config.AGORA_API_BASE}/projects/{config.AGORA_APP_ID}/agents/{agent_id}/speak"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # The user can always talk over an announcement.
            r = await client.post(url, headers=_auth_header(), json={"text": data, "priority": priority, "interruptable": True})
        if r.status_code >= 400:
            _agora_error(r)
        else:
            log.info("agent %s announced: %s", agent_id, data[:120])
    except httpx.HTTPError as e:
        log.info("agent %s: announcement failed: %s", agent_id, e)


events.add_listener(_on_event)


async def _leave(agent_id: str) -> None:
    url = f"{config.AGORA_API_BASE}/projects/{config.AGORA_APP_ID}/agents/{agent_id}/leave"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, headers=_auth_header())
    if r.status_code >= 400 and r.status_code != 404:
        _agora_error(r)
    _agents.pop(agent_id, None)


@router.post("/api/voice/start")
async def start(request: Request):
    if not config.voice_enabled():
        raise HTTPException(503, "Voice is not configured on this server. Type below instead.")
    try:
        body = await request.json()
    except ValueError:
        body = {}
    project_id = str(body.get("project_id", ""))
    if not store.exists(project_id):
        raise HTTPException(404, "Project not found")
    user = auth.current_or_guest(request)
    if user and not store.load_meta(project_id).get("owner"):
        store.update(project_id, owner=user["id"])

    # One agent per project: a second tab or a reconnect replaces the old one.
    for agent_id, pid in list(_agents.items()):
        if pid == project_id:
            await _leave(agent_id)

    channel = f"vox-{project_id}-{secrets.token_hex(3)}"
    user_uid, agent_uid = secrets.randbelow(10**6) + 1000, secrets.randbelow(10**6) + 2_000_000
    app_id, cert = config.AGORA_APP_ID, config.AGORA_APP_CERTIFICATE
    user_token = RtcTokenBuilder.build_token_with_uid(app_id, cert, channel, user_uid, Role_Publisher, TOKEN_TTL, TOKEN_TTL)
    agent_token = RtcTokenBuilder.build_token_with_rtm(app_id, cert, channel, str(agent_uid), Role_Publisher, TOKEN_TTL, TOKEN_TTL)
    if not user_token or not agent_token:
        raise HTTPException(500, "Couldn't sign Agora tokens. Check AGORA_APP_ID and AGORA_APP_CERTIFICATE (32 hex characters each).")

    payload = join_body(project_id, channel, agent_uid, user_uid, agent_token, make_llm_token(project_id))
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.post(f"{config.AGORA_API_BASE}/projects/{app_id}/join", headers=_auth_header(), json=payload)
        except httpx.HTTPError as e:
            log.warning("Agora join unreachable: %s", e)
            raise HTTPException(502, "Couldn't reach Agora. Check the server's network.")
    if r.status_code >= 400:
        raise HTTPException(502, _agora_error(r))
    agent_id = r.json().get("agent_id", "")
    _agents[agent_id] = project_id
    log.info("voice agent %s started for %s in %s (asr %s)", agent_id, project_id, channel, config.ASR_LANGUAGE)
    asyncio.create_task(_watch(agent_id, project_id))
    return {"app_id": app_id, "channel": channel, "uid": user_uid, "token": user_token, "agent_id": agent_id, "agent_uid": agent_uid}


@router.post("/api/voice/stop")
async def stop(request: Request):
    # sendBeacon posts on tab close, sometimes as text/plain.
    try:
        body = await request.json()
    except ValueError:
        body = {}
    agent_id = str((body or {}).get("agent_id", ""))
    if agent_id in _agents:
        await _leave(agent_id)
    return {"ok": True}
