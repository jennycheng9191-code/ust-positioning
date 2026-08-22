"""CFTC Commitments of Traders — TFF（Traders in Financial Futures）。

CFTC 對同一批部位發兩種口徑：
- **合併版**（Combined）：選擇權部位以 delta 加權換算成期貨當量後併入
- **僅期貨版**（FutOnly）：只算期貨

兩者的欄位定義與歷史區間完全一致（六檔同為 1054／544／860 週），
所以逐週相減就得到**選擇權單獨的部位**——這是本站三種口徑的來源：

    options = combined − futonly

實測 2026-08-18 的 10 年期：合併版未平倉 669.5 萬口、僅期貨 559.7 萬口，
選擇權貢獻 109.8 萬口（19.6%）。要留意的是選擇權撐大的主要是**總量**而非淨方向——
同一天資產管理的淨部位，兩種口徑只差 1,611 口，因為選擇權多空的 delta 大致互抵。

資料授權：CFTC 為美國聯邦政府機關，其報告依 17 U.S.C. §105 不受著作權保護，
可自由使用與再散布。
"""
from __future__ import annotations

from common import get_json, to_date

API_TMPL = "https://publicreporting.cftc.gov/resource/%s.json"

# 三種口徑。options 不是 CFTC 發布的資料集，是本站由前兩者相減得出。
DATASETS = {"combined": "yw9f-hn96", "futonly": "gpe5-46if"}
BASES = [
    {"key": "combined", "zh": "期貨＋選擇權", "note": "選擇權以 delta 加權併入"},
    {"key": "futonly",  "zh": "僅期貨",       "note": "CFTC 另一份報告"},
    {"key": "options",  "zh": "僅選擇權",     "note": "合併版減僅期貨版，本站計算"},
]

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


def fetch_contract(code: str, basis: str = "combined") -> list[dict]:
    """抓單一合約的完整歷史，自動分頁。回傳依報告日由舊到新。

    basis 為 'combined' 或 'futonly'；'options' 不在 CFTC 那邊，由 subtract() 算出來。
    """
    api = API_TMPL % DATASETS[basis]
    rows: list[dict] = []
    offset = 0
    while True:
        batch = get_json(api, {
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


def subtract(combined: list[dict], futonly: list[dict]) -> list[dict]:
    """選擇權部位 ＝ 合併版 − 僅期貨版，逐週逐欄相減。

    只保留兩邊都有的報告日：某一期缺一半就整期跳過，不用單邊資料硬湊。
    交易人數（traders_*）不相減——同一個機構可能同時持有期貨與選擇權，
    兩個數字相減出來的不是任何真實的人數。
    """
    fo = {r["date"]: r for r in futonly}
    out = []
    for c in combined:
        f = fo.get(c["date"])
        if not f:
            continue
        rec = {
            "date": c["date"],
            "oi": _sub(c["oi"], f["oi"]),
            "oi_chg": _sub(c["oi_chg"], f["oi_chg"]),
            "units": c["units"],
            "cats": {},
        }
        for k, cv in c["cats"].items():
            fv = f["cats"].get(k, {})
            rec["cats"][k] = {
                "long": _sub(cv["long"], fv.get("long")),
                "short": _sub(cv["short"], fv.get("short")),
                "spread": _sub(cv["spread"], fv.get("spread")),
                "chg_long": _sub(cv["chg_long"], fv.get("chg_long")),
                "chg_short": _sub(cv["chg_short"], fv.get("chg_short")),
                "traders_long": None,
                "traders_short": None,
            }
        out.append(rec)
    return out


def _sub(a, b):
    if a is None or b is None:
        return None
    return a - b


def fetch_all(basis: str = "combined") -> dict:
    """抓六檔。回傳 {contract_key: [週紀錄...]}。"""
    result = {}
    for c in CONTRACTS:
        rows = normalise(fetch_contract(c["code"], basis))
        if not rows:
            raise RuntimeError(f"{c['key']}（{c['code']}／{basis}）抓不到任何資料")
        result[c["key"]] = rows
    return result


