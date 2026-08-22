"""主流程：抓 CFTC 與 FRED、算衍生指標、產出網頁用的 JSON。

產出兩份：
- data/cot_history.csv   完整週歷史（2006 起），給日後回溯研究用，網頁不載入
- data/latest.json       網頁真正吃的那份，只留最新一期與近三年軌跡

歷史用 CSV 而不是 JSON：這份檔每週重產一次，JSON 版 6.4 MB 且是整檔改寫，
git 會為每週存一個全新 blob，一年就把倉庫撐到數百 MB。CSV 實質上是純追加，
delta 壓縮後每週只多幾百 bytes；順帶也比較好直接丟進 Excel 或 pandas。
"""
from __future__ import annotations

import csv
import sys
from datetime import date, timedelta

import cftc
import derive
import fred
from common import DATA, write_json

TRAIL_WEEKS = 156      # 網頁軌跡保留三年
VOL_DAYS = 780         # 波動度序列保留約三年交易日


def next_release(report_date: date) -> date:
    """COT 為週二收盤部位、當週五 15:30 ET 發布，下一期即下週五。

    遇美國假日 CFTC 會順延（多為順延至週一），這裡只給預定日，
    實際過期判定交給 validate.py 的 10 天門檻。
    """
    friday = report_date + timedelta(days=(4 - report_date.weekday()) % 7)
    return friday + timedelta(days=7)


HIST_COLS = ["date", "basis", "contract", "category", "long", "short", "spread",
             "net", "net_chg", "pctile", "z", "traders_long", "traders_short", "oi"]


def write_history_csv(history: dict) -> None:
    """長格式：一列一個「報告日 × 口徑 × 合約 × 交易人類別」。

    basis 欄的值為 combined／futonly／options，要單獨研究選擇權部位就篩 options。
    """
    path = DATA / "cot_history.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HIST_COLS)
        for bkey, by_contract in history.items():
            for ckey, rows in by_contract.items():
                for r in rows:
                    for cat_key, v in r["cats"].items():
                        w.writerow([r["date"], bkey, ckey, cat_key,
                                    v["long"], v["short"], v["spread"],
                                    v.get("net"), v.get("net_chg"), v.get("pctile"), v.get("z"),
                                    v.get("traders_long"), v.get("traders_short"), r["oi"]])


def build() -> dict:
    print("抓取 CFTC COT 合併版（六檔）…")
    raw_combined = cftc.fetch_all("combined")
    print("抓取 CFTC COT 僅期貨版（六檔）…")
    raw_futonly = cftc.fetch_all("futonly")

    # 三種口徑。選擇權那份由前兩者相減得出，不是 CFTC 直接發布的資料。
    raw = {
        "combined": raw_combined,
        "futonly": raw_futonly,
        "options": {c["key"]: cftc.subtract(raw_combined[c["key"]], raw_futonly[c["key"]])
                    for c in cftc.CONTRACTS},
    }

    history, latest, trail = {}, {}, {}
    for b in cftc.BASES:
        bk = b["key"]
        history[bk], latest[bk], trail[bk] = {}, {}, {}
        for c in cftc.CONTRACTS:
            # 極端度必須每種口徑各自算——選擇權部位的歷史分布跟期貨完全不同，
            # 拿期貨的百分位去讀選擇權會得到毫無意義的數字。
            rows = derive.add_extremes(derive.enrich_positions(raw[bk][c["key"]]))
            history[bk][c["key"]] = rows
            latest[bk][c["key"]] = rows[-1]
            trail[bk][c["key"]] = [
                {
                    "date": r["date"],
                    "oi": r["oi"],
                    "cats": {k: {"net": v.get("net"), "pctile": v.get("pctile"), "z": v.get("z")}
                             for k, v in r["cats"].items()},
                }
                for r in rows[-TRAIL_WEEKS:]
            ]
        n = len(history[bk]["ust10y"])
        oi = latest[bk]["ust10y"]["oi"]
        print("  %-9s 10 年期 %5d 週，最新未平倉 %s 口" % (b["zh"], n, f"{oi:,}"))

    print("抓取 FRED 殖利率…")
    yields = fred.fetch_all()
    vol = {}
    for s in fred.SERIES:
        rv = derive.realised_vol(yields[s["key"]])
        vol[s["key"]] = {w: v[-VOL_DAYS:] for w, v in rv.items()}
        vol[s["key"]]["level"] = yields[s["key"]][-VOL_DAYS:]
        print("  %-4s (%s) 已實現波動 20d=%s bp/年" % (s["key"], s["id"], rv["rv20"][-1][1]))

    report_dates = {k: v["date"] for k, v in latest["combined"].items()}
    newest = max(report_dates.values())

    # 刻意不放「本次建置時間」這種欄位。
    #
    # 建置時間每跑一次就變，會讓沒有新資料的那幾次排程也產生 latest.json 差異，
    # git 每次都提交一個內容毫無意義的 commit。改成記錄兩份資料各自涵蓋到哪一天——
    # 這對讀者也更有用（他要知道的是「數字新到哪」，不是「機器幾點跑的」），
    # 而且沒有新資料時檔案完全相同，「沒變更」在 git 層面就是真的沒變更。
    yield_date = max(v[-1][0] for v in yields.values())

    payload = {
        "meta": {
            "report_date": newest,
            "next_release": next_release(date.fromisoformat(newest)).isoformat(),
            "yield_date": yield_date,
            "report_dates": report_dates,
            "source": "CFTC Commitments of Traders — Traders in Financial Futures",
            "source_url": "https://publicreporting.cftc.gov/resource/yw9f-hn96.json",
            "source_url_futonly": "https://publicreporting.cftc.gov/resource/gpe5-46if.json",
            "yield_source": "FRED（DGS2 / DGS5 / DGS10 / DGS30）",
        },
        "contracts": cftc.CONTRACTS,
        "bases": cftc.BASES,
        "categories": [{k: c[k] for k in ("key", "zh", "en")} for c in cftc.CATEGORIES],
        "latest": latest,
        "trail": trail,
        "vol": vol,
    }

    write_history_csv(history)
    write_json(DATA / "latest.json", payload, compact=True)
    return payload


if __name__ == "__main__":
    try:
        p = build()
    except Exception as e:  # noqa: BLE001
        print("建置失敗：%s" % e, file=sys.stderr)
        raise
    print("\n完成。報告日 %s，下期預定 %s" % (p["meta"]["report_date"], p["meta"]["next_release"]))
