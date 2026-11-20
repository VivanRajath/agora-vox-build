# Architecture

## 1. Overview

Vox Build turns speech into website versions. Three systems cooperate:

| Component | Responsibility |
|---|---|
| **Agora Conversational AI (Vox)** | Carries audio over Agora RTC, transcribes (ASR), calls our `/llm` endpoint each turn, speaks the reply with Murf Falcon TTS. Barge-in stops Vox mid-sentence |
| **FastAPI backend** | Starts/stops Agora agents, is Vox's brain (`/llm`, tool loop), runs the edit pipeline, stores versions, serves previews, pushes events |
| **React frontend** | Mic + call state, live preview iframe, element picker, activity log, version timeline |

The core design choice, carried over from the original tiered agent system, is **cost- and latency-aware delegation**. Most voice edits are small ("make it blue"), so they go to a fast, cheap model that returns tiny patches. Only real redesigns pay for a top-tier model that rewrites the whole page.

## 2. Request flow (one voice edit)

```
User speaks: "make the header dark green"
  │
  ▼
Agora Conversational AI: ASR → POST {PUBLIC_BASE_URL}/llm/<project>/chat/completions
  │  Authorization: Bearer <per-session token>, stream: true, messages = recent turns
  ▼
backend.vox_llm.chat_completions
  │  conversation model (VOX_LLM_TIER) with Vox's tools → tool_call edit_site
  │  streams "On it." immediately (SSE chunk) so Murf starts speaking
  │  vox_tools.run("edit_site") → engine.edit() as a task, waits up to TOOL_SYNC_WAIT
  ▼
engine.edit  (serialized per project by an asyncio.Lock)
  │  router.route() → "jnr"
  │  llm.complete(jnr SOUL + site rules + patch protocol, page + selection + instruction)
  │  parse SEARCH/REPLACE blocks → apply (exact, then whitespace-tolerant)
  │  on failure: escalate jnr → snr → architect (full rewrite)
  │  validate HTML → store.commit() → events.publish("job_done")
  ▼
Tool result {"status": "done", "message": "Made the header dark green."} → model's next round
  │                     or {"status": "in_progress", ...} if still running
  ▼
SSE chunks "Done, the header is dark green now. What next?" → data: [DONE] → Murf speaks it

In parallel: browser EventSource gets job_done → iframe reloads (scroll kept)
```

## 3. Components

### 3.1 Voice: Agora Conversational AI + Murf

- **Start** (`backend/voice.py`): `/api/voice/start` creates a channel `vox-<project>-<random>`, signs AccessToken2 RTC tokens for the user and the agent (builder vendored from AgoraIO/Tools), and calls Agora's `POST /v2/projects/{appid}/join` with Basic auth. The agent's `llm.url` points at our `/llm/{project_id}/chat/completions` with a per-session HMAC token as `api_key`; `tts` is `vendor: "murf"` (Falcon). The browser only gets `{app_id, channel, uid, token, agent_id}`.
- **Stop**: `/api/voice/stop` calls `.../agents/{id}/leave`. The browser sends it on hang-up and via `sendBeacon` on tab close; Agora's idle timeout (60 s) covers crashes.
- **Brain** (`backend/vox_llm.py`): Agora doesn't execute tools for a custom LLM, so the endpoint runs the tool loop itself (up to 5 rounds), streaming any text the model says before a tool call so the user hears "On it" while the tool runs. Replies are cleaned for speech (no markdown, URLs spoken as "dot" and "slash").
- **Live state**: Agora's history keeps only spoken text, so each turn's system prompt restates the project state (version, latest job, selection, GitHub).
- **Activity log**: `/llm` publishes `transcript` (user and Vox text) and `tool` (name, status, result) events on the project SSE stream.
- **Tools** (`backend/vox_tools.py`): one registry (name, JSON schema, filler line, async handler) shared by voice and the typed fallback:

| Tool | Purpose |
|---|---|
| `generate_site` | Build a new site from a description |
| `edit_site` | Any change (generates instead if the project is still blank) |
| `undo_change` | Revert to the previous version |
| `describe_site` | Outline of the current page (no LLM call) |
| `check_progress` | Status of the latest background job |

