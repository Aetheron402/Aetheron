"""
Changing a page without rewriting it.

A revision used to hand back the whole document, so altering one headline wrote
thirteen thousand characters to change forty of them. Output is the expensive
half of a model call and nearly all of it was the file being copied out
unchanged.

The danger in patching is a replacement that does not match, or matches twice.
Either would leave the page wrong in a way nobody notices until they open it,
so everything here is about refusing a patch it cannot stand behind. A partly
applied patch is the one outcome worse than an expensive one.
"""

import json

import pytest

import site_patch


PAGE = """<!DOCTYPE html><html><head><style>
h1 { color: #fff; }
p { color: #888; }
</style></head><body>
<h1>Old headline</h1>
<p>Some body text.</p>
<p>Another paragraph.</p>
</body></html>"""


# ── reading what came back ──────────────────────────────────────────────────

def test_a_clean_patch_is_read():
    edits = site_patch.parse('[{"find": "a", "replace": "b"}]')
    assert edits == [{"find": "a", "replace": "b"}]


def test_a_fenced_patch_is_read_rather_than_refused():
    """
    Falling back to a rewrite over three backticks would cost forty times as
    much as stripping them.
    """
    edits = site_patch.parse('```json\n[{"find": "a", "replace": "b"}]\n```')
    assert edits == [{"find": "a", "replace": "b"}]


def test_a_patch_with_a_sentence_in_front_is_still_read():
    edits = site_patch.parse('Here is the change:\n[{"find": "a", "replace": "b"}]')
    assert len(edits) == 1


@pytest.mark.parametrize("bad", [
    "", "not json at all", "[]", "{}", "[1, 2, 3]",
    '[{"find": "", "replace": "b"}]',
    '[{"replace": "b"}]',
    '[{"find": "a"}]',
])
def test_an_unusable_reply_is_refused_rather_than_guessed_at(bad):
    with pytest.raises(site_patch.PatchError):
        site_patch.parse(bad)


def test_the_model_can_say_a_patch_will_not_do():
    """
    Some changes genuinely need a new section written. Saying so is better than
    producing something that half works.
    """
    with pytest.raises(site_patch.PatchError) as exc:
        site_patch.parse('[{"find": "IMPOSSIBLE", "replace": "IMPOSSIBLE"}]')
    assert "rewritten" in str(exc.value)


# ── applying it ─────────────────────────────────────────────────────────────

def test_a_patch_changes_only_what_it_names():
    patched = site_patch.apply(PAGE, [
        {"find": "<h1>Old headline</h1>", "replace": "<h1>New headline</h1>"}])

    assert "New headline" in patched
    assert "Old headline" not in patched
    # Everything else byte for byte.
    assert patched.replace("New headline", "Old headline") == PAGE


def test_several_changes_apply_together():
    patched = site_patch.apply(PAGE, [
        {"find": "<h1>Old headline</h1>", "replace": "<h1>New</h1>"},
        {"find": "h1 { color: #fff; }", "replace": "h1 { color: #f00; }"},
    ])
    assert "<h1>New</h1>" in patched
    assert "#f00" in patched


def test_text_that_is_not_in_the_page_is_refused():
    """
    Silently doing nothing would hand somebody an unchanged page they paid to
    change.
    """
    with pytest.raises(site_patch.PatchError) as exc:
        site_patch.apply(PAGE, [{"find": "<h2>Missing</h2>", "replace": "x"}])
    assert "not in the page" in str(exc.value)


def test_text_that_appears_twice_is_refused():
    """
    There is no telling which one was meant, and picking one is a coin toss
    with somebody's page.
    """
    with pytest.raises(site_patch.PatchError) as exc:
        site_patch.apply(PAGE, [{"find": "<p>", "replace": "<p class='x'>"}])
    assert "appears" in str(exc.value)


def test_nothing_is_applied_when_any_change_fails():
    """
    All or none. A half applied patch is the worst outcome of the three.
    """
    with pytest.raises(site_patch.PatchError):
        site_patch.apply(PAGE, [
            {"find": "<h1>Old headline</h1>", "replace": "<h1>New</h1>"},
            {"find": "not in the page anywhere", "replace": "x"},
        ])


def test_a_patch_that_changes_nothing_is_refused():
    with pytest.raises(site_patch.PatchError) as exc:
        site_patch.apply(PAGE, [
            {"find": "<h1>Old headline</h1>", "replace": "<h1>Old headline</h1>"}])
    assert "changed nothing" in str(exc.value)


def test_a_patch_that_rewrites_most_of_the_page_is_refused():
    """
    That is a rewrite pretending to be a patch, and applying it blind gives up
    the checking that makes patching safe at all.
    """
    with pytest.raises(site_patch.PatchError) as exc:
        site_patch.apply(PAGE, [{"find": PAGE, "replace": "<html>x</html>"}])
    assert "rewrite pretending" in str(exc.value)


def test_a_patch_that_breaks_the_document_is_refused():
    with pytest.raises(site_patch.PatchError) as exc:
        site_patch.apply(PAGE, [{"find": "</html>", "replace": ""}])
    assert "whole document" in str(exc.value)


