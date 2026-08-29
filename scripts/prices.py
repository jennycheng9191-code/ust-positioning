"""商品價格 — 原油與貴金屬的已實現波動模組原始資料。

美債那頁的波動度算的是**殖利率變動**（fred.py，單位 bp／年）；
商品沒有殖利率，算的是**價格報酬**（單位 %／年）。兩者不可並排比較，
所以價格序列與殖利率序列分成兩支模組，年化方式也在 derive.py 分成兩個函式。

來源與授權：

- **原油**：FRED `DCOILWTICO`（WTI Cushing 現貨）與 `DCOILBRENTEU`（Brent 現貨）。
  原始資料為美國 EIA 公布，公開資料。同 fred.py，**必須用偽裝指紋抓**。

- **黃金／白銀**：LBMA 官方價格 JSON（prices.lbma.org.uk）。
  FRED 原本的 `GOLDAMGBD228NLBM` / `GOLDPMGBD228NLBM` 已下架（現在回 404），
  這是目前唯一免金鑰、回溯到 1968 年、且來自基準管理者本人的日資料。
  取 v[0]（美元），v[1]／v[2] 是英鎊與歐元。

  **授權注意**：LBMA 貴金屬價格由 ICE Benchmark Administration 管理，
  LBMA 官網明載白金與鈀金自 2026-07-01 起「取得、使用或再散布須先取得 IBA 授權」，
  黃金白銀未明文但屬同一類資產。本站是密碼閘門後的個人非商業用途、不轉售不再散布，
  且只存日收盤一個數字用於計算波動度。若日後 LBMA 對金銀比照白金鈀金明文設限，
  這一支要跟著撤掉——不要重蹈 CME 那次的覆轍（見專案計畫書的方向調整紀錄）。

為什麼不用 Stooq 或 Yahoo：Stooq 已上 JavaScript 挑戰，一般客戶端拿不到 CSV；
Yahoo 的 GC=F 抓得到，但那是 CME 的結算價，等於繞道踩回 CME 的授權問題。
"""
from __future__ import annotations

from common import get_impersonated

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
LBMA_JSON = "https://prices.lbma.org.uk/json/%s.json"

START = "2006-01-01"   # 對齊 Disagg 最早的 2006-06-13，前置半年供滾動窗暖機

# 各資產分頁的價格序列。
#
# 原油頁放兩條：部位面只有 WTI（Brent 本尊不歸 CFTC 管），但波動度面把 Brent 一起畫，
# 因為兩者的波動差本身就是價差交易的訊號，而且不用多付任何抓取成本。
# M5 的 Brent-WTI 價差也吃這兩條序列。
SERIES = {
    "oil": [
        {"key": "wti",   "src": "fred", "id": "DCOILWTICO",   "zh": "WTI",   "unit": "美元／桶"},
        {"key": "brent", "src": "fred", "id": "DCOILBRENTEU", "zh": "Brent", "unit": "美元／桶"},
    ],
    "metals": [
        {"key": "gold",   "src": "lbma", "id": "gold_pm", "zh": "黃金", "unit": "美元／盎司"},
        {"key": "silver", "src": "lbma", "id": "silver",  "zh": "白銀", "unit": "美元／盎司"},
    ],
}


def fetch_fred(series_id: str, start: str = START) -> list[list]:
    """回傳 [[日期, 價格], ...]，跳過休市日（FRED 以 '.' 表示）。"""
    r = get_impersonated(FRED_CSV, {"id": series_id, "cosd": start})
    out = []
    for i, line in enumerate(r.text.strip().splitlines()):
        if i == 0:            # 標題列（欄名會隨 FRED 改版變動，靠位置不靠名字）
            continue
        parts = line.split(",")
        if len(parts) < 2:
            continue
        d, v = parts[0].strip(), parts[1].strip()
        if v in (".", ""):    # 休市日
            continue
        try:
            out.append([d, float(v)])
        except ValueError:
            continue
    if not out:
        raise RuntimeError(f"FRED {series_id} 沒有取得任何觀測值")
    return out


def fetch_lbma(name: str, start: str = START) -> list[list]:
    """LBMA 價格 JSON → [[日期, 美元價], ...]。

    格式為 [{"d": "2026-08-28", "v": [美元, 英鎊, 歐元]}, ...]，1968 年起。
    早年只有美元、其餘為 null；偶有整日 v 全 null（結算中斷），一律跳過。
    """
    rows = get_impersonated(LBMA_JSON % name).json()
    out = []
    for r in rows:
        d = r.get("d")
        v = (r.get("v") or [None])[0]
        if not d or d < start or v is None:
            continue
        try:
            out.append([d, float(v)])
        except (TypeError, ValueError):
            continue
    if not out:
        raise RuntimeError(f"LBMA {name} 沒有取得任何觀測值")
    out.sort(key=lambda x: x[0])
    return out


def fetch_one(spec: dict, start: str = START) -> list[list]:
    if spec["src"] == "fred":
        return fetch_fred(spec["id"], start)
    if spec["src"] == "lbma":
        return fetch_lbma(spec["id"], start)
    raise ValueError(f"未知的價格來源：{spec['src']}")


def fetch_asset(asset_key: str) -> dict:
    """抓某一資產分頁的所有價格序列。回傳 {series_key: [[日期, 價格], ...]}。"""
    return {s["key"]: fetch_one(s) for s in SERIES.get(asset_key, [])}
