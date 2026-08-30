"""跨期對帳：拿 CFTC 官方的年度壓縮檔，逐列比對本站的歷史 CSV。

為什麼要有這支（跟 reconcile.py 的分工）：

    reconcile.py         比對「最新一期」。來源是 cftc.gov/dea/newcot/ 的純文字週報，
                         那些檔案只含當期，換週就被覆蓋，驗不到歷史。
    reconcile_history.py 比對「全歷史」。來源是 cftc.gov/files/dea/history/ 的年度壓縮檔。

兩支都是外部對帳——本站抓的是 Socrata API（publicreporting.cftc.gov），
這兩條路都是 CFTC 的另一條發布管道，不是自家管線的輸出。

這支要抓的是 API 悄悄修訂歷史：Socrata 是可覆寫的資料庫，CFTC 若回頭更正某一期，
API 會直接改掉舊值，而本站的 cot_history_*.csv 是一次抓下來就存著的。
只對最新一期永遠看不出這種漂移。

**「僅選擇權」口徑不在這裡對**——它是本站由合併版減僅期貨版得出的，CFTC 沒有對應
的原始報告。它的正確性由 validate.py 的恆等式與零和檢查守住。

抓取注意（跟 reconcile.py 相反，不要照抄那邊的做法）：

    cftc.gov/dea/newcot/       一般 requests 403，要用瀏覽器另存
    cftc.gov/files/dea/history/ 一般 requests 200；curl_cffi 偽裝 Chrome 反而 403

同一個網域兩條路徑的防護行為相反，實測於 2026-08-30。這裡用一般 requests。

用法：
    python scripts/reconcile_history.py                 # 全部六組、全歷史
    python scripts/reconcile_history.py tff             # 只對 TFF 兩個口徑
    python scripts/reconcile_history.py tff combined    # 只對其中一組
    python scripts/reconcile_history.py --keep          # 保留下載的壓縮檔供重跑

下載的壓縮檔預設放在系統暫存區，不進版控（總量約 150MB）。
"""
from __future__ import annotations

import csv
import io
import os
import re
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

from cftc import ASSETS, SCHEMES
from common import DATA, get

BASE = "https://www.cftc.gov/files/dea/history/"

# 本站歷史檔按分頁分開，對帳時要知道哪個合約在哪個檔裡。
HISTORY_FILES = {
    "ust": "cot_history_ust.csv",
    "oil": "cot_history_oil.csv",
    "metals": "cot_history_metals.csv",
}

# 年度檔與多年份合輯的清單。
#
# 三個檔名的坑（猜名字猜不出來，是去官方索引頁 MarketReports/CommitmentsofTraders/
# HistoricalCompressed/index.htm 撈連結才拿到的）：
#
#   1. TFF 與 Disagg 的**年度檔只回溯到 2010 年**，2006–2009 只存在於多年份合輯裡。
#      所以每組都是「一個合輯（涵蓋到 2016）＋ 2017 起的年度檔」，
#      66 個檔就蓋滿全歷史，比逐年抓 157 個少一半。
#   2. **合輯與年度檔的字序相反**：合輯是 fin_com_txt_2006_2016.zip，
#      年度檔是 com_fin_txt_2024.zip——fin 與 com 前後對調。Disagg 則是合輯多一個
#      hist 字段（com_disagg_txt_hist_2006_2016.zip）。
#   3. **Legacy 合併版的底線不一致**：1995–2003 是 deahistfo_1995.zip（有底線），
#      2004 起是 deahistfo2004.zip（沒底線）。這裡用合輯避開，但若哪天要逐年抓要記得。
FIRST_ANNUAL = 2017


def _archives(scheme: str, basis: str, thru: int) -> list[str]:
    bundle, annual = {
        ("tff", "combined"): ("fin_com_txt_2006_2016.zip", "com_fin_txt_%d.zip"),
        ("tff", "futonly"): ("fin_fut_txt_2006_2016.zip", "fut_fin_txt_%d.zip"),
        ("disagg", "combined"): ("com_disagg_txt_hist_2006_2016.zip", "com_disagg_txt_%d.zip"),
        ("disagg", "futonly"): ("fut_disagg_txt_hist_2006_2016.zip", "fut_disagg_txt_%d.zip"),
        ("legacy", "combined"): ("deahistfo_1995_2016.zip", "deahistfo%d.zip"),
        ("legacy", "futonly"): ("deacot1986_2016.zip", "deacot%d.zip"),
    }[(scheme, basis)]
    return [bundle] + [annual % y for y in range(FIRST_ANNUAL, thru + 1)]


