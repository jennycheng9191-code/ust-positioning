"""CFTC Commitments of Traders — TFF（Traders in Financial Futures）期貨＋選擇權合併版。

為什麼用「合併版」而不是「僅期貨版」：合併版把選擇權部位以 delta 加權後併入，
本專案原本要做的是選擇權未平倉分析，CME 授權不允許（見 docs/source-audit.md），
改走 COT 之後，合併版是唯一還保留選擇權資訊的口徑。

資料授權：CFTC 為美國聯邦政府機關，其報告依 17 U.S.C. §105 不受著作權保護，
可自由使用與再散布。
"""
from __future__ import annotations

from common import get_json, to_date

API = "https://publicreporting.cftc.gov/resource/yw9f-hn96.json"

# 六檔美債合約。code 為 CFTC 的 cftc_contract_market_code。
# 歷史起點各不相同，Ultra 兩檔上市較晚——極端度統計要標示樣本數，不可跟 20 年樣本並排。
CONTRACTS = [
    {"key": "ust2y",     "code": "042601", "zh": "2 年期",        "short": "2Y"},
    {"key": "ust5y",     "code": "044601", "zh": "5 年期",        "short": "5Y"},
    {"key": "ust10y",    "code": "043602", "zh": "10 年期",       "short": "10Y"},
    {"key": "ultra10y",  "code": "043607", "zh": "超長 10 年期",  "short": "U10Y"},
    {"key": "ustbond",   "code": "020601", "zh": "長債",          "short": "BOND"},
    {"key": "ultrabond", "code": "020604", "zh": "超長債",        "short": "UBOND"},
]

# TFF 五類交易人。欄位命名在 CFTC 那邊並不一致——dealer 與 nonrept 帶 _all 後綴，
# 其餘三類沒有；nonrept 另外沒有 spread 欄位。這張表就是為了把這些差異集中在一處。
CATEGORIES = [
    {
        "key": "dealer", "zh": "交易商／中介", "en": "Dealer/Intermediary",
        "long": "dealer_positions_long_all", "short": "dealer_positions_short_all",
        "spread": "dealer_positions_spread_all",
        "chg_long": "change_in_dealer_long_all", "chg_short": "change_in_dealer_short_all",
        "traders_long": "traders_dealer_long_all", "traders_short": "traders_dealer_short_all",
    },
    {
        "key": "asset_mgr", "zh": "資產管理機構", "en": "Asset Manager/Institutional",
        "long": "asset_mgr_positions_long", "short": "asset_mgr_positions_short",
        "spread": "asset_mgr_positions_spread",
        "chg_long": "change_in_asset_mgr_long", "chg_short": "change_in_asset_mgr_short",
        "traders_long": "traders_asset_mgr_long_all", "traders_short": "traders_asset_mgr_short_all",
    },
    {
        "key": "lev_money", "zh": "槓桿基金", "en": "Leveraged Funds",
        "long": "lev_money_positions_long", "short": "lev_money_positions_short",
        "spread": "lev_money_positions_spread",
        "chg_long": "change_in_lev_money_long", "chg_short": "change_in_lev_money_short",
        "traders_long": "traders_lev_money_long_all", "traders_short": "traders_lev_money_short_all",
    },
    {
        "key": "other_rept", "zh": "其他可報告戶", "en": "Other Reportables",
        "long": "other_rept_positions_long", "short": "other_rept_positions_short",
        "spread": "other_rept_positions_spread",
        "chg_long": "change_in_other_rept_long", "chg_short": "change_in_other_rept_short",
        "traders_long": "traders_other_rept_long_all", "traders_short": "traders_other_rept_short",
    },
    {
        "key": "nonrept", "zh": "非報告小戶", "en": "Non-Reportable",
        "long": "nonrept_positions_long_all", "short": "nonrept_positions_short_all",
        "spread": None,
        "chg_long": "change_in_nonrept_long_all", "chg_short": "change_in_nonrept_short_all",
        "traders_long": None, "traders_short": None,
    },
]

PAGE = 1000  # Socrata 單次回傳上限就是 1000，不分頁會靜默截斷


def _num(v):
    """CFTC 的數字以字串回傳，缺值為 None、空字串或 '-'。

    Socrata API 回的數字沒有千分位，但 CFTC 官網的 CSV 下載有。
    這裡一併吃掉逗號，日後若改走 CSV 來源不必再改一次。
    """
    if v in (None, "", "-"):
        return None
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def fetch_contract(code: str) -> list[dict]:
    """抓單一合約的完整歷史，自動分頁。回傳依報告日由舊到新。"""
    rows: list[dict] = []
    offset = 0
    while True:
        batch = get_json(API, {
            "cftc_contract_market_code": code,
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": PAGE,
            "$offset": offset,
        })
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def normalise(raw: list[dict]) -> list[dict]:
    """挑出需要的欄位並轉成數字，一列一個報告日。"""
    out = []
    for r in raw:
        rec = {
            "date": to_date(r["report_date_as_yyyy_mm_dd"]).isoformat(),
            "oi": _num(r.get("open_interest_all")),
            "oi_chg": _num(r.get("change_in_open_interest_all")),
            "units": (r.get("contract_units") or "").strip(),
            "cats": {},
        }
        for c in CATEGORIES:
            rec["cats"][c["key"]] = {
                "long": _num(r.get(c["long"])),
                "short": _num(r.get(c["short"])),
                "spread": _num(r.get(c["spread"])) if c["spread"] else None,
                "chg_long": _num(r.get(c["chg_long"])),
                "chg_short": _num(r.get(c["chg_short"])),
                "traders_long": _num(r.get(c["traders_long"])) if c["traders_long"] else None,
                "traders_short": _num(r.get(c["traders_short"])) if c["traders_short"] else None,
            }
        out.append(rec)
    out.sort(key=lambda x: x["date"])
    return out


def fetch_all() -> dict:
    """抓六檔。回傳 {contract_key: [週紀錄...]}。"""
    result = {}
    for c in CONTRACTS:
        rows = normalise(fetch_contract(c["code"]))
        if not rows:
            raise RuntimeError(f"{c['key']}（{c['code']}）抓不到任何資料")
        result[c["key"]] = rows
    return result
