import os
import sys
import tempfile
from pathlib import Path

# Isolated data dir and no real keys, before any backend module is imported.
_tmp = tempfile.mkdtemp(prefix="vox-test-")
os.environ["DATA_DIR"] = str(Path(_tmp) / "projects")
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["TOOL_SECRET"] = "test-tool-secret"
for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "PEXELS_API_KEY"):
    os.environ[key] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
