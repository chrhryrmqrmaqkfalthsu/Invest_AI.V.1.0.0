"""Visibility/TIF/fractional-watch fix for Alpaca reserved exit-order buttons.

The first Alpaca exit-order patch appended its UI JavaScript after the generated
real dashboard overlay IIFE.  Most helper functions used by the holding TP/SL
panel are local to that IIFE, so the appended code could not hook the panel and
the buttons were not visible.  This post-processor moves that injected code back
inside the same IIFE and makes the frontend submit DAY TIF, which Alpaca requires
for fractional stock orders.  It also starts a lightweight browser-side evaluator
for fractional OCO remainders.
"""
from __future__ import annotations

from engine.live import real_dashboard_api as real_api

_INSTALLED = False
_ORIG_SLOT_OVERLAY_JS = None

_OUTER_MARKER = "\n(function(){\n  function kmExitNum"
_IIFE_OPEN = "(function(){\n"
_IIFE_CLOSE = "\n})();"


def _patch_fractional_tif(js: str) -> str:
    # Fractional exit orders are rejected by Alpaca unless time_in_force=day.
    js = js.replace("time_in_force:'gtc'", "time_in_force:'day'")
    js = js.replace("time_in_force: 'gtc'", "time_in_force: 'day'")
    old = """const kind=(p.take_profit_price&&p.stop_loss_price)?'OCO':(p.take_profit_price?'지정가 매도':'스탑 매도');
    const msg=`${ticker} ${kind} 예약 주문을 Alpaca LIVE에 제출할까요?\\n\\n수량: ${Number(p.shares||0).toFixed(6)}주\\n익절: ${kmExitMoney(p.take_profit_price)}\\n손절: ${kmExitMoney(p.stop_loss_price)}\\n\\n기존 kingmaker 예약 주문은 취소 후 새로 겁니다.`;"""
    new = """const kind=(p.take_profit_price&&p.stop_loss_price)?'OCO':(p.take_profit_price?'지정가 매도':'스탑 매도');
    const rawShares=Number(p.shares||0);
    const wholeShares=Math.floor(rawShares+1e-6);
    const fracShares=Math.max(0, rawShares-wholeShares);
    const fractionalOco=!!(p.take_profit_price&&p.stop_loss_price&&fracShares>1e-6);
    const submitShares=fractionalOco?wholeShares:rawShares;
    if(fractionalOco && wholeShares<=0){ kmSetAlpacaExitStatus('1주 미만 소수점 보유는 Alpaca OCO 불가 · 익절선/손절선 도달 시 시스템 시장가 매도 감시만 사용하세요', 'bad'); return; }
    const fracNote=fractionalOco?`\\n주의: Alpaca는 소수점 OCO를 허용하지 않아 ${wholeShares}주만 OCO 예약하고 ${fracShares.toFixed(6)}주는 TP/SL 선 도달 시 kingmaker가 시장가 매도합니다.`:'';
    const msg=`${ticker} ${kind} 예약 주문을 Alpaca LIVE에 제출할까요?\\n\\n수량: ${Number(submitShares||0).toFixed(6)}주${fracNote}\\n익절: ${kmExitMoney(p.take_profit_price)}\\n손절: ${kmExitMoney(p.stop_loss_price)}\\n\\n소수점 주식 규칙 때문에 DAY 주문으로 제출됩니다. 기존 kingmaker 예약 주문은 취소 후 새로 겁니다.`;"""
    js = js.replace(old, new)
    js = js.replace(
        "kmSetAlpacaExitStatus(`Alpaca ${st.order_kind||'예약'} 제출 완료 · ${st.client_order_id||''}`, 'good');",
        "kmSetAlpacaExitStatus(d.warning || `Alpaca ${st.order_kind||'예약'} 제출 완료 · ${st.client_order_id||''}`, d.warning?'warn':'good');",
    )
    js = js.replace(
        "if(typeof toast==='function') toast('Alpaca 예약 주문 완료', `${ticker} · ${st.order_kind||kind} · TP ${kmExitMoney(p.take_profit_price)} · SL ${kmExitMoney(p.stop_loss_price)}`, 'good');",
        "if(typeof toast==='function') toast('Alpaca 예약 주문 완료', d.warning || `${ticker} · ${st.order_kind||kind} · TP ${kmExitMoney(p.take_profit_price)} · SL ${kmExitMoney(p.stop_loss_price)}`, d.warning?'warn':'good');",
    )
    monitor = r'''
  async function kmEvaluateFractionalExitWatches(){
    if(window._kmFractionalExitEvalBusy) return;
    window._kmFractionalExitEvalBusy=true;
    try{
      const r=await fetch(`${API}/api/real/alpaca_exit_orders/evaluate_fractional`, {method:'POST', cache:'no-store'});
      const d=await r.json().catch(()=>({}));
      if(d && Array.isArray(d.triggered) && d.triggered.length){
        d.triggered.forEach(x=>{
          const msg=`${x.ticker} 소수점 잔량 ${Number(x.shares||0).toFixed(6)}주 · ${x.trigger_kind} @ ${kmExitMoney(x.price)} 시장가 매도 요청`;
          kmSetAlpacaExitStatus(msg, 'good');
          if(typeof toast==='function') toast('소수점 잔량 자동매도', msg, 'warn');
        });
        kmLoadAlpacaExitStatus();
      }
    }catch(e){}
    finally{ window._kmFractionalExitEvalBusy=false; }
  }
  if(!window._kmFractionalExitEvalInterval){
    window._kmFractionalExitEvalInterval=setInterval(kmEvaluateFractionalExitWatches, 5000);
    setTimeout(kmEvaluateFractionalExitWatches, 1800);
  }
'''
    if "kmEvaluateFractionalExitWatches" not in js:
        js = js.replace(
            "  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', ()=>setTimeout(kmEnsureAlpacaExitButtons, 500), {once:true});\n  else setTimeout(kmEnsureAlpacaExitButtons, 500);",
            monitor + "\n  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', ()=>setTimeout(kmEnsureAlpacaExitButtons, 500), {once:true});\n  else setTimeout(kmEnsureAlpacaExitButtons, 500);",
        )
    return js


