"""回歸測試：擋的是「算錯數字」，不連網，Actions 抓資料前先跑一遍。

每一則測試都對應一個實際踩過或差點踩到的坑，不是為了覆蓋率而寫。
"""
from __future__ import annotations

import math
import sys
from datetime import date

import cftc
import derive
from build import VOL_COLORS, VOL_PLOT, VOL_SPEC, next_release
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


def test_realised_vol_price():
    """商品波動走的是對數報酬，年化後單位是 %／年，不是 bp。"""
    flat = [[f"2026-01-{d:02d}", 80.0] for d in range(1, 26)]
    rv = derive.realised_vol_price(flat, windows=(20,))
    check("價格不動時波動為 0", rv["rv20"][-1][1], 0.0)

    # 每日 +1% 複利：對數報酬固定，標準差仍為 0（這正是用對數報酬的理由，
    # 簡單報酬在等比序列上也會是 0，但在大跌時會低估）
    geo = [[f"2026-01-{d:02d}", 80.0 * (1.01 ** d)] for d in range(1, 26)]
    rv2 = derive.realised_vol_price(geo, windows=(20,))
    check("等比序列的已實現波動為 0", rv2["rv20"][-1][1], 0.0)


def test_realised_vol_price_survives_negative_wti():
    """WTI 2020-04-20 的負結算價會讓 log() 直接丟 ValueError。

    這是真的會發生的資料，不是假想——DCOILWTICO 當日為 −36.98。
    非正價格必須整筆跳過，而不是讓整條管線炸掉或算出 nan。
    """
    series = [["2026-01-01", 20.0], ["2026-01-02", -37.0], ["2026-01-03", 15.0],
              ["2026-01-04", 16.0], ["2026-01-05", 17.0]]
    # 窗長取 1 是為了讓每一筆有效報酬都各自產出一點，直接看得到哪些日期入了列
    rv = derive.realised_vol_price(series, windows=(1,))
    dates = [p[0] for p in rv["rv1"]]
    check("負價當日與其相鄰報酬都不入列", dates, ["2026-01-04", "2026-01-05"])
    check("算得出有限的數字", all(0 <= p[1] < 1e6 for p in rv["rv1"]), True)


def test_pair_series_inner_joins():
    """配對序列只能取兩邊都有報價的日期。

    各商品的休市日不一樣。2006 年起實測：白銀有 40 天黃金沒報價；
    WTI 與 Brent 更是兩邊各有缺口（Brent 獨有 87 天、WTI 獨有 46 天）。
    用前值補會憑空造出「當天的變動」，而這兩張圖的用途正是看它怎麼動——
    補出來的動就是假訊號。
    """
    gold = [["2026-01-01", 4000.0], ["2026-01-02", 4200.0], ["2026-01-03", 4400.0]]
    silver = [["2026-01-01", 50.0], ["2026-01-03", 55.0]]
    out = derive.pair_series(gold, silver, "ratio")
    check("只保留兩邊都有的日期", [d for d, _ in out], ["2026-01-01", "2026-01-03"])
    check("比值算對", out[0][1], 80.0)


def test_pair_series_ratio_skips_bad_denominator():
    """比值的分母為零或負會算出 inf，寧可跳過那一天也不要讓它進圖。"""
    gold = [["2026-01-01", 4000.0], ["2026-01-02", 4000.0], ["2026-01-03", 4000.0]]
    silver = [["2026-01-01", 0.0], ["2026-01-02", None], ["2026-01-03", 50.0]]
    out = derive.pair_series(gold, silver, "ratio")
    check("零與空值都跳過", [d for d, _ in out], ["2026-01-03"])


def test_pair_series_spread_keeps_negative_wti():
    """價差**不可**沿用比值那套非正值過濾。

    WTI 在 2020-04-20 的 −36.98 是真實成交價，當天的 WTI-Brent 價差 −54.34
    是那場事件的核心事實，濾掉等於竄改歷史。減法也不會因為負數而爆掉。
    """
    wti = [["2020-04-17", 18.27], ["2020-04-20", -36.98], ["2020-04-21", 10.01]]
    brent = [["2020-04-17", 25.00], ["2020-04-20", 17.36], ["2020-04-21", 16.00]]
    out = derive.pair_series(wti, brent, "spread")
    check("負油價那天留下來", [d for d, _ in out],
          ["2020-04-17", "2020-04-20", "2020-04-21"])
    check("價差算對（含負值）", out[1][1], -54.34)
    check("空值仍要跳過",
          [d for d, _ in derive.pair_series(
              [["2026-01-01", None], ["2026-01-02", 5.0]],
              [["2026-01-01", 1.0], ["2026-01-02", 2.0]], "spread")],
          ["2026-01-02"])


def test_pair_series_rejects_unknown_op():
    """算法名打錯要當場炸掉，不要靜靜回一個空序列讓 M5 整段消失。"""
    try:
        derive.pair_series([["2026-01-01", 1.0]], [["2026-01-01", 1.0]], "diff")
    except ValueError:
        check("未知算法丟 ValueError", True, True)
    else:
        check("未知算法丟 ValueError", False, True)


