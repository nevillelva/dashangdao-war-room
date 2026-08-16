"""
audit_scoring_wiring.py —— R97新增，評分邏輯參數接線稽核工具

【使用時機，強制規定，見warroom_core.py determine_signal()文件開頭】
只要動到 determine_signal() 的參數清單、或動到任何一處呼叫
determine_signal(...) 的地方（目前是 warroom_v160.py 跟
system_scheduler.py 兩處），改動前後都要跑一次這支腳本。

【這支腳本在查什麼】
determine_signal() 支援的每一個參數，代表系統裡的一個風控/加分機制。
如果某個參數「函式本身支援，但檢查的呼叫端從沒有任何一次明確傳遞過」，
代表這個機制在該呼叫端形同虛設——不會報錯、分數看起來正常，但那個
機制永遠用預設值，等於被靜默停用。R97就是用這個方法，一次抓到
is_volume_dump/trend_gate_triggered/market_bull/landmine 四個
被排程端漏接的真實案例（其中兩個是文件裡明講「最不可退讓」的核心
風控規則，被漏接後系統會在真正該強制出場的情況下顯示續抱）。

【這支腳本做不到的事，必須誠實面對】
只能抓「有沒有接上」，抓不到「接上的門檻/邏輯設計得對不對」。例如
is_volume_dump的門檻是vol_ratio>=2.0，這支腳本沒辦法告訴你2.0這個
數字合不合理——那需要真實資料回測，或人工市場經驗判斷。

【已知、經過人工查證後確認「不算漏接」的例外】
以下參數會被這支腳本列為「排程端沒傳」，但那是查證過的刻意設計，
不是bug，執行時看到這幾個不用重新調查：
  - day_trader_alert：需要券商分點資料，determine_signal自己的文件
    寫明「批次全市場掃描沒有分點資料，不適用」，排程本來就是批次掃描。
    （總指揮官R97決議：這個之後要重新規劃，不是現在的漏接）
  - enable_doomsday：這是總指揮官刻意決定「不做成可調設定，寫死False」
    （理由：排程是全自動下單/賣出流程，跟網頁版人工看盤決定要不要開啟
    末日熔斷的情境不同），排程端會在呼叫時明確傳enable_doomsday=False，
    這其實「有傳」，不會被這支腳本抓到，這裡只是順便說明脈絡。

執行方式：python3 audit_scoring_wiring.py
"""
import ast
import inspect
import re


def audit():
    import sys
    sys.path.insert(0, '.')
    from warroom_core import determine_signal

    sig = inspect.signature(determine_signal)
    all_params = list(sig.parameters.keys())
    positional_only = ('current_price', 'ma5', 'ma20', 'foreign_buy', 'vol_ratio',
                       'is_open_high_close_low', 'buffer_pct')
    keyword_params = [p for p in all_params if p not in positional_only]

    print("=" * 70)
    print("determine_signal() 完整參數清單（共 %d 個，其中 %d 個是關鍵字參數）"
          % (len(all_params), len(keyword_params)))
    print("=" * 70)

    files_to_check = ['warroom_v160.py', 'system_scheduler.py']
    any_issue = False

    for filename in files_to_check:
        try:
            with open(filename, encoding='utf-8') as f:
                src = f.read()
        except FileNotFoundError:
            print(f"\n[{filename}] 檔案不存在，跳過")
            continue

        calls = re.findall(r'determine_signal\((.*?)\n    \)', src, re.S)
        if not calls:
            # 有些呼叫可能縮排層級不同，放寬一次再試
            calls = re.findall(r'determine_signal\((.*?)\)\s*\n', src, re.S)

        print(f"\n【{filename}】找到 {len(calls)} 處呼叫")
        never_passed = []
        for p in keyword_params:
            passed = any(re.search(rf'\b{p}\s*=', call) for call in calls)
            if not passed:
                never_passed.append(p)

        if never_passed:
            any_issue = True
            print(f"  ⚠️ 從未被任何一次呼叫明確傳遞的參數：{never_passed}")
            print(f"     → 請人工查證每一個：是刻意的設計決定，還是真的漏接？")
        else:
            print(f"  ✅ 所有關鍵字參數都至少被一次呼叫明確傳遞過")

    print("\n" + "=" * 70)
    if any_issue:
        print("結論：有參數需要人工查證，不代表一定是bug，但務必逐一確認。")
    else:
        print("結論：目前沒有偵測到明顯漏接的參數。")
    print("（提醒：這支腳本只能查「有沒有接上」，查不出「接上的邏輯對不對」，"
          "邏輯正確性仍需要真實資料回測或人工市場判斷。）")


if __name__ == '__main__':
    audit()
