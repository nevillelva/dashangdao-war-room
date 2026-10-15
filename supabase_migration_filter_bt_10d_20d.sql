-- 【2026-10-15】查N指令回測：除 5 日外，另存 10 日／20 日勝率與樣本數（母數＝該天期有資料的筆數）
alter table public.filter_backtest_weekly_results
  add column if not exists win_rate_10d double precision,
  add column if not exists sample_count_10d integer,
  add column if not exists win_rate_20d double precision,
  add column if not exists avg_return_20d double precision,
  add column if not exists sample_count_20d integer;
