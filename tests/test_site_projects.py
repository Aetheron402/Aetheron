"""
Projects, versions and revisions.

The thing worth guarding here is ownership. A revision costs less than a build,
so if the owner check were missing the cheapest way to edit any page on the
platform would be to edit somebody else's.
"""

import os
import tempfile
import uuid

import pytest


@pytest.fixture
def store(monkeypatch):
    """A fresh sqlite ledger per test, so nothing leaks between them."""
    path = os.path.join(tempfile.mkdtemp(), "ledger.db")
    monkeypatch.setattr("ledger_utils.SQLITE_PATH", path)
    monkeypatch.setattr("ledger_utils.USE_POSTGRES", False)

    import site_projects
    site_projects._initialised = False
    site_projects.init()
    return site_projects


def _token(name="Aurelia", symbol="AUR"):
    return {"name": name, "symbol": symbol, "description": "A test token.",
            "image": None, "mint": None,
            "socials": {"twitter": None, "telegram": None, "website": None}}


def test_a_build_opens_a_project_with_one_version(store):
    asset = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    project_id = store.start("WALLET_A", _token(), "brutalist", "dark", asset)

    project = store.get(project_id)
    assert project["wallet"] == "WALLET_A"
    assert project["symbol"] == "AUR"
    assert len(project["versions"]) == 1
    assert project["versions"][0]["version"] == 1
    assert project["versions"][0]["status"] == "pending"


def test_a_version_is_only_downloadable_once_it_has_a_file(store):
    asset = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    project_id = store.start("WALLET_A", _token(), "brutalist", None, asset)

    assert store.latest_file(project_id) is None
    store.finish(asset, "page.html")
    assert store.latest_file(project_id) == "page.html"


def test_revisions_number_themselves_in_order(store):
    first = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    project_id = store.start("WALLET_A", _token(), "brutalist", None, first)
    store.finish(first, "v1.html")

    second = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    assert store.add_version(project_id, second, "make it gold") == 2
    store.finish(second, "v2.html")

    third = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    assert store.add_version(project_id, third, "bigger headline") == 3

    # The newest finished file is what a revision builds on, not the newest row.
    assert store.latest_file(project_id) == "v2.html"


def test_an_earlier_version_stays_downloadable(store):
    first = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    project_id = store.start("WALLET_A", _token(), "brutalist", None, first)
    store.finish(first, "v1.html")

    second = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    store.add_version(project_id, second, "change it")
    store.finish(second, "v2.html")

    files = {v["filename"] for v in store.get(project_id)["versions"]}
    assert files == {"v1.html", "v2.html"}, "a revision must not replace the old file"


def test_only_the_owner_may_revise(store):
    asset = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    project_id = store.start("WALLET_A", _token(), "brutalist", None, asset)
    store.finish(asset, "v1.html")

    assert store.owned_by(project_id, "WALLET_A") is True
    assert store.owned_by(project_id, "WALLET_B") is False
    assert store.owned_by(project_id, None) is False
    assert store.owned_by("SITE-NOPE", "WALLET_A") is False


def test_a_wallet_sees_only_its_own_sites(store):
    a = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    b = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    store.start("WALLET_A", _token("Aurelia", "AUR"), "brutalist", None, a)
    store.start("WALLET_B", _token("Other", "OTH"), "neon night", None, b)

    mine = store.for_wallet("WALLET_A")
    assert [p["symbol"] for p in mine] == ["AUR"]

    # An absent wallet must return nothing rather than everything.
    assert store.for_wallet(None) == []
    assert store.for_wallet("") == []


def test_updating_details_does_not_wipe_the_ones_left_blank(store):
    asset = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    project_id = store.start("WALLET_A", _token(), "brutalist", None, asset)

    store.update_details(project_id, {"telegram": "https://t.me/aurelia"})

    details = store.get(project_id)["details"]
    assert details["telegram"] == "https://t.me/aurelia"
    assert details["description"] == "A test token.", "a blank field cleared a set one"


def test_a_failed_build_is_marked_rather_than_left_pending(store):
    asset = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    project_id = store.start("WALLET_A", _token(), "brutalist", None, asset)
    store.fail(asset)

    assert store.get(project_id)["versions"][0]["status"] == "failed"
    assert store.latest_file(project_id) is None


def test_another_wallet_cannot_buy_a_revision_of_your_site(store, monkeypatch):
    """
    The check that matters most. A revision is cheaper than a build, so without
    an owner check the cheapest way to edit any page on the platform would be
    to edit one belonging to somebody else. Refused before payment is quoted,
    so nobody pays to be told no.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    asset = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    project_id = store.start("OWNER_WALLET", _token(), "brutalist", None, asset)
    store.finish(asset, "v1.html")

    client = TestClient(Aetheron.app)
    response = client.post(
        "/api/site-builder/revise",
        headers={"X-USER-WALLET": "INTRUDER_WALLET"},
        json={"project_id": project_id, "notes": "change the headline"},
    )

    assert response.status_code == 403
    assert "different wallet" in response.json()["error"]


def test_a_site_with_no_finished_version_cannot_be_revised(store):
    """
    There would be nothing to apply the change to, and the buyer would have
    paid for it.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    asset = "X402-SITE-" + uuid.uuid4().hex[:10].upper()
    project_id = store.start("OWNER_WALLET", _token(), "brutalist", None, asset)

    client = TestClient(Aetheron.app)
    response = client.post(
        "/api/site-builder/revise",
        headers={"X-USER-WALLET": "OWNER_WALLET"},
        json={"project_id": project_id, "notes": "change the headline"},
    )

    assert response.status_code == 409


def test_revising_an_unknown_site_is_a_404_not_a_payment_prompt(store):
    from fastapi.testclient import TestClient
    import Aetheron

    client = TestClient(Aetheron.app)
    response = client.post(
        "/api/site-builder/revise",
        headers={"X-USER-WALLET": "SOME_WALLET"},
        json={"project_id": "SITE-DOESNOTEXIST", "notes": "change something"},
    )

    assert response.status_code == 404
