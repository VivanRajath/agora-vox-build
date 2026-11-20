"""On-disk project store with linear version history.

data/projects/<id>/
  meta.json        id, created, versions[], current, selection, brief, draft, published
  site/            the React source project (see site_template.py)
  history/<n>/     snapshot of site/ for version n
  preview/<n>/     built output of version n (builder.py), served to the preview
"""
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from . import config, site_template

ID_RE = re.compile(r"^[a-f0-9]{8,32}$")

TEXT_SUFFIXES = (".html", ".css", ".js", ".jsx", ".json")


class ProjectNotFound(KeyError):
    pass


def _dir(project_id: str) -> Path:
    if not ID_RE.match(project_id or ""):
        raise ProjectNotFound(project_id)
    return config.DATA_DIR / project_id


def exists(project_id: str) -> bool:
    try:
        return (_dir(project_id) / "meta.json").is_file()
    except ProjectNotFound:
        return False


def create() -> dict:
    project_id = uuid.uuid4().hex[:12]
    root = _dir(project_id)
    for rel, text in {**site_template.fixed_files("My website", ""), **site_template.STARTER_FILES}.items():
        target = root / "site" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (root / "site" / "public" / "images").mkdir(parents=True)
    meta = {"id": project_id, "created": time.time(), "versions": [], "current": 0, "selection": None}
    _snapshot(root, 0)
    meta["versions"].append({"n": 0, "summary": "Blank starter page", "instruction": "", "tier": "", "ts": time.time()})
    _save_meta(root, meta)
    return meta


def load_meta(project_id: str) -> dict:
    path = _dir(project_id) / "meta.json"
    if not path.is_file():
        raise ProjectNotFound(project_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _save_meta(root: Path, meta: dict) -> None:
    tmp = root / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    tmp.replace(root / "meta.json")


def site_dir(project_id: str) -> Path:
    return _dir(project_id) / "site"


def public_dir(project_id: str) -> Path:
    return site_dir(project_id) / "public"


def project_dir(project_id: str) -> Path:
    return _dir(project_id)


def preview_dir(project_id: str, n: int | None = None) -> Path:
    if n is None:
        n = load_meta(project_id)["current"]
    return _dir(project_id) / "preview" / str(n)


def read_files(project_id: str) -> dict[str, str]:
    """The source project's text files (everything except public/ assets)."""
    site = site_dir(project_id)
    return {
        p.relative_to(site).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(site.rglob("*"))
        if p.is_file() and p.suffix in TEXT_SUFFIXES and "public" not in p.relative_to(site).parts[:1]
    }


def _target(site: Path, rel: str) -> Path:
    target = (site / rel).resolve()
    if site.resolve() not in target.parents:
        raise ValueError(f"Refusing to write outside the site: {rel}")
    return target


def commit(project_id: str, files: dict[str, str], summary: str, instruction: str, tier: str, deletes: tuple | list = ()) -> dict:
    """Write (and delete) files in site/ and record a new version. Versions after the
    current one (i.e. undone ones) are discarded, like an editor's undo stack."""
    root = _dir(project_id)
    meta = load_meta(project_id)
    site = root / "site"
    for rel, content in files.items():
        target = _target(site, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for rel in deletes:
        _target(site, rel).unlink(missing_ok=True)

    for v in meta["versions"]:
        if v["n"] > meta["current"]:
            shutil.rmtree(root / "history" / str(v["n"]), ignore_errors=True)
            shutil.rmtree(root / "preview" / str(v["n"]), ignore_errors=True)
    meta["versions"] = [v for v in meta["versions"] if v["n"] <= meta["current"]]

    n = meta["current"] + 1
    _snapshot(root, n)
    version = {"n": n, "summary": summary, "instruction": instruction, "tier": tier, "ts": time.time()}
    meta["versions"].append(version)
    meta["current"] = n
    _save_meta(root, meta)
    return version


def restore(project_id: str, n: int) -> dict:
    root = _dir(project_id)
    meta = load_meta(project_id)
    snap = root / "history" / str(n)
    if not snap.is_dir():
        raise ValueError(f"No version {n}")
    shutil.rmtree(root / "site")
    shutil.copytree(snap, root / "site")
    meta["current"] = n
    _save_meta(root, meta)
    return meta


def undo(project_id: str) -> dict | None:
    meta = load_meta(project_id)
    if meta["current"] <= 0:
        return None
    return restore(project_id, meta["current"] - 1)


def set_selection(project_id: str, selection: dict | None) -> None:
    root = _dir(project_id)
    meta = load_meta(project_id)
    meta["selection"] = selection
    _save_meta(root, meta)


def set_brief(project_id: str, brief: dict) -> None:
    """The researcher's design brief, reused by later edits for consistency."""
    root = _dir(project_id)
    meta = load_meta(project_id)
    meta["brief"] = brief
    _save_meta(root, meta)


def update(project_id: str, **fields) -> dict:
    """Sets top-level meta fields such as owner (user id) and github (last push target)."""
    root = _dir(project_id)
    meta = load_meta(project_id)
    meta.update(fields)
    _save_meta(root, meta)
    return meta


def export_files(project_id: str) -> dict[str, bytes]:
    """Every file of the live site, for the zip download and GitHub push."""
    site = site_dir(project_id)
    return {p.relative_to(site).as_posix(): p.read_bytes() for p in sorted(site.rglob("*")) if p.is_file()}


def _snapshot(root: Path, n: int) -> None:
    dest = root / "history" / str(n)
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(root / "site", dest)
