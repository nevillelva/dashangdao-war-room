-- 【2026-10-13】ETF 價格「先確認、再標記」：人工閱讀查證結果表 + 大幅跳動檢視。
-- 原則（老闆指示）：價格跳動未經確認前只標「資料待確認」，不直接說「異常」；確認後才依結果處理。
-- etf_price_qc：一列 = 一檔 ETF 在某個跳動日的查證結論（附來源網址與證據文字）。
--   confirmed_split     已確認為分割／反分割，factor = 分割前價 ÷ 分割後價（反分割 < 1），回測據此還原
--   confirmed_real_move 已確認為真實行情（折溢價、市場大跌等），不調整、可回測
--   pending             已看過但還無法下結論（寫明缺什麼）；回測仍拒絕並顯示「資料待確認」
-- 沒有列 = 尚未查證，由程式依規則（官方分割紀錄、除息、同日市場同步、停牌天數）自動分類。
-- 由人工閱讀（投信公告／媒體）後以 service key 寫入；RLS 開啟、無 policy（與 etf_price_daily 同慣例）。
create table if not exists public.etf_price_qc (
  symbol text not null,
  jump_date date not null,
  status text not null check (status in ('confirmed_split', 'confirmed_real_move', 'pending')),
  factor numeric(14, 6),
  source_url text,
  evidence text not null,
  checked_at timestamptz not null default now(),
  checked_by text not null default 'Claude 人工閱讀',
  primary key (symbol, jump_date),
  constraint etf_price_qc_split_needs_factor check (status <> 'confirmed_split' or (factor is not null and factor > 0)),
  constraint etf_price_qc_confirmed_needs_source check (status = 'pending' or source_url like 'https://%')
);
alter table public.etf_price_qc enable row level security;

-- etf_price_jumps：etf_price_daily 相鄰兩列收盤變動超過 11% 的日子（台股單日 ±10% 加捨入容差）。
-- 只是「檢視」，供分類程式讀取；不改任何原始資料。security_invoker 讓它沿用資料表的 RLS。
create or replace view public.etf_price_jumps with (security_invoker = true) as
select symbol,
       prev_date,
       trade_date as jump_date,
       prev_close,
       close
from (
  select symbol,
         trade_date,
         close,
         lag(close) over (partition by symbol order by trade_date) as prev_close,
         lag(trade_date) over (partition by symbol order by trade_date) as prev_date
  from public.etf_price_daily
) t
where prev_close > 0
  and abs(close / prev_close - 1) > 0.11;
