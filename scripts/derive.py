"""衍生指標：淨部位、週變化交叉驗證、極端度、已實現波動。

刻意不引入 numpy／pandas——資料量（六檔 × 1054 週）用標準函式庫綽綽有餘，
少一層相依就少一個在 Actions 上壞掉的理由。
"""
from __future__ import annotations

import math
from statistics import fmean, pstdev

# 極端度的統計視窗
Z_WINDOW = 156       # 3 年滾動 z-score（52 週 × 3）
MIN_SAMPLE = 104     # 少於 2 年樣本就不給極端度數字，寧可留白也不給假訊號
TRADING_DAYS = 252   # 波動度年化係數


def net(cat: dict) -> int | None:
    """淨部位 = 多 − 空。spread 部位是同時持有多空的價差交易，不算方向性曝險，故排除。"""
    if cat["long"] is None or cat["short"] is None:
        return None
    return cat["long"] - cat["short"]


def enrich_positions(rows: list[dict]) -> list[dict]:
    """對單一合約的整段歷史，逐週補上淨部位與週變化。"""
    prev = None
    for r in rows:
        for key, cat in r["cats"].items():
            n = net(cat)
            cat["net"] = n

            # 官方 change 欄位與自行相減的交叉驗證。
            #
            # 實測 UST 10Y 全歷史：兩者有 40% 的週別對不上，但差異中位數 1 口、
            # 最大 2 口——這是 CFTC 對多、空兩欄各自四捨五入後相減的殘差，無害。
            # 因此容忍度設 ±2；超過才是真的異常（合約定義變更或補發修正），才標記。
            chg_reported = None
            if cat["chg_long"] is not None and cat["chg_short"] is not None:
                chg_reported = cat["chg_long"] - cat["chg_short"]
            chg_computed = None
            if prev and n is not None:
                pn = prev["cats"][key].get("net")
                if pn is not None:
                    chg_computed = n - pn

            # 主值取自算的差，不取官方欄位：顯示的「淨部位」與「淨部位變化」必須自洽，
            # 用官方值會讓兩者差個一兩口而讀者無從解釋。首週沒有前值時才退回官方值。
            cat["net_chg"] = chg_computed if chg_computed is not None else chg_reported
            cat["chg_mismatch"] = (
                chg_reported is not None and chg_computed is not None
                and abs(chg_reported - chg_computed) > 2
            )
        prev = r
    return rows


def _percentile_rank(history: list[float], value: float) -> float:
    """value 在 history 中的百分位（0-100）。history 需含 value 本身。"""
    if not history:
        return 0.0
    below = sum(1 for h in history if h < value)
    equal = sum(1 for h in history if h == value)
    return 100.0 * (below + 0.5 * equal) / len(history)


def add_extremes(rows: list[dict]) -> list[dict]:
    """逐週計算極端度。

    使用**擴張視窗**（只看該週之前的資料），不是整段歷史——
    否則歷史圖上的每一點都偷看了未來，回頭檢視訊號時會系統性高估準確度。
    """
    cat_keys = list(rows[0]["cats"].keys()) if rows else []
    for key in cat_keys:
        seen: list[float] = []
        for r in rows:
            n = r["cats"][key].get("net")
            if n is None:
                r["cats"][key]["pctile"] = None
                r["cats"][key]["z"] = None
                continue
            seen.append(float(n))
            if len(seen) < MIN_SAMPLE:
                r["cats"][key]["pctile"] = None
                r["cats"][key]["z"] = None
                r["cats"][key]["sample"] = len(seen)
                continue
            r["cats"][key]["pctile"] = round(_percentile_rank(seen, float(n)), 1)
            window = seen[-Z_WINDOW:]
            sd = pstdev(window)
            r["cats"][key]["z"] = round((n - fmean(window)) / sd, 2) if sd > 0 else None
            r["cats"][key]["sample"] = len(seen)
    return rows


def realised_vol(series: list[list], windows=(20, 60)) -> dict:
    """殖利率的已實現波動。

    輸入為 [[日期, 殖利率%], ...]。先轉成日變動（bp），再取滾動標準差並年化。
    單位是 bp/年，跟債券交易員講波動度的習慣一致（不是報酬率百分比）。
    """
    diffs = []
    for i in range(1, len(series)):
        d_prev, v_prev = series[i - 1]
        d_cur, v_cur = series[i]
        diffs.append([d_cur, (v_cur - v_prev) * 100.0])   # 百分點 → bp

    return _roll_annualise(diffs, windows)


def realised_vol_price(series: list[list], windows=(20, 60)) -> dict:
    """價格的已實現波動。

    輸入為 [[日期, 價格], ...]。取日對數報酬（%），滾動標準差年化後以 %／年 表示。
    這跟 realised_vol() 的殖利率版**不是同一個單位，也不可並排比較**——
    債券波動講的是殖利率走了幾個 bp，商品波動講的是價格漲跌了百分之幾。

    非正價格一律略過：WTI 在 2020-04-20 出現過負結算價（DCOILWTICO 為 −36.98），
    取對數會直接丟 ValueError 把整條管線炸掉。跳過該日並連帶跳過與它相鄰的兩筆報酬——
    那兩天的「報酬率」在數學上沒有意義，不該混進標準差裡。
    """
    rets = []
    for i in range(1, len(series)):
        p_prev, p_cur = series[i - 1][1], series[i][1]
        if p_prev is None or p_cur is None or p_prev <= 0 or p_cur <= 0:
            continue
        rets.append([series[i][0], math.log(p_cur / p_prev) * 100.0])
    return _roll_annualise(rets, windows)


def ratio_series(numer: list[list], denom: list[list], ndigits: int = 2) -> list[list]:
    """兩條價格序列相除，回傳 [[日期, 比值], ...]。

    **內連接**：只保留兩邊都有報價的日期。LBMA 的黃金與白銀各有各的休市日——
    2006 年起白銀多出 40 個黃金沒有的交易日（1968 年起算則差 160 多天）。
    用前值補會憑空造出「當天比值變動」，而金銀比的用途正是看它怎麼動，
    補出來的動就是假訊號。

    分母非正一律跳過——理論上貴金屬不會有零或負價，但這條同時擋掉了
    資料源給空值時算出 inf 的情形，成本只有一行。
    """
    d = {x[0]: x[1] for x in denom}
    out = []
    for date, n in numer:
        v = d.get(date)
        if n is None or v is None or v <= 0:
            continue
        out.append([date, round(n / v, ndigits)])
    return out


def _roll_annualise(diffs: list[list], windows) -> dict:
    """對 [[日期, 日變動], ...] 做滾動母體標準差並年化。兩個波動度函式共用。"""
    out = {f"rv{w}": [] for w in windows}
    for w in windows:
        for i in range(len(diffs)):
            if i + 1 < w:
                continue
            chunk = [x[1] for x in diffs[i + 1 - w: i + 1]]
            out[f"rv{w}"].append([diffs[i][0], round(pstdev(chunk) * math.sqrt(TRADING_DAYS), 1)])
    return out
