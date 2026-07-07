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
    take_profit_pct: float | None = None
    stop_loss_pct: float | None = None
    enabled: bool = True
    source: str = "dashboard_chart"


CHART_EXIT_OVERLAY_JS = r"""
(function(){
  const PLAN_API = (typeof API !== 'undefined' ? API : window.location.origin);
  const state = {plans:{}, clickMode:null, lines:[], lastTicker:null, lastInterval:null, evalBusy:false, candles:[], rangeHooked:false, inputBasis:{}};
  const css = `
  .chart-exit-panel{background:#0b1019;border:1px solid var(--line);border-radius:10px;margin:8px 0 12px;padding:10px;position:relative;z-index:8;}
  .chart-exit-head{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:8px;font-size:12px;color:var(--dim);}
  .chart-exit-head b{color:#eaf1ff;}
  .chart-exit-grid{display:grid;grid-template-columns:repeat(4,minmax(86px,1fr)) auto auto auto;gap:7px;align-items:center;}
  .chart-exit-grid input{width:100%;background:#090d14;border:1px solid var(--line);color:var(--txt);border-radius:7px;padding:7px 8px;font-size:12px;font-variant-numeric:tabular-nums;}
  .chart-exit-grid input.active-basis{border-color:var(--accent);box-shadow:0 0 0 1px rgba(59,130,246,.35) inset;}
  .chart-exit-btn{border:1px solid var(--line);background:#121a28;color:var(--txt);border-radius:7px;padding:7px 10px;cursor:pointer;font-size:12px;font-weight:800;white-space:nowrap;}
  .chart-exit-btn:hover{border-color:var(--accent);}
  .chart-exit-btn.green{background:rgba(38,208,124,.12);color:var(--up);border-color:rgba(38,208,124,.35);}
  .chart-exit-btn.red{background:rgba(255,77,106,.12);color:var(--down);border-color:rgba(255,77,106,.35);}
  .chart-exit-btn.active{box-shadow:0 0 0 2px rgba(59,130,246,.35) inset;border-color:var(--accent);}
  .chart-exit-sub{font-size:11px;color:var(--dim);margin-top:7px;line-height:1.45;}
  .chart-exit-sub .ok{color:var(--up);font-weight:800}.chart-exit-sub .bad{color:var(--down);font-weight:800}.chart-exit-sub .warn{color:var(--gold);font-weight:800}
  .km-chart-exit-overlay{position:absolute;inset:0;pointer-events:none;z-index:2;overflow:hidden;border-radius:8px;}
  .km-exit-zone{position:absolute;display:flex;align-items:center;justify-content:flex-end;padding-right:12px;font-size:11px;font-weight:900;letter-spacing:.4px;text-shadow:0 1px 2px rgba(0,0,0,.6);min-height:8px;}
  .km-exit-tp{background:rgba(38,208,124,.15);border:1px solid rgba(38,208,124,.34);color:rgba(150,255,200,.96);}
  .km-exit-sl{background:rgba(255,77,106,.15);border:1px solid rgba(255,77,106,.34);color:rgba(255,175,190,.96);}
  @media(max-width:1100px){.chart-exit-grid{grid-template-columns:1fr 1fr 1fr 1fr}.chart-exit-btn{width:100%;}}
  @media(max-width:700px){.chart-exit-grid{grid-template-columns:1fr 1fr}.chart-exit-btn{width:100%;}}
  `;
  function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function n(v){if(v==null||v==='')return null;const x=Number(v);return isFinite(x)?x:null;}
  function price(v){const x=n(v);return x==null?'—':'$'+x.toFixed(2);}
  function pct(v){const x=n(v);return x==null?'—':x.toFixed(2)+'%';}
  function clamp(x,a,b){return Math.max(a,Math.min(b,x));}
  function toast2(t,b,type){try{ if(typeof toast==='function') toast(t,b,type); else console.log(t,b); }catch(e){console.log(t,b)}}
  function injectCss(){if(document.getElementById('chart-exit-style'))return;const el=document.createElement('style');el.id='chart-exit-style';el.textContent=css;document.head.appendChild(el);}
  async function loadPlans(){try{const r=await fetch(`${PLAN_API}/api/live/chart_exit_plans`);const d=await r.json();state.plans=(d.plans||{});return d;}catch(e){return {plans:{}}}}
  function activePlan(ticker){return state.plans[String(ticker||'').toUpperCase()]||null;}
  function currentSlot(ticker){try{return (slotData||[]).find(x=>String(x.ticker||'').toUpperCase()===String(ticker||'').toUpperCase())||null;}catch(e){return null}}
  function basisFor(ticker){const tk=String(ticker||'').toUpperCase();state.inputBasis[tk]=state.inputBasis[tk]||{};return state.inputBasis[tk];}
  function setBasis(ticker, side, basis){basisFor(ticker)[side]=basis;}
  function displayField(plan, side, kind){
    if(!plan)return '';
    const basis=String(plan[`${side}_basis`]||'price');
    if(kind==='price')return basis==='pct'?'':(plan[`${side}_price`] ?? '');
    return basis==='pct'?(plan[`${side}_pct`] ?? ''):'';
  }
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
    const tp=displayField(p,'take_profit','price');
    const sl=displayField(p,'stop_loss','price');
    const tpPct=displayField(p,'take_profit','pct');
    const slPct=displayField(p,'stop_loss','pct');
    let status='미설정', cls='warn';
    if(enabled){status=`감시중 · TP ${price(p.take_profit_price)} / SL ${price(p.stop_loss_price)}`;cls='ok'}
    else if(st==='triggered'){status=`트리거됨 · ${p.trigger_kind||''} @ ${price(p.triggered_price)}`;cls='bad'}
    else if(st==='disabled'){status='비활성';cls='warn'}
    else if(st==='orphaned'){status='보유 없음';cls='bad'}
    const panel=document.getElementById('chart-exit-panel'); if(!panel)return;
    panel.innerHTML=`
      <div class="chart-exit-head"><b>🎯 차트 TP/SL 자동청산</b><span class="${cls}">${esc(status)}</span></div>
      <div class="chart-exit-grid">
        <input id="chart-exit-tp" type="number" step="0.01" placeholder="익절가 $" value="${esc(tp)}">
        <input id="chart-exit-tp-pct" type="number" step="0.01" placeholder="익절 %" value="${esc(tpPct)}">
        <input id="chart-exit-sl" type="number" step="0.01" placeholder="손절가 $" value="${esc(sl)}">
        <input id="chart-exit-sl-pct" type="number" step="0.01" placeholder="손절 %" value="${esc(slPct)}">
        <button id="chart-exit-click-tp" class="chart-exit-btn green">차트클릭 익절</button>
        <button id="chart-exit-click-sl" class="chart-exit-btn red">차트클릭 손절</button>
        <button id="chart-exit-save" class="chart-exit-btn">저장/감시</button>
        <button id="chart-exit-clear" class="chart-exit-btn">비활성</button>
      </div>
      <div class="chart-exit-sub">진입 ${price(s.entry_price)} · 현재 ${price(s.current_price)} · <b>마지막으로 수정한 칸이 우선</b>. %를 쓰면 가격칸은 자동 무시되고, 가격을 쓰면 %칸은 자동 무시된다. 저장 후에도 입력한 %가 다른 숫자로 바뀌지 않게 표시한다.</div>`;
    const tpInput=document.getElementById('chart-exit-tp'), tpPctInput=document.getElementById('chart-exit-tp-pct'), slInput=document.getElementById('chart-exit-sl'), slPctInput=document.getElementById('chart-exit-sl-pct');
    const b=basisFor(tk);
    if(!b.tp)b.tp=String(p.take_profit_basis||'price')==='pct'?'pct':'price';
    if(!b.sl)b.sl=String(p.stop_loss_basis||'price')==='pct'?'pct':'price';
    function markBasis(){
      [tpInput,tpPctInput,slInput,slPctInput].forEach(x=>x&&x.classList.remove('active-basis'));
      if(b.tp==='pct')tpPctInput&&tpPctInput.classList.add('active-basis'); else tpInput&&tpInput.classList.add('active-basis');
      if(b.sl==='pct')slPctInput&&slPctInput.classList.add('active-basis'); else slInput&&slInput.classList.add('active-basis');
    }
    if(tpInput)tpInput.oninput=()=>{setBasis(tk,'tp','price'); if(tpPctInput)tpPctInput.value=''; markBasis();};
    if(tpPctInput)tpPctInput.oninput=()=>{setBasis(tk,'tp','pct'); if(tpInput)tpInput.value=''; markBasis();};
    if(slInput)slInput.oninput=()=>{setBasis(tk,'sl','price'); if(slPctInput)slPctInput.value=''; markBasis();};
    if(slPctInput)slPctInput.oninput=()=>{setBasis(tk,'sl','pct'); if(slInput)slInput.value=''; markBasis();};
    markBasis();
    const tpBtn=document.getElementById('chart-exit-click-tp'), slBtn=document.getElementById('chart-exit-click-sl');
    if(state.clickMode==='tp')tpBtn.classList.add('active');
    if(state.clickMode==='sl')slBtn.classList.add('active');
    tpBtn.onclick=()=>{state.clickMode=state.clickMode==='tp'?null:'tp';renderPanel(tk);};
    slBtn.onclick=()=>{state.clickMode=state.clickMode==='sl'?null:'sl';renderPanel(tk);};
    document.getElementById('chart-exit-save').onclick=()=>savePlan(tk);
    document.getElementById('chart-exit-clear').onclick=()=>disablePlan(tk);
  }
  function resolveInputs(ticker){
    const s=currentSlot(ticker)||{};
    const entry=n(s.entry_price);
    const b=basisFor(ticker);
    let tpInput=n(document.getElementById('chart-exit-tp')?.value);
    let tpPctInput=n(document.getElementById('chart-exit-tp-pct')?.value);
    let slInput=n(document.getElementById('chart-exit-sl')?.value);
    let slPctInput=n(document.getElementById('chart-exit-sl-pct')?.value);
    if(b.tp==='pct'){tpInput=null;} else {tpPctInput=null;}
    if(b.sl==='pct'){slInput=null;} else {slPctInput=null;}
    let tp=tpInput, sl=slInput;
    if(tpPctInput!=null&&entry!=null)tp=entry*(1+tpPctInput/100);
    if(slPctInput!=null&&entry!=null)sl=entry*(1-Math.abs(slPctInput)/100);
    return {entry,tp,sl,tpInput,tpPctInput,slInput,slPctInput,tpBasis:b.tp||'price',slBasis:b.sl||'price'};
  }
  async function savePlan(ticker){
    const r0=resolveInputs(ticker);
    if(r0.tp==null&&r0.sl==null){toast2('TP/SL 필요','익절가·익절%·손절가·손절% 중 하나 이상 입력','warn');return;}
    if(r0.entry==null){toast2('진입가 없음','퍼센트 계산을 위해 진입가가 필요합니다','warn');return;}
    if(r0.tp!=null&&r0.tp<=r0.entry){toast2('익절 기준 오류','익절가는 진입가보다 위여야 합니다','warn');return;}
    if(r0.sl!=null&&r0.sl>=r0.entry){toast2('손절 기준 오류','손절가는 진입가보다 아래여야 합니다','warn');return;}
    if(r0.tp!=null&&r0.sl!=null&&r0.tp<=r0.sl){toast2('가격 오류','익절가는 손절가보다 커야 함','warn');return;}
    try{
      const body={ticker,take_profit_price:r0.tpInput,stop_loss_price:r0.slInput,take_profit_pct:r0.tpPctInput,stop_loss_pct:r0.slPctInput,enabled:true,source:'dashboard_chart'};
      const r=await fetch(`${PLAN_API}/api/live/chart_exit_plan`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const d=await r.json(); if(!r.ok)throw new Error(d.detail||`HTTP ${r.status}`);
      state.plans[ticker]=d.plan;
      const savedBasis=basisFor(ticker); savedBasis.tp=String(d.plan.take_profit_basis||r0.tpBasis||'price')==='pct'?'pct':'price'; savedBasis.sl=String(d.plan.stop_loss_basis||r0.slBasis||'price')==='pct'?'pct':'price';
      toast2('차트 TP/SL 저장',`${ticker} · TP ${price(d.plan.take_profit_price)}(${pct(d.plan.take_profit_pct)}) · SL ${price(d.plan.stop_loss_price)}(${pct(d.plan.stop_loss_pct)})`,'good');
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
  function parseEntryEpoch(value){
    if(!value)return null;
    const ms=Date.parse(value);
    return isFinite(ms)?Math.floor(ms/1000):null;
  }
  function candleKeyAtEntry(plan){
    const s=currentSlot(plan.ticker)||{};
    const raw=s.entry_date||plan.position_entry_date||'';
    const entryEpoch=parseEntryEpoch(raw);
    if(!entryEpoch)return null;
    const rows=Array.isArray(state.candles)?state.candles:[];
    if(!rows.length){
      if(String(state.lastInterval||'')==='1d')return new Date(entryEpoch*1000).toISOString().slice(0,10);
      return entryEpoch;
    }
    if(typeof rows[0].time==='string'){
      const day=new Date(entryEpoch*1000).toISOString().slice(0,10);
      for(const c of rows){if(String(c.time)>=day)return c.time;}
      return rows[rows.length-1].time;
    }
    for(const c of rows){if(Number(c.time)>=entryEpoch)return c.time;}
    return rows[rows.length-1].time;
  }
  function entryX(plan){
    const chartEl=document.getElementById('chart');const w=chartEl?.clientWidth||0;
    const t=candleKeyAtEntry(plan);
    if(t==null)return 0;
    let x=null;
    try{x=chart.timeScale().timeToCoordinate(t);}catch(e){x=null;}
    if(x==null||!isFinite(x)){
      try{
        const rows=Array.isArray(state.candles)?state.candles:[];
        const idx=rows.findIndex(c=>String(c.time)===String(t));
        const lr=chart.timeScale().getVisibleLogicalRange&&chart.timeScale().getVisibleLogicalRange();
        if(idx>=0&&lr&&isFinite(lr.from)&&isFinite(lr.to)){x=(idx-lr.from)/(lr.to-lr.from)*w;}
      }catch(e){}
    }
    if(x==null||!isFinite(x))return 0;
    return clamp(x,0,w);
  }
  function zoneDiv(cls,left,top,width,height,label){
    if(width<=2||height<=2)return null;
    const z=document.createElement('div');z.className=`km-exit-zone ${cls}`;z.style.left=left+'px';z.style.width=width+'px';z.style.top=top+'px';z.style.height=height+'px';z.textContent=label;return z;
  }
  function drawZones(plan){
    const ov=ensureOverlay();if(!ov)return;ov.innerHTML='';
    if(!plan||plan.status!=='active'||!plan.enabled)return;
    const chartEl=document.getElementById('chart');const h=chartEl?.clientHeight||0,w=chartEl?.clientWidth||0;
    const entry=n(plan.entry_price)||(currentSlot(plan.ticker)||{}).entry_price;
    const entryF=n(entry), tp=n(plan.take_profit_price), sl=n(plan.stop_loss_price);
    if(entryF==null)return;
    let yEntry=null;try{yEntry=series.priceToCoordinate(entryF);}catch(e){}
    if(yEntry==null||!isFinite(yEntry))return;
    const left=entryX(plan); const width=Math.max(0,w-left);
    try{
      if(tp!=null){const yTp=series.priceToCoordinate(tp);if(yTp!=null&&isFinite(yTp)){const top=clamp(Math.min(yTp,yEntry),0,h);const bottom=clamp(Math.max(yTp,yEntry),0,h);const z=zoneDiv('km-exit-tp',left,top,width,Math.max(0,bottom-top),`익절 ${pct(plan.take_profit_pct)}`);if(z)ov.appendChild(z);}}
      if(sl!=null){const ySl=series.priceToCoordinate(sl);if(ySl!=null&&isFinite(ySl)){const top=clamp(Math.min(ySl,yEntry),0,h);const bottom=clamp(Math.max(ySl,yEntry),0,h);const z=zoneDiv('km-exit-sl',left,top,width,Math.max(0,bottom-top),`손절 ${pct(plan.stop_loss_pct)}`);if(z)ov.appendChild(z);}}
    }catch(e){}
  }
  function drawPlan(ticker){
    clearLines(); const tk=String(ticker||state.lastTicker||'').toUpperCase(); const p=activePlan(tk); const ov=ensureOverlay(); if(ov)ov.innerHTML='';
    if(!p||p.status!=='active'||!p.enabled)return;
    const tp=n(p.take_profit_price), sl=n(p.stop_loss_price);
    try{
      if(tp!=null)state.lines.push(series.createPriceLine({price:tp,color:'#26d07c',lineWidth:2,lineStyle:2,axisLabelVisible:true,title:`내 익절 ${pct(p.take_profit_pct)}`}));
      if(sl!=null)state.lines.push(series.createPriceLine({price:sl,color:'#ff4d6a',lineWidth:2,lineStyle:2,axisLabelVisible:true,title:`내 손절 ${pct(p.stop_loss_pct)}`}));
    }catch(e){}
    setTimeout(()=>drawZones(p),40);
  }
  async function refreshCandles(ticker, interval){
    try{
      const r=await fetch(`${PLAN_API}/api/live/candles/${ticker}?interval=${interval||'1d'}`);
      const d=await r.json();
      state.candles=Array.isArray(d)?d:[];
    }catch(e){state.candles=[];}
  }
  async function afterChartDraw(ticker, interval){
    state.lastTicker=String(ticker||'').toUpperCase();state.lastInterval=interval||state.lastInterval||'1d';
    ensurePanel(state.lastTicker);
    await Promise.all([loadPlans(), refreshCandles(state.lastTicker,state.lastInterval)]);
    renderPanel(state.lastTicker);drawPlan(state.lastTicker);
  }
  function installClick(){
    try{chart.subscribeClick(param=>{
      if(!state.clickMode||!param||!param.point)return;
      const py=n(series.coordinateToPrice(param.point.y)); if(py==null)return;
      const id=state.clickMode==='tp'?'chart-exit-tp':'chart-exit-sl'; const input=document.getElementById(id); if(input)input.value=py.toFixed(2);
      if(state.lastTicker){setBasis(state.lastTicker,state.clickMode==='tp'?'tp':'sl','price');const pctId=state.clickMode==='tp'?'chart-exit-tp-pct':'chart-exit-sl-pct';const pctInput=document.getElementById(pctId);if(pctInput)pctInput.value='';}
      state.clickMode=null; renderPanel(state.lastTicker); drawPlan(state.lastTicker);
    });}catch(e){}
  }
  function installRangeHook(){
    if(state.rangeHooked)return; state.rangeHooked=true;
    try{chart.timeScale().subscribeVisibleLogicalRangeChange(()=>{const p=activePlan(state.lastTicker);if(p)setTimeout(()=>drawZones(p),30);});}catch(e){}
    window.addEventListener('resize',()=>{const p=activePlan(state.lastTicker);if(p)setTimeout(()=>drawZones(p),80);});
  }
  async function evaluateLoop(){
    if(state.evalBusy)return; state.evalBusy=true;
    try{
      const r=await fetch(`${PLAN_API}/api/live/chart_exit_plans/evaluate`,{method:'POST'});const d=await r.json();
      if(d&&Array.isArray(d.triggered)&&d.triggered.length){await loadPlans();if(state.lastTicker){renderPanel(state.lastTicker);drawPlan(state.lastTicker);}d.triggered.forEach(x=>toast2('차트 자동청산',`${x.ticker} ${x.trigger_kind} @ ${price(x.trigger_price)}`,'warn'));}
    }catch(e){} finally{state.evalBusy=false;}
  }
  function patch(){
    injectCss(); installClick(); installRangeHook();
    const od=window.drawChart; if(typeof od==='function'&&!od.__chartExitPatched){
      const patched=async function(ticker,interval,opts){const r=await od.apply(this,arguments);try{await afterChartDraw(ticker,interval);}catch(e){}return r;}; patched.__chartExitPatched=true; window.drawChart=patched;
    }
    const orp=window.refreshDetailPanel; if(typeof orp==='function'&&!orp.__chartExitPatched){
      const patched=function(ticker){const r=orp.apply(this,arguments);try{ensurePanel(ticker);drawPlan(ticker);}catch(e){}return r;}; patched.__chartExitPatched=true; window.refreshDetailPanel=patched;
    }
    loadPlans().then(()=>{if(typeof curTicker!=='undefined'&&curTicker)afterChartDraw(curTicker,typeof curInterval!=='undefined'?curInterval:'1d');});
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
    snippet = '<script src="/chart-exit-overlay.js?v=chart_exit_v3"></script>\n'
    if "</body>" in html:
        return html.replace("</body>", snippet + "</body>")
    return html + snippet


def install_chart_exit_routes(app, base_module: Any, *, price_lookup, wake_runner) -> None:
    """Install chart-exit API routes and dashboard JS injection."""
    from engine.live.chart_exit_slot_display import install_slot_display_routes

    install_slot_display_routes(app, base_module)

    def _wake_triggered(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
        wakes: list[dict[str, Any]] = []
        for row in evaluation.get("triggered") or []:
            if not isinstance(row, dict):
                continue
            intent = row.get("intent") if isinstance(row.get("intent", {}), dict) else {}
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
                take_profit_pct=req.take_profit_pct,
                stop_loss_pct=req.stop_loss_pct,
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
