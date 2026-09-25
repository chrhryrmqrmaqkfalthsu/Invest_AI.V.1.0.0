"""Readability CSS patch for dashboard-real.

The real dashboard is often used in a secondary window.  This patch increases
text legibility without changing grid widths, chart heights, slot counts, or
panel placement.
"""
from __future__ import annotations

from typing import Any

from engine.live import real_dashboard_api as real_api

_INSTALLED = False
_ORIG_SLOT_OVERLAY_JS = None


def _patch_slot_overlay_js() -> None:
    global _ORIG_SLOT_OVERLAY_JS
    if _ORIG_SLOT_OVERLAY_JS is not None:
        return
    _ORIG_SLOT_OVERLAY_JS = real_api._real_slot_overlay_js

    readability_js = r'''
(function(){
  function installRealReadabilityStyle(){
    if(document.getElementById('km-real-readability-style')) return;
    const css = `
      /* 가독성 전용: 레이아웃 폭/높이/그리드 유지, 글자만 소폭 확대 */
      body{font-size:14px!important;line-height:1.42!important;-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;}
      button,input,select,textarea{font-size:14px!important;}
      .topbar .logo{font-size:18px!important;letter-spacing:.2px!important;}
      .topbar .nav button,#real-auto-settings-nav-btn,#fs-btn{font-size:14px!important;font-weight:900!important;}
      .topbar .regime,#real-update-badge{font-size:12.5px!important;}
      h2,h3,summary{font-size:15px!important;line-height:1.35!important;}
      .panel{font-size:14px!important;}
      .panel .loading,.cand-empty,.loading{font-size:14px!important;}
      .mslot{font-size:14px!important;line-height:1.38!important;}
      .mslot-top{font-size:14px!important;}
      .mslot-tk{font-size:21px!important;line-height:1.05!important;font-weight:950!important;letter-spacing:.2px!important;}
      .mslot-pnl{font-size:18px!important;line-height:1.05!important;font-weight:950!important;}
      .mslot-sub{font-size:13.5px!important;line-height:1.55!important;}
      .rb-stat .v{font-size:18px!important;line-height:1.05!important;font-weight:950!important;}
      .rb-stat .l{font-size:12.5px!important;line-height:1.25!important;font-weight:850!important;}
      .kv{font-size:14px!important;line-height:1.38!important;}
      .kv>span:first-child{font-size:13px!important;font-weight:850!important;}
      .kv>span:last-child{font-size:14.5px!important;font-weight:900!important;}
      .comment{font-size:14px!important;line-height:1.52!important;}
      .comment b{font-size:14.5px!important;}
      .tag{font-size:12.5px!important;line-height:1.35!important;font-weight:850!important;}
      small{font-size:12px!important;}
      .km-section-label{font-size:14.5px!important;line-height:1.25!important;}
      .km-section-count{font-size:13px!important;}
      .km-section-separator:after{font-size:11px!important;}
      .real-candidate-chart-meta,.real-holding-chart-meta{font-size:12.5px!important;line-height:1.28!important;}
      .real-signal-label,.preview-entry-xline,.preview-exit-zone-label{font-size:12px!important;}
      .real-order-ticket .ticket-head,.real-order-ticket .ticket-foot{font-size:12.5px!important;line-height:1.3!important;}
      .real-order-ticket .amount-prefix{font-size:15px!important;}
      .real-order-ticket .slot-buy-amount{font-size:17px!important;}
      .real-order-ticket .quick-amt,.real-order-ticket .slot-buy-real{font-size:13.5px!important;}
      .manual-buy-amount{font-size:14px!important;}
      .manual-buy-hint{font-size:11.5px!important;}
      #slot-detail-view{font-size:14px!important;}
      #detail-title{font-size:20px!important;line-height:1.2!important;}
      #detail-comment{font-size:14px!important;line-height:1.55!important;}
      #detail-comment ul{font-size:13.5px!important;line-height:1.55!important;}
      #preview-exit-panel,#preview-exit-panel label,#preview-exit-save-state{font-size:13.5px!important;}
      #preview-exit-panel input{font-size:18px!important;}
      #preview-exit-panel button{font-size:14px!important;}
      @media(max-width:900px){
        body{font-size:13.5px!important;}
        .mslot-tk{font-size:19px!important;}
        .mslot-pnl{font-size:16.5px!important;}
        .rb-stat .v{font-size:16.5px!important;}
      }
    `;
    const el=document.createElement('style');
    el.id='km-real-readability-style';
    el.textContent=css;
    document.head.appendChild(el);
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', installRealReadabilityStyle, {once:true});
  else installRealReadabilityStyle();
})();
'''

    def patched_real_slot_overlay_js() -> str:
        js = _ORIG_SLOT_OVERLAY_JS()
        if "km-real-readability-style" in js:
            return js
        return readability_js + "\n" + js

    real_api._real_slot_overlay_js = patched_real_slot_overlay_js


def install_real_dashboard_readability_patch() -> None:
    """Install dashboard-real readability CSS patch once per API process."""
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_slot_overlay_js()
    _INSTALLED = True
