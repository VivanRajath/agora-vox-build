"""code-editor: turns spoken instructions into versions of a React site.

jnr/snr tiers return small multi-file SEARCH/REPLACE patches (fast); the
architect tier writes every file. Each result is built (builder.py) before it
is committed: a patch that fails to parse, apply or build escalates to the next
tier with the error text, ending in a full rewrite.

A new site is planned by a small team first: the researcher writes the brief
(plan() stores it as a draft the user approves), then font-knight (type and
icons), the copywriter and the photo search run in parallel, the architect
builds the project and the design critic reviews it.
"""
import asyncio
import json
import logging
import re
from collections import defaultdict
from pathlib import Path

from .. import builder, config, events, llm, site_template, store
from . import copywriter, fontknight, images, patches, prompts, research
from .patches import EditFailed
from .router import ESCALATION, route

log = logging.getLogger("vox-build.editor")

_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# No emoji/pictographs or em/en dashes on generated pages, whatever the model writes.
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]")
DASH_RE = re.compile(r"\s*(?:—|&mdash;|&#8212;)\s*|–|&ndash;|&#8211;")

# Patches are small; whole projects need room. Reasoning models spend part of the budget thinking.
PATCH_MAX_TOKENS = 6000

# Groq's on-demand tier rejects any single request over 8k tokens (prompt plus the output budget),
# per key. A whole site is ~9k, so on Groq the review and repair passes see only the files they
# need, within this budget (about 4 characters a token), and write shorter replies.
FOCUS_CHARS = 12000
FOCUS_MAX_TOKENS = 2500


def _tight() -> bool:
    return config.LLM_PROVIDER == "groq"


def _focus(files: dict[str, str], wanted: list[str], budget: int = FOCUS_CHARS) -> dict[str, str]:
    """Files in priority order until the budget is spent; the first one always goes in."""
    out, used = {}, 0
    for path in dict.fromkeys(wanted):
        text = files.get(path)
        if text is None or (out and used + len(text) > budget):
            continue
        out[path] = text
        used += len(text)
    return out


def _review_files(files: dict[str, str]) -> dict[str, str]:
    """What the critic sees: the theme and layout first, then the top sections in page order."""
    if not _tight():
        return _llm_files(files)
    order = re.findall(r"<([A-Z][A-Za-z0-9]*)", files.get("src/App.jsx", ""))
    sections = [f"src/components/{name}.jsx" for name in order]
    return _focus(files, ["src/index.css", "src/App.jsx", *sections])


def _repair_files(files: dict[str, str], error: str) -> dict[str, str]:
    """What the repair pass sees: the files the build error names, then App.jsx and the data file."""
    if not _tight():
        return _llm_files(files)
    named = [p for p in re.findall(r"src/[\w./-]+\.(?:jsx|js|css)", error or "") if site_template.LLM_FILE_RE.match(p)]
    return _focus(files, [*named, "src/App.jsx", "src/data/site.js", "src/index.css"])
FULL_MAX_TOKENS = 20000

# Words that point at a component even when its name isn't said.
SECTION_WORDS = {
    "Header": ("header", "nav", "navbar", "navigation", "logo", "top bar"),
    "Hero": ("hero", "headline", "banner", "main heading", "top of the page", "first section"),
    "Footer": ("footer", "bottom of the page", "credits", "copyright"),
    "Testimonials": ("testimonial", "review", "quote"),
    "Gallery": ("gallery", "photos", "pictures"),
    "Contact": ("contact", "form", "address", "phone", "map"),
    "Menu": ("menu", "dish", "price"),
}
STYLE_WORDS = ("colour", "color", "font", "theme", "palette", "background", "dark", "light", "typography")
CONTENT_WORDS = ("text", "wording", "say", "price", "hours", "phone", "address", "name", "title", "copy", "email", "rename", "spell")


# ---------------------------------------------------------------------------
# Public entry points (serialized per project)
# ---------------------------------------------------------------------------