def test_pair_spec_points_at_real_series():
    """M5 的兩條輸入序列與 overlay 都必須指得到實際抓得到的價格序列。

    接錯的話要等抓完資料、build 到最後一步才會 KeyError，而那要好幾分鐘。
    """
    import prices
    from build import PAIR_SPEC
    for akey, spec in PAIR_SPEC.items():
        have = {s["key"] for s in prices.SERIES[akey]}
        check(f"{akey} 的 a 有序列", spec["a"] in have, True)
        check(f"{akey} 的 b 有序列", spec["b"] in have, True)
        check(f"{akey} 算法有效", spec["op"] in ("ratio", "spread"), True)
        # 價差的正負號有意義，圖上一定要看得到零線；比值恆為正，畫零線只是浪費縱軸
        check(f"{akey} 價差才畫零線", spec["zero"], spec["op"] == "spread")
        contracts = {c["key"] for c in cftc.ASSET_BY_KEY[akey]["contracts"]}
        check(f"{akey} overlay 涵蓋全部合約", set(spec["overlay"]) == contracts, True)
        check(f"{akey} overlay 都指到實際序列",
              set(spec["overlay"].values()) <= have, True)


def test_disagg_field_names_keep_cftc_typos():
    """CFTC 資料集本身的欄位拼字錯誤必須照抄，不可「順手修正」。

    swap 的空方與價差是兩個底線、多方是一個；noncomm 的 spread 少一個 r。
    這些若被改成看起來正確的名字，抓到的會是一整排 None，
    而 validate.py 的恆等式檢查會因為分母也跟著少而**驗不出來**。
    """
    swap = next(c for c in cftc.DISAGG_CATEGORIES if c["key"] == "swap")
    check("swap 空方欄雙底線", swap["short"], "swap__positions_short_all")
    check("swap 價差欄雙底線", swap["spread"], "swap__positions_spread_all")
    check("swap 多方欄單底線", swap["long"], "swap_positions_long_all")

    pm = next(c for c in cftc.DISAGG_CATEGORIES if c["key"] == "prod_merc")
    check("生產商沒有價差欄位", pm["spread"], None)

    noncomm = next(c for c in cftc.LEGACY_CATEGORIES if c["key"] == "noncomm")
    check("noncomm 價差欄照抄 CFTC 拼字", noncomm["spread"], "noncomm_positions_spread")


def test_disagg_datasets_not_swapped():
    """Disagg 的合併版是 kh3c-gbw2，與 TFF 的排列相反。

    接反了兩份資料各自的恆等式仍成立，validate.py 抓不出來——
    只會看到「選擇權未平倉量是負的」，而那時已經很難回想是哪裡反了。
    所以在這裡釘死代號。
    """
    check("disagg 合併版代號", cftc.SCHEMES["disagg"]["datasets"]["combined"], "kh3c-gbw2")
    check("disagg 僅期貨版代號", cftc.SCHEMES["disagg"]["datasets"]["futonly"], "72hh-3qpy")
    check("tff 合併版代號", cftc.SCHEMES["tff"]["datasets"]["combined"], "yw9f-hn96")


def test_assets_are_self_consistent():
    """分頁設定與其他模組對得上：分類法存在、預設合約在清單裡、波動度規格齊全。

    擋的是「加了一個分頁但忘了補某張表」——那類錯誤要跑完整條抓取管線才會浮現，
    而完整管線要好幾分鐘。
    """
    import prices
    for a in cftc.ASSETS:
        keys = [c["key"] for c in a["contracts"]]
        check(f"{a['key']} 預設合約在清單內", a["default_contract"] in keys, True)
        check(f"{a['key']} 分類法都有定義",
              all(s in cftc.SCHEMES for s in a["schemes"]), True)
        check(f"{a['key']} 有波動度規格", a["key"] in VOL_SPEC, True)
        check(f"{a['key']} 要畫的序列都有指定顏色",
              all(k in VOL_COLORS for k in VOL_PLOT[a["key"]]), True)
        if a["key"] != "ust":
            have = {s["key"] for s in prices.SERIES[a["key"]]}
            check(f"{a['key']} 要畫的價格序列都抓得到",
                  set(VOL_PLOT[a["key"]]) <= have, True)
    codes = [c["code"] for a in cftc.ASSETS for c in a["contracts"]]
    check("合約代碼不重複", len(codes), len(set(codes)))


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
               test_realised_vol_price, test_realised_vol_price_survives_negative_wti,
               test_pair_series_inner_joins, test_pair_series_ratio_skips_bad_denominator,
               test_pair_series_spread_keeps_negative_wti, test_pair_series_rejects_unknown_op,
               test_pair_spec_points_at_real_series,
               test_disagg_field_names_keep_cftc_typos, test_disagg_datasets_not_swapped,
               test_assets_are_self_consistent,
               test_next_release, test_normalise_sorts_by_date,
               test_subtract_gives_options_leg, test_subtract_drops_traders,
               test_subtract_skips_unmatched_dates, test_subtract_preserves_identity]:
        fn()
    print("\n全部通過。")
