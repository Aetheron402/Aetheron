"""
Put the agent sources into storage, so they do not have to ship in the repo.

Run once against each environment that serves downloads, and again whenever an
agent changes. Reads from the checked out folder, so run it somewhere that has
one, which means the private repo rather than the public one.

    railway run --service Aetheron -- python scripts/upload_agents.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_setup      # noqa: E402
import agent_store      # noqa: E402
import storage          # noqa: E402


def main():
    print(f"storage backend: {storage.backend_name()}")

    uploaded, missing = 0, []
    for agent_id, path in sorted(agent_setup.AGENT_PATHS.items()):
        if not os.path.isdir(path):
            missing.append(agent_id)
            print(f"  {agent_id:26} no folder at {path}, skipped")
            continue

        data = agent_store.pack(path)
        agent_store.put(agent_id, path)
        print(f"  {agent_id:26} {len(data):>9,} bytes")
        uploaded += 1

    print(f"\nuploaded {uploaded} of {len(agent_setup.AGENT_PATHS)}")

    # Reading each one back is the only proof that a download will work on a
    # deployment without the folder, which is the entire point of doing this.
    print("\nreading them back:")
    for agent_id in sorted(agent_setup.AGENT_PATHS):
        if agent_id in missing:
            continue
        try:
            files = agent_store.files_for(agent_id, directory=None)
            entry = agent_setup.entrypoint_from(files)
            print(f"  {agent_id:26} {len(files):>3} files, starts at {entry}")
        except Exception as exc:
            print(f"  {agent_id:26} FAILED: {exc}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
