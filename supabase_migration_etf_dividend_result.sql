-- ETF 除權息結果（FinMind TaiwanStockDividendResult，未還原，與 etf_price_daily 同口徑）。2026-10-12，P5 含息報酬用。
-- 由 etf_price_sync.py 寫入（service key）；RLS 開啟、無 policy，與其他 ETF 表同慣例。
create table if not exists public.etf_dividend_result (
  symbol text not null,
  ex_date date not null,
  cash numeric(12,4) not null,
  before_price numeric(12,4),
  after_price numeric(12,4),
  kind text,
  source text not null default 'finmind',
  updated_at timestamptz not null default now(),
  primary key (symbol, ex_date)
);
alter table public.etf_dividend_result enable row level security;
