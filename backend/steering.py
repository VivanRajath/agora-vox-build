"""How the user steers the agents: standing rules every agent follows, and how Vox behaves.

The user directs; the agents carry it out. Rules ("always dark themes", "no photos of people") go to
Vox, the researcher, the developers and the design critic, and outrank their own taste. Behaviour
switches decide whether Vox asks before building, offers ideas, and how much it says.
"""
from . import events, store

DEFAULTS = {"rules": [], "ask_first": True, "suggestions": False, "short": True}
MAX_RULES = 20


def get(meta: dict) -> dict:
    saved = meta.get("steering") or {}
    out = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
    out["rules"] = [str(r) for r in out["rules"] if str(r).strip()][:MAX_RULES]
    return out


def save(project_id: str, steering: dict) -> dict:
    clean = get({"steering": steering})
    store.update(project_id, steering=clean)
    events.publish(project_id, {"type": "steering", "steering": clean})
    return clean


def add_rule(project_id: str, rule: str) -> dict:
    s = get(store.load_meta(project_id))
    rule = rule.strip().rstrip(".")
    if rule and rule.lower() not in {r.lower() for r in s["rules"]}:
        s["rules"].append(rule)
    return save(project_id, s)


def remove_rule(project_id: str, rule: str) -> tuple[dict, bool]:
    """Removes the rule that best matches the words given ("the dark theme one")."""
    s = get(store.load_meta(project_id))
    words = {w for w in rule.lower().split() if len(w) > 2}
    best = max(s["rules"], key=lambda r: len(words & set(r.lower().split())), default=None)
    if best is None or not (words & set(best.lower().split())):
        return s, False
    s["rules"].remove(best)
    return save(project_id, s), True


def for_vox(meta: dict) -> str:
    """The user's direction, for Vox's system prompt."""
    s = get(meta)
    lines = ["How the user wants you to work (they set this; follow it over your own habits):"]
    lines.append("- Ask before building or changing anything big: " + ("yes, check first." if s["ask_first"] else
                 "NO. Do what they ask straight away, no read-backs or 'shall I?' questions (publishing still needs a yes)."))
    lines.append("- Suggestions: " + ("offer one short idea when it helps." if s["suggestions"] else
                 "don't offer ideas or alternatives unless they ask. Do exactly what they said."))
    lines.append("- Replies: " + ("one short sentence." if s["short"] else "a little more detail is fine, still under 40 words."))
    if s["rules"]:
        lines.append("- Standing rules for the website (apply them to every plan and change):")
        lines += [f"  * {r}" for r in s["rules"]]
    return "\n".join(lines)


def for_builders(meta: dict) -> str:
    """The user's standing rules, for the researcher, developers and design critic."""
    rules = get(meta)["rules"]
    if not rules:
        return ""
    return ("The site owner's standing rules. They override the brief, the design principles and your own taste; "
            "never break them:\n" + "\n".join(f"- {r}" for r in rules))
