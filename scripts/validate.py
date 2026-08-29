"""產出檢查：資料沒過期、三個分頁的合約齊全、數字站得住腳。

任何一項不過就以非零碼結束，讓 Actions 失敗並開 issue，
而不是讓網站靜靜掛著一份舊資料。

期待值一律從 cftc.py 的 ASSETS／SCHEMES 推導，不在這裡另抄一份清單——
兩處各寫一份的下場是加了合約卻忘記改這裡，防呆自己先失效。
"""
from __future__ import annotations

import sys
from datetime import date

import cftc
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

# 價格／殖利率是日資料，過期門檻另算：長假最多連休四個交易日，加上排程延遲，設 10 天。
MAX_PRICE_AGE_DAYS = 10

EXPECTED_BASES = {"combined", "futonly", "options"}


def fail(msg: str) -> None:
    print("✗ " + msg, file=sys.stderr)
    sys.exit(1)


def check_positions(payload: dict) -> None:
    for asset in cftc.ASSETS:
        ak = asset["key"]
        expected_contracts = {c["key"] for c in asset["contracts"]}
        by_scheme = payload["latest"].get(ak)
        if not by_scheme:
            fail(f"缺少資產分頁：{ak}")

        missing_schemes = set(asset["schemes"]) - set(by_scheme)
        if missing_schemes:
            fail(f"{ak} 缺少分類法：{sorted(missing_schemes)}")

        for scheme, by_basis in by_scheme.items():
            expected_cats = {c["key"] for c in cftc.SCHEMES[scheme]["categories"]}
            missing_bases = EXPECTED_BASES - set(by_basis)
            if missing_bases:
                fail(f"{ak}／{scheme} 缺少口徑：{sorted(missing_bases)}")

            for basis, by_contract in by_basis.items():
                missing = expected_contracts - set(by_contract)
                if missing:
                    fail(f"{ak}／{scheme}／{basis} 缺少合約：{sorted(missing)}")

                for key, row in by_contract.items():
                    tag = f"{ak}／{scheme}／{basis}／{key}"
                    if not row.get("oi") or row["oi"] <= 0:
                        fail(f"{tag} 的未平倉量不是正數：{row.get('oi')}")

                    cats_missing = expected_cats - set(row["cats"])
                    if cats_missing:
                        fail(f"{tag} 缺少交易人類別：{sorted(cats_missing)}")

                    for ck, cat in row["cats"].items():
                        if cat.get("net") is None:
                            fail(f"{tag}／{ck} 沒有淨部位")
                        # 選擇權那份是相減出來的，官方沒有對應的 change 欄位可比，跳過這項。
                        if basis != "options" and cat.get("chg_mismatch"):
                            fail(f"{tag}／{ck} 的官方週變化與自算值差距超出容忍範圍，請人工查證")

                    # 各類多方部位加總＋價差部位應該等於總未平倉量。這是 CFTC 報告的內在恆等式，
                    # 對不上就代表欄位對應錯了或漏了一類，是最有效的一道防呆。
                    #
                    # 容忍 2 口：CFTC 對各欄各自四捨五入，實測 Disagg 的黃金合併版
                    # 就會差 1 口。相減出來的 options 口徑最多把兩邊的殘差加起來，故給 2。
                    total_long = sum(c["long"] for c in row["cats"].values()
                                     if c["long"] is not None)
                    total_spread = sum(c["spread"] or 0 for c in row["cats"].values())
                    if abs((total_long + total_spread) - row["oi"]) > 2:
                        fail(f"{tag} 多方部位＋價差部位 {total_long + total_spread:,} "
                             f"對不上未平倉量 {row['oi']:,}")

                    # 淨部位是零和的：所有類別的淨額加總必為 0（每一口多單都有對應的空單）。
                    # 這道檢查與上面的恆等式互補——上面驗總量，這裡驗方向。
                    total_net = sum(c["net"] for c in row["cats"].values()
                                    if c["net"] is not None)
                    if abs(total_net) > 2:
                        fail(f"{tag} 各類淨部位加總為 {total_net:,}，應為 0（零和市場）")

        # 選擇權口徑的合理性：它是相減出來的，理論上不該出現負的未平倉量。
        for scheme in asset["schemes"]:
            for key, row in payload["latest"][ak][scheme]["options"].items():
                cb = payload["latest"][ak][scheme]["combined"][key]["oi"]
                if not (0 < row["oi"] < cb):
                    fail(f"{ak}／{scheme}／{key} 的選擇權未平倉量 {row['oi']:,} "
                         f"不在 0 與合併版 {cb:,} 之間，表示兩份報告可能沒對齊")

        n_contracts = len(asset["contracts"])
        print(f"✓ {asset['zh']}：{len(asset['schemes'])} 種分類法 × 3 種口徑 × "
              f"{n_contracts} 檔齊全，恆等式與零和皆成立")


def check_vol(payload: dict) -> None:
    """波動度序列：該有的都在、日期夠新、數值是有限的正數。

    最後一項擋的是價格來源給出零或負值時算出的 nan／inf——
    WTI 2020 那次負油價若沒被 realised_vol_price 擋掉，就會從這裡浮出來。
    """
    today = date.today()
    for a in payload["assets"]:
        vol = payload["vol"].get(a["key"])
        if not vol:
            fail(f"{a['key']} 沒有波動度資料")

        missing = set(a["vol"]["plot"]) - set(vol)
        if missing:
            fail(f"{a['key']} 的波動度缺少序列：{sorted(missing)}")

        age = (today - date.fromisoformat(a["vol"]["date"])).days
        if age > MAX_PRICE_AGE_DAYS:
            fail(f"{a['key']} 的價格／殖利率停在 {a['vol']['date']}（{age} 天前），"
                 f"門檻 {MAX_PRICE_AGE_DAYS} 天")

        for key, s in vol.items():
            for w in ("rv20", "rv60"):
                if not s.get(w):
                    fail(f"{a['key']}／{key} 沒有 {w} 序列")
                v = s[w][-1][1]
                if not (0 <= v < 1e6):
                    fail(f"{a['key']}／{key} 的 {w} 最新值 {v} 不是合理的波動度")
        print(f"✓ {a['zh']}：波動度 {len(a['vol']['plot'])} 條序列，涵蓋至 {a['vol']['date']}"
              f"（{age} 天前）")


def check_history_files() -> None:
    # 各分頁的歷史檔列數下限。取 2026-08-25 實際列數（ust 159,111／oil 31,353／
    # metals 62,916）的八折左右，抓的是「分頁沒抓完就寫檔」，不是逐列精確比對——
    # CFTC 每週增加的列數本來就會讓精確值一直變。
    floors = {"ust": 130000, "oil": 25000, "metals": 50000}
    for a in cftc.ASSETS:
        path = DATA / f"cot_history_{a['key']}.csv"
        if not path.exists():
            fail(f"{path.name} 不存在")
        lines = sum(1 for _ in path.open(encoding="utf-8"))
        if lines < floors[a["key"]]:
            fail(f"{path.name} 只有 {lines:,} 列，明顯少於預期"
                 f"（下限 {floors[a['key']]:,}）——可能分頁沒抓完")
        print(f"✓ {path.name} {lines:,} 列")


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

    assets_missing = {a["key"] for a in cftc.ASSETS} - {a["key"] for a in payload["assets"]}
    if assets_missing:
        fail(f"payload 缺少資產分頁：{sorted(assets_missing)}")

    check_positions(payload)
    check_vol(payload)
    check_history_files()


if __name__ == "__main__":
    main()
