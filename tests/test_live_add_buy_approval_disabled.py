from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.runner import Runner  # noqa: E402


class FakeApprovalManager:
    def __init__(self, requests=None) -> None:
        self._requests = dict(requests or {})
        self._by_ticker = {
            getattr(req, "ticker", ""): rid
            for rid, req in self._requests.items()
            if str(getattr(req, "status", "")) == "pending"
        }
        self.saved = 0
        self.created = 0
        self.reconfirm_checked = 0

    def _save(self) -> None:
        self.saved += 1

    def create_request(self, *args, **kwargs):
        self.created += 1
        raise AssertionError("manual add-buy approval request must not be created while disabled")

    def should_reconfirm(self, ticker: str) -> bool:
        self.reconfirm_checked += 1
        raise AssertionError("reconfirm must not be checked while manual add-buy approval is disabled")


class FakeRulebook:
    def evaluate(self, *args, **kwargs):
        raise AssertionError("rulebook must not be evaluated for disabled add-buy reconfirm")

    def get_rulebook(self, *args, **kwargs):
        raise AssertionError("rulebook must not be resolved for disabled add-buy reconfirm")


def _runner(policy: dict | None = None, approval_manager=None) -> Runner:
    r = Runner.__new__(Runner)
    r.safety = SimpleNamespace(policy=policy or {})
    r.approval_manager = approval_manager or FakeApprovalManager()
    r.position_manager = SimpleNamespace(get=lambda ticker: SimpleNamespace(ticker=ticker))
    r.rulebook = FakeRulebook()
    r.notifier = SimpleNamespace(send_approval_request=lambda req: (_ for _ in ()).throw(AssertionError("approval alert disabled")))
    r._execute_approved_calls = []
    r._reevaluate_request_calls = []
    r._execute_approved = lambda req: r._execute_approved_calls.append(req)
    r._reevaluate_request = lambda req: r._reevaluate_request_calls.append(req)
    return r


def test_manual_add_buy_approval_is_disabled_by_default():
    r = _runner(policy={"add_buy": {"enabled": True}})
    assert r._add_buy_approval_enabled() is False


def test_disabled_path_rejects_existing_pending_and_approved_requests_without_execution():
    req_pending = SimpleNamespace(request_id="p", ticker="AAPL", status="pending", approved_krw=0, approved_at="")
    req_approved = SimpleNamespace(request_id="a", ticker="MSFT", status="approved", approved_krw=100, approved_at="x")
    req_reeval = SimpleNamespace(request_id="r", ticker="NVDA", status="reevaluating", approved_krw=50, approved_at="x")
    req_done = SimpleNamespace(request_id="d", ticker="TSLA", status="executed", approved_krw=50, approved_at="x")
    mgr = FakeApprovalManager({"p": req_pending, "a": req_approved, "r": req_reeval, "d": req_done})
    r = _runner(policy={}, approval_manager=mgr)

    r._process_pending_approvals()

    assert req_pending.status == "rejected"
    assert req_approved.status == "rejected"
    assert req_reeval.status == "rejected"
    assert req_done.status == "executed"
    assert req_approved.approved_krw == 0
    assert req_reeval.approved_at == ""
    assert r._execute_approved_calls == []
    assert r._reevaluate_request_calls == []
    assert mgr.saved == 1


def test_disabled_maybe_request_approval_does_not_create_request_or_notify():
    mgr = FakeApprovalManager()
    r = _runner(policy={}, approval_manager=mgr)
    sig = SimpleNamespace(score=99.0, threshold=1.0, reasons=["strong"])
    rb = SimpleNamespace(win_rate=1.0, sector_name="tech")

    r._maybe_request_approval("AAPL", 100.0, rb, sig)

    assert mgr.created == 0


def test_disabled_reconfirm_returns_before_approval_manager_and_rulebook_work():
    mgr = FakeApprovalManager()
    r = _runner(policy={}, approval_manager=mgr)

    r._maybe_reconfirm_existing("AAPL", 100.0)

    assert mgr.reconfirm_checked == 0


def test_explicit_policy_flag_preserves_legacy_manual_approval_processing():
    req_approved = SimpleNamespace(request_id="a", ticker="AAPL", status="approved", approved_krw=100, approved_at="x")
    req_reeval = SimpleNamespace(request_id="r", ticker="MSFT", status="reevaluating", approved_krw=50, approved_at="x")
    mgr = FakeApprovalManager({"a": req_approved, "r": req_reeval})
    r = _runner(policy={"add_buy_approval_enabled": True}, approval_manager=mgr)

    r._process_pending_approvals()

    assert r._execute_approved_calls == [req_approved]
    assert r._reevaluate_request_calls == [req_reeval]
    assert mgr.saved == 0


def test_nested_add_buy_approval_flag_can_enable_legacy_path():
    r = _runner(policy={"add_buy": {"approval_enabled": True}})
    assert r._add_buy_approval_enabled() is True
