"""Builds a site's React source into static files, in well under a second.

  esbuild    src/main.jsx -> bundle.js (React resolved from the shared runtime/node_modules)
  tailwind   src/index.css -> styles.css (Tailwind v4 standalone CLI)
  html       site_template.built_html with the fonts link

A build doubles as the validator for every edit: if the import whitelist, esbuild
or Tailwind rejects the result, the edit fails and escalates to the next tier
with the error text.
"""
import asyncio
import logging
import os
import posixpath
import re
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import config, site_template

log = logging.getLogger("vox-build.builder")

RUNTIME_DIR = Path(os.getenv("SITE_RUNTIME_DIR", config.ROOT_DIR / "runtime"))
EXE = ".exe" if os.name == "nt" else ""
ESBUILD = Path(os.getenv("ESBUILD_BIN", RUNTIME_DIR / "bin" / f"esbuild{EXE}"))
TAILWIND = Path(os.getenv("TAILWIND_BIN", RUNTIME_DIR / "bin" / f"tailwindcss{EXE}"))
TIMEOUT = 30

IMPORT_RE = re.compile(
    r"""(?:^|[;\s])(?:import\s+(?:[\w*{}\s,$]+\s+from\s+)?|export\s+(?:\*|\{[^}]*\})\s+from\s+)["']([^"']+)["']"""
    r"""|\bimport\s*\(\s*["']([^"']+)["']\s*\)|\brequire\s*\(\s*["']([^"']+)["']\s*\)""",
    re.MULTILINE,
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class BuildResult:
    ok: bool
    errors: str = ""
    out_dir: Path | None = None
    ms: int = 0
    stage: Path | None = field(default=None, repr=False)


class BuildToolsMissing(RuntimeError):
    pass


def tools_ready() -> bool:
    return ESBUILD.is_file() and TAILWIND.is_file() and (RUNTIME_DIR / "node_modules" / "react").is_dir()


# ---------------------------------------------------------------------------
# Import whitelist
# ---------------------------------------------------------------------------

def check_imports(files: dict[str, str]) -> list[str]:
    """Problems with what the LLM-owned files import; empty when everything is allowed."""
    problems = []
    for path, text in files.items():
        if not path.endswith((".js", ".jsx")) or path in site_template.FIXED_FILES:
            continue
        for match in IMPORT_RE.finditer(text):
            spec = next(g for g in match.groups() if g)
            if spec.startswith("."):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
                if not target.startswith("src/"):
                    problems.append(f"{path}: import {spec!r} points outside src/")
                continue
            package = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
            if package not in site_template.ALLOWED_PACKAGES:
                problems.append(f"{path}: import {spec!r} is not allowed (only react, react-dom, lucide-react, framer-motion and relative files)")
    return problems


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
    return proc.returncode, ANSI_RE.sub("", (proc.stderr or "") + (proc.stdout or "")).strip()


def _build_sync(stage: Path, public_dir: Path | None, title: str, fonts_url: str) -> BuildResult:
    start = time.perf_counter()
    out = stage / "_out"
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "NODE_PATH": str(RUNTIME_DIR / "node_modules")}
    # Independent steps, so they run side by side (Tailwind's startup dominates).
    with ThreadPoolExecutor(max_workers=2) as pool:
        bundle = pool.submit(_run, [
            str(ESBUILD), "src/main.jsx", "--bundle", f"--outfile={out / 'bundle.js'}",
            "--format=iife", "--jsx=automatic", "--loader:.js=jsx", "--loader:.css=empty",
            "--define:process.env.NODE_ENV=\"production\"", "--minify", "--target=es2020",
            "--log-level=error", "--log-limit=6", "--color=false",
        ], stage, env)
        css = pool.submit(_run, [str(TAILWIND), "-i", "src/index.css", "-o", str(out / "styles.css"), "--minify"], stage)
        code, text = bundle.result()
        css_code, css_text = css.result()
    if code != 0:
        return BuildResult(False, _tidy(text, stage), ms=_ms(start), stage=stage)
    code, text = css_code, css_text
    if code != 0 or not (out / "styles.css").is_file():
        return BuildResult(False, "Tailwind: " + _tidy(text, stage), ms=_ms(start), stage=stage)
    (out / "index.html").write_text(site_template.built_html(title, fonts_url), encoding="utf-8")
    if public_dir and public_dir.is_dir():
        shutil.copytree(public_dir, out, dirs_exist_ok=True)
    return BuildResult(True, out_dir=out, ms=_ms(start), stage=stage)


def _tidy(text: str, stage: Path) -> str:
    """Error text for the next developer: stage paths stripped, kept short."""
    text = text.replace(str(stage) + os.sep, "").replace(str(stage), "").replace("\\", "/")
    return text[:2500] or "the build failed without an error message"


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


async def build(files: dict[str, str], work_dir: Path, public_dir: Path | None, title: str, fonts_url: str) -> BuildResult:
    """Builds a full set of source files in a fresh staging folder under work_dir.

    The caller promotes result.out_dir on success and must call discard(result) either way."""
    if not tools_ready():
        raise BuildToolsMissing("The site builder isn't installed. Run scripts/setup_build_tools.sh")
    problems = check_imports(files)
    if problems:
        return BuildResult(False, "\n".join(problems))
    stage = work_dir / f".stage-{uuid.uuid4().hex[:8]}"
    for rel, text in files.items():
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    try:
        result = await asyncio.to_thread(_build_sync, stage, public_dir, title, fonts_url)
    except subprocess.TimeoutExpired:
        result = BuildResult(False, "the build timed out", stage=stage)
    log.info("build %s in %d ms", "ok" if result.ok else "failed", result.ms)
    result.stage = stage
    return result


def promote(result: BuildResult, dest: Path) -> None:
    """Moves a successful build's output to dest and cleans up its staging folder."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(result.out_dir), str(dest))
    discard(result)


def discard(result: BuildResult) -> None:
    if result.stage:
        shutil.rmtree(result.stage, ignore_errors=True)
