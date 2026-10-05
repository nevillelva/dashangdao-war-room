#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DJ 主力賣買超頁解析器單元測試（固定樣本取自 2026-10-05 Actions 實抓的 2330 頁面結構，只留前兩列與合計列）。"""
import warroom_core as wc

SAMPLE = """<html><body><table>
<tr><td class="t10" colspan="10">台積電(2330)
券商分點-進出明細
<div class="t11">單位：張　最後更新日：2026/10/02</div></td></tr>
<tr id="oScrollMenu"><td class="t2" colspan="5">買超</td><td class="t2" colspan="5">賣超</td></tr>
<TR id="oScrollMenu">
<TD class="t2" nowrap>買超券商</TD><TD class="t2">買進</TD><TD class="t2">賣出</TD><TD class="t2">買超</TD><TD class="t2">佔成交比重</TD>
<TD class="t2" nowrap>賣超券商</TD><TD class="t2">買進</TD><TD class="t2">賣出</TD><TD class="t2">賣超</TD><TD class="t2">佔成交比重</TD>
</TR>
<TR>
<TD class="t4t1" nowrap><a href="/z/zc/zco/zco0/zco0.djhtm?a=2330&b=1440&BHID=1440">美林</a></TD>
<TD class="t3n1">1,221</TD><TD class="t3n1">558</TD><TD class="t3n1">663</TD><TD class="t3n1">4.38%</TD>
<TD class="t4t1" nowrap><a href="/z/zc/zco/zco0/zco0.djhtm?a=2330&b=1650&BHID=1650">新加坡商瑞銀</a></TD>
<TD class="t3n1">583</TD><TD class="t3n1">2,691</TD><TD class="t3n1">2,108</TD><TD class="t3n1">13.92%</TD>
</tr>
<TR>
<TD class="t4t1" nowrap><a href="/z/zc/zco/zco0/zco0.djhtm?a=2330&b=0039004100300030&BHID=9A00">永豐金證券</a></TD>
<TD class="t3n1">774</TD><TD class="t3n1">375</TD><TD class="t3n1">399</TD><TD class="t3n1">2.63%</TD>
<TD class="t4t1" nowrap><a href="/z/zc/zco/zco0/zco0.djhtm?a=2330&b=1470&BHID=1470">台灣摩根士丹利</a></TD>
<TD class="t3n1">391</TD><TD class="t3n1">1,321</TD><TD class="t3n1">930</TD><TD class="t3n1">6.14%</TD>
</tr>
<TR id="oScrollFoot">
<TD class="t4t1" nowrap>合計買超張數</td><td class="t3n1" colspan=4>3,327</td>
<TD class="t4t1" nowrap>合計賣超張數</td><td class="t3n1" colspan=4>6,573</td>
</TR>
<TR id="oScrollFoot">
<TD class="t4t1" nowrap>平均買超成本</td><td class="t3n1" colspan=4>2,502.74</td>
<TD class="t4t1" nowrap>平均賣超成本</td><td class="t3n1" colspan=4>2,501.41</td>
</TR>
</table></body></html>"""


def main():
    p = wc.parse_dj_zco_html(SAMPLE)
    assert p["data_date"] == "2026-10-02", p["data_date"]
    assert len(p["buyers"]) == 2 and len(p["sellers"]) == 2
    b0, s0 = p["buyers"][0], p["sellers"][0]
    assert (b0["broker_name"], b0["buy"], b0["sell"], b0["net"], b0["broker_code"]) == ("美林", 1221, 558, 663, "1440"), b0
    assert b0["pct"] == 4.38
    assert (s0["broker_name"], s0["buy"], s0["sell"], s0["net"]) == ("新加坡商瑞銀", 583, 2691, -2108), s0   # 賣超方 net 為負
    assert p["buyers"][1]["broker_code"] == "0039004100300030"
    assert (p["total_buy"], p["total_sell"]) == (3327, 6573), (p["total_buy"], p["total_sell"])
    assert (p["avg_buy_cost"], p["avg_sell_cost"]) == (2502.74, 2501.41)
    df = wc.dj_zco_to_dataframe(p)
    assert list(df.columns)[:4] == ["broker_name", "buy_shares", "sell_shares", "net_shares"]
    assert len(df) == 4 and df.attrs["data_date"] == "2026-10-02"
    assert int(df[df.broker_name == "台灣摩根士丹利"].net_shares.iloc[0]) == -930
    # 非預期頁面 → 空結果，不拋例外
    assert wc.parse_dj_zco_html("<html>Just a moment...</html>")["buyers"] == []
    assert wc.dj_zco_to_dataframe(wc.parse_dj_zco_html("")) is None
    print("✅ DJ 解析器測試通過（日期/兩側分點/買賣超符號/合計/平均成本/空頁面）")


if __name__ == "__main__":
    main()
