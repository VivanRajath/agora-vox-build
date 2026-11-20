# Vox Build

**Build and edit a website by talking to it.**

[![Pitch deck](https://img.shields.io/badge/Pitch_deck-Open-C2331F?style=for-the-badge)](https://vivanrajath.github.io/buildathon-deck/)
[![Demo video](https://img.shields.io/badge/Demo_video-Watch_on_Loom-15171C?style=for-the-badge)](https://www.loom.com/share/fc5195a409e84af18a43ca9baddb31f4)
[![Agora Voice AI Hackathon](https://img.shields.io/badge/Agora-Voice_AI_Hackathon_2026-099DFD?style=for-the-badge)](https://www.agora.io/en/conversational-ai/)

Describe a website out loud and it appears. Then keep talking: *"make the header dark green"*, *"add a testimonials section"*, *"undo that"*. Click any element and say *"make **this** bigger"*. Every change lands in the live preview while the voice agent, Vox, talks back.

Vox runs on the [Agora Conversational AI Engine](https://www.agora.io/en/conversational-ai/) over Agora RTC, and speaks with [Murf Falcon](https://murf.ai/api) TTS. Behind it, a team of AI agents researches, writes, designs and builds the site while you watch.

## Pitch deck and demo

| | |
|---|---|
| **Pitch deck** | [vivanrajath.github.io/buildathon-deck](https://vivanrajath.github.io/buildathon-deck/): 13 slides on the problem, the product, the agent team and how it is built. Use the arrow keys, click or swipe; `F` for full screen. |
| **Demo video** | [Watch on Loom](https://www.loom.com/share/fc5195a409e84af18a43ca9baddb31f4). It is also embedded on slide 4 of the deck. |

## The agent team

Vox is the only agent you talk to. Every other agent has one job, and its persona lives in `agents/*/SOUL.md`, loaded as a live system prompt.

| Agent | Job |
|---|---|
| **Vox** | The voice agent (Agora Conversational AI + Murf). Listens, talks back and calls the build tools through our `/llm` endpoint. |
| **Researcher** | Writes the design brief (audience, tone, art direction, palette, sections) and finds real photos for every image slot. |
| **Font Knight** | Picks a real Google Fonts pairing and Lucide icons, searched from the live catalogues. |
| **Copywriter** | Writes headlines, calls to action and section copy in the brief's tone. |
| **Architect** | Builds the full page from the plan, and handles redesigns. |
| **Design Critic** | Reviews every new page against the design principles and patches the worst issues. |
| **Intent Router** | Sorts each spoken edit into small, bigger or full. |
| **Junior Dev** | A fast, cheap model for small tweaks, applied as precise patches. |
| **Senior Dev** | A stronger model for new sections and layout changes. |

A new site goes **Researcher, then Font Knight + Copywriter + photo search in parallel, then Architect, then Design Critic**. An edit goes through the Intent Router to the cheapest developer that can do it, and moves up a level automatically if its patch can't be applied. See [AGENTS.md](AGENTS.md) for the full hierarchy.

## How it works

```
Browser (React)                          FastAPI backend                       Agora cloud
──────────────────                       ──────────────────────                ─────────────────────
Mic ── /api/voice/start ───────────────▶ RTC tokens + start agent (REST) ───▶ Conversational AI Engine
   │                                                                          ASR → LLM → Murf TTS
   └── agora-rtc-sdk-ng ◀══════════ Agora RTC audio ══════════════════════▶      │
                                         /llm/{id}/chat/completions ◀─────────────┘
                                         (OpenAI-compatible, streamed; tools run here)
                                            │
                                            ▼
   │                                      agent team (router → jnr / snr / architect)
   │                                         │   (cheap model → strong model)
   │                                         ▼
   │                                      new version of the site
   ▼                                         │
Live preview iframe ◀── SSE "job_done" ──────┘
```

1. The mic button calls `/api/voice/start`. The backend signs Agora RTC tokens and starts an Agora Conversational AI agent in a fresh channel; the browser joins that channel with the Agora Web SDK.
2. Agora transcribes your speech and calls this backend's **OpenAI-compatible `/llm` endpoint** every turn. It runs the conversation model with Vox's tools (`generate_site`, `edit_site`, `undo_change`, `describe_site`, `check_progress`) and streams the spoken reply back; Murf Falcon speaks it.
3. For a new site, the planning agents (Researcher, Font Knight, Copywriter) prepare the brief, type, copy and photos; the Architect builds the page and the Design Critic reviews it.
4. For an edit, the **Intent Router** picks a developer. Small tweaks go to a fast model that returns tiny SEARCH/REPLACE patches, features to a standard model, redesigns to the top model.
5. The new version is saved (with undo history) and pushed to the browser over **Server-Sent Events**. The preview reloads and keeps your scroll position.
6. Quick edits come back within `TOOL_SYNC_WAIT` seconds, so Vox can confirm the exact change. Longer builds return "working on it" right away, finish in the background, and Vox reports back with `check_progress`, so the call never goes silent.

## Features

- **Voice-first** site generation and editing (Agora Conversational AI + RTC, Murf Falcon TTS), with barge-in
- **Point and speak**: click an element in the preview, then say "make this rounder"
- **Undo by voice**, plus a clickable version timeline
- **Ask about the page**: "what's on my page?" gets a spoken outline
- **Multilingual**: Vox replies in your language and writes edit instructions in English
- **Real design inputs**: researched briefs, real Google Fonts, real icons, real photos
- **Cost- and latency-aware** model tiers with automatic escalation
- **Settings, no sign-in needed**: add any number of API keys (Anthropic, OpenAI, Gemini, Groq or any OpenAI-compatible endpoint, several per provider), each tested before it is saved. Pick a provider and model per tier and add a Pexels key for photos. When a key is rate limited, the next key for that provider takes over
- **Accounts**: sign in with Google or GitHub
- **Ship it**: download the site as a ZIP, or push it to a new or existing GitHub repo in one commit
- Desktop / tablet / mobile preview sizes, and a typed fallback for every voice action

## Quick start

Requirements: Python 3.11+, Node 18+, one LLM key (Anthropic, OpenAI, Gemini or Groq).

```bash
# 1. Backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env              # fill in your keys

# 2. Frontend
cd frontend && npm install && npm run build && cd ..

# 3. Run (serves the built frontend too)
uvicorn backend.main:app --port 8000
```

Open http://localhost:8000. Typed commands work as soon as an LLM key is set.

### Turning on voice

Agora's cloud calls this backend's `/llm` endpoint during a call, so voice needs a public HTTPS URL.

1. **Agora console** (console.agora.io): create a project with an App Certificate, enable **Conversational AI** for it, and copy the App ID and App Certificate. Under **RESTful API**, add a secret to get a Customer ID and Customer Secret.
2. **Murf** (murf.ai/api): create an API key.
3. Put them in `.env` (`AGORA_*`, `MURF_API_KEY`), and set `TOOL_SECRET` to any long random string.
4. Expose the backend: `cloudflared tunnel --url http://localhost:8000` (or `ngrok http 8000`) and set the https URL as `PUBLIC_BASE_URL`. Restart, and the mic button goes live.

To deploy instead, the repo ships a `Dockerfile` and a Render blueprint (**New + → Blueprint**, then fill in the keys; `TOOL_SECRET` and `SESSION_SECRET` are generated). Render sets `PUBLIC_BASE_URL` for you. Render's free plan sleeps when idle and has no persistent disk, so open the app once before a demo.

### Development mode

```bash
uvicorn backend.main:app --port 8000 --reload
cd frontend && npm run dev        # http://localhost:5173, proxies to :8000
```

## Configuration

| Variable | Purpose |
|---|---|
| `AGORA_APP_ID` / `AGORA_APP_CERTIFICATE` | Agora project. Signs RTC tokens for the browser and the agent |
| `AGORA_CUSTOMER_ID` / `AGORA_CUSTOMER_SECRET` | Agora RESTful API credentials (Basic auth) to start and stop agents |
| `MURF_API_KEY` | Murf API key, passed to Agora's engine for Falcon TTS |
| `MURF_VOICE_ID` / `MURF_LOCALE` / `MURF_MODEL` / `MURF_WS_URL` | Voice (default `Priya`, `en-IN`), model (`FALCON`) and Murf WebSocket endpoint |
| `ASR_LANGUAGE` | Speech recognition language (default `en-US`; `en-IN` also gets semantic turn detection) |
| `VOX_LLM_TIER` | Model tier for Vox's conversation (default `jnr`, the fast model) |
| `PUBLIC_BASE_URL` | Public https URL of this backend; Agora calls `{PUBLIC_BASE_URL}/llm/...` |
| `TOOL_SECRET` | Signs the per-session token Agora sends to `/llm` (expires after 4 hours) |
| `TOOL_SYNC_WAIT` | Seconds a tool call waits before answering "working on it" (default 8) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` / `GROQ_API_KEY` | Server-wide LLM keys, used after a user's own keys from Settings. Each may hold several comma-separated keys, rotated on rate limits. The first provider set is the default, or force one with `LLM_PROVIDER` |
| `PEXELS_API_KEY` | Optional. Better stock photos for the researcher; without it photos come from Wikimedia Commons |
| `MODEL_JNR` / `MODEL_SNR` / `MODEL_ARCHITECT` / `MODEL_RESEARCH` | Optional per-tier model overrides (research defaults to the jnr model) |
| `SESSION_SECRET` | Signs the login cookie and encrypts stored GitHub tokens and user API keys. Auto-generated into `data/.session_secret` if unset; set it explicitly in production |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Optional. Google sign-in. Redirect URI: `<site>/auth/google/callback` |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | Optional. GitHub sign-in and push (scope `repo`). Callback URL: `<site>/auth/github/callback` |

Default tier models:

| Tier | Anthropic | OpenAI | Gemini | Groq |
|---|---|---|---|---|
| jnr (small tweaks) | `claude-haiku-5-5` | `gpt-4.1-mini` | `gemini-2.5-flash` | `qwen/qwen3.8-27b` |
| snr (features) | `claude-sonnet-5-5` | `gpt-4.1` | `gemini-2.5-pro` | `openai/gpt-oss-120b` |
| architect (redesigns) | `claude-opus-5-5` | `gpt-4.1` | `gemini-2.5-pro` | `openai/gpt-oss-120b` |

### Settings and accounts

The **Settings** button in the top bar works without signing in: keys are saved to a guest account tied to the browser (encrypted on the server). Signing in later turns that guest into a full account and keeps its keys and projects.

To enable Google / GitHub sign-in (needed for Push to GitHub):

1. **Google**: Google Cloud Console > APIs & Services > Credentials > Create OAuth client ID (Web application). Add `http://localhost:8000/auth/google/callback` (and/or `http://localhost:5173/...` for Vite dev, and your deployed URL) as an authorized redirect URI.
2. **GitHub**: github.com/settings/developers > New OAuth App. Callback URL `http://localhost:8000/auth/github/callback` (a GitHub OAuth App takes one callback URL, so create one app per environment).
3. Put the four values in `.env` and restart.

A project is owned by the account (or guest) that first works on it. Its edits, typed or by voice, use the owner's keys from Settings first, then the server's `.env` keys. Pushing replaces the repo's default branch contents with the current version as one new commit (earlier history is kept).

## Repository layout

```
backend/
  main.py            FastAPI app: browser API, preview, zip, push
  voice.py           Agora agent start/stop, RTC tokens, per-session LLM tokens
  vox_llm.py         OpenAI-compatible /llm endpoint Agora calls; server-side tool loop, SSE
  vox_tools.py       Vox's tool registry (voice and typed fallback)
  agora_token/       Agora AccessToken2 builder, vendored from AgoraIO/Tools
  auth.py            Google / GitHub OAuth sign-in
  users.py           accounts, encrypted GitHub tokens and per-user AI settings
  github.py          push a site to a repo as one commit (Git Data API)
  editor/research.py researcher: design brief + photo search (Pexels / Wikimedia)
  editor/router.py   intent-router: instruction → tier
  editor/engine.py   code-editor: generate, patch, escalate, undo, describe
  editor/prompts.py  system prompts (tier personas load from agents/*/SOUL.md)
  llm.py             Anthropic / OpenAI / Gemini / Groq completion, per-user keys
  store.py           projects on disk with version history
  events.py          SSE fan-out
  preview_inject.js  element picker + scroll keeper injected into previews
agents/vox/SOUL.md      Vox's system prompt
frontend/               Vite + React app (preview, voice panel, activity, versions)
agents/                 GitAgent specs. code-editor tier SOUL.md files are live prompts
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design details and [AGENTS.md](AGENTS.md) for the agent hierarchy.

## Security notes

- Agora, Murf and LLM keys never reach the browser. It only receives an RTC token for its own channel.
- `/llm/{project_id}/...` requires a per-session HMAC token (signed with `TOOL_SECRET`, bound to the project, expires in 4 hours).
- Generated sites are served from `/preview/<id>/` with path-traversal checks and a `Content-Security-Policy: sandbox` header (no `allow-same-origin`). Anyone with a project link can edit a page, so its scripts run in an opaque origin and can't use the viewer's login to call the API.
- User API keys and GitHub tokens are encrypted at rest and never sent back to the browser. Custom model base URLs must be public `https://` addresses.

---

Built by Vivan Rajath for the Build with Agora Bengaluru 2026 Voice AI Hackathon (track: AI Agents That Get Things Done). [Pitch deck](https://vivanrajath.github.io/buildathon-deck/) · [Demo video](https://www.loom.com/share/fc5195a409e84af18a43ca9baddb31f4)
