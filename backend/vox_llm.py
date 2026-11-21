"""The brain of Vox: an OpenAI-compatible Chat Completions endpoint for Agora.

Agora's engine POSTs every user turn to /llm/{project_id}/chat/completions with
stream: true. We run the conversation model with Vox's tools server-side (Agora
doesn't execute tools for a custom LLM), then stream the spoken reply back as
chat.completion.chunk SSE events ending in data: [DONE].

Text the model says before a tool call is streamed straight away, so the user
hears "Sure, planning that now" while the tool runs instead of silence.
"""
import json
import logging
import re
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import events, jobs, llm, prompts_vox, store, vox_tools
from .voice import verify_llm_token

log = logging.getLogger("vox-build.llm")
router = APIRouter()

MAX_ROUNDS = 5
MAX_HISTORY = 20
REPLY_TOKENS = 1024


def _token(request: Request) -> str:
    """The session token, from whichever place Agora put it."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-vox-token") or request.headers.get("api-key") or request.query_params.get("token", "")


# ---------------------------------------------------------------------------
# Speakable text
# ---------------------------------------------------------------------------

URL_RE = re.compile(r"https?://([^\s]+)")


def speak_url(url: str) -> str:
    """'https://ana.github.io/sweet-crumbs/' -> 'ana dot github dot io slash sweet-crumbs'."""
    bare = re.sub(r"^https?://", "", url).rstrip("/.,")
    return bare.replace(".", " dot ").replace("/", " slash ")


def speakable(text: str) -> str:
    """No markdown, code, reasoning tags or raw URLs in what Murf reads out."""
    text = re.sub(r"<think>.*?(</think>|$)", " ", text, flags=re.DOTALL)
    text = re.sub(r"</?think>?", " ", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = URL_RE.sub(lambda m: speak_url(m.group(0)), text)
    text = re.sub(r"[*_`#>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

def _history(messages: list[dict]) -> list[dict]:
    """User/assistant text from Agora's history; system messages are replaced by ours."""
    convo = []
    for m in messages or []:
        role = m.get("role")
        if role in ("user", "assistant"):
            text = llm.content_text(m.get("content")).strip()
            if text:
                convo.append({"role": role, "content": text})
    return convo[-MAX_HISTORY:]


def _new_user_text(messages: list[dict]) -> str:
    """The user's words this turn: the newest user/assistant message, if it's the user's and not empty."""
    for m in reversed(messages or []):
        if m.get("role") in ("user", "assistant"):
            return llm.content_text(m.get("content")).strip() if m.get("role") == "user" else ""
    return ""


# A plain "yes" to the question Vox just asked. Anything with a change in it goes to the model.
YES_RE = re.compile(
    r"^(yes|yeah|yep|yup|ya|sure|ok|okay|alright|all right|go ahead|go for it|do it|build it|publish it|"
    r"sounds good|sounds great|looks good|looks great|perfect|great|let'?s do it|let'?s go|please do|approved?)\b"
)
CHANGE_RE = re.compile(r"\b(but|change|instead|add|remove|make|without|except|no|not|don'?t|wait|hold on|what|how|why)\b")

# Results the user must hear word for word (a plan to approve, what publishing will do), so the
# model isn't asked to paraphrase them, and doesn't get a chance to drop them.
SPEAK_VERBATIM = {"needs_approval", "name_taken", "needs_github"}


def is_approval(text: str) -> bool:
    words = re.sub(r"[^\w\s']", " ", text.lower()).strip()
    words = re.sub(r"\s+", " ", words)
    return bool(words) and len(words.split()) <= 8 and bool(YES_RE.match(words)) and not CHANGE_RE.search(words)


def approved_action(project_id: str, user_text: str) -> tuple[str, dict] | None:
    """The tool a plain yes approves, if Vox is waiting on one.

    Small conversation models sometimes answer "yes, go ahead" with "building it now" and no tool
    call, so nothing happens. A yes to a pending question is unambiguous, so the server acts on it.
    The publish approval is still the server's own (publish_site checks it again).
    """
    if not is_approval(user_text):
        return None
    meta = store.load_meta(project_id)
    pending = meta.get("publish_approval") or {}
    if pending.get("repo") and time.time() - pending.get("ts", 0) < vox_tools.APPROVAL_TTL:
        return "publish_site", {"repo_name": pending["repo"].split("/")[-1], "confirmed": True}
    if meta.get("draft") and (events.job(project_id) or {}).get("state") != "working":
        return "build_site", {}
    return None