# 各分類法在壓縮檔裡的欄位名稱。
#
# 這張表是對帳的核心，**必須獨立於 cftc.py 的 API 欄位名手寫**——照著 API 那份改大小寫
# 就變成拿自家的對應去驗自家的對應，驗不出接錯欄位。校驗基準只能來自外部。
#
# 三種格式的表頭風格完全不同：
#   TFF／Disagg  底線式（Dealer_Positions_Long_All）
#   Legacy       空格式（Noncommercial Positions-Long (All)），且第 13 欄的表頭
#                本身帶一個前置空白——比對前一律正規化，不要靠字面相等。
#
# ⚠ Disagg 的 Swap__Positions_Short_All 與 Swap__Positions_Spread_All 是**雙底線**，
#   多方欄 Swap_Positions_Long_All 只有一個。這個拼字錯誤在壓縮檔裡跟 API 裡一模一樣
#   （2026-08-30 實測確認），所以它是 CFTC 資料本身的錯，不是 Socrata 的轉換問題。
ARCHIVE_COLS = {
    "tff": {
        "date": "Report_Date_as_YYYY-MM-DD",
        "code": "CFTC_Contract_Market_Code",
        "oi": "Open_Interest_All",
        "cats": {
            "dealer": ("Dealer_Positions_Long_All", "Dealer_Positions_Short_All",
                       "Dealer_Positions_Spread_All"),
            "asset_mgr": ("Asset_Mgr_Positions_Long_All", "Asset_Mgr_Positions_Short_All",
                          "Asset_Mgr_Positions_Spread_All"),
            "lev_money": ("Lev_Money_Positions_Long_All", "Lev_Money_Positions_Short_All",
                          "Lev_Money_Positions_Spread_All"),
            "other_rept": ("Other_Rept_Positions_Long_All", "Other_Rept_Positions_Short_All",
                           "Other_Rept_Positions_Spread_All"),
            "nonrept": ("NonRept_Positions_Long_All", "NonRept_Positions_Short_All", None),
        },
    },
    "disagg": {
        "date": "Report_Date_as_YYYY-MM-DD",
        "code": "CFTC_Contract_Market_Code",
        "oi": "Open_Interest_All",
        "cats": {
            "prod_merc": ("Prod_Merc_Positions_Long_All", "Prod_Merc_Positions_Short_All", None),
            "swap": ("Swap_Positions_Long_All", "Swap__Positions_Short_All",
                     "Swap__Positions_Spread_All"),
            "m_money": ("M_Money_Positions_Long_All", "M_Money_Positions_Short_All",
                        "M_Money_Positions_Spread_All"),
            "other_rept": ("Other_Rept_Positions_Long_All", "Other_Rept_Positions_Short_All",
                           "Other_Rept_Positions_Spread_All"),
            "nonrept": ("NonRept_Positions_Long_All", "NonRept_Positions_Short_All", None),
        },
    },
    "legacy": {
        "date": "As of Date in Form YYYY-MM-DD",
        "code": "CFTC Contract Market Code",
        "oi": "Open Interest (All)",
        "cats": {
            "noncomm": ("Noncommercial Positions-Long (All)",
                        "Noncommercial Positions-Short (All)",
                        "Noncommercial Positions-Spreading (All)"),
            "comm": ("Commercial Positions-Long (All)", "Commercial Positions-Short (All)", None),
            "nonrept": ("Nonreportable Positions-Long (All)",
                        "Nonreportable Positions-Short (All)", None),
        },
    },
}


def norm(s: str) -> str:
    """表頭正規化：只留小寫英數。

    Legacy 的表頭有前置空白、括號與連字號，年份不同的檔案標點也不完全一致，
    靠字面相等比對會在某一年突然全部找不到欄位。
    """
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse_num(s: str) -> int | None:
    s = s.strip().replace(",", "")
    if s in ("", "."):
        return None
    return int(float(s))


