-- 【2026-10-13】etf_price_gaps：etf_price_daily 相鄰兩列相隔超過 14 日曆天的空檔（每段一列）。
-- 只是檢視，供 etf_gap_probe 排程判斷「有沒有還沒確認過的新空檔」，避免每晚把 32 萬列讀回來。
create or replace view public.etf_price_gaps with (security_invoker = true) as
select symbol,
       prev_date,
       trade_date as next_date,
       (trade_date - prev_date) as gap_days
from (
  select symbol,
         trade_date,
         lag(trade_date) over (partition by symbol order by trade_date) as prev_date
  from public.etf_price_daily
) t
where prev_date is not null and trade_date - prev_date > 14;
