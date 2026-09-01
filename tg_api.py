"""
The bot's view of the Aetheron API.

The same trick as the transport seam, for the same reason: behind an interface,
the purchase flow can be built and tested against a fake that answers 402 and
then a task id, rather than against a live service that would need real money
moved to exercise the interesting paths.

It also keeps the shape of the API in one place. Every component takes its input
under a different key, `text` for some, `contract_address` for another, and a
flow that knew each of those would have to be edited every time one changed.

The bot never prices anything itself. It asks, and it repeats what it is told.
A price computed in two places is a price that eventually disagrees with itself,
and the half that is wrong is the half that takes somebody's money.
"""

import time
import logging
import os

import requests

# The bot runs inside the web service, so by default it calls it on the loop
# back address rather than going out to the internet and back. The port comes
# from the same variable the server is started with, since a fixed one would be
# right only on a laptop.
BASE_URL = os.getenv(
    "AETHERON_API_BASE",
    f"http://127.0.0.1:{os.getenv('PORT', '8000')}",
).rstrip("/")
TIMEOUT = int(os.getenv("TG_API_TIMEOUT", "60"))


class ApiError(Exception):
    """The API could not be reached, or answered something unusable."""


# What each component needs, and what to ask a person for.
#
# `field` is the key its input goes under. `ask` is what the bot says when
# somebody runs the command with nothing after it. Components whose input does
# not fit in one message, the site builder for instance, are deliberately absent
# rather than half supported.
# Prices change rarely and a command should not wait on a request to show
# them, so they are kept for a few minutes.
PRICES_TTL_SECONDS = int(os.getenv("TG_PRICES_TTL", "300"))

logger = logging.getLogger(__name__)

COMPONENTS = {
    "prompt-optimizer": {
        "field": "text",
        "label": "Prompt Optimizer",
        "does": "Rewrites a prompt so it does what you actually meant.",
        "ask": "Send the prompt you want tightened up.",
        "extra": {"format": "pdf"},
    },
    "code-explainer": {
        "field": "text",
        "label": "Code Explainer",
        "does": "Explains what a piece of code does, line by line.",
        "ask": "Paste the code you want explained.",
        "extra": {"format": "pdf"},
    },
    "prompt-tester": {
        "field": "text",
        "label": "Prompt Tester",
        "does": "Runs a prompt past several personas and shows where it breaks.",
        "ask": "Send the prompt you want tested against several personas.",
        "extra": {"format": "pdf"},
    },
    "contract-intel": {
        "field": "contract_address",
        "label": "Contract Intelligence",
        "does": "Holders, authorities and risks on a Solana token.",
        "ask": "Send the contract address you want looked at.",
        "extra": {"network": "solana", "format": "pdf"},
    },
}

ALIASES = {
    "prompt": "prompt-optimizer",
    "optimizer": "prompt-optimizer",
    "code": "code-explainer",
    "explain": "code-explainer",
    "tester": "prompt-tester",
    "test": "prompt-tester",
    "contract": "contract-intel",
    "intel": "contract-intel",
}


def resolve_component(name: str) -> str | None:
    """The canonical slug for something somebody typed, or None."""
    key = (name or "").strip().lower().replace("_", "-")
    if key in COMPONENTS:
        return key
    return ALIASES.get(key)


class ApiClient:
    """What the bot needs from the API, and nothing else."""

    def prices(self) -> dict:
        """What each component costs. Slug to price in USDC."""
        raise NotImplementedError

    def agents(self) -> list:
        """The agents that exist, as dicts with id and title."""
        raise NotImplementedError

    def call_component(self, slug, payload, wallet, method="USDC", tx_sig=None):
        raise NotImplementedError

    def job_status(self, task_id):
        raise NotImplementedError

    def download(self, url):
        raise NotImplementedError

    def my_assets(self, wallet):
        raise NotImplementedError

    def example(self, slug, wallet):
        raise NotImplementedError

    def start_preview(self, agent_id, wallet):
        raise NotImplementedError

    def preview_result(self, task_id):
        raise NotImplementedError


