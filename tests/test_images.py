from backend.editor import images

SITE = '''export const site = {
  hero: { heading: "Wild Trails", image: "images/hero-aaaaaa.jpg", alt: "Jungle canopy at sunrise" },
  sections: {
    "big-cats": {
      items: [
        { title: "Lions", image: "images/lion-111111.jpg", alt: "A male lion resting in the grass" },
        { title: "Tigers", image: "images/tiger-222222.jpg", alt: "A tiger walking by a stream" },
      ],
    },
    "birds": { image: "images/parrot-333333.jpg", alt: "A colourful parrot" },
  },
};
'''
FILES = {"src/data/site.js": SITE, "src/components/Gallery.jsx": '<img src="images/parrot-333333.jpg" alt="Parrot" />'}


def test_finds_the_photo_from_words():
    choice = images.pick(FILES, "the lion image", None)
    assert choice.image == "images/lion-111111.jpg" and len(choice.slots) == 1
    assert images.pick(FILES, "change the tiger picture", None).image == "images/tiger-222222.jpg"


def test_asks_when_unsure_or_not_found():
    assert "Which one" in images.pick(FILES, "the elephant", None).message
    assert images.pick(FILES, "this", None).message.startswith("Click the photo")


def test_this_uses_the_clicked_photo_and_section():
    sel = {"path": "section#birds > img", "html": '<img src="images/parrot-333333.jpg" alt="A colourful parrot">', "vox_id": "Birds"}
    choice = images.pick(FILES, "this", sel)
    assert choice.image == "images/parrot-333333.jpg"
    assert all(s.file == "src/data/site.js" for s in choice.slots)  # the Gallery copy is another section


def test_replace_changes_only_that_photo_and_its_alt():
    choice = images.pick(FILES, "the lion", None)
    out = images.replace(FILES, choice.slots, "images/lion-999999.jpg", "A lioness with cubs")
    assert '"images/lion-999999.jpg", alt: "A lioness with cubs"' in out["src/data/site.js"]
    assert "images/tiger-222222.jpg" in out["src/data/site.js"] and "A tiger walking by a stream" in out["src/data/site.js"]
    assert out["src/components/Gallery.jsx"] == FILES["src/components/Gallery.jsx"]
    assert out["src/data/site.js"].replace("lion-999999", "lion-111111").replace("A lioness with cubs", "A male lion resting in the grass") == SITE


def test_hotlinked_unsplash_photos_are_found_and_swapped():
    url = "https://images.unsplash.com/photo-1546182990-dffeafbe841d?ixid=abc&w=1600&q=80&fm=jpg&fit=max"
    files = {"src/data/site.js": f'export const site = {{ hero: {{ image: "{url}", alt: "A lion at dusk" }} }};'}
    choice = images.pick(files, "the lion photo", None)
    assert choice.image == url
    from backend.editor.research import photo_key
    assert photo_key(url) == "photo-1546182990-dffeafbe841d"
    out = images.replace(files, choice.slots, "images/lion-123456.jpg", "")
    assert '"images/lion-123456.jpg", alt: "A lion at dusk"' in out["src/data/site.js"]


def _photo(image, section, **where):
    return images.Photo(image, [images.Slot("src/data/site.js", 0, 1, image, "", "", section)], [{"src": image, "section": section, **where}] if where else [])


def test_position_words_are_resolved_from_the_layout():
    page = [
        _photo("images/hero.jpg", "hero", background=True, h="centre", v="middle"),
        _photo("images/gwen.jpg", "team", h="left", v="bottom", onScreen=True),
        _photo("images/hector.jpg", "team", h="right", v="bottom", onScreen=True),
        _photo("images/hoodie.jpg", "merch", h="left", v="top"),
        _photo("images/keys.jpg", "merch", h="right", v="top"),
    ]
    assert images.spatial(page, "the image on the left is not looking good") == 2
    assert images.spatial(page, "change the one on the right") == 3
    assert images.spatial(page, "change the background image") == 1
    assert images.spatial(page, "the left image in the merch section") == 4
    assert images.spatial(page, "change the keychain picture") == 0  # no position words: the model decides


def test_placeholder_photos_are_found_and_described_by_their_card():
    files = {"src/data/site.js": 'export const site = { team: [ { name: "Gwen Stacy", img: "https://picsum.photos/seed/gwen/400/400" } ] };'}
    (slot,) = images.find_slots(files)
    assert slot.image == "https://picsum.photos/seed/gwen/400/400"
    assert images.describe(slot) == "the picture for 'Gwen Stacy'"
