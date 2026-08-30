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

import os

import requests

BASE_URL = os.getenv("AETHERON_API_BASE", "http://localhost:8000").rstrip("/")
TIMEOUT = int(os.getenv("TG_API_TIMEOUT", "60"))


class ApiError(Exception):
    """The API could not be reached, or answered something unusable."""


# What each component needs, and what to ask a person for.
#
# `field` is the key its input goes under. `ask` is what the bot says when
# somebody runs the command with nothing after it. Components whose input does
# not fit in one message, the site builder for instance, are deliberately absent
# rather than half supported.
COMPONENTS = {
    "prompt-optimizer": {
        "field": "text",
        "label": "Prompt Optimizer",
        "ask": "Send the prompt you want tightened up.",
        "extra": {"format": "pdf"},
    },
    "code-explainer": {
        "field": "text",
        "label": "Code Explainer",
        "ask": "Paste the code you want explained.",
        "extra": {"format": "pdf"},
    },
    "prompt-tester": {
        "field": "text",
        "label": "Prompt Tester",
        "ask": "Send the prompt you want tested against several personas.",
        "extra": {"format": "pdf"},
    },
    "contract-intel": {
        "field": "contract_address",
        "label": "Contract Intelligence",
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

    def call_component(self, slug, payload, wallet, method="USDC", tx_sig=None):
        raise NotImplementedError

    def job_status(self, task_id):
        raise NotImplementedError

    def download(self, url):
        raise NotImplementedError

    def my_assets(self, wallet):
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


class FakeApiClient(ApiClient):
    """
    An API that behaves like the real one without one running.

    Answers 402 until a signature arrives, then a task id, then a queued job
    that finishes after however many polls a test asks for. That sequence is
    the entire purchase, so it is the sequence worth being able to control.
    """

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