### 3.2 researcher (`backend/editor/research.py`)

Runs before the developers so sites aren't built on guesses:

1. **Design brief** (one fast LLM call, JSON): business, audience, tone, palette, Google Fonts pairing, 5-8 sections with concrete elements, and image slots with a specific query plus a 1-2 word fallback query. Stored in `meta.json` and fed back into later edits so new content keeps the same palette and fonts.
2. **Photos**: for each slot, search Pexels (if `PEXELS_API_KEY`) or Wikimedia Commons, skip logos/maps/diagrams and tiny images, prefer landscape, then download a 1280px version into `site/images/`. Local files mean no hotlink failures, and undo snapshots include them. Credits are added to the footer.
3. Results stream to the UI as a `research_done` event (palette swatches + photo thumbnails in the activity log).

### 3.3 intent-router (`backend/editor/router.py`)

A keyword classifier. An LLM classifier would add a round trip of dead air on every voice edit, and misroutes are cheap because of escalation.

| Tier | Triggers | Output protocol |
|---|---|---|
| `architect` | redesign, start over, dark mode, new theme, rebuild… | Full `index.html` |
| `snr` | add a…, section, gallery, form, remove, move, long instructions | SEARCH/REPLACE patches |
| `jnr` | everything else (colours, sizes, text, single elements) | SEARCH/REPLACE patches |

### 3.4 code-editor (`backend/editor/engine.py`)

- **Prompts**: each tier's persona is its GitAgent `SOUL.md` (`agents/code-editor/agents/*/SOUL.md`). Site rules and output protocols are appended in `prompts.py`, because the parser depends on them.
- **Selection context**: when the user has picked an element, its CSS path and `outerHTML` go into the prompt, so "this" and "it" resolve.
- **Patch application**: exact unique match first. Otherwise a line-wise stripped-whitespace match that keeps the file's indentation. If neither matches, `EditFailed` triggers escalation.
- **Validation**: the result must be a full HTML document, or it's rejected (and escalated).
- **describe**: an `HTMLParser` pass that lists the title and sections (`data-section` / `id` plus the first heading).

### 3.5 Store (`backend/store.py`)

```
data/projects/<id>/
  meta.json      versions[], current, selection
  site/          what the preview serves
  history/<n>/   snapshot per version
```

History is linear, like an editor's undo stack. Undo moves `current` back, and a new edit after an undo discards the undone versions.

### 3.6 Preview

`/preview/<id>/` serves the site and injects `preview_inject.js` before `</body>`. The script:
- keeps the scroll position across reloads (`sessionStorage`)
- implements pick mode (hover outline, click → `postMessage` with CSS path, HTML and label)
- highlights the current selection

### 3.7 LLM layer (`backend/llm.py`)

Anthropic goes through the official `anthropic` SDK, with effort `low` for patches and `medium` for full rewrites. Sonnet 5.5 and Opus 5.5 requests opt into server-side refusal fallback. OpenAI and Gemini go through the `openai` SDK (Gemini via its OpenAI-compatible endpoint). Refusals, truncation and API errors become `LLMError`, which reaches the user as a short spoken apology.

## 4. Latency budget

| Step | Typical |
|---|---|
| Agora ASR + end-of-turn + Vox's first model round | ~1-1.5s |
| Text before the tool call streamed to Murf | covers the wait |
| jnr patch (small model, low effort, small output) | 2-5s |
| snr patch | 4-10s |
| architect rewrite / first generation | 20-60s, returns "working on it" and finishes in the background |
| SSE → iframe reload | <300ms |

## 5. Agent specs (GitAgent)

The `agents/` tree keeps the GitAgent layout (`agent.yaml`, `SOUL.md`, `SKILL.md`, `DUTIES.md`). The code-editor tier specs are live: their `SOUL.md` files are loaded as system prompts. The `repo-scanner` specs come from the original repo-sandbox-agent and are not used by the voice runtime.
