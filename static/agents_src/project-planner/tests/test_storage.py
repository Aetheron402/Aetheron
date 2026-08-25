from pathlib import Path

import pytest

from storage.json_store import JsonStore


@pytest.fixture()
def store(tmp_path: Path):
    path = tmp_path / "db.json"
    return JsonStore(path)


def test_add_and_get_all(store):
    store.add("tasks", {"id": "t1", "title": "Task 1"})
    store.add("tasks", {"id": "t2", "title": "Task 2"})

    tasks = store.get_all("tasks")

    ids = {t["id"] for t in tasks}
    assert ids == {"t1", "t2"}


def test_get_by_id(store):
    store.add("notes", {"id": "n1", "title": "Hello"})

    item = store.get_by_id("notes", "n1")

    assert item is not None
    assert item["id"] == "n1"
    assert item["title"] == "Hello"


def test_update(store):
    store.add("milestones", {"id": "m1", "title": "Phase 1", "archived": False})

    store.update("milestones", "m1", {"archived": True})

    updated = store.get_by_id("milestones", "m1")

    assert updated["archived"] is True


def test_delete(store):
    store.add("tasks", {"id": "t1", "title": "Task"})
    store.delete("tasks", "t1")

    assert store.get_by_id("tasks", "t1") is None
    assert store.get_all("tasks") == []


def test_update_missing_raises(store):
    with pytest.raises(KeyError):
        store.update("tasks", "missing", {"title": "x"})


def test_delete_missing_raises(store):
    with pytest.raises(KeyError):
        store.delete("tasks", "missing")