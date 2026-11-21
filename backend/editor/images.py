"""Finds a photo on the site from what the user said ("the lion image", "this picture") and swaps
just that photo: the path string (and its alt text) change in place, nothing is regenerated.

Photos live in the code as relative paths like "images/lion-1a2b3c.jpg", mostly in
src/data/site.js next to their alt text, sometimes straight in a component's src.
"""
import re
from dataclasses import dataclass, field

IMG_RE = re.compile(
    r"""(["'`])(images/[\w.\-]+\.(?:jpe?g|png|webp|gif)"""
    r"""|https://(?:images\.unsplash\.com|picsum\.photos|images\.pexels\.com|upload\.wikimedia\.org)/[^"'`\s]+)\1"""
)
TITLE_RE = re.compile(r"""\b(?:title|name|heading|label|caption)\s*:\s*(["'])(.*?)\1""")
ALT_RE = re.compile(r"""\balt\s*[:=]\s*(["'])(.*?)\1""")
KEY_RE = re.compile(r"""["']?([a-z][a-z0-9-]*)["']?\s*:\s*\{""")
ID_RE = re.compile(r"""\bid=["']([a-z0-9-]+)["']""")
WORD_RE = re.compile(r"[a-z0-9]+")
STOP = {
    "the", "a", "an", "this", "that", "these", "those", "one", "image", "images", "photo", "photos", "picture",
    "pictures", "pic", "pics", "of", "in", "on", "at", "with", "and", "to", "change", "replace", "swap", "section",
    "please", "it", "its", "my", "our", "new", "different", "another", "jpg", "png", "here", "there",
}


@dataclass
class Slot:
    file: str
    start: int  # span of the path inside the quotes
    end: int
    image: str
    context: str
    alt: str = ""
    section: str = ""


@dataclass
class Choice:
    slots: list[Slot] = field(default_factory=list)
    message: str = ""  # why nothing was chosen, to say to the user

    @property
    def image(self) -> str:
        return self.slots[0].image if self.slots else ""

    @property
    def alt(self) -> str:
        return next((s.alt for s in self.slots if s.alt), "")


