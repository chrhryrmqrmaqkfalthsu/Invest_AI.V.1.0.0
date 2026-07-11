from engine.core.config import config
from engine.live import event_policy


def test_missing_policy_key_falls_back_to_on(monkeypatch):
    monkeypatch.setattr(config, "get", lambda key, default=None: default)
    assert event_policy.live_direct_event_enabled() is True


def test_policy_load_exception_falls_back_to_on(monkeypatch):
    def fail_get(key, default=None):
        raise RuntimeError("synthetic policy load failure")

    monkeypatch.setattr(config, "get", fail_get)
    assert event_policy.live_direct_event_enabled() is True
