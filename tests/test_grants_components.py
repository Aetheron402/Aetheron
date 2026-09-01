"""
Free component runs, for giveaway winners.

The two things that matter: a prize can only be used once, and a prize is not
lost when the thing it paid for never happened.
"""

import os
import tempfile

import pytest


@pytest.fixture
def store(monkeypatch):
    """A fresh sqlite ledger per test."""
    path = os.path.join(tempfile.mkdtemp(), "ledger.db")
    monkeypatch.setattr("ledger_utils.SQLITE_PATH", path)
    monkeypatch.setattr("ledger_utils.USE_POSTGRES", False)

    import grants
    grants.init_grants()
    return grants


WALLET = "8mtAJDQmNBkVM7n63Xz6eCDhfc2UjzEoqiopcAubPDQa"


def test_a_granted_wallet_has_a_free_run(store):
    assert store.grant_component(WALLET, "site-builder") is True
    assert store.unclaimed_components(WALLET) == ["site-builder"]


def test_granting_the_same_list_twice_hands_out_one(store):
    """Running the winners list again must not double somebody's prize."""
    assert store.grant_component(WALLET, "site-builder") is True
    assert store.grant_component(WALLET, "site-builder") is False
    assert store.unclaimed_components(WALLET) == ["site-builder"]


def test_a_prize_can_only_be_spent_once(store):
    store.grant_component(WALLET, "site-builder")
    assert store.spend_component(WALLET, "site-builder") is True
    assert store.spend_component(WALLET, "site-builder") is False
    assert store.unclaimed_components(WALLET) == []


def test_spending_something_never_granted_gives_nothing(store):
    assert store.spend_component(WALLET, "site-builder") is False


def test_a_prize_comes_back_when_the_build_never_started(store):
    """
    A dispatch that fails has not built anything, so telling the winner their
    prize is gone would be taking it for nothing.
    """
    store.grant_component(WALLET, "site-builder")
    store.spend_component(WALLET, "site-builder")
    store.return_component(WALLET, "site-builder")
    assert store.unclaimed_components(WALLET) == ["site-builder"]


def test_a_component_grant_is_not_an_agent_grant(store):
    """
    The agent list is served straight out of its table, so a component sitting
    in there would appear on the site as an agent somebody could download.
    """
    store.grant_component(WALLET, "site-builder")
    assert store.unclaimed(WALLET) == []


def test_the_builder_spends_rather_than_checks():
    """
    Two requests arriving together must not both take the same prize, which a
    read then write would allow.
    """
    source = open("Aetheron.py").read()
    route = source.split("def site_builder(")[1].split("\n@app")[0]
    assert "grants.spend_component(user_wallet" in route
    assert "unclaimed_components" not in route


def test_a_free_build_is_recorded_as_free(): 
    """
    A giveaway build charged at full price in the ledger would read as revenue
    that never arrived.
    """
    source = open("Aetheron.py").read()
    route = source.split("def site_builder(")[1].split("\n@app")[0]
    entry = route.split("add_entry(")[1]
    assert "0.0 if on_the_house" in entry
    assert "GIVEAWAY" in entry


def test_a_failed_dispatch_puts_the_prize_back():
    source = open("Aetheron.py").read()
    route = source.split("def site_builder(")[1].split("\n@app")[0]
    failure = route.split("Celery dispatch failed")[0]
    assert "grants.return_component" in failure
