"""Parses and applies the developers' multi-file output (see prompts.PATCH_PROTOCOL / FULL_PROTOCOL).

    FILE: src/components/Hero.jsx        SEARCH/REPLACE blocks for an existing file
    NEW FILE: src/components/Gallery.jsx  a fenced, complete new file
    DELETE FILE: src/components/Old.jsx

Pure functions on {path: text} dicts, so they are easy to test.
"""
import re
import textwrap
from dataclasses import dataclass, field

from ..site_template import LLM_FILE_RE

SUMMARY_RE = re.compile(r"^\s*SUMMARY:\s*(.+)$", re.MULTILINE)
HEADER_RE = re.compile(r"^[ \t]*(?:\*\*)?(FILE|NEW FILE|DELETE FILE):[ \t]*`?([^\s`*]+)`?(?:\*\*)?[ \t]*$", re.MULTILINE)
BLOCK_RE = re.compile(r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n?>>>>>>> REPLACE", re.DOTALL)
FENCE_RE = re.compile(r"```[\w.+-]*[ \t]*\n(.*?)\n?```", re.DOTALL)

PROTECTED = {"src/App.jsx", "src/index.css", "src/data/site.js"}


class EditFailed(Exception):
    pass


@dataclass
class Op:
    kind: str  # patch | create | delete
    path: str
    blocks: list[tuple[str, str]] = field(default_factory=list)
    content: str = ""


def _segments(raw: str):
    """(kind, path, body) for every header in the output."""
    raw = raw.replace("\r\n", "\n")
    headers = list(HEADER_RE.finditer(raw))
    for i, h in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(raw)
        yield h.group(1), h.group(2).lstrip("./"), raw[h.end():end]


def check_path(path: str) -> None:
    if not LLM_FILE_RE.match(path):
        raise EditFailed(f"{path} can't be written: only src/index.css, src/App.jsx, src/data/site.js and src/components/<Name>.jsx are editable")


def parse_patch(raw: str, default_path: str | None = None) -> list[Op]:
    """Ops from patch-protocol output. Blocks without a FILE header go to default_path
    (the only file the developer was shown), if there is one."""
    ops: list[Op] = []
    for kind, path, body in _segments(raw):
        if kind == "DELETE FILE":
            ops.append(Op("delete", path))
        elif kind == "NEW FILE":
            fence = FENCE_RE.search(body)
            if not fence:
                raise EditFailed(f"NEW FILE {path} has no fenced content")
            ops.append(Op("create", path, content=fence.group(1)))
        else:
            blocks = BLOCK_RE.findall(body)
            if blocks:
                ops.append(Op("patch", path, blocks))
            elif fence := FENCE_RE.search(body):
                ops.append(Op("create", path, content=fence.group(1)))  # a whole file under FILE:
    if not ops and default_path:
        blocks = BLOCK_RE.findall(raw.replace("\r\n", "\n"))
        if blocks:
            ops.append(Op("patch", default_path, blocks))
    if not ops:
        raise EditFailed("no file changes in model output")
    return ops


def parse_full(raw: str) -> dict[str, str]:
    """Every FILE entry of full-protocol output -> {path: content}."""
    files = {}
    for kind, path, body in _segments(raw):
        if kind == "DELETE FILE":
            continue
        fence = FENCE_RE.search(body)
        if fence:
            files[path] = fence.group(1)
    for required in ("src/App.jsx", "src/index.css"):
        if required not in files:
            raise EditFailed(f"the model left out {required}")
    return files


def apply(files: dict[str, str], ops: list[Op]) -> tuple[dict[str, str], list[str]]:
    """Returns (all files after the change, deleted paths)."""
    result = dict(files)
    deleted: list[str] = []
    for op in ops:
        check_path(op.path)
        if op.kind == "delete":
            if op.path in PROTECTED:
                raise EditFailed(f"{op.path} can't be deleted")
            if result.pop(op.path, None) is not None:
                deleted.append(op.path)
        elif op.kind == "create":
            result[op.path] = op.content.rstrip() + "\n"
        else:
            if op.path not in result:
                raise EditFailed(f"patch for {op.path}, which doesn't exist")
            text = result[op.path]
            for search, replace in op.blocks:
                text = apply_one(text, search, replace, op.path)
            result[op.path] = text
    return result, deleted


def replace_all(files: dict[str, str], new: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """A full rewrite: new LLM-owned files replace the old ones; fixed files are kept."""
    for path in new:
        check_path(path)
    kept = {p: t for p, t in files.items() if not LLM_FILE_RE.match(p)}
    deleted = [p for p in files if LLM_FILE_RE.match(p) and p not in new]
    return {**kept, **{p: t.rstrip() + "\n" for p, t in new.items()}}, deleted


def apply_one(text: str, search: str, replace: str, path: str = "the file") -> str:
    count = text.count(search)
    if count == 1:
        return text.replace(search, replace, 1)
    if count > 1:
        raise EditFailed(f"patch target in {path} is ambiguous")

    # Whitespace-tolerant fallback: match stripped lines, keep the file's indentation.
    lines = text.split("\n")
    wanted = [line.strip() for line in search.split("\n")]
    hits = [
        i for i in range(len(lines) - len(wanted) + 1)
        if [line.strip() for line in lines[i : i + len(wanted)]] == wanted
    ]
    if len(hits) != 1:
        raise EditFailed(f"patch target not found in {path}")
    i = hits[0]
    indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    replaced = [indent + line if line.strip() else line for line in textwrap.dedent(replace).split("\n")] if replace else []
    return "\n".join(lines[:i] + replaced + lines[i + len(wanted) :])


def summary(raw: str) -> str:
    match = SUMMARY_RE.search(raw)
    return match.group(1).strip() if match else ""