def parse_date(s: str) -> str:
    """把報告日正規化成 YYYY-MM-DD。

    ⚠ **欄位名稱會騙人**：多年份合輯的欄位也叫 `Report_Date_as_YYYY-MM-DD`，
    但值是 `12/27/2016 12:00:00 AM`（美式月/日/年，還帶時間）；只有 2017 起的
    年度檔才真的是 `2016-12-27`。

    這個差異不會讓程式報錯，只會讓 2006–2016 那十一年一列都對不上，
    然後印出「0 個不符」——因為對不上的列根本沒進到比對迴圈。
    這也是為什麼 reconcile_one 一定要回報「本站有、官方沒有的報告日」：
    只數不符的個數，會把整段漏掉的歷史讀成通過。

    不用 As_of_Date_In_Form_YYMMDD 那欄是因為它只有兩位數年份，
    Legacy 要回溯到 1986 年，跨世紀得自己訂 pivot，更容易錯。
    """
    s = s.strip()
    if not s:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        mm, dd, yyyy = m.groups()
        return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
    raise RuntimeError(f"看不懂的報告日格式：{s!r}")


def contract_index() -> tuple[dict[str, str], dict[str, str]]:
    """回傳 {合約代碼: 合約 key} 與 {合約 key: 所屬分頁}。"""
    code_to_key, key_to_asset = {}, {}
    for a in ASSETS:
        for c in a["contracts"]:
            code_to_key[c["code"]] = c["key"]
            key_to_asset[c["key"]] = a["key"]
    return code_to_key, key_to_asset


def load_ours(scheme: str, basis: str) -> dict[tuple[str, str, str], dict]:
    """讀本站歷史 CSV，回傳 {(日期, 合約, 類別): 該列}。"""
    out = {}
    for fname in HISTORY_FILES.values():
        path = DATA / fname
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["scheme"] == scheme and r["basis"] == basis:
                    out[(r["date"], r["contract"], r["category"])] = r
    return out


def fetch_archive(fname: str, cache: Path) -> str:
    """下載並解開一個壓縮檔，回傳裡面那份純文字。"""
    blob = cache / fname
    if blob.exists():
        data = blob.read_bytes()
    else:
        data = get(BASE + fname, timeout=180).content
        blob.write_bytes(data)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in z.namelist() if n.lower().endswith(".txt")]
    if len(names) != 1:
        raise RuntimeError(f"{fname} 裡有 {len(names)} 個 txt，預期 1 個：{z.namelist()}")
    return z.read(names[0]).decode("utf-8", errors="replace")


def official_rows(scheme: str, basis: str, thru: int, cache: Path,
                  wanted_codes: set[str]) -> dict[tuple[str, str], dict[str, str]]:
    """把該分類法／口徑的所有年度檔讀成 {(日期, 合約代碼): {欄位名: 原始字串}}。

    只留下本站有在追的合約，否則光 Legacy 一份就有上千個商品，記憶體吃不消。
    """
    spec = ARCHIVE_COLS[scheme]
    want_headers = {spec["date"], spec["code"], spec["oi"]}
    for lo, sh, sp in spec["cats"].values():
        want_headers.update(x for x in (lo, sh, sp) if x)
    want_norm = {norm(h): h for h in want_headers}

    rows: dict[tuple[str, str], dict[str, str]] = {}
    for fname in _archives(scheme, basis, thru):
        text = fetch_archive(fname, cache)
        rd = csv.reader(io.StringIO(text))
        header = next(rd)
        idx = {}
        for i, h in enumerate(header):
            n = norm(h)
            if n in want_norm and want_norm[n] not in idx:
                idx[want_norm[n]] = i
        missing = want_headers - set(idx)
        if missing:
            raise RuntimeError(f"{fname} 缺少欄位：{sorted(missing)}")
        for row in rd:
            if len(row) <= max(idx.values()):
                continue
            code = row[idx[spec["code"]]].strip()
            if code not in wanted_codes:
                continue
            d = parse_date(row[idx[spec["date"]]])
            rows[(d, code)] = {h: row[i] for h, i in idx.items()}
        print(f"    {fname:38s} 累計 {len(rows):,} 列")
    return rows


