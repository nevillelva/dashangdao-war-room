"""原生 cron 備援的「同日已跑過就跳過」守門員。

背景：日常排程的主要觸發器是 Cloudflare Worker（warroom_monitor_worker.js 的 SCHEDULE 清單），它會
workflow_dispatch 派發階段。原生 cron 只留三條關鍵階段作備援（gate／tail_entry／intraday_force_exit），
GitHub 原生 cron 實測延遲數小時，所以它通常是「Worker 沒派發時」才有用的最後保險。

重複執行的防線（三層）：
1. 備援 cron 的時刻都排在 Worker 該階段的派發時刻之後（見 system_scheduler.yml 的註解），
   讓 Worker 的 hasStageRunSince(<stage>, 排程時刻) 能看到備援寫下的認領列。
2. 備援執行前（本檔）：今天（台北日界）若 system_run_log 已有該階段的任何一列（完成或認領），就跳過。
   與 Worker 自己的判斷條件一致（Worker 也是「有列就不派發」）。
3. 同一 concurrency 群組：備援 cron 與 Worker 派發的同階段會排隊、不並行，排在後面的那次在前一次寫完
   紀錄後才檢查，因此會看到前一次的列。

已知殘留風險（如實記錄）：Worker 在「檢查無列」與「派發」之間、以及備援在工作流程啟動後約 1～2 分鐘
才寫入認領列，這個極短空窗理論上仍可能讓兩者都跑；只有 Worker 派發的那次若排在備援之後，會因舊的
Worker 程式碼不檢查備援認領而重跑。實務上由時刻安排把空窗壓到最小。

結束碼：0 = 執行（已寫入認領列）；3 = 今天已跑過，跳過；查詢失敗一律回 0（寧可偶爾重複，也不漏跑）。
只印階段名與結果，不印任何 Supabase 回應內容與金鑰。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))
BACKUP_STAGES = ("gate", "tail_entry", "intraday_force_exit")
EXIT_RUN = 0
EXIT_SKIP = 3


def taipei_day_start_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(TAIPEI)
    day0 = now.astimezone(TAIPEI).replace(hour=0, minute=0, second=0, microsecond=0)
    return day0.astimezone(timezone.utc).isoformat()


def _headers(key: str, extra: dict | None = None) -> dict:
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if extra:
        h.update(extra)
    return h


def has_today_row(session, base: str, key: str, stage: str, since_utc: str) -> bool:
    """今天（台北日界起）是否已有該階段（完成列或認領列）。"""
    from urllib.parse import quote
    q = (f"{base}/rest/v1/system_run_log?select=id&stage=in.({quote(stage)},{quote(stage + '_claim')})"
         f"&created_at=gte.{quote(since_utc)}&limit=1")
    r = session.get(q, headers=_headers(key), timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"query_failed_{r.status_code}")
    return len(r.json()) > 0


def write_claim(session, base: str, key: str, stage: str) -> None:
    run_date = datetime.now(TAIPEI).strftime("%Y-%m-%d")
    body = {"run_date": run_date, "stage": f"{stage}_claim", "gate_status": "claimed",
            "note": "原生cron備援認領（schedule_backup_guard）"}
    r = session.post(f"{base}/rest/v1/system_run_log", json=body, timeout=20,
                     headers=_headers(key, {"Content-Type": "application/json", "Prefer": "return=minimal"}))
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"claim_failed_{r.status_code}")


def decide(stage: str, event_name: str, session=None, base: str = "", key: str = "", now=None) -> int:
    """回傳 EXIT_RUN 或 EXIT_SKIP。非備援情境（手動、Worker 派發、非備援階段）一律 EXIT_RUN。"""
    if event_name != "schedule" or stage not in BACKUP_STAGES:
        return EXIT_RUN
    try:
        import requests
        sess = session or requests.Session()
        since = taipei_day_start_utc(now)
        if has_today_row(sess, base, key, stage, since):
            print(f"[備援守門] {stage}：今天已有執行或認領紀錄，本次原生 cron 備援跳過。")
            return EXIT_SKIP
        write_claim(sess, base, key, stage)
        print(f"[備援守門] {stage}：今天尚未執行，原生 cron 備援接手並寫入認領列。")
        return EXIT_RUN
    except Exception as e:  # noqa: BLE001 — 查詢失敗寧可執行，不漏跑
        print(f"[備援守門] {stage}：查詢失敗（{type(e).__name__}），保守起見仍執行。")
        return EXIT_RUN


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stage = argv[0] if argv else ""
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    base = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY") or ""
    if event == "schedule" and stage in BACKUP_STAGES and (not base or not key):
        print(f"[備援守門] {stage}：缺少 SUPABASE_URL 或 SUPABASE_KEY（只印名稱），保守起見仍執行。")
        return EXIT_RUN
    return decide(stage, event, base=base, key=key)


if __name__ == "__main__":
    sys.exit(main())