async def plan(project_id: str, description: str) -> dict:
    """researcher only: a design brief, stored as a draft for the user to approve."""
    async with _locks[project_id]:
        events.publish(project_id, {"type": "job_started", "kind": "plan", "instruction": description, "tier": "research"})
        try:
            brief = await research.brief(description)
        except llm.LLMError as e:
            return _fail(project_id, description, str(e))
        if not brief:
            return _fail(project_id, description, "The researcher couldn't plan the site. Try describing it again")
        spoken = research.spoken_plan(brief)
        store.update(project_id, draft={"description": description, "brief": brief, "spoken": spoken})
        _publish_research(project_id, brief, [])
        events.publish(project_id, {"type": "plan", "brief": _brief_card(brief), "spoken": spoken})
        events.publish(project_id, {"type": "job_planned", "summary": spoken})
        return {"ok": True, "message": spoken, "brief": brief}


async def generate(project_id: str, description: str, brief: dict | None = None) -> dict:
    """Builds a whole new site. brief is an approved draft; without one the researcher runs first."""
    async with _locks[project_id]:
        events.publish(project_id, {"type": "job_started", "kind": "generate", "instruction": description, "tier": "research" if brief is None else "team"})
        current = store.read_files(project_id)
        try:
            notes, brief = await _plan_site(project_id, description, brief)
            events.publish(project_id, {"type": "job_stage", "tier": "architect"})
            raw = await llm.complete(prompts.GENERATE_SYSTEM, prompts.generate_user(description, notes), "architect", FULL_MAX_TOKENS)
            files, deleted = patches.replace_all(current, _clean_files(patches.parse_full(raw)))
            summary = _clean_text(patches.summary(raw)) or "Built your new website."
            reviewed = await _review(project_id, files, brief)
            # The critic's version first; if it doesn't build, the architect's; if neither does, a repair pass.
            for candidate in ([reviewed] if reviewed is not files else []) + [files]:
                try:
                    version = await _commit(project_id, candidate, _deleted(current, candidate), summary, description, "architect")
                    store.update(project_id, draft=None)
                    return _done(project_id, version)
                except EditFailed as e:
                    error = str(e)
            version = await _repair(project_id, current, files, error, summary, description)
            store.update(project_id, draft=None)
            return _done(project_id, version)
        except (llm.LLMError, EditFailed, builder.BuildToolsMissing) as e:
            return _fail(project_id, description, str(e))


async def edit(project_id: str, instruction: str) -> dict:
    async with _locks[project_id]:
        tier = route(instruction)
        events.publish(project_id, {"type": "job_started", "kind": "edit", "instruction": instruction, "tier": tier})
        files = store.read_files(project_id)
        meta = store.load_meta(project_id)
        notes = [_brief_reminder(meta.get("brief"))]
        if research.wants_images(instruction):
            events.publish(project_id, {"type": "job_stage", "tier": "research"})
            needs = await research.image_needs(instruction, meta.get("brief"))
            images = await research.find_images(project_id, needs)
            _publish_research(project_id, None, images)
            notes.insert(0, research.notes_for_prompt(None, images))
            # Fresh photos only land in the page through a patch or rewrite, so start at snr.
            tier = "snr" if tier == "jnr" else tier
            events.publish(project_id, {"type": "job_stage", "tier": tier})
        note_text = "\n\n".join(n for n in notes if n)
        tree = sorted(files)

        error = ""
        while tier:
            full_rewrite = tier == "architect"
            context = _context(files, instruction, meta.get("selection"), tier)
            user = prompts.edit_user(instruction, context, tree, meta.get("selection"), note_text, error)
            try:
                max_tokens = FULL_MAX_TOKENS if full_rewrite else PATCH_MAX_TOKENS
                raw = await llm.complete(prompts.edit_system(tier, full_rewrite), user, tier, max_tokens)
                if full_rewrite:
                    new_files, deleted = patches.replace_all(files, _clean_files(patches.parse_full(raw)))
                else:
                    ops = patches.parse_patch(raw, default_path=next(iter(context)) if len(context) == 1 else None)
                    new_files, deleted = patches.apply(files, ops)
                    new_files = {**new_files, **_clean_files({p: t for p, t in new_files.items() if files.get(p) != t})}
                    if tier == "jnr" and config.TEST_BREAK_JNR:
                        new_files = _break_for_test(files, new_files)
                summary = _clean_text(patches.summary(raw)) or "Updated your website."
                changed = sorted(p for p, t in new_files.items() if files.get(p) != t) + deleted
                version = await _commit(project_id, new_files, deleted, summary, instruction, tier, changed)
                return _done(project_id, version)
            except (EditFailed, llm.LLMTruncated) as e:
                # A patch that ran out of room or doesn't build is as useless as one that doesn't apply.
                error = str(e)
                tier = ESCALATION[tier]
                if tier:
                    events.publish(project_id, {"type": "job_escalated", "tier": tier, "reason": error[:300]})
            except builder.BuildToolsMissing as e:
                return _fail(project_id, instruction, str(e))
            except llm.LLMError as e:
                return _fail(project_id, instruction, str(e))
        return _fail(project_id, instruction, _short_error(error) or "Could not apply that change")


