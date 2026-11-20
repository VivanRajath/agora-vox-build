# Architecture

## 1. Overview

Vox Build turns a voice conversation into a published website. Three systems cooperate:

| Component | Responsibility |
|---|---|
| **Agora Conversational AI (Vox)** | Carries audio over Agora RTC, transcribes (ARES ASR), calls our `/llm` endpoint each turn, speaks the reply with Murf Falcon TTS. Barge-in stops Vox mid-sentence |
| **FastAPI backend** | Starts/stops Agora agents, is Vox's brain (`/llm` + tool loop), runs the agent team, builds React projects, stores versions, serves previews, publishes to GitHub Pages, pushes events |
| **React frontend** | Mic + call state, live preview iframe, element picker, activity log with plan/approval cards, version timeline, live card with QR code |

The core design choice is **cost- and latency-aware delegation**. Most voice edits are small ("make it blue"), so they go to a fast model that returns tiny multi-file patches. Only new sites and redesigns pay for a top model that writes every file. Every result is compiled before it is accepted, and a failure escalates.

## 2. Request flow (one voice edit)

```
User speaks: "make the header dark green"
  │
  ▼
Agora Conversational AI: ASR + end-of-turn → POST {PUBLIC_BASE_URL}/llm/<project>/chat/completions
  │  per-session token (Authorization: Bearer / X-Vox-Token), stream: true, recent turns
  ▼
vox_llm.chat_completions
  │  system = agents/vox/SOUL.md + live project state (prompts_vox.state)
  │  llm.chat(VOX_LLM_TIER, tools=vox_tools.specs()) → text "Sure." + tool_call edit_site
  │  "Sure." is streamed at once (chat.completion.chunk) so Murf starts speaking
  │  vox_tools.run("edit_site") → engine.edit() as a task, waits up to TOOL_SYNC_WAIT
  ▼
engine.edit  (serialized per project by an asyncio.Lock)
  │  router.route() → "jnr"
  │  _context(): only the files this change needs (selected data-vox-id, named sections,
  │              index.css for style, site.js for content) + the file tree
  │  llm.complete(jnr SOUL + project rules + patch protocol) → FILE / SEARCH / REPLACE
  │  patches.apply → builder.build (import whitelist, esbuild, Tailwind) in a staging folder
  │  on parse/apply/build failure: escalate jnr → snr → architect with the error text
  │  store.commit() → preview/<n>/ promoted → events.publish("job_done")
  ▼
tool result {"status": "done", "message": "Made the header dark green."} → model's next round
  ▼
SSE "Done, the header is dark green now. What next?" → data: [DONE] → Murf speaks it

In parallel: the browser's EventSource gets transcript, tool and job_done events → the iframe reloads (scroll kept)
```

## 3. Components

### 3.1 Voice: Agora Conversational AI + Murf (`backend/voice.py`, `backend/vox_llm.py`)

- **Start**: `/api/voice/start` creates a channel `vox-<project>-<random>`, signs AccessToken2 RTC tokens for the user (publisher) and the agent (RTC + RTM), and calls `POST https://api.agora.io/api/conversational-ai-agent/v2/projects/{appid}/join` with Basic auth. The agent's `llm.url` is our `/llm/{project_id}/chat/completions`, with a per-session HMAC token as `api_key` (and as an `X-Vox-Token` header, since how Agora sends `api_key` isn't documented). `tts` is `vendor: "murf"` with Falcon. The browser only gets `{app_id, channel, uid, token, agent_id}`. The exact request shapes are in `docs/agora-migration.md`.
- **Stop**: `/api/voice/stop` calls `.../agents/{id}/leave`. The browser sends it on hang-up and via `sendBeacon` on tab close; Agora's idle timeout (60 s) covers crashes. One agent per project: starting again replaces the old one.
- **Brain**: Agora doesn't execute tools for a custom LLM, so the endpoint runs the tool loop itself (up to 5 rounds), streaming any text the model says before a tool call. A filler line covers tool calls with no text. Replies are cleaned for speech (no markdown or code; URLs spoken as "dot" and "slash").
- **Live state**: Agora's history keeps only spoken text, so each turn's system prompt restates the project: version, latest job, draft plan, selection, pending approval, publish state, GitHub connection.
- **Activity log**: `/llm` publishes `transcript` events (user and Vox), and every tool run publishes `tool` events (name, status, result).

### 3.2 Tools (`backend/vox_tools.py`)

One registry (name, JSON schema, filler line, async handler) shared by voice and the typed fallback.

| Tool | Purpose |
|---|---|
| `plan_site(description)` | Researcher only. Returns a spoken plan (business, style, sections, palette in words) as `needs_approval`, and stores the brief as a draft |
| `build_site(changes?)` | Builds from the approved draft. With changes, the researcher re-plans first so the brief includes them |
| `edit_site(instruction)` | Any change to the built site |
| `undo_change()` | Revert to the previous version |
| `describe_site()` | Sections in `App.jsx` order, no LLM call |
| `check_progress()` | Latest job: planning, building, fixing a build error, done, failed |
| `publish_site(repo_name?, confirmed)` | Human in the loop (see 3.7) |
| `publication_status()` | Publishing progress, then the live URL |

The typed fallback maps text to tools without the model: a description → `plan_site`; "go ahead" with a draft → `build_site`; "publish" → `publish_site`; otherwise `edit_site`. Plan and approval cards in the activity log have buttons that call the same tools.

### 3.3 Planning team (`backend/editor/research.py`, `fontknight.py`, `copywriter.py`)

