-- 櫃買熱門股證券商進出排行（2026-10-12）：官方 OpenAPI tpex_active_broker_volume，每日約 30 檔上櫃熱門股 × 前 10 家券商。
-- 層級是『券商總公司』不是分點；只當分點的補充。由 tpex_broker_top.sync() 寫入（service key）；RLS 開啟、無 policy。
create table if not exists public.tpex_broker_top (
  log_date date not null,
  symbol text not null,
  stock_name text,
  stock_rank integer,            -- 該檔在熱門股排行的名次
  firm text not null,            -- 券商總公司名稱（例：元大、富邦）
  firm_rank integer,             -- 該檔內的券商名次
  buy_shares bigint,             -- 總買量（股）
  sell_shares bigint,            -- 總賣量（股）
  updated_at timestamptz not null default now(),
  primary key (log_date, symbol, firm)
);
create index if not exists tpex_broker_top_symbol_idx on public.tpex_broker_top (symbol, log_date desc);
alter table public.tpex_broker_top enable row level security;
