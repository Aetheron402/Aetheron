"""
Where the agent templates are fetched from.

They live in the same storage the generated deliverables use, so a deployment
does not need them present on disk to serve a download or run a preview. This
keeps the running image small and means the templates can be updated without a
redeploy.

A local folder wins whenever it is there. A checkout with the templates present
behaves exactly as it would have without this module and never touches the
network, so nothing about working on them changes.
"""

import io
import logging
import os
import zipfile

import storage

logger = logging.getLogger(__name__)

# Kept out of every archive. A shipped virtualenv is megabytes of someone else's
# absolute paths, and a .git directory would carry the history back out again.
SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", "node_modules", ".pytest_cache"}
SKIP_SUFFIXES = (".pyc", ".pyo")


class AgentStoreError(Exception):
    """The sources for an agent could not be found in either place."""


def key_for(agent_id: str) -> str:
    return f"{storage.PERMANENT_PREFIX}{agent_id}.zip"


def pack(directory: str) -> bytes:
    """Zip a source tree, skipping everything that should never travel."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in sorted(files):
                if name.endswith(SKIP_SUFFIXES):
                    continue
                full = os.path.join(root, name)
                archive.write(full, os.path.relpath(full, directory))
    return buffer.getvalue()


def put(agent_id: str, directory: str) -> str:
    """Upload one agent's sources. Returns where it went."""
    data = pack(directory)
    url = storage.store_asset(data, key_for(agent_id))
    logger.info("Stored %s sources, %d bytes", agent_id, len(data))
    return url


def has(agent_id: str) -> bool:
    return storage.load_asset_bytes(key_for(agent_id)) is not None


def files_for(agent_id: str, directory: str | None = None) -> dict:
    """
    Every file of an agent, as a mapping of relative path to bytes.

    The folder wins when it exists, so a checkout with the sources present
    behaves exactly as it did before this file existed and never touches the
    network. Storage is the fallback, which is what a deployment without them
    uses.
    """
    if directory and os.path.isdir(directory):
        found = {}
        for root, dirs, names in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in names:
                if name.endswith(SKIP_SUFFIXES):
                    continue
                full = os.path.join(root, name)
                with open(full, "rb") as handle:
                    found[os.path.relpath(full, directory)] = handle.read()
        return found

    data = storage.load_asset_bytes(key_for(agent_id))
    if not data:
        raise AgentStoreError(
            f"No sources for {agent_id}, on disk or in storage. Run "
            f"scripts/upload_agents.py against this environment."
        )

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: archive.read(info)
                for info in archive.infolist() if not info.is_dir()}


def materialise(agent_id: str, dest: str, directory: str | None = None) -> str:
    """
    Write an agent's sources out to a directory, for anything that needs real
    files. The preview runner does, because it starts a process against them.
    """
    for relative, data in files_for(agent_id, directory).items():
        # Nothing in these archives should escape the destination, and a crafted
        # entry claiming to is the one thing worth refusing outright.
        target = os.path.normpath(os.path.join(dest, relative))
        if not os.path.abspath(target).startswith(os.path.abspath(dest)):
            raise AgentStoreError(f"Refusing a path that escapes: {relative}")

        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
    return dest
