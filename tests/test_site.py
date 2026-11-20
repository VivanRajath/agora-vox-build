import asyncio
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend import builder, config, events, llm, site_template, store
from backend.editor import engine, patches
from backend.main import app

needs_tools = pytest.mark.skipif(not builder.tools_ready(), reason="run scripts/setup_build_tools.sh")

HERO = """import { site } from "../data/site.js";

export default function Hero() {
  return (
    <section id="hero" data-section="hero" data-vox-id="Hero" className="py-20">
      <h1 className="font-display text-5xl">{site.hero.title}</h1>
      <img src={site.hero.image} alt={site.hero.alt} className="aspect-[4/3] object-cover" />
    </section>
  );
}"""

FOOTER = """import { Clock } from "lucide-react";
import { site } from "../data/site.js";

export default function Footer() {
  return (
    <footer id="footer" data-section="footer" data-vox-id="Footer" className="py-10 bg-surface">
      <Clock className="w-5 h-5" aria-hidden="true" /> {site.hours}
    </footer>
  );
}"""

ARCHITECT_OUTPUT = f"""SUMMARY: Built a warm website for Sweet Crumbs.
FILE: src/index.css
```css
@import "tailwindcss";

@theme {{
  --color-primary: #6b3e26;
  --color-surface: #fff7ec;
  --font-display: "Fraunces", serif;
}}
```
FILE: src/data/site.js
```js
export const site = {{
  name: "Sweet Crumbs",
  hero: {{ title: "Cakes baked in Indiranagar", image: "images/hero-abc123.jpg", alt: "A cake" }},
  hours: "Monday to Sunday, 8 AM to 9 PM",
}};
```
FILE: src/App.jsx
```jsx
import Hero from "./components/Hero.jsx";
import Footer from "./components/Footer.jsx";

export default function App() {{
  return (
    <>
      <main>
        <Hero />
      </main>
      <Footer />
    </>
  );
}}
```
FILE: src/components/Hero.jsx
```jsx
{HERO}
```
FILE: src/components/Footer.jsx
```jsx
{FOOTER}
```
"""

JNR_PATCH = """SUMMARY: Made the hero heading bigger.
FILE: src/components/Hero.jsx
<<<<<<< SEARCH
      <h1 className="font-display text-5xl">{site.hero.title}</h1>
=======
      <h1 className="font-display text-7xl">{site.hero.title}</h1>
>>>>>>> REPLACE
"""


# --- patch protocol ----------------------------------------------------------

def test_parse_patch_multi_file():
    raw = """SUMMARY: Added a gallery.
FILE: src/App.jsx
<<<<<<< SEARCH
a
=======
b
>>>>>>> REPLACE
NEW FILE: src/components/Gallery.jsx
```jsx
export default function Gallery() { return null; }
```
DELETE FILE: src/components/Old.jsx
"""
    ops = patches.parse_patch(raw)
    assert [(o.kind, o.path) for o in ops] == [
        ("patch", "src/App.jsx"), ("create", "src/components/Gallery.jsx"), ("delete", "src/components/Old.jsx"),
    ]
    assert ops[0].blocks == [("a", "b")]
    assert patches.summary(raw) == "Added a gallery."
    files, deleted = patches.apply({"src/App.jsx": "a\n", "src/components/Old.jsx": "x"}, ops)
    assert files["src/App.jsx"] == "b\n" and "src/components/Gallery.jsx" in files and deleted == ["src/components/Old.jsx"]


def test_parse_patch_without_header_uses_the_only_file():
    ops = patches.parse_patch("<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE", default_path="src/components/Hero.jsx")
    assert ops[0].path == "src/components/Hero.jsx"


def test_patch_rejects_fixed_and_protected_files():
    with pytest.raises(patches.EditFailed):
        patches.apply({"package.json": "{}"}, [patches.Op("create", "package.json", content="{}")])
    with pytest.raises(patches.EditFailed):
        patches.apply({"src/App.jsx": "x"}, [patches.Op("delete", "src/App.jsx")])
    with pytest.raises(patches.EditFailed):
        patches.parse_patch("SUMMARY: nothing")


def test_parse_full_and_replace_all():
    new = patches.parse_full(ARCHITECT_OUTPUT)
    assert set(new) == {"src/index.css", "src/data/site.js", "src/App.jsx", "src/components/Hero.jsx", "src/components/Footer.jsx"}
    old = {"package.json": "{}", "src/App.jsx": "", "src/components/Starter.jsx": ""}
    files, deleted = patches.replace_all(old, new)
    assert files["package.json"] == "{}" and deleted == ["src/components/Starter.jsx"]


# --- import whitelist ----------------------------------------------------------

def test_import_whitelist():
    ok = {"src/App.jsx": 'import { useState } from "react";\nimport { motion } from "framer-motion";\nimport { MapPin } from "lucide-react";\nimport Hero from "./components/Hero.jsx";'}
    assert builder.check_imports(ok) == []
    bad = {
        "src/App.jsx": 'import axios from "axios";\nconst x = require("fs");\nimport("left-pad");',
        "src/components/A.jsx": 'import secrets from "../../../backend/config.js";\nexport { a } from "@scope/pkg";',
    }
    problems = " ".join(builder.check_imports(bad))
    for name in ("axios", "fs", "left-pad", "outside src", "@scope/pkg"):
        assert name in problems


# --- builds and the edit pipeline ----------------------------------------------

