"""回歸測試：擋的是「算錯數字」，不連網，Actions 抓資料前先跑一遍。

每一則測試都對應一個實際踩過或差點踩到的坑，不是為了覆蓋率而寫。
"""
from __future__ import annotations

import math
import sys
from datetime import date

import derive
from build import next_release
from cftc import _num, normalise, subtract


def check(name: str, got, want) -> None:
    if got != want:
        print(f"✗ {name}：得到 {got!r}，應為 {want!r}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ {name}")


def _cat(long, short, spread=None, chg_long=None, chg_short=None):
    return {"long": long, "short": short, "spread": spread,
            "chg_long": chg_long, "chg_short": chg_short,
            "traders_long": None, "traders_short": None}


def test_num():
    """CFTC 的數字是字串，缺值有 None／空字串／'-' 三種寫法。"""
    check("_num 解析整數字串", _num("1,234"), 1234)
    check("_num 解析浮點字串", _num("1234.0"), 1234)
    check("_num 空字串視為缺值", _num(""), None)
    check("_num 破折號視為缺值", _num("-"), None)
    check("_num None 視為缺值", _num(None), None)


def test_net_excludes_spread():
    """淨部位只算方向性曝險。spread 是同時持有多空的價差部位，不能併進去。"""
    check("淨部位 = 多 − 空", derive.net(_cat(300, 100, spread=999)), 200)
    check("缺多方時不硬算", derive.net(_cat(None, 100)), None)


def test_net_chg_uses_computed():
    """官方 change 欄位對多、空各自四捨五入，相減後與自算值常差 1-2 口。

    顯示值必須自洽——net 的差要等於 net_chg，否則讀者無從解釋那一兩口。
    所以主值取自算，官方值只拿來驗證。
    """
    rows = [
        {"date": "2026-08-11", "cats": {"a": _cat(1000, 400)}},
        {"date": "2026-08-18", "cats": {"a": _cat(1100, 450, chg_long=101, chg_short=50)}},
    ]
    derive.enrich_positions(rows)
    # 自算：(1100-450) - (1000-400) = 50；官方：101-50 = 51
    check("net_chg 取自算值", rows[1]["cats"]["a"]["net_chg"], 50)
    check("差 1 口不算異常", rows[1]["cats"]["a"]["chg_mismatch"], False)


def test_chg_mismatch_flags_real_gap():
    """差超過 2 口才是真異常（合約定義變更或補發修正），要標出來。"""
    rows = [
        {"date": "2026-08-11", "cats": {"a": _cat(1000, 400)}},
        {"date": "2026-08-18", "cats": {"a": _cat(1100, 450, chg_long=200, chg_short=50)}},
    ]
    derive.enrich_positions(rows)
    check("差 100 口標記為異常", rows[1]["cats"]["a"]["chg_mismatch"], True)


def test_percentile():
    check("最大值落在高百分位", derive._percentile_rank([1, 2, 3, 4], 4), 87.5)
    check("最小值落在低百分位", derive._percentile_rank([1, 2, 3, 4], 1), 12.5)
    check("重複值取中點", derive._percentile_rank([5, 5], 5), 50.0)


def test_extremes_need_min_sample():
    """樣本不足就留白。Ultra 兩檔歷史短，寧可不給數字也不給假訊號。"""
    rows = [{"date": f"2026-{m:02d}-01", "cats": {"a": _cat(100 + i, 50)}}
            for i, m in enumerate(range(1, 13))]
    derive.enrich_positions(rows)
    derive.add_extremes(rows)
    check("12 週樣本不給極端度", rows[-1]["cats"]["a"]["pctile"], None)
    check("樣本數仍記錄下來", rows[-1]["cats"]["a"]["sample"], 12)


def test_realised_vol():
    """殖利率日變動固定 1 bp 時，標準差為 0。"""
    series = [[f"2026-01-{d:02d}", 4.00 + 0.01 * d] for d in range(1, 26)]
    rv = derive.realised_vol(series, windows=(20,))
    check("等差序列的已實現波動為 0", rv["rv20"][-1][1], 0.0)

    # 年化係數要對：日變動標準差 × sqrt(252)
    alt = [[f"2026-01-{d:02d}", 4.00 + (0.01 if d % 2 else 0.0)] for d in range(1, 26)]
    rv2 = derive.realised_vol(alt, windows=(20,))
    expect = round(1.0 * math.sqrt(derive.TRADING_DAYS), 1)   # ±1bp 交替，母體標準差為 1
    check("年化係數為 sqrt(252)", rv2["rv20"][-1][1], expect)