async def change_image(project_id: str, target: str, new_subject: str = "") -> dict:
    """Swaps one photo: find it from the user's words or click, fetch a new one, change only its path.
    No model writes code here, so the rest of the site can't change."""
    instruction = f"Change the {target} photo" + (f" to {new_subject}" if new_subject else "")
    async with _locks[project_id]:
        events.publish(project_id, {"type": "job_started", "kind": "image", "instruction": instruction, "tier": "photos"})
        files = store.read_files(project_id)
        choice = images.pick(files, target, store.load_meta(project_id).get("selection"))
        if not choice.slots:
            return _fail(project_id, instruction, choice.message)

        old_alt = choice.alt
        subject = new_subject.strip() or old_alt or images.describe(choice.slots[0])
        old_id = choice.image.rsplit("-", 1)[-1].split(".")[0]
        slug = re.sub(r"[^a-z0-9]+", "-", (new_subject or "photo").lower()).strip("-")[:24] or "photo"
        need = {
            "slot": slug,
            "query": " ".join(subject.split()[:6]),
            "fallback": " ".join(subject.split()[-2:]),
            "alt": new_subject.strip().capitalize() if new_subject.strip() else old_alt,
            "avoid": [old_id],
        }
        found = await research.find_images(project_id, [need])
        if not found:
            return _fail(project_id, instruction, f"I couldn't find a photo of {subject}. Try describing it another way")
        photo = found[0]
        _publish_research(project_id, None, found)
        new_files = images.replace(files, choice.slots, photo["path"], photo["alt"] if new_subject.strip() else "")
        where = f" in {len(choice.slots)} places" if len(choice.slots) > 1 else ""
        summary = f"Swapped that photo{where} for {'a new one showing ' + new_subject.strip() if new_subject.strip() else 'a different one'}."
        try:
            version = await _commit(project_id, new_files, [], summary, instruction, "photos")
        except EditFailed as e:
            return _fail(project_id, instruction, str(e))
        return _done(project_id, version)


async def undo(project_id: str) -> dict:
    async with _locks[project_id]:
        meta = store.undo(project_id)
        if meta is None:
            return {"ok": False, "message": "There is nothing to undo."}
        version = next(v for v in meta["versions"] if v["n"] == meta["current"])
        events.publish(project_id, {"type": "version", "current": meta["current"], "versions": meta["versions"], "undo": True})
        return {"ok": True, "message": f"Undone. You're back to: {version['summary']}", "version": meta["current"]}


async def restore(project_id: str, n: int) -> dict:
    async with _locks[project_id]:
        meta = store.restore(project_id, n)
        events.publish(project_id, {"type": "version", "current": meta["current"], "versions": meta["versions"]})
        return {"ok": True, "version": n}


