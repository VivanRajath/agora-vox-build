# Agora + Murf + React migration plan

Branch: `agora`. The previous voice vendor is removed entirely. Voice runs on Agora Conversational AI Engine + Agora RTC
(Web SDK), and speech output uses Murf Falcon TTS. Generated sites become Vite + React + Tailwind v4 projects.

## Verified request shapes (read 2026-11-20)

Sources: Agora `llms.txt` index and the pages below. Where two docs disagree, both are noted.

### Start agent
`POST https://api.agora.io/api/conversational-ai-agent/v2/projects/{appid}/join`
(docs.agora.io/en/api-reference/api-ref/conversational-ai/join)

- Auth: `Authorization: Basic base64(customer_id:customer_secret)` (Console > RESTful API) (…/conversational-ai/authentication)
- Body: `{ "name": <unique>, "properties": {...} }`
- `properties`:
  - `channel`, `token` (agent RTC token), `agent_rtc_uid` (string), `remote_rtc_uids` (array of strings, one UID)
  - `idle_timeout` (seconds after remote users leave, default 30)
  - `advanced_features.enable_rtm` (default false; not needed, we use our own SSE stream)
  - `llm`: `url`, `api_key`, `system_messages` [{role, content}], `greeting_message`, `failure_message`,
    `max_history` (default 32), `params` (e.g. `{model}`), `style` ("openai" default), `vendor`, `headers`
    (merged into each LLM request)
  - `asr`: `vendor` (default "ares"), `language` (BCP-47). The ARES page's REST sample is just
    `{"vendor": "ares", "language": "en-US"}`, while the join reference marks `params` required. We follow the ARES sample.
  - `tts`: `vendor: "murf"`, `params` (see below), `skip_patterns` (optional; 3 = skip text in parentheses)
  - `turn_detection` (interrupt-agent page): `config.start_of_speech.mode: "vad"` with
    `vad_config.interrupt_duration_ms`, and `config.end_of_speech.mode: "semantic"` (semantic works for en-US / en-IN)
  - `interruption.enable` (default true): barge-in is on by default
- Response 200: `{agent_id, create_ts, status}`; errors carry `{detail, reason}`

### Stop agent
`POST https://api.agora.io/api/conversational-ai-agent/v2/projects/{appid}/agents/{agentId}/leave`, no body, same auth.

### Murf TTS (docs.agora.io/en/ai/models/tts/murf, murf.ai/api/docs/integrations/agora)
```json
"tts": {"vendor": "murf", "params": {
  "api_key": "...", "base_url": "wss://global.api.murf.ai/v1/speech/stream-input",
  "voiceId": "Matthew", "locale": "en-US", "model": "FALCON", "sample_rate": 24000 }}
```
- Conflicts: Agora's page uses `"model": "FALCON"`, while Murf's streaming docs use `"falcon-2.1"`. `MURF_MODEL` is
  configurable, with `FALCON` as the default per Agora.
- Falcon voice IDs are bare names. The English (India) Falcon voices include Priya, Aarav, Anisha, Nikhil and Pooja.
  The default is `Priya` with locale `en-IN`.
- Tested against Murf directly (2026-11-21): the stream-input WebSocket returns identical audio for `Priya` and
  `en-IN-priya`, and for `FALCON` and `falcon-2.1`. Murf's `GET /v1/speech/voices?model=falcon-2.1` lists
  `en-IN-*` IDs (117 voices). The connection was routed to the `ap-south-1` region, with a concurrency limit of 3.
- Regional hosts (`in.api.murf.ai`) are documented for HTTP streaming only, not WebSocket, so the default `MURF_WS_URL`
  is the confirmed global URL. Set `wss://in.api.murf.ai/v1/speech/stream-input` to try the India region.

### Custom LLM (docs.agora.io/en/ai/build/custom-model-integration/custom-llm)
- Agora POSTs OpenAI Chat Completions with `stream: true`. A non-streaming endpoint is rejected.
- Reply: `text/event-stream`, `data: {chat.completion.chunk}` lines, ending with `data: [DONE]`.
- Agora does not execute tools for custom LLMs, so our endpoint runs the tool loop server-side.
- How `api_key` is sent is **not documented**. We set it as `api_key` (OpenAI clients send
  `Authorization: Bearer`), and also as an `X-Vox-Token` header via `llm.headers`. The endpoint accepts either.

### RTC tokens
AccessToken2 (`007…`) from the official AgoraIO/Tools repo, `DynamicKey/AgoraDynamicKey/python3/src`
(MIT, commit da06bfb). It is vendored in `backend/agora_token/`. The agent gets `build_token_with_rtm` (RTC+RTM, as
the docs recommend for ConvoAI) and the browser user gets `build_token_with_uid` (publisher).

## Files

### Phase 1 (voice → Agora + Murf)
- delete: the old voice prompt folder, its agent-creation script and its web-call SDK
- add: `backend/agora_token/` (vendored), `backend/voice.py` (start/stop + session tokens),
  `backend/vox_llm.py` (`/llm/{project_id}/chat/completions`), `backend/vox_tools.py` (tool registry),
  `agents/vox/SOUL.md`
- change: `backend/config.py`, `backend/main.py` (drop the old session route and `/tools/*`), `backend/llm.py`
  (`chat()` with tool calling for Anthropic + OpenAI-compatible), `frontend/src/useVoiceCall.js` (agora-rtc-sdk-ng),
  `VoicePanel.jsx`, `App.jsx`, `ActivityLog.jsx`, `.env.example`, `render.yaml`, `Dockerfile`, docs and agent specs

### Phase 2 (React projects)
- add: `backend/builder.py` (esbuild + Tailwind standalone), `backend/site_template.py` (fixed files), `runtime/package.json`,
  `scripts/setup_build_tools.sh`
- change: `backend/store.py` (file sets), `backend/editor/engine.py` (multi-file patch protocol, build-as-validator),
  `backend/editor/prompts.py` (JSX rules), `backend/editor/research.py` (`public/images/`), `backend/preview_inject.js` (`data-vox-id`),
  `/preview` serving, ZIP export

### Phase 3 (track flow)
- add: `backend/publish.py` (GitHub Pages flow)
- change: `backend/vox_tools.py` (plan_site, build_site, publish_site, publication_status), `backend/github.py`
  (branches, Pages API), `backend/editor/engine.py` (plan/build split), frontend publish card + QR code

### Phase 4
- `Dockerfile`, `render.yaml`, README, ARCHITECTURE, AGENTS, `tests/`

### Turn detection (join reference, properties.turn_detection.config, verified 2026-11-21)
- `turn_detection.mode: "default"` with `config.start_of_speech` (`mode: "vad"`, `vad_config.interrupt_duration_ms`
  and `speaking_interrupt_duration_ms`, default 160) and `config.end_of_speech.mode` (`vad`, `semantic` or `manual`).
  The flat fields (`type`, `silence_duration_ms`, `threshold`...) are deprecated.
- Semantic end of speech works only for `en-US`, `en-IN` and Chinese, with a single language; otherwise Agora falls back to VAD.
  Its defaults are `silence_duration_ms` 320 and `max_wait_ms` 3000.
- ARES supports `en-IN` (supported-languages page). It is the default because Indian English came through
  fragmented with `en-US` plus a fixed 640 ms silence cut-off.
