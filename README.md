# Vox Build

**Build, edit and publish a website by talking to it.**

[![Pitch deck](https://img.shields.io/badge/Pitch_deck-Open-C2331F?style=for-the-badge)](https://vivanrajath.github.io/buildathon-deck/)
[![Demo video](https://img.shields.io/badge/Demo_video-Watch_on_Loom-15171C?style=for-the-badge)](https://www.loom.com/share/fc5195a409e84af18a43ca9baddb31f4)
[![Agora Voice AI Hackathon](https://img.shields.io/badge/Agora-Voice_AI_Hackathon_2026-099DFD?style=for-the-badge)](https://www.agora.io/en/conversational-ai/)

Tell Vox about your business out loud. It plans a site and reads the plan back to you. Once you approve it, a team of AI agents builds a real React project while you watch. Keep talking to refine it: *"make the header dark green"*, *"add a section for birthday cakes"*, or click any element and say *"make **this** bigger"*. Say *"put it online"*, give the go-ahead, and Vox publishes it to GitHub Pages and reads you the live address. A QR code appears for your shop counter.

Vox runs on the **[Agora Conversational AI Engine](https://www.agora.io/en/conversational-ai/)** over **Agora RTC**, and speaks with **[Murf Falcon](https://murf.ai/api)** TTS. Built for the Build with Agora Bengaluru 2026 Voice AI Hackathon, track **AI Agents That Get Things Done**.

## The demo flow (Initiate → Plan → Execute → Adapt → Confirm)

| Step | You say | What happens |
|---|---|---|
| **Initiate** | "Build a website for my bakery, Sweet Crumbs, in Indiranagar. We do cakes and coffee." | Vox calls `plan_site`. The researcher writes a design brief. |
| **Plan** | (Vox reads the plan: style, sections, colours) "Shall I build it, or change anything?" | The plan card in the activity log shows the palette and a **Build it** button. |
| **Execute** | "Add a section for custom birthday cakes, then go ahead." | `build_site`: Font Knight, the copywriter and the photo search run in parallel, then the architect writes the React project, the design critic reviews it, and esbuild + Tailwind compile it. Every step shows live in the activity log. |
| **Adapt** | Click the hero image: "make this bigger" | `edit_site`: a junior developer patches only `Hero.jsx`. If a patch doesn't compile, the build error is logged and the change moves up to the senior developer, then the architect. |
| **Approve** | "Put it online." | `publish_site` returns what will happen ("a public repo called sweet-crumbs…"), and Vox asks. Only a clear "yes" publishes; the server enforces it. If the name is taken, Vox offers two free alternatives. |
| **Confirm and follow up** | "Yes." | The source goes to `main` and the built site to `gh-pages`, Pages is enabled, and the backend waits for the URL to answer. Vox says "it's live at ana dot github dot io slash sweet crumbs", and the UI shows the link and a QR code. Later edits prompt "Republish the changes?" |

## The agent team

Vox is the only agent you talk to. Every other agent has one job, and its persona lives in `agents/*/SOUL.md`, loaded as a live system prompt.

| Agent | Job |
|---|---|
| **Vox** | The voice agent. Plans with you, calls the tools, asks before anything irreversible, reports results. |
| **Researcher** | Writes the design brief (audience, tone, art direction, palette, sections) and finds real photos. |
| **Font Knight** | Picks a real Google Fonts pairing and Lucide icons, searched from the live catalogues. |
| **Copywriter** | Writes headlines, calls to action and section copy in the brief's tone. |
| **Architect** | Writes the whole React project from the plan, and handles redesigns. |
| **Design Critic** | Reviews every new site against the design principles and patches the worst issues. |
| **Intent Router** | Sorts each spoken edit into small, bigger or full. |
| **Junior Dev** | A fast model for small tweaks, as multi-file patches. |
| **Senior Dev** | A stronger model for new sections, and for fixing build errors. |

See [AGENTS.md](AGENTS.md) for the hierarchy and [ARCHITECTURE.md](ARCHITECTURE.md) for the details.

## How it works

```
Browser (React)                                     Agora cloud
────────────────                                    ────────────────────────────────────
mic ── agora-rtc-sdk-ng ◀═══════ Agora RTC audio ═══▶ Conversational AI Engine
  │                                                   ASR (ARES) → LLM → Murf Falcon TTS
  │  /api/voice/start                                              │   (barge-in)
  ▼                                                                │ every user turn
FastAPI backend ◀── POST /llm/{project}/chat/completions ──────────┘
  │   OpenAI-compatible, stream: true; Vox's tool loop runs here; replies stream back as SSE
  ▼
tool registry (vox_tools.py): plan_site · build_site · edit_site · undo_change ·
                              describe_site · check_progress · publish_site · publication_status
  ▼
agent team ─▶ React project (Vite + React 18 + Tailwind v4) ─▶ esbuild + Tailwind build (<1 s)
  │                                                                │
  │                                     preview iframe ◀── SSE ────┘
  ▼
publish.py ─▶ GitHub: source → main, build → gh-pages, Pages enabled ─▶ live URL + QR code
```

1. The mic button calls `/api/voice/start`. The backend signs Agora RTC tokens and starts an Agora Conversational AI agent in a fresh channel; the browser joins it with the Agora Web SDK. No keys ever reach the browser.
2. Agora transcribes your speech and calls this backend's **OpenAI-compatible `/llm` endpoint** each turn. The endpoint runs the conversation model with Vox's tools server-side and streams the spoken reply back. Murf Falcon speaks it, and talking over Vox interrupts it.
3. Anything Vox says before a tool call streams immediately ("Sure, planning that now"), so the call never goes quiet. Jobs that take longer than `TOOL_SYNC_WAIT` seconds return "working on it", finish in the background, and appear in the preview over SSE.
4. Generated sites are real React projects: one component per section, all copy in `src/data/site.js`, the palette and fonts in Tailwind's `@theme`. Each version is compiled with esbuild and the Tailwind standalone CLI against a shared, pinned `runtime/node_modules`, in under a second. **The build is the validator**: a change that doesn't compile escalates to a stronger developer with the error text.
5. The ZIP download is a runnable Vite project (`npm install && npm run dev`). A Next.js export could be added later, since the sections are plain React components.

## Features

- **Voice-first**: Agora Conversational AI + RTC, Murf Falcon TTS, barge-in, and a live transcript in the activity log
- **Plan before build**: a spoken plan you approve or change
- **Point and speak**: click an element in the preview, then say "make this rounder"; the editor opens that component's file
- **Undo by voice**, plus a clickable version timeline
- **Human in the loop, enforced server-side**: publishing needs an explicit yes for that exact repository
- **One-step publish**: GitHub Pages with a speakable URL and a QR code; republish after edits
- **Real design inputs**: researched briefs, real Google Fonts, real Lucide icons, real photos
- **Cost- and latency-aware** model tiers with automatic escalation on patch or build failure
- **Settings, no sign-in needed**: any number of API keys (Anthropic, OpenAI, Gemini, Groq or any OpenAI-compatible endpoint), a model per tier, and key rotation on rate limits
- **Typed fallback for every action**, including plan, build and publish (approval buttons in the activity log)

## Quick start

Requirements: Python 3.11+, Node 18+, curl, and one LLM key (Anthropic, OpenAI, Gemini or Groq).

```bash
# 1. Backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env              # add an LLM key (or add one later in Settings)

# 2. Site builder: esbuild, Tailwind standalone and the shared React runtime (into runtime/)
sh scripts/setup_build_tools.sh

# 3. Frontend
cd frontend && npm install && npm run build && cd ..

# 4. Run (serves the built frontend too)
uvicorn backend.main:app --port 8000
```

Open http://localhost:8000 and type a description. Without voice keys, the typed flow works end to end: plan, **Build it**, edits, undo, ZIP and publish.

### Turning on voice

Agora's cloud calls this backend's `/llm` endpoint during a call, so voice needs a public HTTPS URL.

1. **Agora console** (console.agora.io): create a project with an **App Certificate** (secured mode), enable **Conversational AI** for it, and copy the **App ID** and **App Certificate**. Under **Developer Toolkit → RESTful API**, add a secret to get a **Customer ID** and **Customer Secret**.
2. **Murf** (murf.ai/api): create an API key.
3. In `.env`, set `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`, `AGORA_CUSTOMER_ID`, `AGORA_CUSTOMER_SECRET`, `MURF_API_KEY`, and set `TOOL_SECRET` to any long random string.
4. Expose the backend with `cloudflared tunnel --url http://localhost:8000` (or `ngrok http 8000`) and set the https URL as `PUBLIC_BASE_URL`. Restart, and the mic button goes live.

### Turning on publishing

Publishing uses the GitHub account the user connects (OAuth, `repo` scope):

1. github.com/settings/developers → **New OAuth App**. Callback URL: `<your site>/auth/github/callback` (for example the tunnel URL, or `http://localhost:8000/auth/github/callback` for typed-only use).
2. Set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` in `.env` and restart. Users click **Connect GitHub** in the top bar.

### Deploying

The repo ships a `Dockerfile` (frontend, backend and the site-builder runtime in one image) and a Render blueprint: **New + → Blueprint**, pick the repo, then fill in the keys. `TOOL_SECRET` and `SESSION_SECRET` are generated, and Render sets the public URL itself. Render's free plan sleeps when idle and has no persistent disk, so open the app once before a demo.

### Development mode

```bash
uvicorn backend.main:app --port 8000 --reload
cd frontend && npm run dev        # http://localhost:5173, proxies to :8000
.venv/Scripts/python -m pytest    # tests (build tests need scripts/setup_build_tools.sh)
```

## Configuration

| Variable | Purpose |
|---|---|
| `AGORA_APP_ID` / `AGORA_APP_CERTIFICATE` | Agora project. Signs RTC tokens for the browser and the agent |
| `AGORA_CUSTOMER_ID` / `AGORA_CUSTOMER_SECRET` | Agora RESTful API credentials (Basic auth) to start and stop agents |
| `MURF_API_KEY` | Murf API key, passed to Agora's engine for Falcon TTS |
| `MURF_VOICE_ID` / `MURF_LOCALE` | Voice and locale (default `Priya`, `en-IN`; Falcon voice ids are bare names) |
| `MURF_MODEL` / `MURF_WS_URL` | Murf model (`FALCON`) and WebSocket endpoint (global by default) |
| `ASR_LANGUAGE` | Speech recognition language (default `en-US`; `en-IN` is also supported) |
| `END_OF_SPEECH` | Optional: `semantic` (the turn ends when the thought is finished) or `vad`. Empty keeps Agora's defaults |
| `INTERRUPT_MS` | Optional: how long you must speak to interrupt Vox. Empty keeps Agora's 160 ms |
| `VOX_LLM_TIER` | Model tier for Vox's conversation. Empty: `gpt-oss-20b` on Groq (reliable tool calls, its own quota), the `jnr` model elsewhere. `MODEL_VOX` picks an exact model |
| `PUBLIC_BASE_URL` | Public https URL of this backend; Agora calls `{PUBLIC_BASE_URL}/llm/...` (Render sets it automatically) |
| `TOOL_SECRET` | Signs the per-session token Agora sends to `/llm` (bound to the project, expires after 4 hours) |
| `TOOL_SYNC_WAIT` | Seconds a tool waits for a job before answering "working on it" (default 8) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` / `GROQ_API_KEY` | Server-wide LLM keys, used after a user's own keys from Settings. Each may hold several comma-separated keys, rotated on rate limits. Force one with `LLM_PROVIDER` |
| `UNSPLASH_ACCESS_KEY` | Optional, tried first. Unsplash photos, hotlinked from images.unsplash.com with credits (Unsplash's API rules). Demo keys allow 50 requests an hour |
| `PEXELS_API_KEY` | Optional. Better stock photos; without either key photos come from Wikimedia Commons |
| `MODEL_JNR` / `MODEL_SNR` / `MODEL_ARCHITECT` | Optional per-tier model overrides |
| `DESIGN_CRITIC` | `0` skips the design critic's review pass |
| `SESSION_SECRET` | Signs the login cookie and encrypts stored GitHub tokens and user API keys. Auto-generated into `data/.session_secret` if unset |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Optional Google sign-in |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | GitHub sign-in, push and publishing (scope `repo`) |
| `SITE_RUNTIME_DIR`, `ESBUILD_BIN`, `TAILWIND_BIN` | Optional overrides for where the site builder's tools live (default `runtime/`) |
| `VOX_TEST_BREAK_JNR` | Test hook: `1` makes every junior-dev patch fail to build, to demo escalation |

Default tier models:

| Tier | Anthropic | OpenAI | Gemini | Groq |
|---|---|---|---|---|
| jnr (small tweaks, Vox, planners) | `claude-haiku-5-5` | `gpt-4.1-mini` | `gemini-2.5-flash` | `qwen/qwen3.8-27b` |
| snr (features, build fixes, critic) | `claude-sonnet-5-5` | `gpt-4.1` | `gemini-2.5-pro` | `openai/gpt-oss-120b` |
| architect (new sites, redesigns) | `claude-opus-5-5` | `gpt-4.1` | `gemini-2.5-pro` | `openai/gpt-oss-120b` |

## Repository layout

```
backend/
  main.py            FastAPI app: browser API, preview, zip, push, publish button
  voice.py           Agora agent start/stop, RTC tokens, per-session LLM tokens
  vox_llm.py         OpenAI-compatible /llm endpoint Agora calls; server-side tool loop, SSE
  vox_tools.py       Vox's tool registry (voice and typed fallback), publish approval
  prompts_vox.py     Vox's system prompt + live project state
  publish.py         GitHub Pages publishing (main + gh-pages, poll until live)
  github.py          GitHub Git Data API + Pages API
  builder.py         esbuild + Tailwind standalone build, import whitelist
  site_template.py   fixed project files (package.json, vite.config.js, index.html, main.jsx)
  agora_token/       Agora AccessToken2 builder, vendored from AgoraIO/Tools (MIT)
  editor/engine.py   code-editor: plan, generate, patch, build, escalate, undo, describe
  editor/patches.py  multi-file patch protocol parser
  editor/prompts.py  project rules + design principles (personas load from agents/*/SOUL.md)
  editor/research.py researcher: design brief, spoken plan, photo search
  editor/router.py   intent-router: instruction → tier
  llm.py             Anthropic / OpenAI / Gemini / Groq, tool calling, per-user keys
  store.py           projects on disk with version history and built previews
  events.py          SSE fan-out and job tracking
  preview_inject.js  element picker (reports data-vox-id) + scroll keeper
runtime/package.json   pinned React, lucide-react, framer-motion, esbuild for the site builder
scripts/setup_build_tools.sh   installs runtime/ (esbuild, Tailwind standalone, node_modules)
frontend/              Vite + React app (preview, voice, activity, versions, live card)
agents/                agent specs; SOUL.md files are live prompts (agents/vox/SOUL.md is Vox)
tests/                 pytest
docs/agora-migration.md   verified Agora / Murf request shapes
```

## Security notes

- Agora, Murf and LLM keys never reach the browser. It only receives an RTC token for its own channel.
- `/llm/{project_id}/...` requires a per-session HMAC token (signed with `TOOL_SECRET`, bound to the project, expires in 4 hours).
- Publishing is gated on the server: `publish_site` only pushes after it has asked for approval of that exact repository and is called again with `confirmed: true` within 15 minutes.
- Generated code may import only `react`, `react-dom`, `lucide-react`, `framer-motion` and its own files; it's bundled, never run on the server.
- Previews are served from `/preview/<id>/` with path-traversal checks and a `Content-Security-Policy: sandbox` header (no `allow-same-origin`), so their scripts can't use the viewer's login to call the API.
- User API keys and GitHub tokens are encrypted at rest and never sent back to the browser.

---

Built by Vivan Rajath for the Build with Agora Bengaluru 2026 Voice AI Hackathon (track: AI Agents That Get Things Done). The pitch deck and demo video above show the first version of Vox Build (single-page HTML sites, a different voice stack).
