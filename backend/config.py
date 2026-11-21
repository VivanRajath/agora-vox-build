import os
import re
import secrets
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT_DIR / "data" / "projects"))
USERS_DIR = Path(os.getenv("USERS_DIR", DATA_DIR.parent / "users"))
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


def _session_secret() -> str:
    """Signs session cookies and encrypts stored tokens/keys. Without an env
    value, a random one is kept on disk so restarts don't log everyone out."""
    explicit = os.getenv("SESSION_SECRET", "")
    if explicit:
        return explicit
    path = DATA_DIR.parent / ".session_secret"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    path.write_text(value, encoding="utf-8")
    return value


SESSION_SECRET = _session_secret()

# OAuth apps. Callback URLs: <site>/auth/google/callback and <site>/auth/github/callback
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")

# Agora Conversational AI (voice). App ID + certificate sign RTC tokens; the
# customer ID/secret authenticate the REST calls that start and stop the agent.
AGORA_APP_ID = os.getenv("AGORA_APP_ID", "")
AGORA_APP_CERTIFICATE = os.getenv("AGORA_APP_CERTIFICATE", "")
AGORA_CUSTOMER_ID = os.getenv("AGORA_CUSTOMER_ID", "")
AGORA_CUSTOMER_SECRET = os.getenv("AGORA_CUSTOMER_SECRET", "")
AGORA_API_BASE = os.getenv("AGORA_API_BASE", "https://api.agora.io/api/conversational-ai-agent/v2")

# Murf Falcon TTS, called by Agora's engine. Falcon voice ids are bare names.
MURF_API_KEY = os.getenv("MURF_API_KEY", "")
MURF_VOICE_ID = os.getenv("MURF_VOICE_ID", "Priya")
MURF_LOCALE = os.getenv("MURF_LOCALE", "en-IN")
MURF_WS_URL = os.getenv("MURF_WS_URL", "wss://global.api.murf.ai/v1/speech/stream-input")
MURF_MODEL = os.getenv("MURF_MODEL", "FALCON")

ASR_LANGUAGE = os.getenv("ASR_LANGUAGE", "en-IN")
# End of the user's turn: "semantic" waits for a finished thought (en-US, en-IN and Chinese only;
# other languages fall back to VAD), "vad" ends after a fixed silence.
END_OF_SPEECH = os.getenv("END_OF_SPEECH", "semantic")
# How long the user must speak before Vox stops talking. Higher ignores coughs, clicks and echo.
INTERRUPT_MS = int(os.getenv("INTERRUPT_MS", "300"))

# Model tier for Vox's conversational layer (the /llm endpoint Agora calls each turn).
VOX_LLM_TIER = os.getenv("VOX_LLM_TIER", "jnr")

# Signs the per-session tokens Agora sends back to our /llm endpoint.
# Falls back to the session secret so local dev works without it.
TOOL_SECRET = os.getenv("TOOL_SECRET", "") or SESSION_SECRET

# Public https URL Agora's cloud uses to reach /llm. Render sets RENDER_EXTERNAL_URL itself.
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")

# How long a voice tool call waits for an edit before answering
# "still working" and letting the edit finish in the background.
TOOL_SYNC_WAIT = float(os.getenv("TOOL_SYNC_WAIT", "8"))


def voice_enabled() -> bool:
    return all((AGORA_APP_ID, AGORA_APP_CERTIFICATE, AGORA_CUSTOMER_ID, AGORA_CUSTOMER_SECRET, MURF_API_KEY, PUBLIC_BASE_URL))


# LLM provider: "anthropic", "openai", "gemini" or "groq". Auto-detected from keys.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# Each variable may hold several comma-separated keys, and numbered extras work too
# (GROQ_API_KEY_1, GROQ_API_KEY_2, ...). They're rotated on rate limits.
def _env_keys(name: str) -> list[str]:
    values = [os.getenv(name, "")]
    values += [v for k, v in sorted(os.environ.items()) if re.fullmatch(rf"{name}_\d+", k)]
    keys: list[str] = []
    for value in values:
        for key in value.split(","):
            key = key.strip()
            if key and key not in keys:
                keys.append(key)
    return keys


ENV_KEYS = {
    provider: _env_keys(f"{provider.upper()}_API_KEY")
    for provider in ("anthropic", "openai", "gemini", "groq")
}

# Optional: better stock photos for the researcher (free key at pexels.com/api).
# Without it, photos come from Wikimedia Commons.
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")


def _detect_provider() -> str:
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    for provider in ("anthropic", "openai", "gemini", "groq"):
        if ENV_KEYS[provider]:
            return provider
    return "none"


LLM_PROVIDER = _detect_provider()

# Tier -> model per provider. jnr = fast/cheap, snr = standard, architect = top.
DEFAULT_MODELS = {
    "anthropic": {"jnr": "claude-haiku-5-5", "snr": "claude-sonnet-5-5", "architect": "claude-opus-5-5"},
    "openai": {"jnr": "gpt-4.1-mini", "snr": "gpt-4.1", "architect": "gpt-4.1"},
    "gemini": {"jnr": "gemini-2.5-flash", "snr": "gemini-2.5-pro", "architect": "gemini-2.5-pro"},
    "groq": {"jnr": "qwen/qwen3.8-27b", "snr": "openai/gpt-oss-120b", "architect": "openai/gpt-oss-120b"},
}

# Tried in order when a model is rate limited. Groq's per-model token quotas
# are small on the free tier, so spilling over to a sibling model keeps a
# live demo going instead of stalling.
RATE_LIMIT_FALLBACKS = {
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"],
}


# Helper agents borrow a developer tier's model. The planners run on the fast
# model (its own rate-limit quota on Groq); the design critic needs the stronger one.
AGENT_TIER = {"research": "jnr", "fontknight": "jnr", "copywriter": "jnr", "critic": "snr", "vox": VOX_LLM_TIER}

# Set DESIGN_CRITIC=0 to skip the review pass on new sites (saves one model call).
DESIGN_CRITIC = os.getenv("DESIGN_CRITIC", "1") != "0"


def model_for(tier: str) -> str:
    override = os.getenv(f"MODEL_{tier.upper()}")
    if override:
        return override
    if tier in AGENT_TIER:
        return model_for(AGENT_TIER[tier])
    return DEFAULT_MODELS.get(LLM_PROVIDER, {}).get(tier, "")

# Test hook: VOX_TEST_BREAK_JNR=1 makes every jnr patch fail to build, to show escalation.
TEST_BREAK_JNR = os.getenv("VOX_TEST_BREAK_JNR", "") == "1"