1. **Researcher**: one fast JSON call for the design brief (business, audience, tone, art direction, palette, 5-8 sections, image slots). `spoken_plan()` turns it into two sentences, with colours named in words.
2. On build, **Font Knight** (Google Fonts pairing + Lucide icons from the live catalogues, written as `lucide-react` imports), the **Copywriter** and the **photo search** (Pexels or Wikimedia Commons, saved to `public/images/`) run in parallel.
3. The **Architect** writes every file; the **Design Critic** patches the worst design issues. If the reviewed project doesn't build, the architect's version is used; if that doesn't build either, the senior developer gets one repair pass with the build errors.

### 3.4 Site projects (`backend/site_template.py`, `backend/builder.py`)

```
package.json, vite.config.js, index.html, src/main.jsx      fixed, written by code (fonts link + title from the brief)
src/index.css            @import "tailwindcss" + @theme (palette, fonts)   ┐
src/App.jsx              section order                                     │ written by the
src/components/*.jsx     one per section, root has id, data-section,       │ developers
                         data-vox-id="<Component>"                        │
src/data/site.js         all copy, prices, hours, links, image paths       ┘
public/images/*          researched photos
```

- **Imports**: only `react`, `react-dom`, `lucide-react`, `framer-motion` and relative files inside `src/`. `check_imports` rejects anything else before building.
- **Build**: esbuild (IIFE bundle, React resolved from the shared `runtime/node_modules` via `NODE_PATH`) and the Tailwind v4 standalone CLI run in parallel in a staging folder, in about 0.85 s on Windows and faster on Linux. The output is `index.html` + `bundle.js` + `styles.css` + images, with relative paths so it works in the preview and under `/<repo>/` on GitHub Pages. A classic deferred script, not a module, so the sandboxed preview (an opaque origin) needs no CORS.
- **Build as validator**: a failed build fails the edit; the error text goes to the next tier and appears as a `tool` event named `build` in the activity log. `VOX_TEST_BREAK_JNR=1` forces this for demos.
- **Export**: the ZIP is the source project with real dependencies (`vite`, `@vitejs/plugin-react`, `tailwindcss`, `@tailwindcss/vite`); `npm install && npm run dev` works.

### 3.5 code-editor (`backend/editor/engine.py`, `patches.py`, `router.py`)

- **Router**: a keyword classifier (an LLM would add dead air). Misroutes are cheap because of escalation. `architect` for redesigns and themes, `snr` for sections and features, `jnr` for everything else.
- **Patch protocol**: `FILE:` + SEARCH/REPLACE blocks, `NEW FILE:` + a fenced file, `DELETE FILE:`. Only `src/index.css`, `src/App.jsx`, `src/data/site.js` and `src/components/<Name>.jsx` are writable. Matching is exact first, then whitespace-tolerant.
- **Context**: jnr/snr see the file tree plus only the relevant files; the architect sees everything.
- **Point and speak**: `preview_inject.js` reports the clicked element's CSS path, an HTML snippet and the nearest `data-vox-id`, which picks the component file.

### 3.6 Store (`backend/store.py`)

```
data/projects/<id>/
  meta.json      versions[] (with changed files), current, selection, brief, draft, publish, published
  site/          the source project
  history/<n>/   snapshot of site/ per version
  preview/<n>/   built output per version (rebuilt on demand from history/<n> if missing)
```

History is linear, like an editor's undo stack. Undo moves `current` back, and a new edit after an undo discards the undone versions and their previews.

### 3.7 Publishing (`backend/publish.py`, `backend/github.py`)

- **Approval, enforced server-side**: `publish_site` without a matching prior `needs_approval` for that exact repo (within 15 minutes) never publishes; it records the pending approval and returns a summary to read out. Only a second call with `confirmed: true` and the same repo starts the job. The UI's **Publish** button goes through the same check.
- **Adapt**: no GitHub token → `needs_github` (the UI highlights **Connect GitHub**, and the ZIP is offered). Repo name taken → `name_taken` with two free alternatives. Transient GitHub errors (5xx, 429, network) are retried once.
- **Steps**: source → default branch (repo created public if needed); built output + `.nojekyll` → an orphan `gh-pages` branch; `POST /repos/{repo}/pages` (or `PUT` if already enabled); poll `pages/builds/latest` and then the URL itself until it answers 200 (4-minute timeout).
- **Follow-up**: the URL is saved in `meta.published` and announced with a `published` event; the UI shows the link and a client-side QR code. Later edits mark the published version as stale, and Vox offers to republish (approval again).

### 3.8 LLM layer (`backend/llm.py`)

`chat()` takes an OpenAI-style message list with tool calls; `complete()` wraps it for single prompts. Anthropic goes through the `anthropic` SDK (history converted to content blocks, with effort `low` for patches and `medium` for rewrites, plus server-side refusal fallback on Sonnet/Opus). OpenAI, Gemini, Groq and custom endpoints go through the `openai` SDK. User keys come first, then the server's; rate-limited keys rotate, and Groq spills over to sibling models.

## 4. Latency budget

| Step | Typical |
|---|---|
| Agora ASR + end-of-turn + Vox's first model round | ~1-1.5 s |
| Text before the tool call streamed to Murf | covers the wait |
| Plan (one researcher call) | 3-8 s |
| jnr patch + build | 2-5 s + <1 s |
| snr patch + build | 4-10 s + <1 s |
| New site (team + architect + critic + build) | 40-90 s, returns "working on it" and finishes in the background |
| SSE → iframe reload | <300 ms |
| GitHub Pages first deploy | 30-90 s |

## 5. Agent specs (GitAgent)

The `agents/` tree keeps the GitAgent layout (`agent.yaml`, `SOUL.md`, `SKILL.md`, `DUTIES.md`). The `vox`, researcher, font-knight, copywriter, design-critic and code-editor tier `SOUL.md` files are live system prompts. The `repo-scanner` specs come from the original repo-sandbox-agent and are not used by the voice runtime.
