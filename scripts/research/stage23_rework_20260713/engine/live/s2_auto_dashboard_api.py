"""S2 auto trading settings dashboard routes.

UI/API layer only. This module reads/writes ``live_auto_config.json`` and never
submits broker orders or calls the S2AutoTrader execution path.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from engine.live.s2_auto_config import CONFIG_PATH, default_config, direct_order_env_enabled, load_live_auto_config

STATE_PATH = Path("data/_system/live_auto_state.json")
LIVE_SLOTS_STATE_PATH = Path("data/_system/live_slots_state.json")
POSITIONS_PATH = Path("data/_system/positions.json")
CONFIG_CHANGE_EVENTS_PATH = Path("data/_system/live_auto_config_change_events.jsonl")
REAL_ORDER_ENABLE_PHRASE = "REAL_ORDERS_ENABLED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if out != out:
            return default
        return out
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{int(datetime.now().timestamp() * 1000)}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _deep_merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in dict(patch).items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge(dict(out[key]), value)
        else:
            out[key] = value
    return out


def _normalize_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    cfg = _deep_merge(default_config(), raw)
    for key in ["master_enabled", "auto_buy_enabled", "auto_exit_enabled", "real_orders_enabled", "dry_run", "allow_intraday_immediate"]:
        cfg[key] = bool(cfg.get(key))
    cfg["entry_timing"] = "next_open" if str(cfg.get("entry_timing") or "next_open") != "next_open" else "next_open"
    cfg["portfolio_K"] = max(1, _safe_int(cfg.get("portfolio_K"), 20))
    cfg["display_slots"] = max(1, _safe_int(cfg.get("display_slots"), 8))
    cfg["total_capital_mode"] = "fixed_from_account_at_start"
    cfg["capital_source"] = "available_cash"
    capital = cfg.setdefault("capital", {})
    if not isinstance(capital, dict):
        capital = {}; cfg["capital"] = capital
    capital["total_capital_usd"] = None if capital.get("total_capital_usd") in (None, "") else _safe_float(capital.get("total_capital_usd"), None)
    capital["allocation_mode"] = "equal_weight_fixed_slot"
    capital["cash_buffer_usd"] = _safe_float(capital.get("cash_buffer_usd"), 10.0)
    capital["cash_buffer_pct"] = _safe_float(capital.get("cash_buffer_pct"), 0.02)
    capital["rebalance_existing_positions"] = bool(capital.get("rebalance_existing_positions", False))
    exit_cfg = cfg.setdefault("exit", {})
    if not isinstance(exit_cfg, dict):
        exit_cfg = {}; cfg["exit"] = exit_cfg
    exit_cfg["s2_take_profit_enabled"] = bool(exit_cfg.get("s2_take_profit_enabled", False))
    exit_cfg["engine"] = "PositionManager_ExitPolicy"
    exit_cfg["require_exit_live_policy"] = bool(exit_cfg.get("require_exit_live_policy", True))
    exit_cfg["allow_legacy_fallback"] = bool(exit_cfg.get("allow_legacy_fallback", False))
    risk = cfg.setdefault("risk_limits", {})
    if not isinstance(risk, dict):
        risk = {}; cfg["risk_limits"] = risk
    risk["max_order_notional_usd"] = _safe_float(risk.get("max_order_notional_usd"), 100.0)
    risk["max_daily_orders"] = max(0, _safe_int(risk.get("max_daily_orders"), 3))
    risk["max_daily_buy_notional_usd"] = _safe_float(risk.get("max_daily_buy_notional_usd"), 300.0)
    risk["max_total_exposure_usd"] = _safe_float(risk.get("max_total_exposure_usd"), 650.0)
    risk["min_cash_buffer_usd"] = _safe_float(risk.get("min_cash_buffer_usd"), 10.0)
    risk["one_position_per_ticker"] = bool(risk.get("one_position_per_ticker", True))
    approval = cfg.setdefault("operator_approval", {})
    if not isinstance(approval, dict):
        approval = {}; cfg["operator_approval"] = approval
    approval["operator_armed"] = bool(approval.get("operator_armed", False))
    approval["armed_until_utc"] = str(approval.get("armed_until_utc") or "")
    approval["confirmation_phrase_required"] = bool(approval.get("confirmation_phrase_required", True))
    approval["confirmation_phrase"] = str(approval.get("confirmation_phrase") or "S2_AUTO_LIVE_APPROVE")
    return cfg


def _diff_config(before: Mapping[str, Any], after: Mapping[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    keys = sorted(set(before.keys()) | set(after.keys()))
    for key in keys:
        path = f"{prefix}.{key}" if prefix else str(key)
        old = before.get(key)
        new = after.get(key)
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            changes.extend(_diff_config(old, new, path))
        elif old != new:
            changes.append({"path": path, "old": old, "new": new})
    return changes


def _account_status(base_module: Any = None) -> dict[str, Any]:
    try:
        from engine.live.real_dashboard_api import _get_real_broker

        broker = _get_real_broker()
        if broker is None:
            return {"ok": False, "cash_usd": None, "total_value_usd": None, "holdings_count": 0, "error": "broker_unavailable"}
        bal = broker.get_balance()
        cash = _safe_float(getattr(bal, "cash_usd", getattr(bal, "cash_krw", None)), None)
        total = _safe_float(getattr(bal, "total_value_usd", getattr(bal, "total_value_krw", cash)), cash)
        holdings = getattr(bal, "holdings", []) or []
        return {"ok": True, "cash_usd": cash, "total_value_usd": total, "holdings_count": len(holdings), "broker_mode": str(getattr(broker, "mode", ""))}
    except Exception as exc:
        return {"ok": False, "cash_usd": None, "total_value_usd": None, "holdings_count": 0, "error": f"{type(exc).__name__}: {exc}"}


def _slots_status() -> dict[str, Any]:
    state = _read_json(LIVE_SLOTS_STATE_PATH, {})
    slots = state.get("slots") if isinstance(state, Mapping) else []
    waitlist = state.get("waitlist") if isinstance(state, Mapping) else []
    return {
        "ok": bool(state),
        "slots_filled": sum(1 for s in (slots or []) if isinstance(s, Mapping) and s.get("candidate_id")),
        "slot_count": len(slots or []),
        "waitlist_count": len(waitlist or []),
        "last_refresh": (state.get("last_refresh") or {}).get("time") if isinstance(state, Mapping) else None,
    }


def _position_status() -> dict[str, Any]:
    data = _read_json(POSITIONS_PATH, {})
    return {"positions_state_count": len(data) if isinstance(data, Mapping) else 0}


def _config_status_payload(base_module: Any = None) -> dict[str, Any]:
    cfg = _normalize_config(load_live_auto_config(CONFIG_PATH))
    account = _account_status(base_module)
    k = max(1, _safe_int(cfg.get("portfolio_K"), 20))
    cash = _safe_float(account.get("cash_usd"), None)
    position_notional = None if cash is None else cash / k
    state = _read_json(STATE_PATH, {})
    return {
        "ok": True,
        "config_path": str(CONFIG_PATH),
        "config": cfg,
        "status": {
            "account": account,
            "position_notional_from_cash": position_notional,
            "portfolio_K": k,
            "direct_order_env_enabled": direct_order_env_enabled(),
            "slots": _slots_status(),
            "positions": _position_status(),
            "auto_state": state if isinstance(state, Mapping) else {},
            "master_enabled": bool(cfg.get("master_enabled")),
            "dry_run": bool(cfg.get("dry_run")),
            "real_orders_enabled": bool(cfg.get("real_orders_enabled")),
        },
        "real_order_enable_phrase": REAL_ORDER_ENABLE_PHRASE,
    }


class LiveAutoConfigSaveRequest(BaseModel):
    config: dict[str, Any]
    real_orders_ack: bool = False
    real_orders_phrase: str = ""
    actor: str = "dashboard-real-auto-settings"


def _validate_save_request(before: Mapping[str, Any], after: Mapping[str, Any], req: LiveAutoConfigSaveRequest) -> None:
    before_real = bool(before.get("real_orders_enabled"))
    after_real = bool(after.get("real_orders_enabled"))
    if after_real and not before_real:
        if not req.real_orders_ack or str(req.real_orders_phrase or "").strip() != REAL_ORDER_ENABLE_PHRASE:
            raise HTTPException(status_code=400, detail=f"real_orders_enabled requires checkbox and phrase {REAL_ORDER_ENABLE_PHRASE}")
    if after_real and bool(after.get("dry_run", True)):
        raise HTTPException(status_code=400, detail="real_orders_enabled cannot be true while dry_run is true")
    if after_real and not direct_order_env_enabled():
        raise HTTPException(status_code=400, detail="direct order env is disabled; cannot save real_orders_enabled=true")
    if str(after.get("entry_timing") or "next_open") != "next_open":
        raise HTTPException(status_code=400, detail="only entry_timing=next_open is enabled")


def _settings_html() -> str:
    return r'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>S2 자동매매 설정</title>
<style>
:root{--bg:#0b111c;--panel:#111a2b;--line:#263246;--text:#e7eefb;--dim:#93a4bd;--gold:#f5c451;--bad:#ff6b7a;--good:#39d98a;--warn:#ffb020}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,Apple SD Gothic Neo,Malgun Gothic,sans-serif}.wrap{max-width:1180px;margin:0 auto;padding:22px}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px}.title{font-size:24px;font-weight:800}.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:14px}.danger{border-color:#6a2630;background:linear-gradient(180deg,#25131a,#111a2b)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}.row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.06)}.row:last-child{border-bottom:0}.label{font-weight:750}.hint{color:var(--dim);font-size:12px;margin-top:4px}.pill{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;padding:6px 10px;color:var(--dim);font-size:12px}.pill.good{color:var(--good);border-color:#2a6a4a}.pill.bad{color:var(--bad);border-color:#6a2630}.pill.warn{color:var(--warn);border-color:#7a551c}input,select{background:#0d1524;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:9px 10px;min-width:120px}.num{max-width:120px}button{border:0;border-radius:10px;padding:10px 14px;font-weight:800;cursor:pointer}.save{background:var(--gold);color:#111}.reload{background:#22314a;color:var(--text)}.kill{background:var(--bad);color:#fff}.switch{transform:scale(1.2);min-width:auto}.metric{font-size:28px;font-weight:850}.metric small{font-size:13px;color:var(--dim);font-weight:600}.warnbox{background:#25131a;border:1px solid #6a2630;color:#ffd5d9;border-radius:12px;padding:12px;margin-top:10px}.okbox{background:#10281d;border:1px solid #2a6a4a;color:#c9f7df;border-radius:12px;padding:12px;margin-top:10px}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}.footer{color:var(--dim);font-size:12px;margin-top:10px}a{color:var(--gold);text-decoration:none}
</style>
</head>
<body>
<div class="wrap">
  <div class="top"><div><div class="title">S2 자동매매 설정</div><div class="hint">live_auto_config.json UI · 설정 변경만 수행 · 주문 실행 없음</div></div><div><a href="/dashboard-real">← 실거래 대시보드</a></div></div>
  <div class="panel danger">
    <div class="row"><div><div class="label">마스터 스위치 / 전체 Kill Switch</div><div class="hint">false면 자동매수·자동청산 전체가 즉시 중단됩니다.</div></div><input id="master_enabled" class="switch" type="checkbox"></div>
    <button class="kill" onclick="killNow()">전체 즉시 정지(master=false 저장)</button>
    <span id="topStatus" class="pill"></span>
  </div>
  <div class="panel"><div class="grid">
    <div><div class="hint">현재 계좌 available cash</div><div class="metric" id="cash">-</div></div>
    <div><div class="hint">포지션당 금액 cash / K</div><div class="metric" id="posNotional">-</div></div>
    <div><div class="hint">브로커 보유 수</div><div class="metric" id="holdings">-</div></div>
    <div><div class="hint">후보 슬롯 / 대기열</div><div class="metric" id="slots">-</div></div>
  </div></div>
  <div class="panel"><h3>자동매매 스위치</h3>
    <div class="row"><div><div class="label">자동매수</div><div class="hint">candidate_pool에서 next_open 주문 계획을 생성합니다.</div></div><input id="auto_buy_enabled" class="switch" type="checkbox"></div>
    <div class="row"><div><div class="label">자동청산</div><div class="hint">PositionManager + ExitPolicy 청산 경로를 사용합니다.</div></div><input id="auto_exit_enabled" class="switch" type="checkbox"></div>
    <div class="row"><div><div class="label">Dry run</div><div class="hint">true면 실주문 제출 금지. 초기값 true.</div></div><input id="dry_run" class="switch" type="checkbox"></div>
    <div class="row"><div><div class="label">실주문</div><div class="hint">켜면 실제 돈이 나갈 수 있습니다. 별도 확인 필요.</div></div><input id="real_orders_enabled" class="switch" type="checkbox" onchange="realWarn()"></div>
    <div id="realWarn" class="warnbox" style="display:none"><b>실제 돈이 나갑니다.</b><br>실주문을 켜려면 체크박스와 문구 재입력이 필요합니다.<br><label><input id="real_orders_ack" type="checkbox"> 위험을 이해했습니다.</label><br><input id="real_orders_phrase" placeholder="REAL_ORDERS_ENABLED" style="margin-top:8px;min-width:260px"></div>
  </div>
  <div class="panel"><h3>S2 전략 파라미터</h3>
    <div class="row"><div><div class="label">take_profit 토글</div><div class="hint">OFF = S2 no-TP. ON = 기존 target/take_profit 허용.</div></div><input id="s2_take_profit_enabled" class="switch" type="checkbox"></div>
    <div class="row"><div><div class="label">최대 보유 종목 수 K</div><div class="hint">기본 20. 변경 시 포지션당 금액이 즉시 재계산됩니다.</div></div><input id="portfolio_K" class="num" type="number" min="1" step="1" oninput="recalc()"></div>
    <div class="row"><div><div class="label">진입 타이밍</div><div class="hint">현재 활성 지원은 next_open뿐입니다.</div></div><select id="entry_timing"><option value="next_open">next_open</option><option value="intraday_immediate" disabled>intraday_immediate 비활성</option></select></div>
    <div class="row"><div><div class="label">자본 모드</div><div class="hint">세션 시작 시 available cash를 고정합니다.</div></div><input id="total_capital_mode" value="fixed_from_account_at_start" disabled></div>
    <div class="row"><div><div class="label">자본 소스</div><div class="hint">마진 제외 available cash 기준.</div></div><input id="capital_source" value="available_cash" disabled></div>
  </div>
  <div class="panel"><h3>안전 한도</h3>
    <div class="grid">
      <label>max_order_notional_usd<br><input id="max_order_notional_usd" class="num" type="number" step="0.01"></label>
      <label>max_daily_orders<br><input id="max_daily_orders" class="num" type="number" step="1"></label>
      <label>max_daily_buy_notional_usd<br><input id="max_daily_buy_notional_usd" class="num" type="number" step="0.01"></label>
      <label>max_total_exposure_usd<br><input id="max_total_exposure_usd" class="num" type="number" step="0.01"></label>
      <label>min_cash_buffer_usd<br><input id="min_cash_buffer_usd" class="num" type="number" step="0.01"></label>
    </div>
  </div>
  <div class="panel"><button class="save" onclick="save()">설정 저장</button> <button class="reload" onclick="load()">다시 읽기</button><div id="msg" class="footer"></div></div>
</div>
<script>
let cfg=null, cash=null, phrase='REAL_ORDERS_ENABLED';
function money(x){return x==null?'-':'$'+Number(x).toLocaleString(undefined,{maximumFractionDigits:2});}
function set(id,v){const e=document.getElementById(id); if(!e) return; if(e.type==='checkbox') e.checked=!!v; else e.value=(v??'');}
function getBool(id){return !!document.getElementById(id).checked;}
function num(id){const v=parseFloat(document.getElementById(id).value); return Number.isFinite(v)?v:null;}
function recalc(){const k=parseInt(document.getElementById('portfolio_K').value||'20',10)||20; document.getElementById('posNotional').innerHTML=money(cash==null?null:cash/k)+` <small>K=${k}</small>`;}
function realWarn(){document.getElementById('realWarn').style.display=getBool('real_orders_enabled')?'block':'none';}
async function load(){
  const d=await (await fetch('/api/real/live_auto_config',{cache:'no-store'})).json(); cfg=d.config; cash=d.status.account.cash_usd; phrase=d.real_order_enable_phrase||phrase;
  set('master_enabled',cfg.master_enabled); set('auto_buy_enabled',cfg.auto_buy_enabled); set('auto_exit_enabled',cfg.auto_exit_enabled); set('dry_run',cfg.dry_run); set('real_orders_enabled',cfg.real_orders_enabled);
  set('s2_take_profit_enabled',cfg.exit.s2_take_profit_enabled); set('portfolio_K',cfg.portfolio_K); set('entry_timing',cfg.entry_timing); set('total_capital_mode',cfg.total_capital_mode); set('capital_source',cfg.capital_source);
  set('max_order_notional_usd',cfg.risk_limits.max_order_notional_usd); set('max_daily_orders',cfg.risk_limits.max_daily_orders); set('max_daily_buy_notional_usd',cfg.risk_limits.max_daily_buy_notional_usd); set('max_total_exposure_usd',cfg.risk_limits.max_total_exposure_usd); set('min_cash_buffer_usd',cfg.risk_limits.min_cash_buffer_usd);
  document.getElementById('cash').textContent=money(cash); document.getElementById('holdings').textContent=d.status.account.holdings_count??'-'; document.getElementById('slots').textContent=`${d.status.slots.slots_filled}/${d.status.slots.slot_count} · 대기 ${d.status.slots.waitlist_count}`;
  const top=document.getElementById('topStatus'); top.className='pill '+(cfg.master_enabled?'good':'bad'); top.textContent=cfg.master_enabled?'MASTER ON':'MASTER OFF';
  recalc(); realWarn(); document.getElementById('msg').textContent='읽기 완료 '+new Date().toLocaleString();
}
function build(){
  const c=JSON.parse(JSON.stringify(cfg));
  c.master_enabled=getBool('master_enabled'); c.auto_buy_enabled=getBool('auto_buy_enabled'); c.auto_exit_enabled=getBool('auto_exit_enabled'); c.dry_run=getBool('dry_run'); c.real_orders_enabled=getBool('real_orders_enabled');
  c.entry_timing=document.getElementById('entry_timing').value; c.portfolio_K=parseInt(document.getElementById('portfolio_K').value||'20',10)||20; c.total_capital_mode='fixed_from_account_at_start'; c.capital_source='available_cash';
  c.exit.s2_take_profit_enabled=getBool('s2_take_profit_enabled');
  c.risk_limits.max_order_notional_usd=num('max_order_notional_usd'); c.risk_limits.max_daily_orders=parseInt(document.getElementById('max_daily_orders').value||'0',10)||0; c.risk_limits.max_daily_buy_notional_usd=num('max_daily_buy_notional_usd'); c.risk_limits.max_total_exposure_usd=num('max_total_exposure_usd'); c.risk_limits.min_cash_buffer_usd=num('min_cash_buffer_usd');
  return c;
}
async function save(){
  const body={config:build(), real_orders_ack:getBool('real_orders_ack'), real_orders_phrase:document.getElementById('real_orders_phrase').value, actor:'dashboard-real-auto-settings'};
  const r=await fetch('/api/real/live_auto_config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); const d=await r.json();
  if(!r.ok||!d.ok){document.getElementById('msg').innerHTML='<span style="color:var(--bad)">저장 실패: '+(d.detail||d.error||JSON.stringify(d))+'</span>'; return;}
  cfg=d.config; document.getElementById('real_orders_ack').checked=false; document.getElementById('real_orders_phrase').value=''; await load(); document.getElementById('msg').innerHTML='<span style="color:var(--good)">저장 완료 · 변경 '+d.changes.length+'개</span>';
}
async function killNow(){document.getElementById('master_enabled').checked=false; await save();}
load();
</script>
</body></html>'''


def install_s2_auto_dashboard_routes(app: Any, base_module: Any = None) -> None:
    @app.get("/dashboard-real/auto-settings", include_in_schema=False)
    def s2_auto_settings_page():
        return HTMLResponse(_settings_html())

    @app.get("/api/real/live_auto_config")
    def get_live_auto_config():
        return _config_status_payload(base_module)

    @app.post("/api/real/live_auto_config")
    def save_live_auto_config(req: LiveAutoConfigSaveRequest, request: Request):
        before = _normalize_config(load_live_auto_config(CONFIG_PATH))
        after = _normalize_config(req.config or {})
        _validate_save_request(before, after, req)
        changes = _diff_config(before, after)
        _atomic_write_json(CONFIG_PATH, after)
        event = {
            "time": _utc_now(),
            "actor": req.actor or "dashboard-real-auto-settings",
            "client": request.client.host if request.client else "unknown",
            "changes": changes,
            "master_enabled_changed": any(c.get("path") == "master_enabled" for c in changes),
            "real_orders_enabled_changed": any(c.get("path") == "real_orders_enabled" for c in changes),
        }
        _append_jsonl(CONFIG_CHANGE_EVENTS_PATH, event)
        return {"ok": True, "config": after, "changes": changes, "event_log": str(CONFIG_CHANGE_EVENTS_PATH)}

    @app.get("/api/real/live_auto_config_events")
    def live_auto_config_events(limit: int = 100):
        if not CONFIG_CHANGE_EVENTS_PATH.exists():
            return {"ok": True, "events": [], "path": str(CONFIG_CHANGE_EVENTS_PATH)}
        rows = []
        for line in CONFIG_CHANGE_EVENTS_PATH.read_text(encoding="utf-8").splitlines()[-max(1, min(int(limit), 500)):]:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
        return {"ok": True, "events": rows, "path": str(CONFIG_CHANGE_EVENTS_PATH)}
