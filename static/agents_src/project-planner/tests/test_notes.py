from pathlib import Path

import pytest

from storage.json_store import JsonStore


@pytest.fixture()
def store(tmp_path: Path):
    return JsonStore(tmp_path / "db.json")


def test_notes_collection_exists(store):
    assert isinstance(store.get_all("notes"), list)


def test_create_note(store):
    note = {
        "id": "n1",
        "title": "Idea",
        "body": "Build a modular agent.",
    }

    store.add("notes", note)

    fetched = store.get_by_id("notes", "n1")

    assert fetched is not None
    assert fetched["title"] == "Idea"
    assert fetched["body"] == "Build a modular agent."


def test_update_note(store):
    store.add("notes", {"id": "n1", "title": "Idea", "body": "v1"})

    store.update("notes", "n1", {"body": "v2"})

    fetched = store.get_by_id("notes", "n1")

    assert fetched is not None
    assert fetched["body"] == "v2"


def test_delete_note(store):
    store.add("notes", {"id": "n1", "title": "x", "body": "y"})

    store.delete("notes", "n1")

    assert store.get_by_id("notes", "n1") is None