class HttpApiClient(ApiClient):
    """
    The real one, talking to the running service.

    Kept thin deliberately. Anything clever here is logic that the tests reach
    only through a network call, which is the same as logic that is not tested.
    """

    def __init__(self, base_url: str | None = None, timeout: int = TIMEOUT):
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.timeout = timeout
        self._prices: dict = {}
        self._prices_at = 0.0
        self._agents: list = []
        self._agents_at = 0.0

    def _headers(self, wallet, method, tx_sig):
        headers = {"Content-Type": "application/json"}
        if wallet:
            headers["X-USER-WALLET"] = wallet
        if method:
            headers["X-PAYMENT-METHOD"] = method
        if tx_sig:
            headers["X-TX-SIG"] = tx_sig
        return headers

    def call_component(self, slug, payload, wallet, method="USDC", tx_sig=None):
        try:
            response = requests.post(
                f"{self.base_url}/api/{slug}", json=payload,
                headers=self._headers(wallet, method, tx_sig), timeout=self.timeout)
        except requests.RequestException as exc:
            raise ApiError(f"Could not reach the API: {exc}")

        try:
            body = response.json()
        except ValueError:
            raise ApiError(f"The API answered {response.status_code} with no JSON")

        body["_status"] = response.status_code
        return body

    def prices(self) -> dict:
        """
        What each component costs, kept for a few minutes.

        Asked for rather than written down, so the number shown is the number
        charged. A failure returns nothing rather than raising: a price list
        that cannot be fetched should cost somebody a price, not a command.
        """
        now = time.time()
        if self._prices and now - self._prices_at < PRICES_TTL_SECONDS:
            return self._prices

        try:
            response = requests.get(f"{self.base_url}/api/prices",
                                    timeout=self.timeout)
            response.raise_for_status()
            self._prices = response.json() or {}
            self._prices_at = now
        except (requests.RequestException, ValueError):
            logger.warning("Could not fetch prices", exc_info=True)
            return self._prices or {}
        return self._prices

    def agents(self) -> list:
        """
        The agents, kept for a few minutes.

        This used to read an attribute that only the test double had, so the
        deployed bot listed nothing at all while every test passed. Anything
        the bot needs has to come through a call both clients implement.
        """
        now = time.time()
        if self._agents and now - self._agents_at < PRICES_TTL_SECONDS:
            return self._agents

        try:
            response = requests.get(f"{self.base_url}/api/agents",
                                    timeout=self.timeout)
            response.raise_for_status()
            self._agents = (response.json() or {}).get("agents") or []
            self._agents_at = now
        except (requests.RequestException, ValueError):
            logger.warning("Could not fetch agents", exc_info=True)
            return self._agents or []
        return self._agents

    def job_status(self, task_id):
        try:
            response = requests.get(f"{self.base_url}/api/job-status/{task_id}",
                                    timeout=self.timeout)
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ApiError(f"Could not read job status: {exc}")

    def download(self, url):
        """
        Fetch a finished file.

        The API hands back either a path on this service or an absolute URL
        when object storage is in use, so both are accepted.
        """
        full = url if url.startswith("http") else f"{self.base_url}{url}"
        try:
            response = requests.get(full, timeout=self.timeout)
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            raise ApiError(f"Could not download the file: {exc}")

    def my_assets(self, wallet):
        try:
            response = requests.get(f"{self.base_url}/api/my-assets/{wallet}",
                                    timeout=self.timeout)
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ApiError(f"Could not read your assets: {exc}")

    def example(self, slug, wallet):
        """
        One example report against the wallet's allowance.

        The status is returned rather than raised on, because 429 means the
        allowance is spent and 401 means no wallet, and both are things to tell
        somebody rather than errors to swallow.
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/examples/{slug}",
                headers={"X-USER-WALLET": wallet or ""}, timeout=self.timeout)
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ApiError(f"Could not read that example: {exc}")
        body["_status"] = response.status_code
        return body

    def start_preview(self, agent_id, wallet):
        try:
            response = requests.post(
                f"{self.base_url}/api/agents/{agent_id}/preview",
                headers={"X-USER-WALLET": wallet or ""}, timeout=self.timeout)
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ApiError(f"Could not start that preview: {exc}")
        body["_status"] = response.status_code
        return body

    def preview_result(self, task_id):
        try:
            response = requests.get(
                f"{self.base_url}/api/agents/preview/{task_id}",
                timeout=self.timeout)
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ApiError(f"Could not read that preview: {exc}")


class FakeApiClient(ApiClient):
    """
    An API that behaves like the real one without one running.

    Answers 402 until a signature arrives, then a task id, then a queued job
    that finishes after however many polls a test asks for. That sequence is
    the entire purchase, so it is the sequence worth being able to control.
    """

    def prices(self) -> dict:
        return {slug: 0.50 for slug in COMPONENTS}

    def agents(self) -> list:
        return [{"id": name, "title": name.replace("-", " ").title()}
                for name in sorted(getattr(self, "previewable", ()) or ())]

    def __init__(self, price=0.25, currency="USDC",
                 pay_wallet="FZtoQTD7MLHvJzxxSPcUaQkXB5yP6qKYBZ8tUV18hHo1"):
        self.price = price
        self.currency = currency
        self.pay_wallet = pay_wallet
        self.calls = []
        self.polls_before_done = 1
        self._poll_counts = {}
        self.file_bytes = b"%PDF-1.4 pretend report"
        self.fail_next_call = None
        self.reject_signature = False
        self.job_error = None
        self.assets = {"entries": []}
        self._next_task = 1

        self.allowance = 3
        self.example_slugs = set(COMPONENTS) | {"risk-engine"}
        self.previewable = {"wallet-watcher", "market-tracker", "alpha-scanner"}
        self.example_text = "EXAMPLE REPORT\n\n" + "line of a report\n" * 20
        self.preview_output = "[12:00:01] watching\n[12:00:04] transfer seen\n"
        self.preview_failure = None
        self.example_calls = []
        self.preview_calls = []
        self._seen_examples = {}
        self._seen_previews = {}

    def call_component(self, slug, payload, wallet, method="USDC", tx_sig=None):
        self.calls.append({"slug": slug, "payload": payload, "wallet": wallet,
                           "method": method, "tx_sig": tx_sig})

        if self.fail_next_call:
            reason, self.fail_next_call = self.fail_next_call, None
            raise ApiError(reason)

        if not tx_sig:
            return {
                "_status": 402, "status": 402,
                "message": "Payment required",
                "component": slug,
                "required": self.price,
                "list_price": self.price,
                "currency": self.currency,
                "wallet": self.pay_wallet,
                "accepted_methods": ["USDC", "AETH"],
            }

        if self.reject_signature:
            return {"_status": 402, "status": 402,
                    "message": "Payment required",
                    "required": self.price, "currency": self.currency,
                    "wallet": self.pay_wallet}

        task_id = f"task-{self._next_task}"
        self._next_task += 1
        return {"_status": 200, "task_id": task_id,
                "asset_id": f"X402-{task_id.upper()}", "status": "queued"}

    def job_status(self, task_id):
        if self.job_error:
            return {"state": "FAILURE", "error": self.job_error}

        seen = self._poll_counts.get(task_id, 0) + 1
        self._poll_counts[task_id] = seen
        if seen < self.polls_before_done:
            return {"state": "PENDING"}

        return {"state": "SUCCESS", "result": {
            "download_url": f"/download/{task_id}.pdf",
            "filename": f"aetheron_{task_id}.pdf", "format": "pdf"}}

    def download(self, url):
        return self.file_bytes

    def my_assets(self, wallet):
        return self.assets

    # ── the free tier ───────────────────────────────────────────────────────
    #
    # Metered server side, three per wallet, shared across the whole shop. The
    # fake counts the same way so the bot can be shown behaving correctly when
    # somebody runs out, which is the case worth getting right: it is the last
    # thing they see before deciding whether to pay.

    def example(self, slug, wallet):
        self.example_calls.append((slug, wallet))

        if not wallet:
            return {"_status": 401,
                    "detail": "Connect a wallet to read an example."}
        if slug not in self.example_slugs:
            return {"_status": 404, "detail": "No example for that component."}

        seen = self._seen_examples.setdefault(wallet, set())
        already = slug in seen
        if not already and len(seen) >= self.allowance:
            return {"_status": 429,
                    "detail": f"You have opened all {self.allowance} of your "
                              "examples. You can still reopen the ones you chose."}
        seen.add(slug)

        return {"_status": 200, "slug": slug, "report": self.example_text,
                "as_page": False, "already_seen": already,
                "remaining": max(0, self.allowance - len(seen))}

    def start_preview(self, agent_id, wallet):
        self.preview_calls.append((agent_id, wallet))

        if not wallet:
            return {"_status": 401,
                    "detail": "Connect a wallet to watch an agent run."}
        if agent_id not in self.previewable:
            return {"_status": 404, "detail": "Invalid agent ID"}

        seen = self._seen_previews.setdefault(wallet, set())
        already = agent_id in seen
        if not already and len(seen) >= self.allowance:
            return {"_status": 429,
                    "detail": f"You have watched all {self.allowance} of your "
                              "agent runs. You can still rewatch the ones you chose."}
        seen.add(agent_id)

        task_id = f"preview-{self._next_task}"
        self._next_task += 1
        return {"_status": 200, "task_id": task_id, "agent_id": agent_id,
                "seconds": 25, "already_seen": already,
                "remaining": max(0, self.allowance - len(seen))}

    def preview_result(self, task_id):
        if self.preview_failure:
            return {"ready": True, "ok": False, "reason": self.preview_failure}

        seen = self._poll_counts.get(task_id, 0) + 1
        self._poll_counts[task_id] = seen
        if seen < self.polls_before_done:
            return {"ready": False, "state": "PENDING"}

        return {"ready": True, "ok": True, "agent_id": "wallet-watcher",
                "output": self.preview_output, "seconds": 25,
                "stopped_on_deadline": True, "exit_code": None}
