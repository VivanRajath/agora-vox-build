"""Finds a photo on the site from what the user said ("the lion image", "this picture") and swaps
just that photo: the path string (and its alt text) change in place, nothing is regenerated.

Photos live in the code as relative paths like "images/lion-1a2b3c.jpg", mostly in
src/data/site.js next to their alt text, sometimes straight in a component's src.
"""
import re
from dataclasses import dataclass, field

IMG_RE = re.compile(r"""(["'`])(images/[\w.\-]+\.(?:jpe?g|png|webp|gif)|https://images\.unsplash\.com/[^"'`\s]+)\1""")
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
    if slot.alt or slot.image.startswith("http"):
        return slot.alt or "a photo"
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
