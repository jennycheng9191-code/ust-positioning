"""CFTC Commitments of Traders — 三套分類法 × 三種口徑 × 三個資產分頁。

**資產分頁**（見 ASSETS）：本站分成美債、原油、貴金屬三頁，
差別不只是換合約代碼——CFTC 對金融期貨與實體商品發的是不同的報告：

    美債         TFF（金融期貨五類）＋ Legacy
    原油／貴金屬  Disaggregated（實體商品五類）＋ Legacy

**分類法**（見 SCHEMES）：

- **TFF**（2006 起）：交易商／資產管理／槓桿基金／其他可報告／小戶。只有金融期貨有。
- **Disaggregated**（2006 起）：生產商貿易商／交換商／管理基金／其他可報告／小戶。
  只有實體商品有。「管理基金」（Managed Money）就是商品市場講的投機資金，
  角色上對應 TFF 的槓桿基金；「生產商／貿易商」是真正持有現貨的避險方。
- **Legacy**（1986／1995 起）：非商業（投機）／商業（避險）／小戶。所有市場都有。

Legacy 對商品其實比對美債好用——它的「商業」原意就是持有現貨、用期貨避險的人，
套在原油與黃金上名副其實；套在美債上才是大雜燴（資產管理與交易商都被歸進去）。
所以商品頁的 Legacy 不只是「多 20 年歷史」，它本身就有解讀價值。

**口徑**（見 BASES）：每套分類法各有合併版與僅期貨版兩個資料集，欄位定義一致，
逐週相減就得到選擇權單獨的部位：

    options = combined − futonly

但**歷史起點不一定一致**，以 WTI 為例：

    Legacy 僅期貨   1986-01-15 起（40 年）
    Legacy 合併版   1995-03-21 起（31 年）← CFTC 這年才開始發含選擇權的版本
    Disagg 兩種口徑 2006-06-13 起（20 年）

所以 subtract() 只保留兩邊都有的報告日。極端度的樣本數因此隨分類法與口徑而異，
頁面上要標出來，不可並排解讀。

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

# Disaggregated 五類。實體商品專用，2009 年推出、回溯編制到 2006-06-13。
#
# 欄位命名的坑比 TFF 更多，全部照抄 CFTC 原樣，不要「順手修正」：
#   · prod_merc 的部位欄不帶 _all（prod_merc_positions_long），且**沒有 spread 欄位**
#     ——生產商在 CFTC 的定義下不列價差部位，這不是漏抓。
#   · swap 的空方與價差欄名是 `swap__positions_short_all`、`swap__positions_spread_all`，
#     **中間兩個底線**，但多方欄 `swap_positions_long_all` 只有一個。這是 CFTC 資料集
#     本身的既有拼字錯誤（同類的還有 Legacy 的 noncomm spread 少一個 r），只能照抄。
#   · other_rept 的部位欄不帶 _all，但 traders 的多方欄帶、空方欄不帶。
DISAGG_CATEGORIES = [
    {
        "key": "prod_merc", "zh": "生產商／貿易商", "en": "Producer/Merchant/Processor/User",
        "long": "prod_merc_positions_long", "short": "prod_merc_positions_short",
        "spread": None,
        "chg_long": "change_in_prod_merc_long", "chg_short": "change_in_prod_merc_short",
        "traders_long": "traders_prod_merc_long_all", "traders_short": "traders_prod_merc_short_all",
    },
    {
        "key": "swap", "zh": "交換商", "en": "Swap Dealers",
        "long": "swap_positions_long_all", "short": "swap__positions_short_all",
        "spread": "swap__positions_spread_all",
        "chg_long": "change_in_swap_long_all", "chg_short": "change_in_swap_short_all",
        "traders_long": "traders_swap_long_all", "traders_short": "traders_swap_short_all",
    },
    {
        "key": "m_money", "zh": "管理基金", "en": "Managed Money",
        "long": "m_money_positions_long_all", "short": "m_money_positions_short_all",
        "spread": "m_money_positions_spread",
        "chg_long": "change_in_m_money_long_all", "chg_short": "change_in_m_money_short_all",
        "traders_long": "traders_m_money_long_all", "traders_short": "traders_m_money_short_all",
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

# Legacy 三類。CFTC 最早的分類法，實體商品的僅期貨版可回溯到 1986 年。
# 市場新聞講的「投機客淨部位」指的就是這裡的非商業。
LEGACY_CATEGORIES = [
    {
        "key": "noncomm", "zh": "非商業（投機）", "en": "Non-Commercial",
        "long": "noncomm_positions_long_all", "short": "noncomm_positions_short_all",
        # ⚠ 這一欄有兩個長得都像對的名字，選錯不會報錯、只會在 2000 年以前給出別的數字：
        #     noncomm_postions_spread_all  ← 正確的「All」值（postions 少一個 i，CFTC 的拼字錯誤）
        #     noncomm_positions_spread     ← 拼字是對的，但它裝的是 **Old 作物年度** 的值
        #   兩者在 2000 年以後幾乎完全相同（多數期別分毫不差），所以接錯的話近 26 年
        #   全部正確，只有 1986–1999 會偏掉，而且偏得很大（1987-09-15 長債：
        #   正確 14,390，錯的欄位 6,294）。恆等式驗不出來——validate.py 只驗最新一期。
        #   2026-08-30 的跨期對帳（reconcile_history.py）就是這樣抓出來的。
        #   規則同 swap 的雙底線：**照抄 CFTC 的拼字錯誤，不要挑看起來正確的名字**。
        #   （同一個資料集裡還有 change_in_noncomm_spead_all、traders_noncomm_spead_old
        #     這種少一個 r 的欄位，本站沒用到，但要改動時記得也是照抄不修。）
        "spread": "noncomm_postions_spread_all",
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

# 三套分類法。
#
# 注意 disagg 的兩個資料集代號**與 TFF 的排列相反**：kh3c-gbw2 才是合併版。
# 這不是筆誤，是實測出來的——WTI 在 kh3c-gbw2 的未平倉量 248.3 萬口，
# 在 72hh-3qpy 只有 190.7 萬口，合併版必然大於僅期貨版。照 TFF 的直覺去填會全部接反，
# 而且因為兩份資料的內部恆等式各自成立，validate.py 的恆等式檢查抓不出來。
SCHEMES = {
    "tff": {
        "zh": "TFF 五類", "en": "Traders in Financial Futures",
        "note": "2006 年起。對金融期貨分得較細，看得到基差交易的對峙",
        "datasets": {"combined": "yw9f-hn96", "futonly": "gpe5-46if"},
        "categories": TFF_CATEGORIES,
    },
    "disagg": {
        "zh": "Disagg 五類", "en": "Disaggregated",
        "note": "2006 年起。實體商品專用，把避險的生產商與投機的管理基金分開",
        "datasets": {"combined": "kh3c-gbw2", "futonly": "72hh-3qpy"},
        "categories": DISAGG_CATEGORIES,
    },
    "legacy": {
        "zh": "Legacy 三類", "en": "Legacy",
        "note": "商品可回溯至 1986 年。市場新聞引用的「投機客淨部位」即此處的非商業",
        "datasets": {"combined": "jun7-fc8e", "futonly": "6dca-aqww"},
        "categories": LEGACY_CATEGORIES,
    },
}

# 分類法在各資產頁的解讀提醒。同一套 Legacy 在美債與商品上的可信度天差地遠，
# 這段字直接進頁面，避免把兩者當同一回事讀。
SCHEME_NOTES = {
    ("tff", "ust"):
        "資產管理機構淨多對槓桿基金淨空的對峙，就是基差交易（basis trade）的體溫計。",
    ("legacy", "ust"):
        "<b>Legacy 用在美債要打折扣</b>——它的「商業」原意是持有現貨、用期貨避險的人，"
        "套到美債上，資產管理機構與交易商都被歸進去，所以它的「非商業淨空」跟 TFF 的"
        "「槓桿基金淨空」不是同一件事，數量級也不同。CFTC 後來另出 TFF 就是為了這個。",
    ("disagg", "oil"):
        "管理基金（Managed Money）就是原油市場講的投機資金，角色對應美債頁的槓桿基金；"
        "生產商／貿易商是真正持有現貨的避險方，其淨空部位反映的是產業的避險壓力，"
        "不是對油價的看法。",
    ("legacy", "oil"):
        "<b>Legacy 用在商品名副其實</b>——「商業」就是煉油廠、貿易商這些真的碰到現貨的人，"
        "「非商業」就是投機資金。它與 Disagg 的差別在細緻度不在正確性："
        "Disagg 把商業再拆成生產商與交換商，而交換商多半是替商品指數基金做對手方，"
        "行為偏被動，跟真正的避險需求混在一起會看不清。",
    ("disagg", "metals"):
        "管理基金是貴金屬的方向性投機主力，看極端度最有意義的就是這一類。"
        "交換商在金銀市場多半是銀行替客戶做的對手方部位，會與管理基金呈鏡像，"
        "兩者一起看才知道多空是誰對誰。",
    ("legacy", "metals"):
        "<b>Legacy 用在貴金屬要留意「商業」的組成</b>——黃金白銀沒有煉油廠那樣的天然賣方，"
        "這裡的商業主要是造市的自營商與精煉／庫存業者，其淨空部位多是替投機方做對手方的結果，"
        "不宜直接讀成「產業界看空」。要看方向性投機，Disagg 的管理基金乾淨得多。",
}

# 三個資產分頁。
#
# 合約選擇的理由：
#   WTI 067651 是 NYMEX 的實體交割合約，OI 248 萬口，全球原油的定價中心。
#     ICE 的 Brent 本尊歸英國 FCA 管，CFTC 沒有；NYMEX 那檔 Brent Last Day（06765T）
#     只回溯到 2011 年、OI 不到 WTI 的六分之一，代表性不足，故不納入部位面，
#     只在波動度模組放 Brent 價格供對照。
#   黃金 088691 與白銀 084691 都是 COMEX 主約，Legacy 僅期貨版自 1986 年起。
#     Micro Gold 088695 是同一批部位的縮小版，重複計算，不納入。
ASSETS = [
    {
        "key": "ust", "zh": "美債", "sub": "六檔美債合約 ‧ 金融期貨五類",
        "schemes": ["tff", "legacy"],
        "default_contract": "ust10y",
        "contracts": [
            {"key": "ust2y",     "code": "042601", "zh": "2 年期",        "short": "2Y"},
            {"key": "ust5y",     "code": "044601", "zh": "5 年期",        "short": "5Y"},
            {"key": "ust10y",    "code": "043602", "zh": "10 年期",       "short": "10Y"},
            {"key": "ultra10y",  "code": "043607", "zh": "超長 10 年期",  "short": "U10Y"},
            {"key": "ustbond",   "code": "020601", "zh": "長債",          "short": "BOND"},
            {"key": "ultrabond", "code": "020604", "zh": "超長債",        "short": "UBOND"},
        ],
    },
    {
        "key": "oil", "zh": "原油", "sub": "NYMEX WTI ‧ 實體商品五類",
        "schemes": ["disagg", "legacy"],
        "default_contract": "wti",
        "contracts": [
            {"key": "wti", "code": "067651", "zh": "WTI 原油", "short": "WTI"},
        ],
    },
    {
        "key": "metals", "zh": "貴金屬", "sub": "COMEX 黃金／白銀 ‧ 實體商品五類",
        "schemes": ["disagg", "legacy"],
        "default_contract": "gold",
        "contracts": [
            {"key": "gold",   "code": "088691", "zh": "黃金", "short": "GOLD"},
            {"key": "silver", "code": "084691", "zh": "白銀", "short": "SILVER"},
        ],
    },
]

ASSET_BY_KEY = {a["key"]: a for a in ASSETS}

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
            # CFTC 把單位寫成 "(CONTRACTS OF 1,000 BARRELS)"，外層那對括號沒有資訊，
            # 留著在頁面上會變成括號中又套括號。內容照抄不翻譯——這行字要能直接對回原始報告。
            "units": (r.get("contract_units") or "").strip().strip("()").strip(),
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


def fetch_asset(asset: dict, scheme: str, basis: str) -> dict:
    """抓某一資產分頁的所有合約。回傳 {contract_key: [週紀錄...]}。"""
    result = {}
    for c in asset["contracts"]:
        rows = normalise(fetch_contract(c["code"], scheme, basis), scheme)
        if not rows:
            raise RuntimeError(
                f"{asset['key']}／{c['key']}（{c['code']}／{scheme}／{basis}）抓不到任何資料")
        result[c["key"]] = rows
    return result
