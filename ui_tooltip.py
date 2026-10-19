"""【2026-10-19 老闆：浮動標籤被遮擋看不到完整】全站 .m-tooltip 浮動說明框的定位修正。
原因：①表格外框 overflow-x:auto 會連帶把 overflow-y 也變成 auto，往上展開的說明框被切掉；②說明框以「觸發文字」為中心展開，
靠近畫面左右緣時會超出可視範圍；③往上展開時，標籤靠近畫面頂端（或 Streamlit 頂欄）就被蓋住。
修法：滑過／點一下時用 position:fixed 重新定位——水平夾在可視範圍內、預設往下展開、下方放不下才往上、都放不下就貼齊可視範圍。
fixed 不受任何外框 overflow 裁切。事件掛在父頁面 document（只裝一次），Streamlit 重繪後仍有效。純函式字串，可在瀏覽器測試。
"""

TOOLTIP_CSS = """
/* 2026-10-19：表格外框不再裁切浮動說明；沒有 JS 時的備援＝窄螢幕把說明框貼在畫面底部、完整顯示 */
.wr-table { overflow: visible; padding-top: 4px; }
.wr-table .m-tooltip .m-tooltiptext { top: 125%; bottom: auto; left: 0; transform: none; }
.wr-table td:last-child .m-tooltip .m-tooltiptext { left: auto; right: 0; }
@media (max-width: 640px) {
  .m-tooltip .m-tooltiptext { position: fixed !important; left: 8px !important; right: 8px !important; bottom: 12px !important; top: auto !important;
    transform: none !important; width: auto !important; max-width: none !important; }
}
"""

TOOLTIP_JS = r"""
(function(){
  var doc = document; try { if (window.parent && window.parent.document) doc = window.parent.document; } catch(e) {}
  var win = doc.defaultView || window;
  if (doc.__wrTipInstalled) return; doc.__wrTipInstalled = true;
  var active = null;
  function place(t){
    var box = t.querySelector('.m-tooltiptext'); if(!box) return;
    var vw = doc.documentElement.clientWidth || win.innerWidth, vh = win.innerHeight, M = 8;
    box.style.setProperty('position','fixed','important'); box.style.setProperty('transform','none','important');
    box.style.setProperty('bottom','auto','important'); box.style.setProperty('right','auto','important');
    box.style.setProperty('width','max-content','important');
    box.style.setProperty('max-width', Math.min(340, vw - 2*M) + 'px', 'important');
    box.style.setProperty('left','0px','important'); box.style.setProperty('top','0px','important');
    var r = t.getBoundingClientRect(), bw = box.offsetWidth, bh = box.offsetHeight;
    var left = Math.max(M, Math.min(r.left, vw - bw - M));
    var top = r.bottom + 6;
    if (top + bh > vh - M) { var up = r.top - bh - 6; top = up >= M ? up : Math.max(M, vh - bh - M); }
    box.style.setProperty('left', left + 'px', 'important'); box.style.setProperty('top', top + 'px', 'important');
  }
  function on(e){ var el = e.target && e.target.closest ? e.target.closest('.m-tooltip') : null; if (el) { active = el; place(el); } }
  doc.addEventListener('mouseover', on, true);
  doc.addEventListener('touchstart', on, {capture:true, passive:true});
  doc.addEventListener('focusin', on, true);
  function again(){ if (active && active.isConnected && active.matches(':hover')) place(active); }
  doc.addEventListener('scroll', again, true); win.addEventListener('resize', again);
})();
"""