async def ensure_preview(project_id: str, n: int) -> Path | None:
    """The built output of version n, building it from its snapshot if needed (v0, or after a restart)."""
    dest = store.preview_dir(project_id, n)
    if (dest / "index.html").is_file():
        return dest
    async with _build_locks[project_id]:
        if (dest / "index.html").is_file():
            return dest
        snap = store.project_dir(project_id) / "history" / str(n)
        if not snap.is_dir():
            return None
        files = {
            p.relative_to(snap).as_posix(): p.read_text(encoding="utf-8")
            for p in snap.rglob("*") if p.is_file() and p.suffix in store.TEXT_SUFFIXES and p.relative_to(snap).parts[0] != "public"
        }
        title, fonts = site_template.page_settings(store.load_meta(project_id))
        result = await builder.build(files, store.project_dir(project_id), snap / "public", title, fonts)
        if not result.ok:
            builder.discard(result)
            log.warning("preview build of v%d failed: %s", n, result.errors[:300])
            return None
        builder.promote(result, dest)
        return dest


_build_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def describe(project_id: str) -> str:
    meta = store.load_meta(project_id)
    files = store.read_files(project_id)
    brief = meta.get("brief") or {}
    lines = [f"Website: {brief.get('business') or 'untitled'}."]
    order = section_order(files.get("src/App.jsx", ""))
    if order:
        lines.append("Sections from top to bottom: " + ", ".join(order) + ".")
    if meta.get("selection"):
        lines.append(f"The user has selected: {meta['selection'].get('label', 'an element')}.")
    lines.append(f"There are {meta['current']} versions so far.")
    return " ".join(lines)


def section_order(app_jsx: str) -> list[str]:
    """Component names in the order App.jsx renders them."""
    body = app_jsx.split("return", 1)[-1]
    seen: list[str] = []
    for name in re.findall(r"<([A-Z][A-Za-z0-9]*)", body):
        if name not in seen and name != "Fragment":
            seen.append(name)
    return seen


# ---------------------------------------------------------------------------
# Build + commit
# ---------------------------------------------------------------------------

async def _commit(project_id: str, files: dict[str, str], deleted: list[str], summary: str, instruction: str, tier: str, changed: list[str] | None = None) -> dict:
    """Builds the candidate project; on success commits it as a new version with its preview."""
    meta = store.load_meta(project_id)
    title, fonts = site_template.page_settings(meta)
    files = {**files, **site_template.fixed_files(title, fonts)}
    events.publish(project_id, {"type": "tool", "name": "build", "status": "started"})
    result = await builder.build(files, store.project_dir(project_id), store.public_dir(project_id), title, fonts)
    if not result.ok:
        builder.discard(result)
        events.publish(project_id, {"type": "tool", "name": "build", "status": "failed", "message": _short_error(result.errors), "tier": tier})
        raise EditFailed(f"The build failed:\n{result.errors}")
    events.publish(project_id, {"type": "tool", "name": "build", "status": "done", "message": f"Built in {result.ms} ms"})
    changed_files = {p: t for p, t in files.items() if changed is None or p in changed or p in site_template.FIXED_FILES}
    version = store.commit(project_id, changed_files, summary, instruction, tier, deleted)
    if changed is not None:
        version["files"] = changed
        _record_files(project_id, version)
    builder.promote(result, store.preview_dir(project_id, version["n"]))
    return version


async def _repair(project_id: str, current: dict, files: dict, error: str, summary: str, description: str) -> dict:
    """One snr patch pass over a new project that doesn't build."""
    events.publish(project_id, {"type": "job_escalated", "tier": "repair", "reason": _short_error(error)})
    budget = FOCUS_MAX_TOKENS if _tight() else PATCH_MAX_TOKENS
    raw = await llm.complete(prompts.repair_system(), prompts.repair_user(_repair_files(files, error), error[:1500]), "snr", budget)
    fixed, deleted = patches.apply(files, patches.parse_patch(raw))
    return await _commit(project_id, fixed, _deleted(current, fixed), summary, description, "architect")


def _record_files(project_id: str, version: dict) -> None:
    meta = store.load_meta(project_id)
    for v in meta["versions"]:
        if v["n"] == version["n"]:
            v["files"] = version["files"]
    store.update(project_id, versions=meta["versions"])


