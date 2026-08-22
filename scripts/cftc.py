"""CFTC Commitments of Traders — 兩套分類法 × 三種口徑。

**分類法**（見 SCHEMES）：對美債而言 CFTC 只發兩份，
Disaggregated 那份是實體商品用的，不含美債。

- **TFF**（2006 起）：交易商／資產管理／槓桿基金／其他可報告／小戶，共五類
- **Legacy**（1995 起）：非商業（投機）／商業（避險）／小戶，共三類

兩者不可互相取代，也不該混用——它們回答的是不同問題。同一天的 10 年期
（2026-08-18）：Legacy 說投機客淨空 87.6 萬，TFF 說槓桿基金淨空 217 萬。
差距來自 Legacy 的「商業」對金融期貨是個大雜燴，資產管理與交易商都被歸進去。

**口徑**（見 BASES）：每套分類法各有合併版與僅期貨版兩個資料集，欄位定義一致，
逐週相減就得到選擇權單獨的部位：

    options = combined − futonly

但**歷史起點不一定一致**，以 10 年期為例：

    Legacy 僅期貨   1986-01-15 起（1931 週，40 年）
    Legacy 合併版   1995-03-21 起（1640 週，31 年）← CFTC 這年才開始發含選擇權的版本
    TFF 兩種口徑    2006-06-13 起（1054 週，20 年）

所以 subtract() 只保留兩邊都有的報告日，Legacy 的選擇權序列自 1995 起。
極端度的樣本數因此隨分類法與口徑而異，頁面上要標出來，不可並排解讀。

2026-08-18 的 10 年期：合併版 669.5 萬口，其中選擇權 109.8 萬口（佔 16.4%）。
選擇權撐大的主要是**總量**而非淨方向——同期資產管理的淨部位在兩種口徑下只差
1,611 口，因為選擇權多空的 delta 大致互抵。

資料授權：CFTC 為美國聯邦政府機關，其報告依 17 U.S.C. §105 不受著作權保護，
可自由使用與再散布。
"""
from __future__ import annotations

from common import get_json, to_date

API_TMPL = "https://publicreporting.cftc.gov/resource/%s.json"

# 三種口徑。options 不是 CFTC 發布的資料集，是本站由前兩者相減得出。
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
TFF_CATEGORIES = [
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

# Legacy 三類。CFTC 最早的分類法，1995 年就有——比 TFF 多 11 年歷史，
# 市場新聞講的「投機客淨部位」指的就是這裡的非商業。
#
# 但要注意它對金融期貨很粗糙：「商業」原意是持有現貨、用期貨避險的人，
# 套到美債上，資產管理機構與交易商都被歸進去，所以 Legacy 的
# 「非商業淨空」跟 TFF 的「槓桿基金淨空」不是同一件事，數量級也不同
# （2026-08-18 的 10 年期：前者 −87.6 萬，後者 −217 萬）。CFTC 後來另出 TFF 就是為了這個。
LEGACY_CATEGORIES = [
    {
        "key": "noncomm", "zh": "非商業（投機）", "en": "Non-Commercial",
        "long": "noncomm_positions_long_all", "short": "noncomm_positions_short_all",
        "spread": "noncomm_positions_spread",
        # CFTC 這個欄位名少了一個 r（spead），是他們資料集本身的既有拼字錯誤，只能照抄
        "chg_long": "change_in_noncomm_long_all", "chg_short": "change_in_noncomm_short_all",
        "traders_long": "traders_noncomm_long_all", "traders_short": "traders_noncomm_short_all",
    },
    {
        "key": "comm", "zh": "商業（避險）", "en": "Commercial",
        "long": "comm_positions_long_all", "short": "comm_positions_short_all",
        "spread": None,
        "chg_long": "change_in_comm_long_all", "chg_short": "change_in_comm_short_all",
        "traders_long": "traders_comm_long_all", "traders_short": "traders_comm_short_all",
    },
    {
        "key": "nonrept", "zh": "非報告小戶", "en": "Non-Reportable",
        "long": "nonrept_positions_long_all", "short": "nonrept_positions_short_all",
        "spread": None,
        "chg_long": "change_in_nonrept_long_all", "chg_short": "change_in_nonrept_short_all",
        "traders_long": None, "traders_short": None,
    },
]

# 兩套分類法。對美債而言 CFTC 只有這兩份——Disaggregated 那份是實體商品用的，不含美債。
SCHEMES = {
    "tff": {
        "zh": "TFF 五類", "en": "Traders in Financial Futures",
        "note": "2006 年起。對金融期貨分得較細，看得到基差交易的對峙",
        "datasets": {"combined": "yw9f-hn96", "futonly": "gpe5-46if"},
        "categories": TFF_CATEGORIES,
    },
    "legacy": {
        "zh": "Legacy 三類", "en": "Legacy",
        "note": "1995 年起。市場新聞引用的「投機客淨部位」即此處的非商業",
        "datasets": {"combined": "jun7-fc8e", "futonly": "6dca-aqww"},
        "categories": LEGACY_CATEGORIES,
    },
}

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


def fetch_contract(code: str, scheme: str = "tff", basis: str = "combined") -> list[dict]:
    """抓單一合約的完整歷史，自動分頁。回傳依報告日由舊到新。

    basis 為 'combined' 或 'futonly'；'options' 不在 CFTC 那邊，由 subtract() 算出來。
    """
    api = API_TMPL % SCHEMES[scheme]["datasets"][basis]
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


def normalise(raw: list[dict], scheme: str = "tff") -> list[dict]:
    """挑出需要的欄位並轉成數字，一列一個報告日。"""
    categories = SCHEMES[scheme]["categories"]
    out = []
    for r in raw:
        rec = {
            "date": to_date(r["report_date_as_yyyy_mm_dd"]).isoformat(),
            "oi": _num(r.get("open_interest_all")),
            "oi_chg": _num(r.get("change_in_open_interest_all")),
            "units": (r.get("contract_units") or "").strip(),
            "cats": {},
        }
        for c in categories:
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


def fetch_all(scheme: str = "tff", basis: str = "combined") -> dict:
    """抓六檔。回傳 {contract_key: [週紀錄...]}。"""
    result = {}
    for c in CONTRACTS:
        rows = normalise(fetch_contract(c["code"], scheme, basis), scheme)
        if not rows:
            raise RuntimeError(f"{c['key']}（{c['code']}／{scheme}／{basis}）抓不到任何資料")
        result[c["key"]] = rows
    return result


