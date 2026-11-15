"""schedule_backup_guard 離線測試：假 session 模擬 Supabase REST，不連網、不需金鑰。"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import schedule_backup_guard as g  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))


class FakeResp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body if body is not None else []

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, rows=None, query_status=200, post_status=201):
        self.rows = rows or []
        self.query_status = query_status
        self.post_status = post_status
        self.gets = []
        self.posts = []

    def get(self, url, **kw):
        self.gets.append(url)
        return FakeResp(self.query_status, self.rows if self.query_status == 200 else None)

    def post(self, url, **kw):
        self.posts.append((url, kw.get("json")))
        return FakeResp(self.post_status)


NOON_TPE = datetime(2026, 11, 15, 12, 0, tzinfo=TAIPEI)
BASE = "https://example.supabase.co"
KEY = "test-key-not-real"


class GuardTest(unittest.TestCase):
    def test_taipei_day_start_is_utc_previous_afternoon(self):
        self.assertEqual(g.taipei_day_start_utc(NOON_TPE), "2026-11-14T16:00:00+00:00")

    def test_non_schedule_event_always_runs_without_queries(self):
        s = FakeSession(rows=[{"id": 1}])
        self.assertEqual(g.decide("gate", "workflow_dispatch", s, BASE, KEY, NOON_TPE), g.EXIT_RUN)
        self.assertEqual(s.gets, [])
        self.assertEqual(s.posts, [])

    def test_non_backup_stage_on_schedule_runs(self):
        s = FakeSession(rows=[{"id": 1}])
        self.assertEqual(g.decide("signal", "schedule", s, BASE, KEY, NOON_TPE), g.EXIT_RUN)
        self.assertEqual(s.gets, [])

    def test_already_ran_today_skips_and_writes_no_claim(self):
        s = FakeSession(rows=[{"id": 9}])
        self.assertEqual(g.decide("tail_entry", "schedule", s, BASE, KEY, NOON_TPE), g.EXIT_SKIP)
        self.assertEqual(s.posts, [])
        self.assertIn("stage=in.(tail_entry,tail_entry_claim)", s.gets[0])

    def test_not_run_today_claims_then_runs(self):
        s = FakeSession(rows=[])
        self.assertEqual(g.decide("gate", "schedule", s, BASE, KEY, NOON_TPE), g.EXIT_RUN)
        self.assertEqual(len(s.posts), 1)
        url, body = s.posts[0]
        self.assertTrue(url.endswith("/rest/v1/system_run_log"))
        self.assertEqual(body["stage"], "gate_claim")
        self.assertEqual(body["run_date"], "2026-11-15")

    def test_query_failure_fails_open(self):
        s = FakeSession(query_status=500)
        self.assertEqual(g.decide("intraday_force_exit", "schedule", s, BASE, KEY, NOON_TPE), g.EXIT_RUN)
        self.assertEqual(s.posts, [])

    def test_claim_failure_fails_open(self):
        s = FakeSession(rows=[], post_status=403)
        self.assertEqual(g.decide("gate", "schedule", s, BASE, KEY, NOON_TPE), g.EXIT_RUN)

    def test_missing_config_runs_and_names_only(self):
        saved = {k: os.environ.pop(k, None) for k in ("SUPABASE_URL", "SUPABASE_KEY")}
        os.environ["GITHUB_EVENT_NAME"] = "schedule"
        try:
            self.assertEqual(g.main(["gate"]), g.EXIT_RUN)
        finally:
            os.environ.pop("GITHUB_EVENT_NAME", None)
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_backup_stages_are_the_three_critical_ones(self):
        self.assertEqual(set(g.BACKUP_STAGES), {"gate", "tail_entry", "intraday_force_exit"})


if __name__ == "__main__":
    unittest.main()
