-- 分點型態每日彙總（2026-10-05）：隔日沖型 / 建倉型 / 外資型 買超佔比與主導判讀。
-- 由 system_scheduler.py 的 compute_and_store_broker_style() 寫入（service key）；RLS 開啟、無 policy（只有 service key 能讀寫）。
create table if not exists public.broker_style_daily (
  symbol text not null,
  log_date date not null,
  verdict text not null,            -- build / flip / foreign / mixed / unclear / nodata
  top15_buy integer,                -- 前15大買超分點合計買超（張）
  flip_buy integer, build_buy integer, foreign_buy integer, other_buy integer,
  flip_pct numeric(5,1), build_pct numeric(5,1), foreign_pct numeric(5,1),
  flip_sell integer,                -- 隔日沖型分點當日賣超合計（張）
  build_sell integer,               -- 建倉型分點當日賣超合計（張）
  build_net_win integer,            -- 建倉型分點近 N 日累計淨買（張）
  hist_days integer,                -- 這檔已累積幾個資料日
  detail jsonb,
  updated_at timestamptz not null default now(),
  primary key (symbol, log_date)
);
create index if not exists broker_style_daily_date_idx on public.broker_style_daily (log_date);
alter table public.broker_style_daily enable row level security;
