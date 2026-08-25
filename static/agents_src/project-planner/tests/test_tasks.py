from pathlib import Path

import pytest

from storage.json_store import JsonStore


@pytest.fixture()
def store(tmp_path: Path):
    return JsonStore(tmp_path / "db.json")


def test_tasks_collection_exists(store):
    assert isinstance(store.get_all("tasks"), list)


def test_create_task_minimal(store):
    task = {
        "id": "t1",
        "title": "Ship agent",
        "status": "open",
    }

    store.add("tasks", task)

    fetched = store.get_by_id("tasks", "t1")

    assert fetched is not None
    assert fetched["title"] == "Ship agent"
    assert fetched["status"] == "open"


def test_mark_task_done(store):
    store.add("tasks", {"id": "t1", "title": "Do it", "status": "open"})

    store.update("tasks", "t1", {"status": "done"})

    fetched = store.get_by_id("tasks", "t1")

    assert fetched is not None
    assert fetched["status"] == "done"


def test_task_update_does_not_drop_fields(store):
    store.add(
        "tasks",
        {
            "id": "t1",
            "title": "A",
            "status": "open",
            "priority": 2,
        },
    )

    store.update("tasks", "t1", {"status": "done"})

    fetched = store.get_by_id("tasks", "t1")

    assert fetched["title"] == "A"
    assert fetched["priority"] == 2
    assert fetched["status"] == "done"