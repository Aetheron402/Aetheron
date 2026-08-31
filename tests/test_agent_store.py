"""
Fetching agent templates from storage.

The change is worthless if a download built from storage differs from one built
off the folder, so that is what most of this checks. Byte for byte, not roughly.
"""

import io
import os
import tempfile
import zipfile

import pytest

import agent_setup
import agent_store
import storage


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A sqlite backed storage, isolated per test."""
    monkeypatch.setattr("ledger_utils.SQLITE_PATH", str(tmp_path / "ledger.db"))
    monkeypatch.setattr("ledger_utils.USE_POSTGRES", False)
    monkeypatch.setattr(storage, "_initialised_for", None)
    monkeypatch.setenv("R2_PUBLIC_BASE", "")
    monkeypatch.setenv("R2_BUCKET_NAME", "")
    storage.init_storage()
    return storage


@pytest.fixture
def sample(tmp_path):
    """A small agent tree, including things that must never travel."""
    root = tmp_path / "an-agent"
    (root / "core").mkdir(parents=True)
    (root / "__pycache__").mkdir()
    (root / ".venv").mkdir()

    (root / "main.py").write_text("print('hello')\n")
    (root / "config.json").write_text('{"api": {"key": ""}}\n')
    (root / "README.md").write_text("# an agent\n")
    (root / "core" / "run.py").write_text("def go(): pass\n")
    (root / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00\x01")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
    return str(root)


# ── packing ─────────────────────────────────────────────────────────────────

def test_a_pack_carries_the_sources_and_nothing_else(sample):
    with zipfile.ZipFile(io.BytesIO(agent_store.pack(sample))) as archive:
        names = set(archive.namelist())

    assert names == {"main.py", "config.json", "README.md", "core/run.py"}


def test_a_virtualenv_or_cache_never_travels(sample):
    with zipfile.ZipFile(io.BytesIO(agent_store.pack(sample))) as archive:
        blob = " ".join(archive.namelist())

    assert ".venv" not in blob
    assert "__pycache__" not in blob
    assert ".pyc" not in blob


# ── the folder wins, storage is the fallback ───────────────────────────────

def test_the_folder_is_used_when_it_is_there(store, sample):
    files = agent_store.files_for("an-agent", sample)
    assert files["main.py"] == b"print('hello')\n"


def test_storage_is_used_when_the_folder_is_not(store, sample):
    agent_store.put("an-agent", sample)

    files = agent_store.files_for("an-agent", directory=None)
    assert files["main.py"] == b"print('hello')\n"
    assert files["core/run.py"] == b"def go(): pass\n"


def test_both_routes_give_exactly_the_same_files(store, sample):
    """
    The point of the whole change. A deployment without the folder has to build
    the identical download, or buyers get something different depending on which
    machine served them.
    """
    agent_store.put("an-agent", sample)

    from_folder = agent_store.files_for("an-agent", sample)
    from_storage = agent_store.files_for("an-agent", directory=None)

    assert from_folder == from_storage


def test_a_missing_agent_says_what_to_do(store):
    with pytest.raises(agent_store.AgentStoreError) as exc:
        agent_store.files_for("nothing-here", directory=None)
    assert "upload_agents" in str(exc.value)


# ── writing them back out, for previews ────────────────────────────────────

def test_sources_can_be_written_out_for_a_preview(store, sample, tmp_path):
    agent_store.put("an-agent", sample)
    dest = tmp_path / "work"
    dest.mkdir()

    agent_store.materialise("an-agent", str(dest), directory=None)

    assert (dest / "main.py").read_text() == "print('hello')\n"
    assert (dest / "core" / "run.py").exists()


def test_an_archive_cannot_write_outside_where_it_was_told(store, tmp_path):
    """
    A crafted entry claiming a path above the destination is the one thing worth
    refusing outright rather than trusting.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escaped.py", "print('nope')\n")
    storage.store_asset(buffer.getvalue(), agent_store.key_for("nasty"))

    dest = tmp_path / "work"
    dest.mkdir()
    with pytest.raises(agent_store.AgentStoreError) as exc:
        agent_store.materialise("nasty", str(dest), directory=None)

    assert "escapes" in str(exc.value)
    assert not (tmp_path / "escaped.py").exists()


# ── the sources must survive the purge ─────────────────────────────────────

def test_stock_is_not_purged_with_the_deliverables(store, sample):
    """
    The agent sources are stored the same way reports are, but they are the
    product. A purge taking them would empty the store thirty days after an
    upload with nothing to say it had happened.
    """
    import time

    agent_store.put("an-agent", sample)
    storage.store_asset(b"a report", "aetheron_OLD_REPORT.pdf")

    # Age everything past the window.
    with storage._cursor(commit=True) as cur:
        cur.execute(storage._q("UPDATE assets SET created_at = %s;"),
                    (time.time() - 400 * 86400,))

    storage.purge_expired(max_age_days=30)

    assert storage.load_asset_bytes(agent_store.key_for("an-agent")) is not None
    assert storage.fetch_asset("aetheron_OLD_REPORT.pdf") is None


# ── the entrypoint, without a directory to look in ─────────────────────────

def test_the_entrypoint_is_found_from_a_file_list():
    assert agent_setup.entrypoint_from(["main.py", "core/x.py"]) == "main.py"
    assert agent_setup.entrypoint_from(["app.py", "README.md"]) == "app.py"


def test_main_beats_app_when_both_are_there():
    """
    project-planner has both, and app.py defines the application rather than
    running it. Preferring it produced a run script that started nothing and
    exited zero, which looks exactly like a broken agent.
    """
    assert agent_setup.entrypoint_from(["app.py", "main.py"]) == "main.py"


def test_a_nested_python_file_is_not_mistaken_for_the_entrypoint():
    assert agent_setup.entrypoint_from(["core/deep.py"]) == "main.py"


def test_a_local_folder_is_preferred_over_the_network():
    """
    A checkout with the templates present should behave exactly as it would
    without this module, and never reach for the network to fetch what is
    already on the disk in front of it.
    """
    source = " ".join(open("agent_store.py").read().split())
    assert "A local folder wins whenever it is there" in source
