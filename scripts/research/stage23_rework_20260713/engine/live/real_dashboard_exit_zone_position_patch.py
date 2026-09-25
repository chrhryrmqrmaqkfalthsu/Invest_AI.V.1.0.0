"""Stabilize real-dashboard TP/SL zone overlays during chart zoom/pan.

The base dashboard draws the profit/stop areas as absolutely positioned DOM
blocks over the Lightweight Charts canvas.  It only recalculated them on
``timeScale`` range changes, so price-scale zooming, mouse-wheel timing, and
canvas re-layout could leave the zones offset until the next redraw.

This patch injects a later implementation of the zone renderer inside the same
real overlay IIFE.  The replacement:
- anchors DOM coordinates to the actual chart canvas/pane bounds,
- re-renders after animation frames so Lightweight Charts finishes its layout,
- listens to wheel/pointer/resize and runs a light active interval while the
  detail chart is open.
"""
from __future__ import annotations

from engine.live import real_dashboard_api as real_api

_INSTALLED = False
_ORIG_SLOT_OVERLAY_JS = None


_PATCH_MARKER = "window.__kmExitZonePositionPatch=true"


_EXIT_ZONE_PATCH_JS = r'''
  window.__kmExitZonePositionPatch=true;
  function kmPreviewExitPaneBox(){
    const wrap=document.getElementById('chart');
    if(!wrap) return {left:0,top:0,width:0,height:0};
    const wr=wrap.getBoundingClientRect ? wrap.getBoundingClientRect() : {left:0,top:0,width:wrap.clientWidth||0,height:wrap.clientHeight||0};
    let best=null, bestArea=0;
    try{
      const canvases=Array.from(wrap.querySelectorAll('canvas'));
      for(const c of canvases){
        const r=c.getBoundingClientRect();
        const area=(r.width||0)*(r.height||0);
        if(area>bestArea){ bestArea=area; best=r; }
      }
    }catch(e){}
    if(best && best.width>20 && best.height>20){
      return {
        left:Math.max(0,best.left-wr.left),
        top:Math.max(0,best.top-wr.top),
        width:Math.max(0,best.width),
        height:Math.max(0,best.height)
      };
    }
    let tsWidth=0;
    try{ if(chart && chart.timeScale && chart.timeScale().width) tsWidth=Number(chart.timeScale().width())||0; }catch(e){}
    return {left:0,top:0,width:tsWidth>20?tsWidth:(wrap.clientWidth||0),height:Math.max(0,(wrap.clientHeight||0)-24)};
  }
  function kmPreviewExitScheduleUpdate(delay){
    try{
      if(window._previewExitZoneRaf) cancelAnimationFrame(window._previewExitZoneRaf);
      const run=()=>{
        window._previewExitZoneRaf=requestAnimationFrame(()=>{
          window._previewExitZoneRaf=requestAnimationFrame(()=>renderPreviewExitZoneOverlay(window._previewExitZoneState));
        });
      };
      if(delay) setTimeout(run, delay); else run();
    }catch(e){ try{ setTimeout(()=>renderPreviewExitZoneOverlay(window._previewExitZoneState), delay||0); }catch(_e){} }
  }
  function clearPreviewExitZoneElements(){
    document.querySelectorAll('.preview-exit-zone,.preview-exit-zone-label,.preview-entry-xline').forEach(el=>el.remove());
  }
  function renderPreviewExitZoneOverlay(state){
    clearPreviewExitZoneElements();
    if(!state || typeof series==='undefined' || typeof chart==='undefined') return;
    if(typeof series.priceToCoordinate!=='function') return;
    const wrap=document.getElementById('chart');
    if(!wrap) return;
    const pane=kmPreviewExitPaneBox();
    if(!pane || pane.width<=2 || pane.height<=2) return;
    const {s, plan, entryTime}=state;
    const entry=num(s.entry_price), stop=num(plan.stop_loss_price), take=num(plan.take_profit_price);
    if(!(entry>0)) return;
    const clampY=y=>pane.top+Math.max(0,Math.min(pane.height,Number(y)));
    const entryYRaw=series.priceToCoordinate(entry);
    if(entryYRaw==null || !Number.isFinite(entryYRaw)) return;
    const entryY=clampY(entryYRaw);
    let xRaw=null;
    try{ if(entryTime!=null) xRaw=chart.timeScale().timeToCoordinate(entryTime); }catch(e){}
    if(xRaw==null || !Number.isFinite(xRaw)) return;
    const entryVisible = xRaw >= 0 && xRaw <= pane.width;
    if(xRaw > pane.width) return;
    const xStartRaw = xRaw < 0 ? 0 : xRaw;
    const xStart = pane.left + xStartRaw;
    const zoneW = Math.max(0, pane.width - xStartRaw);
    if(zoneW <= 1) return;
    try{ wrap.style.position='relative'; }catch(e){}
    if(entryVisible){
      const line=document.createElement('div');
      line.className='preview-entry-xline';
      line.style.cssText=`position:absolute;left:${pane.left+xRaw}px;top:${pane.top}px;height:${pane.height}px;width:2px;background:rgba(59,130,246,.78);box-shadow:0 0 10px rgba(59,130,246,.45);z-index:17;pointer-events:none;`;
      wrap.appendChild(line);
      const tag=document.createElement('div');
      tag.className='preview-exit-zone-label entry';
      tag.textContent='진입 봉';
      tag.style.cssText=`position:absolute;left:${Math.min(pane.left+pane.width-96,Math.max(pane.left+8,pane.left+xRaw+8))}px;top:${Math.max(pane.top+4,entryY-14)}px;z-index:18;pointer-events:none;border-radius:999px;padding:3px 8px;font-size:11px;font-weight:950;background:rgba(59,130,246,.15);border:1px solid rgba(59,130,246,.45);color:#bfdbfe;`;
      wrap.appendChild(tag);
    }
    const addZone=(kind, boundaryPrice, label)=>{
      const boundaryRaw=series.priceToCoordinate(boundaryPrice);
      if(boundaryRaw==null || !Number.isFinite(boundaryRaw)) return;
      const isProfit=kind==='profit';
      const boundaryY=clampY(boundaryRaw);
      const top=Math.max(pane.top, Math.min(boundaryY, entryY));
      const bottom=Math.min(pane.top+pane.height, Math.max(boundaryY, entryY));
      const height=Math.max(0,bottom-top);
      if(height<3) return;
      const zone=document.createElement('div');
      zone.className=`preview-exit-zone ${kind}`;
      zone.style.cssText=`position:absolute;left:${xStart}px;width:${zoneW}px;top:${top}px;height:${height}px;z-index:14;pointer-events:none;border-left:${entryVisible?'1px solid rgba(59,130,246,.30)':'0'};border-top:1px solid ${isProfit?'rgba(38,208,124,.48)':'rgba(255,77,106,.18)'};border-bottom:1px solid ${isProfit?'rgba(38,208,124,.18)':'rgba(255,77,106,.48)'};background:${isProfit?'rgba(38,208,124,.16)':'rgba(255,77,106,.16)'};`;
      wrap.appendChild(zone);
      const tag=document.createElement('div');
      tag.className=`preview-exit-zone-label ${kind}`;
      tag.textContent=label;
      tag.style.cssText=`position:absolute;right:${Math.max(10,(wrap.clientWidth||0)-(pane.left+pane.width)+10)}px;top:${Math.max(pane.top+4,top+Math.min(12,Math.max(2,height/2-8)))}px;z-index:18;pointer-events:none;border-radius:999px;padding:3px 8px;font-size:11px;font-weight:950;background:${isProfit?'rgba(38,208,124,.18)':'rgba(255,77,106,.18)'};border:1px solid ${isProfit?'rgba(38,208,124,.50)':'rgba(255,77,106,.50)'};color:${isProfit?'#86efac':'#fecdd3'};`;
      wrap.appendChild(tag);
    };
    if(plan.take_enabled && take>entry) addZone('profit', take, `익절 영역 · 진입 이후 +${((take/entry-1)*100).toFixed(2)}%`);
    if(plan.stop_enabled && stop>0 && stop<entry) addZone('stop', stop, `손절 영역 · 진입 이후 -${((1-stop/entry)*100).toFixed(2)}%`);
  }
  function clearPreviewExitLines(){
    try{
      if(window._previewExitLines && typeof series!=='undefined'){
        for(const line of window._previewExitLines){ try{ series.removePriceLine(line); }catch(e){} }
      }
    }catch(e){}
    window._previewExitLines=[];
    try{ if(window._previewExitZoneUnsub && typeof chart!=='undefined') chart.timeScale().unsubscribeVisibleLogicalRangeChange(window._previewExitZoneUnsub); }catch(e){}
    try{ if(window._previewExitZoneResize) window.removeEventListener('resize', window._previewExitZoneResize); }catch(e){}
    try{
      const wrap=document.getElementById('chart');
      if(wrap && window._previewExitZoneWheel) wrap.removeEventListener('wheel', window._previewExitZoneWheel);
      if(wrap && window._previewExitZonePointer) wrap.removeEventListener('pointermove', window._previewExitZonePointer);
      if(wrap && window._previewExitZonePointer) wrap.removeEventListener('pointerup', window._previewExitZonePointer);
    }catch(e){}
    try{ if(window._previewExitZoneInterval) clearInterval(window._previewExitZoneInterval); }catch(e){}
    try{ if(window._previewExitZoneRaf) cancelAnimationFrame(window._previewExitZoneRaf); }catch(e){}
    window._previewExitZoneUnsub=null;
    window._previewExitZoneResize=null;
    window._previewExitZoneWheel=null;
    window._previewExitZonePointer=null;
    window._previewExitZoneInterval=null;
    window._previewExitZoneRaf=null;
    window._previewExitZoneState=null;
    clearPreviewExitZoneElements();
  }
  async function drawPreviewExitZones(s){
    clearPreviewExitZoneElements();
    if(!s || typeof series==='undefined' || typeof chart==='undefined') return;
    const plan=previewPlanDefaults(s);
    const entryTime=await previewEntryChartTime(s);
    const state={s, plan, entryTime};
    window._previewExitZoneState=state;
    const update=()=>kmPreviewExitScheduleUpdate(0);
    try{ if(window._previewExitZoneUnsub) chart.timeScale().unsubscribeVisibleLogicalRangeChange(window._previewExitZoneUnsub); }catch(e){}
    try{ if(window._previewExitZoneResize) window.removeEventListener('resize', window._previewExitZoneResize); }catch(e){}
    try{
      const wrap=document.getElementById('chart');
      if(wrap && window._previewExitZoneWheel) wrap.removeEventListener('wheel', window._previewExitZoneWheel);
      if(wrap && window._previewExitZonePointer) wrap.removeEventListener('pointermove', window._previewExitZonePointer);
      if(wrap && window._previewExitZonePointer) wrap.removeEventListener('pointerup', window._previewExitZonePointer);
    }catch(e){}
    try{ if(window._previewExitZoneInterval) clearInterval(window._previewExitZoneInterval); }catch(e){}
    window._previewExitZoneUnsub=update;
    window._previewExitZoneResize=update;
    window._previewExitZoneWheel=()=>kmPreviewExitScheduleUpdate(20);
    window._previewExitZonePointer=()=>kmPreviewExitScheduleUpdate(20);
    try{ chart.timeScale().subscribeVisibleLogicalRangeChange(update); }catch(e){}
    try{ window.addEventListener('resize', update); }catch(e){}
    try{
      const wrap=document.getElementById('chart');
      if(wrap){
        wrap.addEventListener('wheel', window._previewExitZoneWheel, {passive:true});
        wrap.addEventListener('pointermove', window._previewExitZonePointer, {passive:true});
        wrap.addEventListener('pointerup', window._previewExitZonePointer, {passive:true});
      }
    }catch(e){}
    window._previewExitZoneInterval=setInterval(()=>{
      if(window._previewExitZoneState && document.getElementById('preview-exit-panel')) kmPreviewExitScheduleUpdate(0);
    }, 350);
    kmPreviewExitScheduleUpdate(0);
    kmPreviewExitScheduleUpdate(90);
    kmPreviewExitScheduleUpdate(220);
  }
'''


def _insert_inside_last_iife(js: str) -> str:
    if _PATCH_MARKER in js:
        return js
    close = js.rfind("})();")
    if close < 0:
        return js + "\n" + _EXIT_ZONE_PATCH_JS
    return js[:close] + _EXIT_ZONE_PATCH_JS + "\n" + js[close:]


def install_real_dashboard_exit_zone_position_patch() -> None:
    """Install the TP/SL zone positioning patch once per API process."""
    global _INSTALLED, _ORIG_SLOT_OVERLAY_JS
    if _INSTALLED:
        return
    _ORIG_SLOT_OVERLAY_JS = real_api._real_slot_overlay_js

    def patched_real_slot_overlay_js() -> str:
        return _insert_inside_last_iife(_ORIG_SLOT_OVERLAY_JS())

    real_api._real_slot_overlay_js = patched_real_slot_overlay_js
    _INSTALLED = True
