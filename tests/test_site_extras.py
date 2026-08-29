"""
Typography, the favicon, the deploy bundle and rewriting one section.

Each of these exists because of something a buyer hits. System fonts are the
loudest signal a page was generated. A blank tab icon is in front of them
constantly. A bare HTML file leaves people who have never deployed anything
holding something they cannot use. And between changing a line of text and
rebuilding the page there was nothing at all.
"""

import io
import re
import zipfile

import pytest

import site_bundle
import site_fonts


# ── typography ──────────────────────────────────────────────────────────────

def test_every_direction_gets_a_face_that_is_not_a_system_stack():
    import site_data

    for entry in site_data.DIRECTIONS:
        display, body = site_fonts.pair_for(entry["name"])
        assert display in site_fonts.FILES, entry["name"]
        assert body in site_fonts.FILES, entry["name"]


def test_directions_are_not_all_set_in_the_same_thing():
    """
    Two tokens looking the same is the failure the whole direction mechanism
    exists to prevent, and typography is most of what makes them differ.
    """
    import site_data

    pairs = {site_fonts.pair_for(d["name"]) for d in site_data.DIRECTIONS}
    assert len(pairs) >= 5


def test_a_face_is_embedded_rather_than_fetched():
    css = site_fonts.css_for("brutalist")

    assert "@font-face" in css
    assert "data:font/woff2;base64," in css
    assert "http" not in css, "a page must not fetch a font"


def test_the_face_goes_into_the_head_where_the_stylesheet_can_use_it():
    page = "<html><head><title>x</title></head><body>y</body></html>"
    out = site_fonts.inject(page, "brutalist")

    assert out.index("@font-face") > out.index("<head>")
    assert out.index("@font-face") < out.index("</head>")


def test_a_page_with_no_head_is_left_alone_rather_than_guessed_at():
    """
    A page that does not parse is not one to start editing.
    """
    broken = "<div>no head here</div>"
    assert site_fonts.inject(broken, "brutalist") == broken


def test_a_missing_face_does_not_fail_a_paid_build(monkeypatch):
    monkeypatch.setattr(site_fonts, "FONT_DIR", "no/such/place")
    site_fonts._cache.clear()

    page = "<html><head></head><body>x</body></html>"
    assert site_fonts.inject(page, "brutalist") == page
    site_fonts._cache.clear()


def test_every_family_falls_back_to_something_deliberate():
    """
    A face that fails to decode, or a browser too old for woff2, has to land
    somewhere chosen rather than on Times.
    """
    for family in site_fonts.FILES:
        stack = site_fonts.stack(family)
        assert stack.startswith(f"'{family}'")
        assert "," in stack, f"{family} has no fallback"


def test_the_model_is_told_names_and_never_asked_for_font_data():
    """
    A model asked for twenty kilobytes of base64 produces twenty kilobytes of
    something that looks like base64.
    """
    brief = site_fonts.brief_for("brutalist")

    assert "Archivo Black" in brief
    assert "base64" not in brief
    assert "do not add a font import" in brief


def test_the_fonts_are_small_enough_to_carry():
    """
    Two faces ride in every page. Latin subsets, which is why they are twenty
    kilobytes rather than two hundred.
    """
    import os

    for name in site_fonts.FILES.values():
        size = os.path.getsize(os.path.join(site_fonts.FONT_DIR, name))
        assert size < 40_000, f"{name} is {size} bytes"


def test_the_licences_are_shipped_with_them():
    import os
    assert os.path.exists(os.path.join(site_fonts.FONT_DIR, "LICENCE.md"))


# ── the favicon ─────────────────────────────────────────────────────────────

def test_the_brief_asks_for_a_favicon_that_reads_at_sixteen_pixels():
    brief = " ".join(open("celery_worker.py").read().split())

    assert "A favicon, so the tab does not show a blank page icon" in brief
    assert "It is sixteen pixels and it needs to read at that size" in brief


# ── the deploy bundle ───────────────────────────────────────────────────────

def test_the_bundle_names_the_page_index_html():
    """
    The most common way this goes wrong is a file called something else sitting
    in a bucket doing nothing.
    """
    data = site_bundle.build("<html><body>hi</body></html>")

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {"index.html", "README.md"}
        assert archive.read("index.html").decode() == "<html><body>hi</body></html>"


def test_the_instructions_name_hosts_somebody_can_actually_use():
    readme = site_bundle.readme_for(launched=False)

    assert "Netlify" in readme and "Cloudflare" in readme and "GitHub Pages" in readme
    assert "nothing to install" in readme


def test_the_instructions_match_whether_the_token_has_launched():
    """
    Telling somebody to fill in a line that is already filled in reads as
    instructions belonging to a different file.
    """
    pending = site_bundle.readme_for(launched=False)
    done = site_bundle.readme_for(launched=True)

    assert 'const CONTRACT_ADDRESS = ""' in pending
    assert "already on the page" in done
    assert "Put your address between the quotes" not in done


def test_whether_a_page_has_launched_is_read_from_the_page():
    empty = '<html><script>const CONTRACT_ADDRESS = "";</script></html>'
    filled = '<html><script>const CONTRACT_ADDRESS = "D3qncu";</script></html>'
    neither = "<html>no constant at all</html>"

    assert site_bundle.is_launched(empty) is False
    assert site_bundle.is_launched(filled) is True
    # Built with the address already in it, so there is nothing to tell them.
    assert site_bundle.is_launched(neither) is True


def test_an_empty_page_is_not_packaged():
    with pytest.raises(ValueError):
        site_bundle.build("")


# ── rewriting one section ───────────────────────────────────────────────────

def test_a_section_rewrite_costs_between_an_edit_and_a_build():
    import pricing

    edit = pricing.revision_quote(
        [{"selector": None, "description": "change the whole page"}])["price"]
    section = pricing.section_price()
    build = pricing.list_price("site-builder")

    assert edit < section < build


def test_only_the_sections_that_exist_can_be_rewritten():
    source = open("Aetheron.py").read()
    route = source.split("def site_builder_section")[1].split("\ndef ")[0]

    assert '"hero", "contract", "about", "how-to-buy", "market", "links", "footer"' in route
    assert "There is no" in route


def test_the_rewrite_finds_the_section_bounds_rather_than_guessing():
    """
    A regeneration that replaced the wrong span would quietly delete something
    somebody paid for.
    """
    task = open("celery_worker.py").read().split("def process_site_section")[1]

    assert "depth" in task, "nested tags have to be counted"
    assert "has no" in task and "section to rewrite" in task


def test_a_rewrite_that_loses_the_section_id_is_refused():
    task = open("celery_worker.py").read().split("def process_site_section")[1]
    assert "lost its id" in task


def test_the_rewritten_section_cannot_bring_its_own_styling():
    """
    It has to sit inside a page the model cannot see, so it uses the classes
    that are already there.
    """
    task = open("celery_worker.py").read().split("def process_site_section")[1]
    assert "do not add a <style> block" in task
    assert "use the class names and the" in task
