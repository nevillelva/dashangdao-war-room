-- 2026-10-16 twse_market_snapshot 補存融資餘額／融資限額／融券餘額／融券增減（來源：證交所 OpenAPI MI_MARGN）
alter table public.twse_market_snapshot
  add column if not exists margin_balance numeric,
  add column if not exists margin_limit numeric,
  add column if not exists short_balance numeric,
  add column if not exists short_diff numeric;