@needs_tools
def test_build_smoke(tmp_path):
    files = {**site_template.fixed_files("Test", ""), **site_template.STARTER_FILES}
    result = asyncio.run(builder.build(files, tmp_path, None, "Test", ""))
    try:
        assert result.ok, result.errors
        assert {"index.html", "bundle.js", "styles.css"} <= {p.name for p in result.out_dir.iterdir()}
        assert result.ms < 5000
    finally:
        builder.discard(result)


@pytest.fixture
def fake_team(monkeypatch):
    async def brief(description):
        return {"business": "Sweet Crumbs", "sections": [{"id": "hero"}, {"id": "footer"}], "palette": {"background": "#fff7ec", "primary": "#6b3e26", "accent": "#c2185b"}, "images": []}

    async def pick(brief, description):
        return {"heading": "Fraunces", "body": "Instrument Sans", "link": site_template.DEFAULT_FONTS, "css": {"heading": "Fraunces", "body": "Instrument Sans"}, "icons": []}

    async def nothing(*args, **kwargs):
        return {}

    async def no_images(project_id, needs):
        return []

    from backend.editor import copywriter, fontknight, research
    monkeypatch.setattr(research, "brief", brief)
    monkeypatch.setattr(research, "find_images", no_images)
    monkeypatch.setattr(fontknight, "pick", pick)
    monkeypatch.setattr(copywriter, "write", nothing)
    monkeypatch.setattr(config, "DESIGN_CRITIC", False)


def _fake_llm(monkeypatch, replies: dict, seen: list):
    async def complete(system, user, tier, max_tokens=0, json_mode=False):
        seen.append((tier, user))
        reply = replies[tier]
        return reply(user) if callable(reply) else reply
    monkeypatch.setattr(llm, "complete", complete)


def _collect(project_id):
    got = []
    original = events.publish

    def publish(pid, event):
        if pid == project_id:
            got.append(event)
        original(pid, event)
    return got, publish


@needs_tools
def test_generate_then_jnr_edit_touches_only_hero(monkeypatch, fake_team):
    meta = store.create()
    pid = meta["id"]
    seen = []
    _fake_llm(monkeypatch, {"architect": ARCHITECT_OUTPUT, "jnr": JNR_PATCH}, seen)

    result = asyncio.run(engine.generate(pid, "a bakery called Sweet Crumbs"))
    assert result["ok"], result
    assert store.preview_dir(pid, 1).joinpath("bundle.js").is_file()
    assert not (store.site_dir(pid) / "src/components/Hero.jsx").read_text().count("site.title")  # starter replaced

    store.set_selection(pid, None)
    result = asyncio.run(engine.edit(pid, "make the hero heading bigger"))
    assert result["ok"] and result["tier"] == "jnr", result
    meta = store.load_meta(pid)
    assert meta["versions"][-1]["files"] == ["src/components/Hero.jsx"]
    assert "text-7xl" in (store.site_dir(pid) / "src/components/Hero.jsx").read_text()
    # The jnr developer was shown the hero component, not the whole project.
    jnr_prompt = [u for t, u in seen if t == "jnr"][0]
    assert "FILE: src/components/Hero.jsx" in jnr_prompt and "FILE: src/components/Footer.jsx" not in jnr_prompt

    # Point and speak: the selected component's file is what the developer gets.
    store.set_selection(pid, {"path": "footer", "html": "<footer>", "label": "footer", "vox_id": "Footer"})
    context = engine._context(store.read_files(pid), "make this bigger", store.load_meta(pid)["selection"], "jnr")
    assert list(context) == ["src/components/Footer.jsx"]

    assert "Hero, Footer" in engine.describe(pid)
    undone = asyncio.run(engine.undo(pid))
    assert undone["ok"] and store.load_meta(pid)["current"] == 1


@needs_tools
def test_broken_jnr_patch_escalates_to_snr(monkeypatch, fake_team):
    meta = store.create()
    pid = meta["id"]
    seen = []
    _fake_llm(monkeypatch, {"architect": ARCHITECT_OUTPUT, "jnr": JNR_PATCH, "snr": JNR_PATCH}, seen)
    assert asyncio.run(engine.generate(pid, "a bakery"))["ok"]

    got, publish = _collect(pid)
    monkeypatch.setattr(events, "publish", publish)
    monkeypatch.setattr(config, "TEST_BREAK_JNR", True)
    result = asyncio.run(engine.edit(pid, "make the hero heading bigger"))
    assert result["ok"] and result["tier"] == "snr", result
    assert any(e["type"] == "tool" and e.get("name") == "build" and e["status"] == "failed" for e in got)
    assert any(e["type"] == "job_escalated" and e["tier"] == "snr" for e in got)
    snr_prompt = [u for t, u in seen if t == "snr"][0]
    assert "previous attempt" in snr_prompt


@needs_tools
def test_zip_is_a_runnable_vite_project(monkeypatch, fake_team):
    meta = store.create()
    _fake_llm(monkeypatch, {"architect": ARCHITECT_OUTPUT}, [])
    assert asyncio.run(engine.generate(meta["id"], "a bakery"))["ok"]
    r = TestClient(app).get(f"/api/projects/{meta['id']}/download")
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {"package.json", "vite.config.js", "index.html", "src/main.jsx", "src/App.jsx", "README.md"} <= names
    preview = TestClient(app).get(f"/preview/{meta['id']}/")
    assert preview.status_code == 200 and "preview-inject.js" in preview.text and "bundle.js" in preview.text
