import datetime as dt, market_derivs as M
T = dt.date(2026, 10, 15)
r, j = M.parse_fut([{"date": "2026-10-14", "futures_id": "TX", "institutional_investors": "外資", "long_deal_volume": "1", "short_deal_volume": "2",
                      "long_open_interest_balance_volume": "3", "short_open_interest_balance_volume": "4"},
                     {"date": "2026-13-14", "futures_id": "TX"}], T)
assert len(r) == 1 and j == 1 and r[0]["symbol"] == "TX|外資"
r, j = M.pc_ratio([{"date": "2026-10-14", "call_put": "call", "open_interest": "100", "volume": "50", "trading_session": "position"},
                    {"date": "2026-10-14", "call_put": "put", "open_interest": "120", "volume": "60", "trading_session": "position"},
                    {"date": "2026-10-14", "call_put": "put", "open_interest": "999", "volume": "9", "trading_session": "after_market"}], T)
assert r[0]["payload"]["pc_oi"] == 120.0 and r[0]["payload"]["pc_vol"] == 120.0, r
r, j = M.parse_total_margin([{"date": "2026-10-16", "name": "x", "TodayBalance": "1", "YesBalance": "1", "buy": "1", "sell": "1"}], T)
assert not r and j == 1
print("ok test_market_derivs")