def _move_alpaca_exit_iife_inside_overlay(js: str) -> str:
    if "kmApplyAlpacaExitOrder" not in js:
        return _patch_fractional_tif(js)
    start = js.find(_OUTER_MARKER)
    if start < 0:
        # Already inside the main overlay IIFE or injected in another shape.
        return _patch_fractional_tif(js)
    prefix = js[:start]
    outer = js[start + 1 :].strip()
    if not outer.startswith(_IIFE_OPEN) or not outer.endswith(_IIFE_CLOSE.strip()):
        return _patch_fractional_tif(js)
    body = outer[len(_IIFE_OPEN) :]
    if body.endswith(_IIFE_CLOSE.strip()):
        body = body[: -len(_IIFE_CLOSE.strip())]
    close = prefix.rfind("})();")
    if close < 0:
        return _patch_fractional_tif(js)
    if "window.__kmAlpacaExitButtonsInsideOverlay" in prefix:
        return _patch_fractional_tif(prefix)
    wrapped_body = "\n  window.__kmAlpacaExitButtonsInsideOverlay=true;\n" + body.rstrip() + "\n"
    return _patch_fractional_tif(prefix[:close] + wrapped_body + prefix[close:])


def install_real_dashboard_alpaca_exit_order_visibility_patch() -> None:
    """Install the JS placement/TIF/fractional-watch fix once per API process."""
    global _INSTALLED, _ORIG_SLOT_OVERLAY_JS
    if _INSTALLED:
        return
    _ORIG_SLOT_OVERLAY_JS = real_api._real_slot_overlay_js

    def patched_real_slot_overlay_js() -> str:
        return _move_alpaca_exit_iife_inside_overlay(_ORIG_SLOT_OVERLAY_JS())

    real_api._real_slot_overlay_js = patched_real_slot_overlay_js
    _INSTALLED = True
