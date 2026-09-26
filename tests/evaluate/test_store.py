"""Opening the store from the evaluator process, with fake store modules (no network)."""

from __future__ import annotations

import sys
import types

from adaptive_harness.evaluate import store as evaluate_store


def _fake_lane(monkeypatch, **attrs):
    module = types.ModuleType("adaptive_harness.store")
    for name, value in attrs.items():
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, "adaptive_harness.store", module)


def test_open_store_uses_from_env_when_mongostore_provides_it(monkeypatch):
    class FakeMongoStore:
        made = []

        def __init__(self, client, db):  # needs arguments, like the real store
            self.args = (client, db)

        @classmethod
        def from_env(cls):
            store = cls("fake-client", "fake-db")
            cls.made.append(store)
            return store

    _fake_lane(monkeypatch, MongoStore=FakeMongoStore)
    opened = evaluate_store.open_store()
    assert isinstance(opened, FakeMongoStore) and FakeMongoStore.made == [opened]
    assert opened.args == ("fake-client", "fake-db")


def test_open_store_falls_back_to_a_factory(monkeypatch):
    sentinel = object()
    _fake_lane(monkeypatch, get_store=lambda: sentinel)
    assert evaluate_store.open_store() is sentinel
