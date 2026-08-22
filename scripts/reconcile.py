"""外部對帳：拿 CFTC 官網公布的純文字報告，逐欄比對本站產出的 latest.json。

為什麼要有這支：Socrata API 與這份純文字報告是**兩條不同的發布管道**。
只用 API 自己對自己，驗不出「欄位對應接錯」這類錯誤——例如把 asset_mgr_spread
當成 asset_mgr_short 用，API 內部永遠自洽，只有跟原始報告比才看得出來。
校驗基準必須來自外部，不能是自家管線的輸出。

用法：
    python scripts/reconcile.py                    # 直接抓（cftc.gov 常擋自動請求）
    python scripts/reconcile.py FinComWk.txt       # 用手動存下來的報告檔

cftc.gov 主站有 Akamai 機器人偵測，一般 HTTP 客戶端與 curl_cffi 都會拿到 403。
擋住時請用瀏覽器開 https://www.cftc.gov/dea/newcot/FinComWk.txt 另存，再把路徑傳進來。
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

from cftc import CONTRACTS
from common import DATA, get, read_json

REPORT_URL = "https://www.cftc.gov/dea/newcot/FinComWk.txt"

# FinComWk.txt 的欄位位置（TFF 期貨＋選擇權合併版）。
# 這張表是對帳的核心：如果 cftc.py 的欄位對應接錯，就是在這裡被抓出來。
COLS = {
    "code": 3, "oi": 7,
    "dealer_long": 8, "dealer_short": 9, "dealer_spread": 10,
    "asset_mgr_long": 11, "asset_mgr_short": 12, "asset_mgr_spread": 13,
    "lev_money_long": 14, "lev_money_short": 15, "lev_money_spread": 16,
    "other_rept_long": 17, "other_rept_short": 18, "other_rept_spread": 19,
    "nonrept_long": 22, "nonrept_short": 23,
    "oi_chg": 24,
}

CATS = ["dealer", "asset_mgr", "lev_money", "other_rept", "nonrept"]


def load_report(arg: str | None) -> str:
    if arg:
        return Path(arg).read_text(encoding="utf-8", errors="replace")
    try:
        return get(REPORT_URL).text
    except Exception as e:  # noqa: BLE001
        print(f"直接抓取失敗（{e}）。", file=sys.stderr)
        print(f"請用瀏覽器開 {REPORT_URL} 另存後，把檔案路徑當參數傳進來。", file=sys.stderr)
        sys.exit(2)


def parse(text: str) -> dict[str, list[str]]:
    """回傳 {合約代碼: 欄位陣列}。用 csv 模組解析，欄位內含逗號的引號字串才不會拆錯。"""
    wanted = {c["code"]: c["key"] for c in CONTRACTS}
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) <= max(COLS.values()):
            continue
        code = row[COLS["code"]].strip()
        if code in wanted:
            out[code] = [f.strip() for f in row]
    return out


def main() -> None:
    text = load_report(sys.argv[1] if len(sys.argv) > 1 else None)
    official = parse(text)
    ours = read_json(DATA / "latest.json")
    if not ours:
        print("找不到 data/latest.json，請先跑 scripts/build.py", file=sys.stderr)
        sys.exit(1)

    checked = mismatches = 0
    for c in CONTRACTS:
        row = official.get(c["code"])
        if not row:
            print(f"⚠ {c['zh']}（{c['code']}）不在這份報告裡，跳過")
            continue
        mine = ours["latest"][c["key"]]

        def cmp(label, want, got):
            nonlocal checked, mismatches
            checked += 1
            if int(want) != (got if got is not None else -1):
                mismatches += 1
                print(f"✗ {c['zh']} {label}：官方 {int(want):,} vs 本站 {got:,}")

        cmp("未平倉量", row[COLS["oi"]], mine["oi"])
        cmp("未平倉量週變化", row[COLS["oi_chg"]], mine["oi_chg"])
        for cat in CATS:
            v = mine["cats"][cat]
            cmp(f"{cat} 多方", row[COLS[f"{cat}_long"]], v["long"])
            cmp(f"{cat} 短方", row[COLS[f"{cat}_short"]], v["short"])
            if f"{cat}_spread" in COLS:
                cmp(f"{cat} 價差", row[COLS[f"{cat}_spread"]], v["spread"])
        print(f"✓ {c['zh']}（{c['code']}）比對完成")

    print(f"\n共比對 {checked} 個數字，{mismatches} 個不符。")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
