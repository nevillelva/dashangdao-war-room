"""【2026-10-19】浮動說明框（.m-tooltip）瀏覽器實測：390／820／1280 寬、表格在畫面頂端／下方，共 3×2×全部標籤，檢查說明框完整落在可視範圍內且沒被裁切／遮住。
用法：python test_tooltip_browser.py new（新版，應為 0 個問題）；python test_tooltip_browser.py old（舊版，重現問題）。需 playwright＋chromium。"""
import sys, json
from playwright.sync_api import sync_playwright
TIP = "查6營收雙增：最新月營收年增、近3月累計年增皆為正。5 年回測（隨機 300 檔）查3∧查2 加查6 營收雙增 10 日比大盤 +0.76%、樣本外 +1.49%；沒有查6 −0.06%。每天只挑 1 檔樣本外 +1.50%、3 檔 +0.82%、5 檔 +0.66%。"
def chip(label, tip=TIP, color="#4aa3df"):
    return (f"<span class='m-tooltip' style='border:1px solid {color};border-radius:10px;padding:0 6px;margin:1px 3px 1px 0;"
            f"font-size:12px;white-space:nowrap;border-bottom:1px solid {color};'>{label}"
            f"<span class='m-tooltiptext' style='width:min(320px,80vw);white-space:normal;'>{tip}</span></span>")
def page(css, wrapper, ncols_tags=True, pad_top=0, extra=''):
    rows = ""
    for i in range(6):
        rows += ("<tr>" + "".join(f"<td style='padding:4px 8px;border-bottom:1px solid #2a2a2a;vertical-align:top;'>{c}</td>" for c in
                 ["多方", f"230{i}", "名稱", "查3∧查2（便宜貼線）"+"說明"*8, chip("查6營收雙增")+chip("查11高殖利率")+chip("小型股","小型股說明"*20,"#e67e22"),
                  chip("10:00 前不動","10:00 判斷說明"*25,"#27ae60")]) + "</tr>")
    th = "".join(f"<th style='text-align:left;padding:4px 8px;border-bottom:1px solid #444;white-space:nowrap;'>{h}</th>" for h in ["軌道","代號","名稱","理由","標註（滑過看說明）","10:00 判斷"])
    table = f"<table style='width:100%;border-collapse:collapse;font-size:13px;'><thead><tr>{th}</tr></thead><tbody>{rows}</tbody></table>"
    return f"<html><head><meta name=viewport content='width=device-width'><style>body{{background:#0e1117;color:#ddd;margin:0;padding:0 16px;font-family:sans-serif}}{css}</style></head><body><div style='height:{pad_top}px'></div>{wrapper.format(table=table)}<div style='height:900px'></div>{extra}</body></html>"
OLD_CSS = """.m-tooltip { position: relative; display: inline-block; border-bottom: 1px dotted #888; cursor: help; }
.m-tooltip .m-tooltiptext { visibility: hidden; width: max-content; max-width: min(220px, 78vw); background-color: #333; color: #fff; text-align: left; border-radius: 6px; padding: 10px; position: absolute; z-index: 999; bottom: 125%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.3s; font-size: 12px; font-weight: normal; line-height:1.6; overflow-wrap: break-word; word-break: break-word;}
.m-tooltip:hover .m-tooltiptext { visibility: visible; opacity: 1; }
.m-tooltip-down .m-tooltiptext { top: 125%; bottom: auto; }"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ui_tooltip as U
NEW_CSS = OLD_CSS + U.TOOLTIP_CSS
OLD_WRAP = "<div style='overflow-x:auto;overflow-y:visible;padding-top:4px;'>{table}</div>"
NEW_WRAP = "<div class='wr-table'>{table}</div>"
mode = sys.argv[1] if len(sys.argv) > 1 else 'old'
css = NEW_CSS if mode == 'new' else OLD_CSS
wrap = NEW_WRAP if mode == 'new' else OLD_WRAP
bad = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/opt/pw-browsers/chromium-1194/chrome-linux/chrome') if False else p.chromium.launch()
    for vw in (390, 820, 1280):
        for pad in (0, 200):
            ctx = b.new_context(viewport={'width': vw, 'height': 700}); pg = ctx.new_page()
            pg.set_content(page(css, wrap, pad_top=pad, extra=('<script>'+U.TOOLTIP_JS+'</script>') if mode=='new' else '')); pg.wait_for_timeout(100)
            n = pg.locator('.m-tooltip').count()
            for i in range(n):
                el = pg.locator('.m-tooltip').nth(i)
                el.scroll_into_view_if_needed(); el.hover(); pg.wait_for_timeout(380)
                r = pg.evaluate("""(i)=>{const t=document.querySelectorAll('.m-tooltip')[i].querySelector('.m-tooltiptext');const R=t.getBoundingClientRect();
                  const pts=[[R.left+3,R.top+3],[R.right-3,R.top+3],[R.left+3,R.bottom-3],[R.right-3,R.bottom-3],[R.left+R.width/2,R.top+R.height/2]];
                  let ok=true;for(const [x,y] of pts){ if(x<0||y<0||x>innerWidth||y>innerHeight){ok=false;continue;} const e=document.elementFromPoint(x,y); if(!(e&&(t===e||t.contains(e)))) ok=false;}
                  return {l:R.left,r:R.right,t:R.top,b:R.bottom,ok:ok,vw:innerWidth,vh:innerHeight,sw:document.documentElement.scrollWidth}}""", i)
                if not r['ok'] or r['l'] < 0 or r['r'] > r['vw'] + 0.5:
                    bad.append((vw, pad, i, {k: round(v) if isinstance(v, float) else v for k, v in r.items()}))
            ctx.close()
    b.close()
print(mode, "tooltips checked per case; problems:", len(bad))
for x in bad[:12]: print(x)