def _deleted(before: dict, after: dict) -> list[str]:
    return [p for p in before if p not in after]


def _llm_files(files: dict[str, str]) -> dict[str, str]:
    return {p: t for p, t in files.items() if site_template.LLM_FILE_RE.match(p)}


def _context(files: dict[str, str], instruction: str, selection: dict | None, tier: str) -> dict[str, str]:
    """Only the files a change needs: the selected component, components the instruction
    names, index.css for style changes, site.js for content, App.jsx for structure."""
    own = _llm_files(files)
    if tier == "architect":
        return own
    text = f" {instruction.lower()} "
    comps = {Path(p).stem: p for p in own if p.startswith("src/components/")}
    picked: list[str] = []

    def pick(path: str | None) -> None:
        if path and path in own and path not in picked:
            picked.append(path)

    vox_id = (selection or {}).get("vox_id")
    if vox_id:
        pick(comps.get(vox_id))
    for name, path in comps.items():
        words = (name.lower(), re.sub(r"(?<!^)([A-Z])", r" \1", name).lower()) + SECTION_WORDS.get(name, ())
        if any(re.search(rf"\b{re.escape(w)}", text) for w in words):
            pick(path)
    if any(w in text for w in STYLE_WORDS):
        pick("src/index.css")
    structural = tier == "snr" or not any(p.startswith("src/components/") for p in picked)
    if structural:
        pick("src/App.jsx")
    if structural or any(w in text for w in CONTENT_WORDS):
        pick("src/data/site.js")
    return {p: own[p] for p in picked}


def _break_for_test(before: dict, after: dict) -> dict:
    """Test hook (VOX_TEST_BREAK_JNR=1): a jnr result that can't build, to show escalation."""
    for path, text in after.items():
        if path.endswith(".jsx") and before.get(path) != text:
            return {**after, path: text + "\nexport const broken = ;\n"}
    return {**after, "src/App.jsx": after["src/App.jsx"] + "\nexport const broken = ;\n"}


def _short_error(error: str) -> str:
    """First meaningful line of a build error, for logs and speech."""
    lines = [l.strip() for l in (error or "").splitlines() if l.strip() and not l.strip().startswith(("│", "╵", "^"))]
    lines = [l for l in lines if l != "The build failed:"]
    return " ".join(lines[:2])[:240]


# ---------------------------------------------------------------------------
# Planning team
# ---------------------------------------------------------------------------

async def _plan_site(project_id: str, description: str, brief: dict | None = None) -> tuple[str, dict]:
    """Researcher (unless the brief was approved already), then the rest of the team in parallel.
    Returns (notes for the architect, brief)."""
    if brief is None:
        brief = await research.brief(description)
    events.publish(project_id, {"type": "job_stage", "tier": "team"})
    images, typography, copy = await asyncio.gather(
        research.find_images(project_id, brief.get("images", [])),
        fontknight.pick(brief, description),
        copywriter.write(brief, description) if brief else _nothing(),
    )
    brief["typography"] = typography
    store.set_brief(project_id, brief)
    _publish_research(project_id, brief if brief.get("sections") else None, images)
    events.publish(project_id, {"type": "agent_note", "agent": "fontknight", "summary": fontknight.summary(typography), "fonts": typography})
    if copy:
        events.publish(project_id, {"type": "agent_note", "agent": "copywriter", "summary": copywriter.summary(copy)})
    notes = [research.notes_for_prompt(brief if brief.get("sections") else None, images), fontknight.notes_for_prompt(typography)]
    if copy:
        notes.append(copywriter.notes_for_prompt(copy))
    return "\n\n".join(n for n in notes if n), brief


async def _nothing() -> dict:
    return {}


