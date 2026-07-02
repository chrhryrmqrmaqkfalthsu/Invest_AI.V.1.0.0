"""FastAPI routes and injected dashboard UI for chart TP/SL exit plans."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from engine.live.chart_exit_plan import (
    disable_chart_exit_plan,
    evaluate_chart_exit_plans,
    load_chart_exit_state,
    upsert_chart_exit_plan,
)


class ChartExitPlanRequest(BaseModel):
    ticker: str
    take_profit_price: float | None = None
    stop_loss_price: float | None = None
    enabled: bool = True
    source: str = "dashboard_chart"


CHART_EXIT_OVERLAY_JS = r"""
(function(){
  const PLAN_API = (typeof API !== 'undefined' ? API : window.location.origin);
  const state = {plans:{}, clickMode:null, lines:[], lastTicker:null, lastInterval:null, evalBusy:false};
  const css = `
  .chart-exit-panel{background:#0b1019;border:1px solid var(--line);border-radius:10px;margin:8px 0 12px;padding:10px;position:relative;z-index:8;}
  .chart-exit-head{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:8px;font-size:12px;color:var(--dim);}
  .chart-exit-head b{color:#eaf1ff;}
  .chart-exit-grid{display:grid;grid-template-columns:1fr 1fr auto auto auto;gap:7px;align-items:center;}
  .chart-exit-grid input{width:100%;background:#090d14;border:1px solid var(--line);color:var(--txt);border-radius:7px;padding:7px 8px;font-size:12px;font-variant-numeric:tabular-nums;}
  .chart-exit-btn{border:1px solid var(--line);background:#121a28;color:var(--txt);border-radius:7px;padding:7px 10px;cursor:pointer;font-size:12px;font-weight:800;white-space:nowrap;}
  .chart-exit-btn:hover{border-color:var(--accent);}
  .chart-exit-btn.green{background:rgba(38,208,124,.12);color:var(--up);border-color:rgba(38,208,124,.35);}
  .chart-exit-btn.red{background:rgba(255,77,106,.12);color:var(--down);border-color:rgba(255,77,106,.35);}
  .chart-exit-btn.active{box-shadow:0 0 0 2px rgba(59,130,246,.35) inset;border-color:var(--accent);}
  .chart-exit-sub{font-size:11px;color:var(--dim);margin-top:7px;line-height:1.45;}
  .chart-exit-sub .ok{color:var(--up);font-weight:800}.chart-exit-sub .bad{color:var(--down);font-weight:800}.chart-exit-sub .warn{color:var(--gold);font-weight:800}
  .km-chart-exit-overlay{position:absolute;inset:0;pointer-events:none;z-index:2;overflow:hidden;border-radius:8px;}
  .km-exit-zone{position:absolute;left:0;right:0;display:flex;align-items:center;justify-content:flex-end;padding-right:12px;font-size:11px;font-weight:900;letter-spacing:.4px;text-shadow:0 1px 2px rgba(0,0,0,.6);}
  .km-exit-tp{background:rgba(38,208,124,.13);border-bottom:1px solid rgba(38,208,124,.36);color:rgba(150,255,200,.95);}
  .km-exit-sl{background:rgba(255,77,106,.13);border-top:1px solid rgba(255,77,106,.36);color:rgba(255,175,190,.95);}
  @media(max-width:900px){.chart-exit-grid{grid-template-columns:1fr 1fr}.chart-exit-btn{width:100%;}}
  `;
  function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function n(v){const x=Number(v);return isFinite(x)?x:null;}
  function price(v){const x=n(v);return x==null?'—':'$'+x.toFixed(2);}
  function toast2(t,b,type){try{ if(typeof toast==='function') toast(t,b,type); else console.log(t,b); }catch(e){console.log(t,b)}}
  function injectCss(){if(document.getElementById('chart-exit-style'))return;const el=document.createElement('style');el.id='chart-exit-style';el.textContent=css;document.head.appendChild(el);}
  async function loadPlans(){try{const r=await fetch(`${PLAN_API}/api/live/chart_exit_plans`);const d=await r.json();state.plans=(d.plans||{});return d;}catch(e){return {plans:{}}}}
  function activePlan(ticker){return state.plans[String(ticker||'').toUpperCase()]||null;}
  function currentSlot(ticker){try{return (slotData||[]).find(x=>String(x.ticker||'').toUpperCase()===String(ticker||'').toUpperCase())||null;}catch(e){return null}}
  function ensurePanel(ticker){
    injectCss();
    const chartEl=document.getElementById('chart'); if(!chartEl)return null;
    let panel=document.getElementById('chart-exit-panel');
    if(!panel){
      panel=document.createElement('div');panel.id='chart-exit-panel';panel.className='chart-exit-panel';
      const strip=document.getElementById('sellomen-strip');
      if(strip&&strip.parentElement) strip.parentElement.insertBefore(panel, chartEl); else chartEl.parentElement.insertBefore(panel, chartEl);
    }
    renderPanel(ticker);
    return panel;
  }
  function renderPanel(ticker){
    const tk=String(ticker||'').toUpperCase();
    const p=activePlan(tk)||{};
    const s=currentSlot(tk)||{};
    const st=p.status||'none';
    const enabled=!!p.enabled && st==='active';
    const tp=p.take_profit_price ?? '';
    const sl=p.stop_loss_price ?? '';
    let status='미설정', cls='warn';
    if(enabled){status='감시중';cls='ok'}
    else if(st==='triggered'){status=`트리거됨 · ${p.trigger_kind||''} @ ${price(p.triggered_price)}`;cls='bad'}
    else if(st==='disabled'){status='비활성';cls='warn'}
    else if(st==='orphaned'){status='보유 없음';cls='bad'}
    const panel=document.getElementById('chart-exit-panel'); if(!panel)return;
    panel.innerHTML=`
      <div class="chart-exit-head"><b>🎯 차트 가상 TP/SL 자동청산</b><span class="${cls}">${esc(status)}</span></div>
      <div class="chart-exit-grid">
        <input id="chart-exit-tp" type="number" step="0.01" placeholder="익절가 TP" value="${esc(tp)}">
        <input id="chart-exit-sl" type="number" step="0.01" placeholder="손절가 SL" value="${esc(sl)}">
        <button id="chart-exit-click-tp" class="chart-exit-btn green">차트클릭 익절</button>
        <button id="chart-exit-click-sl" class="chart-exit-btn red">차트클릭 손절</button>
        <button id="chart-exit-save" class="chart-exit-btn">저장/감시</button>
        <button id="chart-exit-clear" class="chart-exit-btn">비활성</button>
      </div>
      <div class="chart-exit-sub">현재 ${price(s.current_price)} · 진입 ${price(s.entry_price)} · 실제 매도는 <b>정규장</b>에서만 runner가 실행. 차트 클릭 버튼을 누른 뒤 차트 가격 위치를 클릭하면 입력칸에 반영된다.</div>`;
    const tpBtn=document.getElementById('chart-exit-click-tp'), slBtn=document.getElementById('chart-exit-click-sl');
    if(state.clickMode==='tp')tpBtn.classList.add('active');
    if(state.clickMode==='sl')slBtn.classList.add('active');
    tpBtn.onclick=()=>{state.clickMode=state.clickMode==='tp'?null:'tp';renderPanel(tk);};
    slBtn.onclick=()=>{state.clickMode=state.clickMode==='sl'?null:'sl';renderPanel(tk);};
    document.getElementById('chart-exit-save').onclick=()=>savePlan(tk);
    document.getElementById('chart-exit-clear').onclick=()=>disablePlan(tk);
  }
  async function savePlan(ticker){
    const tp=n(document.getElementById('chart-exit-tp')?.value), sl=n(document.getElementById('chart-exit-sl')?.value);
    if(tp==null&&sl==null){toast2('TP/SL 필요','익절가 또는 손절가 중 하나 이상 입력','warn');return;}
    if(tp!=null&&sl!=null&&tp<=sl){toast2('가격 오류','익절가는 손절가보다 커야 함','warn');return;}
    try{
      const r=await fetch(`${PLAN_API}/api/live/chart_exit_plan`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker,take_profit_price:tp,stop_loss_price:sl,enabled:true,source:'dashboard_chart'})});
      const d=await r.json(); if(!r.ok)throw new Error(d.detail||`HTTP ${r.status}`);
      state.plans[ticker]=d.plan; toast2('차트 TP/SL 저장',`${ticker} · TP ${price(tp)} · SL ${price(sl)}`,'good');
      renderPanel(ticker); drawPlan(ticker); if(d.evaluation&&d.evaluation.triggered&&d.evaluation.triggered.length)toast2('자동청산 트리거',`${ticker} 기준 도달 · 매도 intent 생성`,'warn');
    }catch(e){toast2('저장 실패',String(e.message||e),'warn')}
  }
  async function disablePlan(ticker){
    try{
      const r=await fetch(`${PLAN_API}/api/live/chart_exit_plan/${encodeURIComponent(ticker)}`,{method:'DELETE'});const d=await r.json();if(!r.ok)throw new Error(d.detail||`HTTP ${r.status}`);
      state.plans[ticker]=d.plan;toast2('차트 TP/SL 비활성',ticker,'good');renderPanel(ticker);drawPlan(ticker);
    }catch(e){toast2('비활성 실패',String(e.message||e),'warn')}
  }
  function clearLines(){try{state.lines.forEach(l=>series.removePriceLine(l));}catch(e){}state.lines=[];}
  function ensureOverlay(){
    const chartEl=document.getElementById('chart');if(!chartEl)return null;
    let ov=document.getElementById('km-chart-exit-overlay');
    if(!ov){ov=document.createElement('div');ov.id='km-chart-exit-overlay';ov.className='km-chart-exit-overlay';chartEl.appendChild(ov);}
    return ov;
  }
  function drawZones(plan){
    const ov=ensureOverlay();if(!ov)return;ov.innerHTML='';
    if(!plan||plan.status!=='active'||!plan.enabled)return;
    const h=document.getElementById('chart')?.clientHeight||0;
    const tp=n(plan.take_profit_price), sl=n(plan.stop_loss_price);
    try{
      if(tp!=null){const y=series.priceToCoordinate(tp);if(y!=null&&isFinite(y)){const z=document.createElement('div');z.className='km-exit-zone km-exit-tp';z.style.top='0px';z.style.height=Math.max(0,Math.min(h,y))+'px';z.textContent='익절 영역';ov.appendChild(z);}}
      if(sl!=null){const y=series.priceToCoordinate(sl);if(y!=null&&isFinite(y)){const z=document.createElement('div');z.className='km-exit-zone km-exit-sl';z.style.top=Math.max(0,Math.min(h,y))+'px';z.style.height=Math.max(0,h-Math.max(0,Math.min(h,y)))+'px';z.textContent='손절 영역';ov.appendChild(z);}}
    }catch(e){}
  }
  function drawPlan(ticker){
    clearLines(); const tk=String(ticker||state.lastTicker||'').toUpperCase(); const p=activePlan(tk); const ov=ensureOverlay(); if(ov)ov.innerHTML='';
    if(!p||p.status!=='active'||!p.enabled)return;
    const tp=n(p.take_profit_price), sl=n(p.stop_loss_price);
    try{
      if(tp!=null)state.lines.push(series.createPriceLine({price:tp,color:'#26d07c',lineWidth:2,lineStyle:2,axisLabelVisible:true,title:'내 익절'}));
      if(sl!=null)state.lines.push(series.createPriceLine({price:sl,color:'#ff4d6a',lineWidth:2,lineStyle:2,axisLabelVisible:true,title:'내 손절'}));
    }catch(e){}
    setTimeout(()=>drawZones(p),30);
  }
  async function afterChartDraw(ticker){state.lastTicker=String(ticker||'').toUpperCase();ensurePanel(state.lastTicker);await loadPlans();renderPanel(state.lastTicker);drawPlan(state.lastTicker);}
  function installClick(){
    try{chart.subscribeClick(param=>{
      if(!state.clickMode||!param||!param.point)return;
      const py=n(series.coordinateToPrice(param.point.y)); if(py==null)return;
      const id=state.clickMode==='tp'?'chart-exit-tp':'chart-exit-sl'; const input=document.getElementById(id); if(input)input.value=py.toFixed(2);
      state.clickMode=null; renderPanel(state.lastTicker); drawPlan(state.lastTicker);
    });}catch(e){}
  }
  async function evaluateLoop(){
    if(state.evalBusy)return; state.evalBusy=true;
    try{
      const r=await fetch(`${PLAN_API}/api/live/chart_exit_plans/evaluate`,{method:'POST'});const d=await r.json();
      if(d&&Array.isArray(d.triggered)&&d.triggered.length){await loadPlans();if(state.lastTicker){renderPanel(state.lastTicker);drawPlan(state.lastTicker);}d.triggered.forEach(x=>toast2('차트 자동청산',`${x.ticker} ${x.trigger_kind} @ ${price(x.trigger_price)}`,'warn'));}
    }catch(e){} finally{state.evalBusy=false;}
  }
  function patch(){
    injectCss(); installClick();
    const od=window.drawChart; if(typeof od==='function'&&!od.__chartExitPatched){
      const patched=async function(ticker,interval,opts){const r=await od.apply(this,arguments);try{await afterChartDraw(ticker);}catch(e){}return r;}; patched.__chartExitPatched=true; window.drawChart=patched;
    }
    const orp=window.refreshDetailPanel; if(typeof orp==='function'&&!orp.__chartExitPatched){
      const patched=function(ticker){const r=orp.apply(this,arguments);try{ensurePanel(ticker);drawPlan(ticker);}catch(e){}return r;}; patched.__chartExitPatched=true; window.refreshDetailPanel=patched;
    }
    loadPlans().then(()=>{if(typeof curTicker!=='undefined'&&curTicker)afterChartDraw(curTicker);});
    setInterval(evaluateLoop,10000);
    setTimeout(evaluateLoop,1500);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',patch);else patch();
})();
"""


def _inject_dashboard_script(html: str) -> str:
    marker = "chart-exit-overlay.js"
    if marker in html:
        return html
    snippet = '<script src="/chart-exit-overlay.js?v=chart_exit_v1"></script>\n'
    if "</body>" in html:
        return html.replace("</body>", snippet + "</body>")
    return html + snippet


def install_chart_exit_routes(app, base_module: Any, *, price_lookup, wake_runner) -> None:
    """Install chart-exit API routes and dashboard JS injection."""

    def _wake_triggered(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
        wakes: list[dict[str, Any]] = []
        for row in evaluation.get("triggered") or []:
            if not isinstance(row, dict):
                continue
            intent = row.get("intent") if isinstance(row.get("intent"), dict) else {}
            if intent:
                try:
                    wakes.append(wake_runner(intent))
                except Exception as exc:
                    wakes.append({"ok": False, "reason": type(exc).__name__, "message": str(exc)})
        return wakes

    @app.get("/chart-exit-overlay.js", include_in_schema=False)
    def chart_exit_overlay_js():
        return Response(content=CHART_EXIT_OVERLAY_JS, media_type="application/javascript; charset=utf-8")

    @app.get("/api/live/chart_exit_plans")
    def chart_exit_plans():
        return load_chart_exit_state()

    @app.post("/api/live/chart_exit_plan")
    def chart_exit_plan(req: ChartExitPlanRequest):
        try:
            plan = upsert_chart_exit_plan(
                ticker=req.ticker,
                take_profit_price=req.take_profit_price,
                stop_loss_price=req.stop_loss_price,
                enabled=req.enabled,
                source=req.source or "dashboard_chart",
                positions_path=base_module.MANUAL_SELL_POSITIONS_PATH,
            )
            evaluation = evaluate_chart_exit_plans(
                price_lookup=price_lookup,
                positions_path=base_module.MANUAL_SELL_POSITIONS_PATH,
                intent_path=base_module.MANUAL_SELL_INTENT_PATH,
                source="chart_exit_plan",
            )
            wakes = _wake_triggered(evaluation)
            return {"ok": True, "plan": plan, "evaluation": {k: v for k, v in evaluation.items() if k != "state"}, "runner_wake": wakes}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/api/live/chart_exit_plan/{ticker}")
    def chart_exit_plan_delete(ticker: str):
        try:
            plan = disable_chart_exit_plan(ticker=ticker)
            return {"ok": True, "plan": plan}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/live/chart_exit_plans/evaluate")
    def chart_exit_plan_evaluate():
        evaluation = evaluate_chart_exit_plans(
            price_lookup=price_lookup,
            positions_path=base_module.MANUAL_SELL_POSITIONS_PATH,
            intent_path=base_module.MANUAL_SELL_INTENT_PATH,
            source="chart_exit_plan",
        )
        wakes = _wake_triggered(evaluation)
        return {**{k: v for k, v in evaluation.items() if k != "state"}, "runner_wake": wakes}

    def _dashboard_html_with_chart_exit():
        response = base_module._dashboard_html_response()
        body = getattr(response, "body", b"")
        html = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
        return HTMLResponse(content=_inject_dashboard_script(html), media_type="text/html")

    target_paths = {"/dashboard", "/dashboard_home.html"}
    app.router.routes = [
        route
        for route in app.router.routes
        if not (getattr(route, "path", "") in target_paths and "GET" in set(getattr(route, "methods", set()) or set()))
    ]

    @app.get("/dashboard", include_in_schema=False)
    def dashboard_chart_exit():
        return _dashboard_html_with_chart_exit()

    @app.get("/dashboard_home.html", include_in_schema=False)
    def dashboard_home_chart_exit():
        return _dashboard_html_with_chart_exit()
