#!/usr/bin/env python3
"""
check_python_version_compat.py
──────────────────────────────
【R98續111新增，總指揮官指示：版本問題要寫死成強制準則】

【為什麼需要這支腳本 —— 真實事故，不是假想風險】
2026-09-03 至 09-07，全系統排程停擺 94 小時、連續影響 4 個交易日。
根因是 system_scheduler.py 裡一段「跨行的 f-string 運算式」：

    f"{'文字A'
       '文字B' if 條件 else ''}"          ← 這種寫法

這是 Python 3.12 才放寬允許的語法（PEP 701）。
- 開發/驗證環境用 Python 3.12 → ast.parse 永遠通過，一路綠燈
- GitHub Actions runner 用 Python 3.11 → 直接 SyntaxError，程式根本沒啟動

因為失敗發生在「Python 解析檔案」的階段，比任何 try/except 都早，所以：
  ① 完全沒有寫進 system_run_log（看起來像「排程沒被觸發」）
  ② 完全沒有寫進 stage_crash_* 崩潰紀錄表
  ③ 排程健康監控那支 workflow 因為用不同的套件與 python3，完全正常
     → 更容易誤判成「GitHub 平台故障」而不是自己的程式碼問題

【同類地雷（3.12 允許、3.11 會炸）】
  - f-string 運算式跨行書寫
  - f-string 運算式內出現反斜線跳脫（例如 \\" ）
  - f-string 運算式內用跟外層相同的引號

【重要：不能用 ast.parse(feature_version=(3,11)) 代替】
實測確認該參數抓不到上述 f-string 問題（CPython 3.12 的 f-string 由新的
tokenizer 處理，不受 feature_version 影響）。**必須用真正的 3.11 直譯器。**

【用法】
    python3.11 check_python_version_compat.py
    （若手邊沒有 3.11：`uv python install 3.11` 可快速取得）

【正式環境版本對照，改任何程式碼前務必確認一致】
    GitHub Actions（system_scheduler.py / warroom_core.py）：Python 3.11
      ← 由 .github/workflows/system_scheduler.yml 的 setup-python 指定
    Streamlit Cloud（dashangdao.py / dashangdao_helpers.py）：依平台預設
"""
import ast
import sys

TARGET_FILES = [
    "system_scheduler.py",
    "warroom_core.py",
    "dashangdao.py",
    "dashangdao_helpers.py",
]

# GitHub Actions runner 實際使用的版本，跟 system_scheduler.yml 的
# setup-python 設定必須一致。那邊改版本，這裡也要跟著改。
REQUIRED_MAJOR_MINOR = (3, 11)


def main():
    running = sys.version_info[:2]
    print(f"目前直譯器版本：Python {running[0]}.{running[1]}")

    if running != REQUIRED_MAJOR_MINOR:
        print(f"\n❌ 這支腳本必須用 Python {REQUIRED_MAJOR_MINOR[0]}."
              f"{REQUIRED_MAJOR_MINOR[1]} 執行才有意義。")
        print("   用比較新的版本跑會漏掉 f-string 之類的語法差異，"
              "那正是 2026-09-03 那次 94 小時停擺沒被抓到的原因。")
        print(f"\n   取得方式：uv python install "
              f"{REQUIRED_MAJOR_MINOR[0]}.{REQUIRED_MAJOR_MINOR[1]}")
        return 2

    failed = []
    for path in TARGET_FILES:
        try:
            with open(path, encoding="utf-8") as f:
                ast.parse(f.read())
            print(f"  ✅ {path}")
        except FileNotFoundError:
            print(f"  ⚠️  {path}（檔案不存在，跳過）")
        except SyntaxError as e:
            print(f"  ❌ {path} 第 {e.lineno} 行：{e.msg}")
            failed.append((path, e.lineno, e.msg))

    print()
    if failed:
        print(f"❌ 有 {len(failed)} 個檔案在 Python "
              f"{REQUIRED_MAJOR_MINOR[0]}.{REQUIRED_MAJOR_MINOR[1]} 下語法錯誤，"
              f"絕對不能部署——部署上去會讓整個排程系統完全停擺。")
        return 1

    print(f"✅ 全部檔案在 Python {REQUIRED_MAJOR_MINOR[0]}."
          f"{REQUIRED_MAJOR_MINOR[1]} 下語法皆正確。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
