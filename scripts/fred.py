"""FRED 公債殖利率 — 已實現波動模組的原始資料。

用 fredgraph.csv 端點，不需要 API key。
序列本身是美國財政部每日公布的 CMT 殖利率，同樣屬公開資料。
"""
from __future__ import annotations

from common import get

CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"

SERIES = [
    {"key": "y2",  "id": "DGS2",  "zh": "2 年期"},
    {"key": "y5",  "id": "DGS5",  "zh": "5 年期"},
    {"key": "y10", "id": "DGS10", "zh": "10 年期"},
    {"key": "y30", "id": "DGS30", "zh": "30 年期"},
]

START = "2006-01-01"   # 對齊 COT 最早的 2006-06-13，前置半年供滾動窗暖機


def fetch_series(series_id: str, start: str = START) -> list[list]:
    """回傳 [[日期, 殖利率], ...]，跳過休市日（FRED 以 '.' 表示）。"""
    r = get(CSV, {"id": series_id, "cosd": start})
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


def fetch_all() -> dict:
    return {s["key"]: fetch_series(s["id"]) for s in SERIES}