def reconcile_one(scheme: str, basis: str, thru: int, cache: Path) -> tuple[int, int, list[str]]:
    spec = ARCHIVE_COLS[scheme]
    code_to_key, _ = contract_index()
    # 只對這套分類法涵蓋得到的分頁。拿 disagg 去對美債會整批「不在報告裡」。
    codes = {c["code"] for a in ASSETS if scheme in a["schemes"] for c in a["contracts"]}
    key_to_code = {code_to_key[c]: c for c in codes}

    print(f"\n=== {SCHEMES[scheme]['zh']} ／ {basis} ===")
    ours = load_ours(scheme, basis)
    if not ours:
        print("  本站沒有這組的歷史列，跳過")
        return 0, 0, []
    official = official_rows(scheme, basis, thru, cache, codes)

    checked = bad = 0
    problems: list[str] = []
    missing_dates: set[str] = set()

    for (d, contract, cat), mine in sorted(ours.items()):
        code = key_to_code.get(contract)
        if not code:
            continue
        row = official.get((d, code))
        if row is None:
            missing_dates.add(d)
            continue
        cols = spec["cats"].get(cat)
        if not cols:
            continue
        pairs = [("long", cols[0]), ("short", cols[1]), ("spread", cols[2]), ("oi", spec["oi"])]
        for field, col in pairs:
            if col is None:
                continue
            want = parse_num(row[col])
            got = parse_num(mine[field]) if mine[field] != "" else None
            if want is None and got is None:
                continue
            checked += 1
            if want != got:
                bad += 1
                problems.append(
                    f"{d} {contract} {cat} {field}：官方 {want} vs 本站 {got}")

    if missing_dates:
        ds = sorted(missing_dates)
        problems.append(
            f"本站有、官方年度檔沒有的報告日 {len(ds)} 天："
            f"{ds[0]} … {ds[-1]}（前五天 {ds[:5]}）")

    # 反方向也要查：官方有、本站沒有的週。
    #
    # 只比對「本站已有的列」會漏掉整週不見的情況——Socrata 單次回傳上限 1000 筆，
    # 沒分頁就會靜默截斷，抓到的每個數字都對，但序列少了一截。
    # 逐列比對對這種缺漏完全無感，必須另外數週數。
    ours_dates = {(d, contract) for (d, contract, _) in ours}
    dropped = sorted({
        (d, code_to_key[code]) for (d, code) in official
        if (d, code_to_key[code]) not in ours_dates
    })
    if dropped:
        by_contract: dict[str, list[str]] = defaultdict(list)
        for d, k in dropped:
            by_contract[k].append(d)
        for k, ds in sorted(by_contract.items()):
            problems.append(
                f"官方有、本站缺的報告日 {k} 共 {len(ds)} 天："
                f"{ds[0]} … {ds[-1]}（前五天 {ds[:5]}）")

    print(f"  比對 {checked:,} 個數字，{bad} 個不符"
          f"{f'，本站多出 {len(missing_dates)} 天' if missing_dates else ''}"
          f"{f'，本站缺 {len(dropped)} 個(週×合約)' if dropped else ''}")
    return checked, bad, problems


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keep = "--keep" in sys.argv

    schemes = [args[0]] if args else list(ARCHIVE_COLS)
    bases = [args[1]] if len(args) > 1 else ["combined", "futonly"]
    for s in schemes:
        if s not in ARCHIVE_COLS:
            print("用法：reconcile_history.py [tff|disagg|legacy] [combined|futonly] [--keep]",
                  file=sys.stderr)
            sys.exit(2)

    cache = Path(os.environ.get("COT_CACHE") or (Path(tempfile.gettempdir()) / "cot_history_zips"))
    cache.mkdir(parents=True, exist_ok=True)
    print(f"壓縮檔快取：{cache}")

    thru = date.today().year
    total = mismatches = 0
    all_problems: list[str] = []
    for s in schemes:
        for b in bases:
            c, m, p = reconcile_one(s, b, thru, cache)
            total += c
            mismatches += m
            all_problems += [f"[{s}/{b}] {x}" for x in p]

    if all_problems:
        print("\n--- 需要處理的項目 ---")
        for p in all_problems[:200]:
            print("  " + p)
        if len(all_problems) > 200:
            print(f"  …另有 {len(all_problems) - 200} 項未列出")

    print(f"\n共比對 {total:,} 個數字，{mismatches} 個不符。")
    if not keep:
        print(f"（壓縮檔留在 {cache}，下次重跑會直接沿用；要重抓就把該目錄刪掉）")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
