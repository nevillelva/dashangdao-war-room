-- ETF 日收盤價（未還原，來源 FinMind TaiwanStockPrice）。2026-10-12，P5 股債配置試算用。
-- 由 etf_price_sync.py 寫入（service key）；RLS 開啟、無 policy，與 broker_style_daily 同慣例。
create table if not exists public.etf_price_daily (
  symbol text not null,
  trade_date date not null,
  close numeric(12,4) not null,
  source text not null default 'finmind',
  updated_at timestamptz not null default now(),
  primary key (symbol, trade_date)
);
alter table public.etf_price_daily enable row level security;
