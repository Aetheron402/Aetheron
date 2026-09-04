# License Notice:
# This template is licensed for personal use only.
# Redistribution or resale is strictly prohibited.
# See LICENSE.txt for details.

"""
Where signals come from.

Deliberately dumb, and deliberately not a watcher. This reads what something
else produced: a file your other agents append to, an HTTP endpoint, or a
folder of JSON dropped by a script. The desk does not care which.

That is the whole point of it being a publisher rather than a tenth monitor.
Anything that can write a line of JSON can feed it, including every agent
already in the store.
"""

import json
import os
import time


class Inbox:
    def __init__(self, config, logger):
        self.logger = logger
        self.mode = config.get("mode", "file")
        self.path = config.get("file", "signals.jsonl")
        self.url = config.get("url", "")
        self.folder = config.get("folder", "signals")
        self.offset = 0

        # An existing file is not a backlog to publish. Starting at the end
        # means turning the desk on does not dump a week of history into a
        # channel.
        #
        # Set start_at to "beginning" when you do want the backlog, which is
        # the case when you are feeding it a file you prepared rather than one
        # being appended to live.
        self.start_at = config.get("start_at", "end")
        if (self.mode == "file" and self.start_at == "end"
                and os.path.exists(self.path)):
            self.offset = os.path.getsize(self.path)

    def describe(self) -> str:
        if self.mode == "http":
            return f"an endpoint at {self.url}"
        if self.mode == "folder":
            return f"the {self.folder} folder"
        return f"the file {self.path}"

    def read(self) -> list:
        try:
            if self.mode == "http":
                return self._from_http()
            if self.mode == "folder":
                return self._from_folder()
            return self._from_file()
        except Exception as error:
            self.logger.warning("Could not read the inbox: %s", error)
            return []

    # ── one line of JSON per signal, appended by anything ───────────────────

    def _from_file(self) -> list:
        if not os.path.exists(self.path):
            return []

        size = os.path.getsize(self.path)
        if size < self.offset:
            # Truncated or rotated, so start again rather than reading rubbish
            # from the middle of a line.
            self.offset = 0

        with open(self.path) as handle:
            handle.seek(self.offset)
            lines = handle.readlines()
            self.offset = handle.tell()

        return [s for s in (self._parse(line) for line in lines) if s]

    def _from_folder(self) -> list:
        """A file per signal, deleted once read, which suits shell scripts."""
        if not os.path.isdir(self.folder):
            return []

        found = []
        for name in sorted(os.listdir(self.folder)):
            if not name.endswith(".json"):
                continue
            full = os.path.join(self.folder, name)
            try:
                with open(full) as handle:
                    found.append(json.load(handle))
                os.remove(full)
            except Exception as error:
                self.logger.warning("Skipping %s: %s", name, error)
        return found

    def _from_http(self) -> list:
        import requests

        response = requests.get(self.url, timeout=10)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, list) else body.get("signals", [])

    def _parse(self, line):
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except ValueError:
            # A half written line from a producer mid append. It will be
            # complete next time round, and complaining about it every second
            # helps nobody.
            return None
