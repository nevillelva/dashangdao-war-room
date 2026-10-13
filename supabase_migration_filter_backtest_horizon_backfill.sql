-- 2026-10-14 補標舊列的 horizon_days（老闆指示「直接補標」）。已用 MCP 套用到資料庫。
alter table filter_backtest_weekly_results add column if not exists horizon_basis text;
comment on column filter_backtest_weekly_results.horizon_basis is 'horizon_days 的依據：code=程式碼已是 5 日寫法（2026-10-02 起，可由 git 驗證）；inferred=依開發輪次日期推定（R98續120≈9/09、R98續132~138≤9/12），9/09~9/10 一帶可能差一個 run_date';
update filter_backtest_weekly_results set horizon_basis='code' where horizon_days=5 and horizon_basis is null;
update filter_backtest_weekly_results set horizon_days=3, horizon_basis='inferred' where horizon_days is null and run_date <= '2026-09-09';
update filter_backtest_weekly_results set horizon_days=5, horizon_basis='inferred' where horizon_days is null and run_date >= '2026-09-10';
