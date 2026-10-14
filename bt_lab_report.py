# -*- coding: utf-8 -*-
"""【2026-10-14】用自助回測引擎＋5 年資料集，一次跑老闆原問題：戰卡分數首次跨越 ≥6，進場後持有 5/10/20/30 日與分數跌破出場。
結果只寫私有表 ui_selftest_reports(summary='bt_lab_first_cross_ge6')；公開日誌只印數量。"""
import json
import os
import sys

import bt_dataset_io as dio
import bt_lab as B


def main():
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    meta, panel = dio.load(sb, "main")
    if panel is None:
        print("無資料集")
        return 1
    D = B.prepare(panel)
    out = {"meta": meta, "runs": []}
    n = 0
    for entry_at in ("next_open", "next_close"):
        for mode in ("first_cross", "every_day"):
            n += 1
            spec = B.Spec(score_min=6.0, mode=mode, entry_at=entry_at, max_hold=60,
                          exits=[{"type": "hold", "n": h} for h in (5, 10, 20, 30)] + [{"type": "score_below", "y": 5.0}, {"type": "score_below", "y": 4.0}, {"type": "ma20"}])
            res = B.run(D, spec, n_tried=n)
            out["runs"].append({"entry_at": entry_at, "mode": mode, "n_entries": res["n_entries"], "cost": res["cost"],
                                "results": {k: {"verdict": v["verdict"], "reasons": v["reasons"], "summary": {kk: vv for kk, vv in v["summary"].items()}}
                                            for k, v in res["results"].items()}})
    sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "bt_lab_first_cross_ge6",
                                            "report": json.loads(json.dumps(out, default=float).replace("NaN", "null").replace("Infinity", "null"))}).execute()
    print(f"完成：{len(out['runs'])} 組設定")
    return 0


if __name__ == "__main__":
    sys.exit(main())