def test_next_release():
    """COT 報週二部位、當週五發布，下一期是再下個週五。"""
    check("週二 → 下下個週五", next_release(date(2026, 8, 18)), date(2026, 8, 28))
    check("跨月正確", next_release(date(2026, 12, 29)), date(2027, 1, 8))


def _row(date, oi, long, short, spread=0, traders=7):
    return {"date": date, "oi": oi, "oi_chg": None, "units": "u",
            "cats": {"a": {"long": long, "short": short, "spread": spread,
                           "chg_long": None, "chg_short": None,
                           "traders_long": traders, "traders_short": traders}}}


def test_subtract_gives_options_leg():
    """選擇權部位 ＝ 合併版 − 僅期貨版，逐欄相減。"""
    cb = [_row("2026-08-18", 1000, 600, 300, 100)]
    fo = [_row("2026-08-18", 700, 450, 200, 50)]
    out = subtract(cb, fo)
    check("選擇權未平倉量", out[0]["oi"], 300)
    check("選擇權多方", out[0]["cats"]["a"]["long"], 150)
    check("選擇權空方", out[0]["cats"]["a"]["short"], 100)
    check("選擇權價差", out[0]["cats"]["a"]["spread"], 50)


def test_subtract_drops_traders():
    """交易人數不能相減——同一機構可能同時持有期貨與選擇權，
    兩個數字相減出來的不是任何真實的人數。"""
    out = subtract([_row("2026-08-18", 1000, 600, 300, 0, traders=90)],
                   [_row("2026-08-18", 700, 450, 200, 0, traders=80)])
    check("選擇權不給交易人數", out[0]["cats"]["a"]["traders_long"], None)


def test_subtract_skips_unmatched_dates():
    """某一期只有單邊有資料就整期跳過，不用半份資料硬湊。"""
    cb = [_row("2026-08-11", 1000, 600, 300), _row("2026-08-18", 1100, 650, 320)]
    fo = [_row("2026-08-18", 700, 450, 200)]
    out = subtract(cb, fo)
    check("只保留兩邊都有的期別", [r["date"] for r in out], ["2026-08-18"])


def test_subtract_preserves_identity():
    """恆等式（五類多方＋價差＝未平倉量）在相減後仍須成立——
    這是 validate.py 用來確認兩份報告有對齊的那道檢查。"""
    cb = [_row("2026-08-18", 1000, 900, 300, 100)]   # 900 + 100 = 1000 ✓
    fo = [_row("2026-08-18", 700, 650, 200, 50)]     # 650 +  50 =  700 ✓
    o = subtract(cb, fo)[0]
    total = o["cats"]["a"]["long"] + o["cats"]["a"]["spread"]
    check("相減後恆等式仍成立", total, o["oi"])


def test_normalise_sorts_by_date():
    """Socrata 分頁若順序被打亂，滾動統計會全錯，所以正規化時強制排序。"""
    raw = [
        {"report_date_as_yyyy_mm_dd": "2026-08-18T00:00:00.000", "open_interest_all": "2"},
        {"report_date_as_yyyy_mm_dd": "2026-08-11T00:00:00.000", "open_interest_all": "1"},
    ]
    out = normalise(raw)
    check("正規化後依日期由舊到新", [r["date"] for r in out],
          ["2026-08-11", "2026-08-18"])


if __name__ == "__main__":
    for fn in [test_num, test_net_excludes_spread, test_net_chg_uses_computed,
               test_chg_mismatch_flags_real_gap, test_percentile,
               test_extremes_need_min_sample, test_realised_vol,
               test_next_release, test_normalise_sorts_by_date,
               test_subtract_gives_options_leg, test_subtract_drops_traders,
               test_subtract_skips_unmatched_dates, test_subtract_preserves_identity]:
        fn()
    print("\n全部通過。")