def test_an_earlier_change_can_set_up_a_later_one():
    """
    Checked against the document as it stands, so edits that build on each
    other work and edits made ambiguous by an earlier one are caught.
    """
    patched = site_patch.apply(PAGE, [
        {"find": "<p>Some body text.</p>", "replace": "<p id='a'>Some body text.</p>"},
        {"find": "<p id='a'>Some body text.</p>", "replace": "<p id='a'>Changed.</p>"},
    ])
    assert "Changed." in patched


# ── the reason any of this exists ───────────────────────────────────────────

def test_a_small_change_writes_a_small_amount():
    """
    The whole point. A headline change should write out the headline, not the
    file.
    """
    edits = [{"find": "<h1>Old headline</h1>", "replace": "<h1>New headline</h1>"}]
    patched = site_patch.apply(PAGE, edits)
    written = site_patch.summarise(PAGE, patched, edits)["characters_written"]

    assert written < len(PAGE) / 5, (
        f"wrote {written} characters to change a headline in a "
        f"{len(PAGE)} character page")


# ── the task itself, not a reading of it ────────────────────────────────────

def test_the_patch_path_runs_end_to_end(monkeypatch, tmp_path):
    """
    Exercising this rather than reading the source, because the first version
    of it crashed on a NameError in a log line and every source level check
    passed. A test that runs the code is the only kind that catches that.
    """
    import celery_worker

    page = PAGE
    stored = {}

    monkeypatch.setattr(celery_worker.llm, "complete",
                        lambda blocks, prompt: json.dumps([
                            {"find": "<h1>Old headline</h1>",
                             "replace": "<h1>Patched headline</h1>"}]))

    def refuse_rewrite(*a, **k):
        raise AssertionError("the rewrite path ran when the patch was fine")
    monkeypatch.setattr(celery_worker.llm, "complete_streamed", refuse_rewrite)

    monkeypatch.setattr(celery_worker, "load_asset_text", lambda name: page)
    monkeypatch.setattr(celery_worker, "store_asset",
                        lambda data, name: stored.update(
                            {"data": data, "name": name}) or f"/download/{name}")
    monkeypatch.setattr(celery_worker, "finalize_asset", lambda *a: None)
    monkeypatch.setattr(celery_worker, "asset_filename", lambda a, e: f"{a}.{e}")

    import site_projects, site_stream
    monkeypatch.setattr(site_projects, "get", lambda pid: {
        "project_id": pid, "symbol": "TEST", "mint": None,
        "details": {"name": "Test", "symbol": "TEST"},
        "versions": [{"asset_id": "A1", "version": 2}]})
    monkeypatch.setattr(site_projects, "latest_file", lambda pid: "prev.html")
    monkeypatch.setattr(site_projects, "finish", lambda *a: None)
    monkeypatch.setattr(site_projects, "fail", lambda *a: None)

    pushed = []
    monkeypatch.setattr(site_stream, "begin", lambda a: None)
    monkeypatch.setattr(site_stream, "push", lambda a, t: pushed.append(t))
    monkeypatch.setattr(site_stream, "finish", lambda *a, **k: None)
    monkeypatch.setattr(site_stream, "fail", lambda *a: None)

    result = celery_worker.process_site_revision(
        "A1", "SITE-TEST", "change the headline", "WALLET")

    assert result["format"] == "html"
    written = stored["data"].decode()
    assert "Patched headline" in written
    assert "Old headline" not in written
    # Everything else identical, which is the promise a patch makes.
    assert written.replace("Patched headline", "Old headline") == page
    assert pushed and "Patched headline" in pushed[0]


def test_a_bad_patch_falls_through_to_a_rewrite(monkeypatch):
    """
    The fallback is what makes patching safe to try at all, so it has to
    actually run rather than merely exist.
    """
    import celery_worker

    monkeypatch.setattr(celery_worker.llm, "complete",
                        lambda blocks, prompt: "this is not a patch")

    rewritten = "<html><body><h1>Rebuilt</h1></body></html>"
    calls = []

    def rewrite(blocks, prompt, on_text=None):
        calls.append(1)
        return rewritten
    monkeypatch.setattr(celery_worker.llm, "complete_streamed", rewrite)

    stored = {}
    monkeypatch.setattr(celery_worker, "load_asset_text", lambda name: PAGE)
    monkeypatch.setattr(celery_worker, "store_asset",
                        lambda data, name: stored.update({"data": data}) or "/x")
    monkeypatch.setattr(celery_worker, "finalize_asset", lambda *a: None)
    monkeypatch.setattr(celery_worker, "asset_filename", lambda a, e: f"{a}.{e}")

    import site_projects, site_stream
    monkeypatch.setattr(site_projects, "get", lambda pid: {
        "project_id": pid, "symbol": "T", "mint": None, "details": {},
        "versions": [{"asset_id": "A2", "version": 2}]})
    monkeypatch.setattr(site_projects, "latest_file", lambda pid: "prev.html")
    monkeypatch.setattr(site_projects, "finish", lambda *a: None)
    monkeypatch.setattr(site_projects, "fail", lambda *a: None)
    for name in ("begin", "push", "finish", "fail"):
        monkeypatch.setattr(site_stream, name, lambda *a, **k: None)

    celery_worker.process_site_revision("A2", "SITE-T", "do something", "W")

    assert calls == [1], "the rewrite did not run when the patch was unusable"
    assert stored["data"].decode() == rewritten
