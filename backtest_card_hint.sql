-- 【2026-10-14】戰卡分數提示回測（在 Supabase SQL 執行；只看彙總，結果存私有表 ui_selftest_reports）
-- 窗口：factor_snapshot 起日（2026-08-24）～最新；close_price 8 月起才完整。報酬＝t 日收盤到 t+5 個交易日收盤，扣當日全市場平均。
-- 2026-10-14 結果：不支持買賣提示（見 ui_selftest_reports.run_id = card_hint_backtest_20261014）。事件樣本累積到 200 件以上再重跑。
with px as (
  select symbol, trade_date, close_price, lead(close_price,1) over w c1, lead(close_price,5) over w c5
  from twse_market_snapshot where close_price is not null and trade_date >= '2026-08-01'
  window w as (partition by symbol order by trade_date)
), j as (
  select f.trade_date d, f.total_score_default_weight s, (p.c1/p.close_price-1)*100 r1, (p.c5/p.close_price-1)*100 r5
  from factor_snapshot f join px p on p.symbol=f.symbol and p.trade_date=f.trade_date
  where f.total_score_default_weight is not null and p.c5 is not null
), adj as (select d, s, r1-avg(r1) over (partition by d) a1, r5-avg(r5) over (partition by d) a5 from j)
select d, count(*) filter (where s>=2) np, count(*) filter (where s<=-2) nn,
  round((avg(a1) filter (where s>=2) - avg(a1) filter (where s<=-2))::numeric,3) spread1,
  round((avg(a5) filter (where s>=2) - avg(a5) filter (where s<=-2))::numeric,3) spread5
from adj group by d having count(*) filter (where s>=2)>=20 and count(*) filter (where s<=-2)>=20 order by d;