def _enclosing(text: str, pos: int, reach: int = 700) -> tuple[int, int]:
    """The object literal (or nearby text) around pos, so the alt and title that go with a photo are found."""
    depth, start = 0, max(0, pos - reach)
    for i in range(pos, start, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                start = i
                break
            depth -= 1
    depth, end = 0, min(len(text), pos + reach)
    for i in range(pos, end):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                end = i + 1
                break
            depth -= 1
    return start, end


def _close(text: str, open_at: int) -> int:
    """Index of the brace that closes the one at open_at (len(text) if it never closes)."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def _data_section(text: str, pos: int) -> str:
    """The section a photo in site.js belongs to: the key right under `sections`, else the top-level key."""
    ancestors = []
    for k in KEY_RE.finditer(text, 0, pos):
        brace = text.index("{", k.start())
        if _close(text, brace) > pos:
            ancestors.append(k.group(1))
    ancestors = [a for a in ancestors if a != "site"]
    if "sections" in ancestors and ancestors.index("sections") + 1 < len(ancestors):
        return ancestors[ancestors.index("sections") + 1]
    return ancestors[0] if ancestors else ""


def find_slots(files: dict[str, str]) -> list[Slot]:
    """Every place a photo path appears in the source, with the text around it."""
    slots = []
    for path, text in files.items():
        if not path.startswith("src/") or not path.endswith((".js", ".jsx")):
            continue
        component_ids = ID_RE.findall(text) if path.startswith("src/components/") else []
        for m in IMG_RE.finditer(text):
            a, b = _enclosing(text, m.start())
            context = text[a:b]
            alt = ALT_RE.search(context)
            if path.endswith("site.js"):
                section = _data_section(text, m.start())
            else:
                section = component_ids[0] if component_ids else path.rsplit("/", 1)[-1].removesuffix(".jsx")
            slots.append(Slot(path, m.start(2), m.end(2), m.group(2), context, alt.group(2) if alt else "", section))
    return slots


def _words(text: str) -> set[str]:
    return {w for w in WORD_RE.findall(text.lower().replace("-", " ")) if w not in STOP and len(w) > 1}


def _kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def describe(slot: Slot) -> str:
    """What the photo shows, in words: its alt text, else the card it sits in, else its file or seed name."""
    if slot.alt:
        return slot.alt
    title = TITLE_RE.search(slot.context)
    if title:
        return f"the picture for '{title.group(2)}'"
    seed = re.search(r"picsum\.photos/seed/([\w-]+)", slot.image)
    if seed:
        return f"a placeholder picture ({seed.group(1)})"
    if slot.image.startswith("http"):
        return "a photo"
    return slot.image.split("/")[-1].rsplit("-", 1)[0].replace("-", " ")


def pick(files: dict[str, str], target: str, selection: dict | None) -> Choice:
    slots = find_slots(files)
    if not slots:
        return Choice(message="This site has no photos yet. Ask me to add one.")

    pointed = bool(re.search(r"\b(this|that|here|selected|clicked)\b", target.lower())) or not _words(target)
    if pointed:
        if not selection:
            return Choice(message="Click the photo on the page first, then ask me again.")
        src = re.search(r"""src=["']([^"']+)""", selection.get("html", ""))
        image = src.group(1).replace("&amp;", "&") if src else ""
        if not image.startswith("http"):
            image = image.split("?")[0]
        if image.startswith("./"):
            image = image[2:]
        chosen = [s for s in slots if s.image == image]
        if not chosen:
            return Choice(message="The element you clicked isn't one of the site's photos. Click the photo itself and ask again.")
        # The same photo can appear in several places; keep the ones in the clicked section.
        section = re.search(r"section#([\w-]+)", selection.get("path", ""))
        wanted = {section.group(1)} if section else set()
        if selection.get("vox_id"):
            wanted.add(_kebab(selection["vox_id"]))
        narrowed = [s for s in chosen if s.section in wanted or any(w and w in s.file.lower() for w in wanted)]
        return Choice(slots=narrowed or chosen)

    words = _words(target)
    scored = []
    for s in slots:
        haystack = _words(f"{s.alt} {s.context} {s.section} {'' if s.image.startswith('http') else s.image}")
        scored.append((len(words & haystack), s))
    best = max(score for score, _ in scored)
    if best == 0:
        options = list(dict.fromkeys(describe(s) for s in slots))[:4]
        return Choice(message=f"I couldn't tell which photo you mean. The photos show: {'; '.join(options)}. Which one?")
    top = [s for score, s in scored if score == best]
    images = list(dict.fromkeys(s.image for s in top))
    if len(images) > 1:
        options = [describe(next(s for s in top if s.image == i)) for i in images[:3]]
        return Choice(message=f"There are {len(images)} photos that could be: {'; or '.join(options)}. Which one?")
    return Choice(slots=top)


def replace(files: dict[str, str], slots: list[Slot], new_image: str, new_alt: str = "") -> dict[str, str]:
    """Swaps the path (and the alt next to it) in exactly these places, last first so offsets hold."""
    out = dict(files)
    for path in {s.file for s in slots}:
        text = out[path]
        for s in sorted((s for s in slots if s.file == path), key=lambda s: s.start, reverse=True):
            text = text[: s.start] + new_image + text[s.end :]
            if new_alt and s.alt:
                a, b = _enclosing(text, s.start)
                region = text[a:b]
                m = ALT_RE.search(region)
                if m and m.group(2) == s.alt:
                    i = a + m.start(2)
                    text = text[:i] + new_alt.replace(m.group(1), "") + text[i + len(s.alt):]
        out[path] = text
    return out


# ---------------------------------------------------------------------------
# Understanding what the user means: the page's real layout plus a small model
# ---------------------------------------------------------------------------

_layout: dict[str, list[dict]] = {}  # project id -> photos as the preview reported them


def remember_layout(project_id: str, items: list[dict]) -> None:
    _layout[project_id] = [i for i in items if isinstance(i, dict) and i.get("src")][:60]
    from .. import store
    store.update(project_id, layout=_layout[project_id])  # survives restarts


@dataclass
class Photo:
    """One distinct photo on the page: every code slot that shows it, plus where it appears."""
    image: str
    slots: list[Slot]
    where: list[dict]  # layout entries for this photo, top of page first


def photos(files: dict[str, str], project_id: str) -> list[Photo]:
    slots = find_slots(files)
    by_image: dict[str, list[Slot]] = {}
    for s in slots:
        by_image.setdefault(s.image, []).append(s)
    seen = _layout.get(project_id)
    if seen is None:
        from .. import store
        seen = store.load_meta(project_id).get("layout") or []
    out = []
    for image, group in by_image.items():
        where = sorted((w for w in seen if w["src"] == image), key=lambda w: w.get("y", 0))
        out.append(Photo(image, group, where))
    # Page order: what's rendered, top first; then anything the preview didn't report.
    out.sort(key=lambda p: (not p.where, p.where[0].get("y", 0) if p.where else 0))
    return out


def _line(n: int, p: Photo) -> str:
    alt = next((s.alt for s in p.slots if s.alt), "") or describe(p.slots[0])
    if p.where:
        w = p.where[0]
        if w.get("background"):
            place = "full-width BACKGROUND photo behind the text"
        else:
            place = f"photo on the {w.get('h', 'centre')} ({w.get('width', '?')}% of the section width), {w.get('v', 'middle')} of the section"
        section = w.get("section") or p.slots[0].section
        extra = f", shown {len(p.where)} times" if len(p.where) > 1 else ""
        visible = " [ON SCREEN NOW]" if any(x.get("onScreen") for x in p.where) else ""
        return f"{n}. In the '{section}' section: {place}{extra}.{visible} Shows: {alt}"
    return f"{n}. In the '{p.slots[0].section}' section (not visible on screen right now). Shows: {alt}"


RESOLVE_SYSTEM = """The user is talking, by voice, about a photo on their website and wants it changed. You get the photos on the
page (numbered, in page order, with where each one sits) and the user's exact words, which come from speech recognition
and may contain small mistakes. Work out which photo they mean and what the new photo should show.

Return ONLY a JSON object:
{"photo": <the photo's number, or 0 if you really can't tell>,
 "question": "<if photo is 0: one short question naming the likely options>",
 "query": "<3-7 word stock photo search for the NEW photo. Keep the old photo's subject and mood unless the user asked for
            something different. Only when they ask for a look, turn it into searchable words (asked for brighter: add
            'bright'; asked for moodier: add 'dark moody')>",
 "tone": "bright" only if they asked for brighter/lighter/sunnier, "dark" only if they asked for darker/moodier, else "any",
 "alt": "<alt text for the new photo, under 12 words>"}

Background means a photo behind the text. Left/right/top/bottom/first/second/last refer to the positions given.
The user is looking at the photos marked ON SCREEN NOW: when they say "the image on the left" or "this one" without naming
a section, choose among those. If the user clicked a photo and says this/that/here, it's the clicked one.
"photo" is only about WHICH existing photo they mean. Never answer 0 because they didn't say what the new photo should
show: then keep the old subject and pick a better photo of it. Only answer 0 when several photos fit equally well."""


SPATIAL = {
    "left": ("h", "left"), "right": ("h", "right"), "middle": ("h", "centre"), "centre": ("h", "centre"), "center": ("h", "centre"),
    "top": ("v", "top"), "upper": ("v", "top"), "bottom": ("v", "bottom"), "lower": ("v", "bottom"),
}


def _in_section(p: Photo, words: set[str]) -> bool:
    return bool(_words(p.slots[0].section) & words) or any(_words(w.get("section", "")) & words for w in p.where)


def spatial(page: list[Photo], request: str) -> int:
    """The photo a position word points at ("the image on the left", "the background"), decided from the
    real layout instead of by a model: 1-based index, or 0 when the words don't single one out."""
    words = set(re.findall(r"[a-z]+", request.lower()))
    if not page:
        return 0
    if words & {"background", "backdrop", "behind"}:
        backs = [i for i, p in enumerate(page) if any(w.get("background") for w in p.where)]
        if len(backs) == 1:
            return backs[0] + 1
    wanted = [SPATIAL[w] for w in words if w in SPATIAL]
    if not wanted:
        return 0
    # "the left image in the team section" looks in that section; otherwise at what's on screen.
    named = [(i, p) for i, p in enumerate(page) if _in_section(p, words)]
    if named:
        pool, spots = named, lambda p: p.where
    else:
        pool = [(i, p) for i, p in enumerate(page) if any(w.get("onScreen") for w in p.where)]
        spots = lambda p: [w for w in p.where if w.get("onScreen")]
    hits = [i for i, p in pool if any(all(w.get(axis) == value for axis, value in wanted) for w in spots(p))]
    return hits[0] + 1 if len(hits) == 1 else 0


async def resolve(files: dict[str, str], project_id: str, request: str, selection: dict | None, brief: dict | None) -> tuple[list[Slot], dict, str]:
    """(slots to change, {query, tone, alt}, question). Empty slots plus a question when it's unclear."""
    from .. import llm
    from .research import parse_json

    page = photos(files, project_id)
    if not page:
        return [], {}, "This site has no photos yet. Ask me to add one."
    lines = [_line(i + 1, p) for i, p in enumerate(page)]
    clicked = ""
    if selection:
        src = re.search(r"""src=["']([^"']+)""", selection.get("html", ""))
        hit = next((i + 1 for i, p in enumerate(page) if src and p.image == src.group(1).replace("&amp;", "&")), 0)
        clicked = f"\nThe user has clicked photo {hit}." if hit else "\nThe user has clicked something that isn't a photo."
    about = f"The site is for: {brief.get('business')}.\n" if brief and brief.get("business") else ""
    known = spatial(page, request)
    decided = f"\nFrom the page layout, the user means photo {known}: answer photo {known}." if known else ""
    user = f"{about}Photos on the page:\n" + "\n".join(lines) + clicked + decided + f"\n\nThe user said: \"{request}\""
    raw = await llm.complete(RESOLVE_SYSTEM, user, "vox", 600, json_mode=True)
    data = parse_json(raw)
    try:
        n = known or int(data.get("photo") or 0)
    except (TypeError, ValueError):
        n = 0
    if not 1 <= n <= len(page):
        question = str(data.get("question") or "").strip()
        options = "; ".join(_line(i + 1, p).split(". ", 1)[1][:80] for i, p in enumerate(page[:3]))
        return [], {}, question or f"Which photo do you mean? For example: {options}."
    wish = {k: str(data.get(k) or "").strip() for k in ("query", "tone", "alt")}
    if wish["tone"] not in ("bright", "dark"):
        wish["tone"] = "any"
    return page[n - 1].slots, wish, ""
