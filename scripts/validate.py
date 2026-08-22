"""產出檢查：資料沒過期、六檔齊全、數字站得住腳。

任何一項不過就以非零碼結束，讓 Actions 失敗並開 issue，
而不是讓網站靜靜掛著一份舊資料。
"""
from __future__ import annotations

import sys
from datetime import date

from common import DATA, read_json

# 過期門檻。
#
# 推導方式是「期別結束 → 下一期發布」的最大間隔，不是名目週期：
# COT 報的是週二收盤部位，當週五 15:30 ET 才發布，本站每週六抓。
# 正常情況下週六看到的是四天前（本週二）的報告。
# 但遇美國假日 CFTC 會把發布順延到下週一——那個週六抓到的就還是上一期，
# 報告日已是 11 天前。門檻若照名目的 7 天或 10 天設，每逢假日就會誤判成掛掉。
# 設 14 天：真正代表連續漏掉兩期，那才是壞了。
MAX_AGE_DAYS = 14

EXPECTED_BASES = {"combined", "futonly", "options"}
EXPECTED_CONTRACTS = {"ust2y", "ust5y", "ust10y", "ultra10y", "ustbond", "ultrabond"}
EXPECTED_CATEGORIES = {"dealer", "asset_mgr", "lev_money", "other_rept", "nonrept"}


def fail(msg: str) -> None:
    print("✗ " + msg, file=sys.stderr)
    sys.exit(1)


def main() -> None:
    payload = read_json(DATA / "latest.json")
    if not payload:
        fail("data/latest.json 不存在或是空的")

    meta = payload["meta"]
    report_date = date.fromisoformat(meta["report_date"])
    age = (date.today() - report_date).days
    if age > MAX_AGE_DAYS:
        fail(f"資料過期：報告日 {report_date} 已是 {age} 天前（門檻 {MAX_AGE_DAYS} 天）")
    print(f"✓ 報告日 {report_date}（{age} 天前），下期預定 {meta['next_release']}")

    bases_missing = EXPECTED_BASES - set(payload["latest"])
    if bases_missing:
        fail(f"缺少口徑：{sorted(bases_missing)}")

    for basis, by_contract in payload["latest"].items():
        missing = EXPECTED_CONTRACTS - set(by_contract)
        if missing:
            fail(f"{basis} 缺少合約：{sorted(missing)}")

        for key, row in by_contract.items():
            if not row.get("oi") or row["oi"] <= 0:
                fail(f"{basis}／{key} 的未平倉量不是正數：{row.get('oi')}")

            cats_missing = EXPECTED_CATEGORIES - set(row["cats"])
            if cats_missing:
                fail(f"{basis}／{key} 缺少交易人類別：{sorted(cats_missing)}")

            for ck, cat in row["cats"].items():
                if cat.get("net") is None:
                    fail(f"{basis}／{key}／{ck} 沒有淨部位")
                # 選擇權那份是相減出來的，官方沒有對應的 change 欄位可比，跳過這項。
                if basis != "options" and cat.get("chg_mismatch"):
                    fail(f"{basis}／{key}／{ck} 的官方週變化與自算值差距超出容忍範圍，請人工查證")

            # 五類的多方部位加總＋價差部位應該等於總未平倉量。這是 CFTC 報告的內在恆等式，
            # 對不上就代表欄位對應錯了或漏了一類，是最有效的一道防呆。
            # 相減出來的 options 口徑同樣要成立——兩邊各自成立，相減後自然也成立，
            # 不成立就表示兩份報告的期別沒有正確對齊。
            total_long = sum(c["long"] for c in row["cats"].values() if c["long"] is not None)
            total_spread = sum(c["spread"] or 0 for c in row["cats"].values())
            if abs((total_long + total_spread) - row["oi"]) > 1:
                fail(f"{basis}／{key} 多方部位＋價差部位 {total_long + total_spread:,} "
                     f"對不上未平倉量 {row['oi']:,}")

    print("✓ 三種口徑 × 六檔齊全，五類交易人加總與未平倉量一致")

    # 選擇權口徑的合理性：它是相減出來的，理論上不該出現負的未平倉量。
    for key, row in payload["latest"]["options"].items():
        cb = payload["latest"]["combined"][key]["oi"]
        if not (0 < row["oi"] < cb):
            fail(f"{key} 的選擇權未平倉量 {row['oi']:,} 不在 0 與合併版 {cb:,} 之間，"
                 f"表示兩份報告可能沒對齊")
    print("✓ 選擇權口徑落在合理範圍（0 < 選擇權 < 合併版）")

    hist = DATA / "cot_history.csv"
    if not hist.exists():
        fail("data/cot_history.csv 不存在")
    lines = sum(1 for _ in hist.open(encoding="utf-8"))
    if lines < 60000:
        fail(f"歷史檔只有 {lines} 列，明顯少於預期（三口徑 × 六檔約 84,300 列）——可能分頁沒抓完")
    print(f"✓ 歷史檔 {lines:,} 列")


if __name__ == "__main__":
    main()