async def converse(project_id: str, messages: list[dict]):
    """Yields spoken text pieces for one user turn, running tools in between."""
    llm.use_settings(jobs.owner_settings(project_id))
    convo = _history(messages)
    user_text = _new_user_text(messages)
    if not user_text:
        # Agora sometimes sends a turn whose newest user message is empty (noise, a click). Answering
        # the last real message again made Vox repeat itself, so an empty turn gets no reply.
        log.info("empty turn [%s], not answering", project_id)
        return
    if user_text:
        log.info("heard [%s]: %r", project_id, user_text)
        events.publish(project_id, {"type": "transcript", "role": "user", "text": user_text})

    spoken: list[str] = []
    action = approved_action(project_id, user_text) if user_text else None
    if action:
        name, args = action
        log.info("approval %r -> %s %s", user_text, name, args)
        filler = vox_tools.TOOLS[name].filler
        yield filler + " "
        result = await vox_tools.run(project_id, name, args)
        reply = speakable(str(result.get("message", "")))
        if reply:
            yield reply
        events.publish(project_id, {"type": "transcript", "role": "assistant", "text": f"{filler} {reply}".strip()})
        return

    tools = vox_tools.specs(project_id)
    try:
        for _ in range(MAX_ROUNDS):
            reply = await llm.chat(prompts_vox.system(project_id), convo, "vox", tools=tools, max_tokens=REPLY_TOKENS)
            text = speakable(reply.text)
            if text:
                spoken.append(text)
                yield text + " "
            if not reply.tool_calls:
                break
            if not text:
                filler = vox_tools.TOOLS.get(reply.tool_calls[0].name)
                if filler and not spoken:
                    spoken.append(filler.filler)
                    yield filler.filler + " "
            convo.append({
                "role": "assistant",
                "content": reply.text,
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in reply.tool_calls
                ],
            })
            verbatim = None
            for call in reply.tool_calls:
                log.info("tool call %s %s", call.name, call.arguments)
                result = await vox_tools.run(project_id, call.name, call.arguments)
                convo.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
                if result.get("status") in SPEAK_VERBATIM and result.get("message"):
                    verbatim = speakable(result["message"])
            if verbatim:
                spoken.append(verbatim)
                yield verbatim
                break
        else:
            closing = "I've started on that. It will show up on screen shortly."
            spoken.append(closing)
            yield closing
    except llm.LLMError as e:
        log.warning("vox turn failed: %s", e)
        sorry = f"Sorry, I couldn't think that through: {speakable(str(e))}."
        spoken.append(sorry)
        yield sorry
    if spoken:
        events.publish(project_id, {"type": "transcript", "role": "assistant", "text": " ".join(spoken)})


def chunk(completion_id: str, created: int, delta: dict, finish: str | None = None) -> str:
    body = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": "vox",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body)}\n\n"


async def sse(project_id: str, messages: list[dict]):
    completion_id, created = f"chatcmpl-{uuid.uuid4().hex[:16]}", int(time.time())
    yield chunk(completion_id, created, {"role": "assistant", "content": ""})
    async for piece in converse(project_id, messages):
        yield chunk(completion_id, created, {"content": piece})
    yield chunk(completion_id, created, {}, "stop")
    yield "data: [DONE]\n\n"


@router.post("/llm/{project_id}/chat/completions")
async def chat_completions(project_id: str, request: Request):
    if not verify_llm_token(_token(request), project_id):
        raise HTTPException(401, "Bad or expired session token")
    if not store.exists(project_id):
        raise HTTPException(404, "Project not found")
    body = await request.json()
    messages = body.get("messages") or []
    if body.get("stream", True):
        return StreamingResponse(
            sse(project_id, messages),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    text = "".join([piece async for piece in converse(project_id, messages)]).strip()
    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "vox",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
    })