async def _review(project_id: str, files: dict[str, str], brief: dict) -> dict[str, str]:
    """design-critic: one patch pass over a new project. Fixes that don't apply are skipped;
    the caller falls back to the architect's files if the reviewed ones don't build."""
    if not config.DESIGN_CRITIC:
        return files
    events.publish(project_id, {"type": "job_stage", "tier": "critic"})
    try:
        budget = FOCUS_MAX_TOKENS if _tight() else PATCH_MAX_TOKENS
        raw = await llm.complete(prompts.critic_system(), prompts.critic_user(_review_files(files), _brief_reminder(brief)), "critic", budget)
        ops = patches.parse_patch(raw)
    except (llm.LLMError, EditFailed) as e:
        events.publish(project_id, {"type": "agent_note", "agent": "critic", "summary": "Design critic signed off with no changes"})
        log.info("design critic made no changes: %s", e)
        return files
    reviewed, applied = files, 0
    for op in ops:
        for block in op.blocks or [None]:
            single = patches.Op(op.kind, op.path, [block] if block else [], op.content)
            try:
                reviewed, _ = patches.apply(reviewed, [single])
                applied += 1
            except EditFailed:
                continue  # one stale fix shouldn't throw away the others
    if not applied:
        events.publish(project_id, {"type": "agent_note", "agent": "critic", "summary": "Design critic signed off with no changes"})
        return files
    reviewed = {**reviewed, **_clean_files({p: t for p, t in reviewed.items() if files.get(p) != t})}
    note = f"Design critic made {applied} fix{'es' if applied != 1 else ''}"
    events.publish(project_id, {"type": "agent_note", "agent": "critic", "summary": note})
    return reviewed


def _publish_research(project_id: str, brief: dict | None, images: list[dict]) -> None:
    parts = []
    if brief:
        sections = [s.get("id", "?") for s in brief.get("sections", []) if isinstance(s, dict)]
        parts.append(f"planned {len(sections)} sections ({', '.join(sections)})")
        if (brief.get("style") or {}).get("direction"):
            parts.append(f"art direction: {brief['style']['direction']}")
    parts.append(f"found {len(images)} photo{'s' if len(images) != 1 else ''}")
    events.publish(project_id, {
        "type": "research_done",
        "summary": "Researcher " + ", ".join(parts),
        "palette": (brief or {}).get("palette"),
        "images": [{"path": i["path"], "alt": i["alt"]} for i in images],
    })


def _brief_card(brief: dict) -> dict:
    return {
        "business": brief.get("business"),
        "style": (brief.get("style") or {}).get("direction"),
        "sections": [s.get("id") for s in brief.get("sections", []) if isinstance(s, dict)],
        "palette": brief.get("palette"),
    }


def _brief_reminder(brief: dict | None) -> str:
    """Keeps palette, type and icons consistent when an edit adds new content."""
    if not brief:
        return ""
    keep = {k: brief[k] for k in ("palette", "fonts", "tone", "style") if k in brief}
    text = "Design brief to stay consistent with:\n" + json.dumps(keep)
    if brief.get("typography"):
        text += "\n\n" + fontknight.notes_for_prompt(brief["typography"])
    return text


def _done(project_id: str, version: dict) -> dict:
    meta = store.load_meta(project_id)
    events.publish(project_id, {"type": "job_done", "version": version, "current": meta["current"], "versions": meta["versions"]})
    return {"ok": True, "message": version["summary"], "version": version["n"], "tier": version["tier"]}


def _fail(project_id: str, instruction: str, reason: str) -> dict:
    reason = _short_error(reason) or reason
    log.warning("job failed for %s: %s", project_id, reason)
    events.publish(project_id, {"type": "job_failed", "instruction": instruction, "reason": reason})
    return {"ok": False, "message": f"Sorry, that change didn't work: {reason}"}


def _clean_text(text: str) -> str:
    text = EMOJI_RE.sub("", text)
    # Spaced em dashes read as a pause, so they become " - "; en dashes in ranges become plain hyphens.
    return DASH_RE.sub(lambda m: " - " if m.group(0).strip() != m.group(0) else "-", text)


def _clean_files(files: dict[str, str]) -> dict[str, str]:
    return {p: _clean_text(t) for p, t in files.items()}
