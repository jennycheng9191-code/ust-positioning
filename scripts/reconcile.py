"""外部對帳：拿 CFTC 官網公布的純文字報告，逐欄比對本站產出的 latest.json。

為什麼要有這支：Socrata API 與這些純文字報告是**兩條不同的發布管道**。
只用 API 自己對自己，驗不出「欄位對應接錯」這類錯誤——例如把 asset_mgr_spread
當成 asset_mgr_short 用，API 內部永遠自洽，只有跟原始報告比才看得出來。
校驗基準必須來自外部，不能是自家管線的輸出。

用法：
    python scripts/reconcile.py                          # 四份全對（直接抓）
    python scripts/reconcile.py tff combined FinComWk.txt   # 指定其中一份，用本機檔

「僅選擇權」口徑不在這裡對——它是本站由合併版減僅期貨版得出的，CFTC 沒有對應的
原始報告。它的正確性由 validate.py 的三道檢查守住：恆等式（各類多方＋價差＝未平倉量）
在相減後仍須成立、各類淨部位加總為零、選擇權未平倉量落在 0 與合併版之間。

cftc.gov 主站有 Akamai 機器人偵測，一般 HTTP 客戶端與 curl_cffi 都會拿到 403。
擋住時請用瀏覽器開下列網址另存，再把檔案路徑當第三個參數傳進來。
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

from cftc import CONTRACTS, SCHEMES
from common import DATA, get, read_json

BASE = "https://www.cftc.gov/dea/newcot/"

# 各報告的欄位位置。這張表是對帳的核心：cftc.py 的欄位對應若接錯，就是在這裡被抓出來。
#
# 兩套分類法的檔案格式不同，欄位位置不能共用：
#   TFF    每類都有 long/short/spread 三欄，五類連續排列
#   Legacy 只有 noncomm 有 spread；[13][14] 是可報告戶合計，要跳過；
#          [17] 之後是 old／other 期別的重複區塊，週變化要到 [37] 才開始
REPORTS = {
    "tff": {
        "combined": "FinComWk.txt",
        "futonly": "FinFutWk.txt",
        "cols": {
            "code": 3, "oi": 7, "oi_chg": 24,
            "dealer_long": 8, "dealer_short": 9, "dealer_spread": 10,
            "asset_mgr_long": 11, "asset_mgr_short": 12, "asset_mgr_spread": 13,
            "lev_money_long": 14, "lev_money_short": 15, "lev_money_spread": 16,
            "other_rept_long": 17, "other_rept_short": 18, "other_rept_spread": 19,
            "nonrept_long": 22, "nonrept_short": 23,
        },
    },
    "legacy": {
        "combined": "deacom.txt",
        "futonly": "deafut.txt",
        "cols": {
            "code": 3, "oi": 7, "oi_chg": 37,
            "noncomm_long": 8, "noncomm_short": 9, "noncomm_spread": 10,
            "comm_long": 11, "comm_short": 12,
            "nonrept_long": 15, "nonrept_short": 16,
        },
    },
}


def load_report(scheme: str, basis: str, path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    url = BASE + REPORTS[scheme][basis]
    try:
        return get(url).text
    except Exception as e:  # noqa: BLE001
        print(f"直接抓取失敗（{e}）。", file=sys.stderr)
        print(f"請用瀏覽器開 {url} 另存後，把檔案路徑當第三個參數傳進來。", file=sys.stderr)
        sys.exit(2)


def parse(text: str, code_col: int, min_cols: int) -> dict[str, list[str]]:
    """回傳 {合約代碼: 欄位陣列}。

    用 csv 模組解析而不是 split(',')：第一欄的合約名稱與 contract_units
    本身就含逗號，包在引號裡，硬拆會整列錯位。
    """
    wanted = {c["code"] for c in CONTRACTS}
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) <= min_cols:
            continue
        code = row[code_col].strip()
        if code in wanted:
            out[code] = [f.strip() for f in row]
    return out


def reconcile_one(ours: dict, scheme: str, basis: str, path: str | None) -> tuple[int, int]:
    spec = REPORTS[scheme]
    cols = spec["cols"]
    text = load_report(scheme, basis, path)
    official = parse(text, cols["code"], max(cols.values()))
    cat_keys = [c["key"] for c in SCHEMES[scheme]["categories"]]

    checked = bad = 0
    print(f"\n=== {SCHEMES[scheme]['zh']} ／ {basis}（{spec[basis]}）===")
    for c in CONTRACTS:
        row = official.get(c["code"])
        if not row:
            print(f"⚠ {c['zh']}（{c['code']}）不在這份報告裡，跳過")
            continue
        mine = ours["latest"][scheme][basis][c["key"]]

        def cmp(label, col_key, got):
            nonlocal checked, bad
            if col_key not in cols:
                return
            checked += 1
            want = int(row[cols[col_key]].replace(",", ""))
            if want != (got if got is not None else object()):
                bad += 1
                print(f"✗ {c['zh']} {label}：官方 {want:,} vs 本站 "
                      f"{got if got is None else format(got, ',')}")

        cmp("未平倉量", "oi", mine["oi"])
        cmp("未平倉量週變化", "oi_chg", mine["oi_chg"])
        for ck in cat_keys:
            v = mine["cats"][ck]
            cmp(f"{ck} 多方", f"{ck}_long", v["long"])
            cmp(f"{ck} 空方", f"{ck}_short", v["short"])
            cmp(f"{ck} 價差", f"{ck}_spread", v["spread"])
        print(f"✓ {c['zh']}（{c['code']}）")
    return checked, bad


def main() -> None:
    ours = read_json(DATA / "latest.json")
    if not ours:
        print("找不到 data/latest.json，請先跑 scripts/build.py", file=sys.stderr)
        sys.exit(1)

    args = sys.argv[1:]
    if args:
        scheme, basis = args[0], args[1]
        path = args[2] if len(args) > 2 else None
        if scheme not in REPORTS or basis not in ("combined", "futonly"):
            print("用法：reconcile.py [tff|legacy] [combined|futonly] [檔案路徑]", file=sys.stderr)
            sys.exit(2)
        jobs = [(scheme, basis, path)]
    else:
        jobs = [(s, b, None) for s in REPORTS for b in ("combined", "futonly")]

    total = mismatches = 0
    for scheme, basis, path in jobs:
        c, b = reconcile_one(ours, scheme, basis, path)
        total += c
        mismatches += b

    print(f"\n共比對 {total} 個數字，{mismatches} 個不符。")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
