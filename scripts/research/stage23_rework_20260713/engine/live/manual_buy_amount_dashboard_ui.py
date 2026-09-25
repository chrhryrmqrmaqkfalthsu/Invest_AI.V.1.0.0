"""Dashboard manual BUY amount input overlay.

대시보드 후보 카드에 매수 금액 입력칸을 추가하고, 입력 금액을
manual_buy_intent.json에 함께 기록한다. 이 모듈은 broker를 직접 호출하지 않는다.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from engine.live.manual_buy_intent import create_manual_buy_intent, read_json


class ManualBuyAmountIntentRequest(BaseModel):
    candidate_id: str
    source: str = "dashboard"
    notional: float | None = None


MANUAL_BUY_AMOUNT_OVERLAY_JS = r"""
(function(){
  const BUY_API = (typeof API !== 'undefined' ? API : window.location.origin);
  const state = {candidates:{}, lastLoad:0, busy:{}};
  const css = `
  .manual-buy-amount{width:104px;background:#090d14;border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:7px 8px;font-size:12px;font-weight:800;font-variant-numeric:tabular-nums;}
  .manual-buy-amount:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 1px rgba(59,130,246,.35) inset;}
  .manual-buy-amount:disabled{opacity:.55;cursor:not-allowed;}
  .manual-buy-hint{font-size:10px;color:var(--dim);margin-top:3px;text-align:right;font-variant-numeric:tabular-nums;}
  .cand-right.manual-buy-enhanced{align-items:flex-end;}
  .manual-buy-controls{display:flex;flex-direction:column;gap:3px;align-items:flex-end;}
  .manual-buy-row{display:flex;gap:6px;align-items:center;}
  @media(max-width:700px){.manual-buy-row{flex-wrap:wrap;justify-content:flex-end}.manual-buy-amount{width:96px;}}
  `;
  function injectCss(){
    if(document.getElementById('manual-buy-amount-style')) return;
    const el=document.createElement('style');
    el.id='manual-buy-amount-style';
    el.textContent=css;
    document.head.appendChild(el);
  }
  function toast2(t,b,type){
    try{ if(typeof toast==='function') toast(t,b,type); else console.log(t,b); }catch(e){ console.log(t,b); }
  }
  function html(s){
    try{ if(typeof htmlEsc==='function') return htmlEsc(s); }catch(e){}
    return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  function num(v){
    if(v==null) return null;
    const x=Number(String(v).replace(/,/g,'').trim());
    return isFinite(x)?x:null;
  }
  function money(v){
    const x=num(v);
    return x==null?'—':'$'+x.toLocaleString(undefined,{maximumFractionDigits:0});
  }
  async function loadCandidateMap(force){
    const now=Date.now();
    if(!force && now-state.lastLoad<10000) return state.candidates;
    state.lastLoad=now;
    try{
      const r=await fetch(`${BUY_API}/api/live/central_candidates`);
      const d=await r.json();
      state.candidates=(d&&d.candidates)||{};
    }catch(e){}
    return state.candidates;
  }
  function cardTicker(btn, c){
    if(c && c.ticker) return String(c.ticker).toUpperCase();
    const card=btn.closest('.cand-card');
    return String((card&&card.dataset&&card.dataset.candTicker)||'').toUpperCase();
  }
  function defaultNotional(c){
    const n=num(c && (c.manual_requested_notional || c.manual_notional || c.notional));
    return n!=null && n>0 ? n : null;
  }
  function decorateButtons(){
    injectCss();
    document.querySelectorAll('.cand-buy[data-candidate-id]').forEach(btn=>{
      if(btn.dataset.amountEnhanced==='1') return;
      if(btn.disabled) return;
      const cid=String(btn.dataset.candidateId||'');
      if(!cid) return;
      const c=state.candidates[cid]||{};
      const parent=btn.parentElement;
      if(!parent) return;
      parent.classList.add('manual-buy-enhanced');
      const controls=document.createElement('div');
      controls.className='manual-buy-controls';
      const row=document.createElement('div');
      row.className='manual-buy-row';
      const input=document.createElement('input');
      input.className='manual-buy-amount';
      input.type='number';
      input.min='1';
      input.step='100';
      input.placeholder='매수금액 $';
      input.dataset.candidateId=cid;
      const dn=defaultNotional(c);
      if(dn!=null) input.value=String(Math.round(dn));
      input.title='이번 후보에 투입할 매수 금액(USD)';
      input.addEventListener('click',ev=>ev.stopPropagation());
      input.addEventListener('mousedown',ev=>ev.stopPropagation());
      input.addEventListener('keydown',ev=>{
        ev.stopPropagation();
        if(ev.key==='Enter') btn.click();
      });
      const oldText=btn.textContent||'지금 매수';
      btn.dataset.originalText=oldText;
      btn.textContent=state.busy[cid]?'요청 중…':'금액 체결';
      btn.dataset.amountEnhanced='1';
      row.appendChild(input);
      row.appendChild(btn);
      const hint=document.createElement('div');
      hint.className='manual-buy-hint';
      hint.textContent=dn!=null ? `기본 ${money(dn)}` : '금액 입력';
      controls.appendChild(row);
      controls.appendChild(hint);
      parent.appendChild(controls);
    });
  }
  async function sendBuyIntent(btn){
    const cid=String(btn.dataset.candidateId||'');
    if(!cid || state.busy[cid]) return;
    const box=btn.closest('.manual-buy-controls')||btn.parentElement;
    const input=box&&box.querySelector ? box.querySelector('.manual-buy-amount') : null;
    const amount=num(input&&input.value);
    const c=state.candidates[cid]||{};
    const ticker=cardTicker(btn,c)||cid;
    if(amount==null || amount<=0){
      toast2('매수 금액 필요', `${ticker} 매수 금액을 달러 기준으로 입력하세요`, 'warn');
      if(input) input.focus();
      return;
    }
    state.busy[cid]=true;
    btn.disabled=true;
    if(input) input.disabled=true;
    btn.textContent='요청 중…';
    try{
      const r=await fetch(`${BUY_API}/api/live/manual_buy_intent`,{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({candidate_id:cid, source:'dashboard_amount', notional:amount})
      });
      const d=await r.json().catch(()=>({}));
      if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`);
      toast2('매수 요청됨', `${html(ticker)} · ${money(amount)} · 다음 tick에서 체결 처리`, 'good');
    }catch(e){
      toast2('매수 요청 거부', String(e.message||e), 'warn');
      btn.disabled=false;
      if(input) input.disabled=false;
      btn.textContent='금액 체결';
    }finally{
      state.busy[cid]=false;
      try{ if(typeof loadCandidates==='function') await loadCandidates(); }catch(e){}
      setTimeout(()=>loadCandidateMap(true).then(decorateButtons),300);
    }
  }
  document.addEventListener('click', ev=>{
    const btn=ev.target&&ev.target.closest ? ev.target.closest('.cand-buy[data-candidate-id]') : null;
    if(!btn || btn.dataset.amountEnhanced!=='1') return;
    ev.preventDefault();
    ev.stopPropagation();
    ev.stopImmediatePropagation();
    sendBuyIntent(btn);
  }, true);
  async function tick(){
    await loadCandidateMap(false);
    decorateButtons();
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', tick); else tick();
  setInterval(tick, 1500);
  try{
    const target=document.getElementById('cand-list')||document.body;
    new MutationObserver(()=>tick()).observe(target,{childList:true,subtree:true});
  }catch(e){}
})();
"""


def inject_manual_buy_amount_script(html: str) -> str:
    marker = "manual-buy-amount-overlay.js"
    if marker in html:
        return html
    snippet = '<script src="/manual-buy-amount-overlay.js?v=manual_buy_amount_v1"></script>\n'
    if "</body>" in html:
        return html.replace("</body>", snippet + "</body>")
    return html + snippet


def install_manual_buy_amount_routes(app, base_module: Any) -> None:
    """Install dashboard amount input JS and enhanced manual BUY intent route."""
    app.router.routes = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", "") == "/api/live/manual_buy_intent"
            and "POST" in set(getattr(route, "methods", set()) or set())
        )
    ]

    @app.get("/manual-buy-amount-overlay.js", include_in_schema=False)
    def manual_buy_amount_overlay_js():
        return Response(content=MANUAL_BUY_AMOUNT_OVERLAY_JS, media_type="application/javascript; charset=utf-8")

    @app.get("/api/live/manual_buy_intents")
    def manual_buy_intents_amount_compat():
        return read_json(base_module.MANUAL_BUY_INTENT_PATH, {"schema_version": 1, "intents": {}})

    @app.post("/api/live/manual_buy_intent")
    def manual_buy_intent_amount(req: ManualBuyAmountIntentRequest):
        try:
            row = create_manual_buy_intent(
                candidate_id=req.candidate_id,
                source=req.source or "dashboard_amount",
                manual_notional=req.notional,
                candidate_path=base_module.CENTRAL_BUY_CANDIDATES_PATH,
                intent_path=base_module.MANUAL_BUY_INTENT_PATH,
            )
            return {"ok": True, "intent": row}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
