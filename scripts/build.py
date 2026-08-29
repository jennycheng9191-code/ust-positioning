"""主流程：抓 CFTC、FRED 與 LBMA，算衍生指標，產出網頁用的 JSON。

產出：
- data/cot_history_{資產}.csv   各分頁的完整週歷史（最早 1986 起），給日後回溯研究用，網頁不載入
- data/latest.json              網頁真正吃的那份，只留最新一期與近三年軌跡

歷史用 CSV 而不是 JSON：這份檔每週重產一次，JSON 版是整檔改寫，
git 會為每週存一個全新 blob，一年就把倉庫撐到數百 MB。CSV 實質上是純追加，
delta 壓縮後每週只多幾百 bytes；順帶也比較好直接丟進 Excel 或 pandas。

**歷史檔按資產分開**（原本是單一 cot_history.csv）：加了原油與貴金屬之後，
商品的 Legacy 僅期貨版回溯到 1986 年，合起來一個檔會逼近 30 MB，
每週改寫一次對 git 不友善。拆開之後改一頁只動一個檔，另外兩個檔在 git 眼中完全沒變。
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import date, timedelta

import cftc
import derive
import fred
import prices
from common import DATA, write_json

TRAIL_WEEKS = 156      # 網頁軌跡保留三年
VOL_DAYS = 780         # 波動度序列保留約三年交易日

# 網頁的「手動更新」按鈕要連到這個 repo 的 update workflow。
# 寫在這裡而不是寫死在 app.js：前端不該知道自己被部署在哪，
# 換 repo 或 fork 出去時只要改這一行。Actions 上會用環境變數覆蓋。
REPO = "jennycheng9191-code/ust-positioning"

# 波動度模組的顯示規格。美債算殖利率變動（bp），商品算價格報酬（%），
# 兩者單位不同不可並排，所以連文案都各給一份，由前端照著渲染。
VOL_SPEC = {
    "ust": {
        "unit": "bp／年", "kind": "yield",
        "note": "殖利率日變動的滾動標準差，年化後以 bp 表示。"
                "這是<b>已實現</b>波動，不是選擇權隱含波動——它講的是市場已經走過什麼，"
                "不是市場預期什麼。",
    },
    "oil": {
        "unit": "%／年", "kind": "price",
        "note": "價格日對數報酬的滾動標準差，年化後以 % 表示。"
                "<b>與美債頁的 bp 不同單位，不可並排比較</b>。"
                "部位面只有 WTI（Brent 本尊歸英國 FCA 管，CFTC 沒有部位資料），"
                "但這裡把 Brent 一起畫——兩者的波動差本身就是價差交易的訊號。",
    },
    "metals": {
        "unit": "%／年", "kind": "price",
        "note": "價格日對數報酬的滾動標準差，年化後以 % 表示。"
                "<b>與美債頁的 bp 不同單位，不可並排比較</b>。"
                "白銀的波動長期是黃金的兩倍上下，兩條線一起看才知道現在是不是偏離常態。",
    },
}

# 折線圖的線色。前端只認 CSS 變數名，這裡指定哪條序列用哪一色。
VOL_COLORS = {
    "y2": "var(--c-dealer)", "y10": "var(--c-asset)", "y30": "var(--c-lev)",
    "wti": "var(--c-asset)", "brent": "var(--c-dealer)",
    "gold": "var(--c-asset)", "silver": "var(--c-other)",
}
# 美債頁不是四條殖利率全畫，5 年期與 10 年期高度重疊，畫三條就夠讀。
VOL_PLOT = {"ust": ["y2", "y10", "y30"], "oil": ["wti", "brent"], "metals": ["gold", "silver"]}

# M5 比價模組。目前只有貴金屬頁有——金銀比是這個市場自己的老指標，
# 沒有等價的東西可以套到美債或原油上（WTI-Brent 是價差不是比值，語義不同，另議）。
#
# overlay 決定選到哪一檔合約時，右軸要疊哪條價格線：看黃金就疊金價、看白銀就疊銀價。
# 比值序列本身兩檔共用同一條，換合約只換右軸。
RATIO_SPEC = {
    "metals": {
        "zh": "金銀比", "en": "Gold/Silver Ratio",
        "numer": "gold", "denom": "silver",
        "color": "var(--c-lev)",
        "overlay": {"gold": "gold", "silver": "silver"},
        "note": "一盎司黃金換得幾盎司白銀。<b>比值走高＝白銀相對弱</b>——"
                "白銀有一半以上的需求來自工業，景氣轉弱或避險情緒升高時它跌得比黃金兇；"
                "比值走低則多半出現在再通膨與工業需求回溫的階段。"
                "左軸為比值、右軸為選定金屬的價格，兩軸各自縮放，"
                "看的是<b>兩條線的方向關係</b>，不是誰高誰低。",
    },
}


# 頁尾的資料來源說明。跟著分頁走，因為三頁的來源與授權狀況並不相同。
SOURCE_NOTES = {
    "ust": [
        "部位：CFTC Commitments of Traders — Traders in Financial Futures（TFF）與 Legacy。",
        "殖利率：FRED（DGS2 / DGS5 / DGS10 / DGS30），原始資料為美國財政部 CMT 殖利率。",
        "兩者皆為美國聯邦政府公開資料。",
    ],
    "oil": [
        "部位：CFTC Commitments of Traders — Disaggregated 與 Legacy，"
        "合約為 NYMEX WTI-PHYSICAL（067651）。",
        "價格：FRED（DCOILWTICO / DCOILBRENTEU），原始資料為美國 EIA 公布的現貨價。",
        "兩者皆為美國聯邦政府公開資料。",
    ],
    "metals": [
        "部位：CFTC Commitments of Traders — Disaggregated 與 Legacy，"
        "合約為 COMEX GOLD（088691）與 SILVER（084691），美國聯邦政府公開資料。",
        "價格：LBMA 官方價格（gold_pm / silver），由 ICE Benchmark Administration 管理。"
        "本站為個人非商業用途，僅取每日一個數字用於計算已實現波動，不再散布。",
    ],
}


def next_release(report_date: date) -> date:
    """COT 為週二收盤部位、當週五 15:30 ET 發布，下一期即下週五。

    遇美國假日 CFTC 會順延（多為順延至週一），這裡只給預定日，
    實際過期判定交給 validate.py 的 14 天門檻。
    """
    friday = report_date + timedelta(days=(4 - report_date.weekday()) % 7)
    return friday + timedelta(days=7)


HIST_COLS = ["date", "scheme", "basis", "contract", "category", "long", "short", "spread",
             "net", "net_chg", "pctile", "z", "traders_long", "traders_short", "oi"]


def write_history_csv(asset_key: str, history: dict) -> int:
    """長格式：一列一個「報告日 × 分類法 × 口徑 × 合約 × 交易人類別」。

    scheme 欄為 tff／disagg／legacy，basis 欄為 combined／futonly／options。
    要單獨研究選擇權部位就篩 basis=options；要對照新聞說的「投機客淨部位」，
    篩 scheme=legacy & category=noncomm。
    """
    path = DATA / f"cot_history_{asset_key}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HIST_COLS)
        for skey, by_basis in history.items():
            for bkey, by_contract in by_basis.items():
                for ckey, rows in by_contract.items():
                    for r in rows:
                        for cat_key, v in r["cats"].items():
                            w.writerow([r["date"], skey, bkey, ckey, cat_key,
                                        v["long"], v["short"], v["spread"],
                                        v.get("net"), v.get("net_chg"), v.get("pctile"), v.get("z"),
                                        v.get("traders_long"), v.get("traders_short"), r["oi"]])
                            n += 1
    return n


def build_positions(asset: dict) -> tuple[dict, dict, dict]:
    """抓某一資產分頁的部位資料，回傳（完整歷史, 最新一期, 近三年軌跡）。"""
    history, latest, trail = {}, {}, {}
    for skey in asset["schemes"]:
        scheme = cftc.SCHEMES[skey]
        print("  %s（合併版＋僅期貨版）…" % scheme["zh"])
        raw_combined = cftc.fetch_asset(asset, skey, "combined")
        raw_futonly = cftc.fetch_asset(asset, skey, "futonly")

        # 三種口徑。選擇權那份由前兩者相減得出，不是 CFTC 直接發布的資料。
        raw = {
            "combined": raw_combined,
            "futonly": raw_futonly,
            "options": {c["key"]: cftc.subtract(raw_combined[c["key"]], raw_futonly[c["key"]])
                        for c in asset["contracts"]},
        }

        history[skey], latest[skey], trail[skey] = {}, {}, {}
        for b in cftc.BASES:
            bk = b["key"]
            history[skey][bk], latest[skey][bk], trail[skey][bk] = {}, {}, {}
            for c in asset["contracts"]:
                # 極端度必須每種分類法、每種口徑各自算——選擇權部位的歷史分布跟期貨
                # 完全不同，Legacy 的樣本又比 TFF／Disagg 多十幾年，混用會得到無意義的百分位。
                rows = derive.add_extremes(derive.enrich_positions(raw[bk][c["key"]]))
                history[skey][bk][c["key"]] = rows
                latest[skey][bk][c["key"]] = rows[-1]
                trail[skey][bk][c["key"]] = [
                    {
                        "date": r["date"],
                        "oi": r["oi"],
                        "cats": {k: {"net": v.get("net"), "pctile": v.get("pctile"), "z": v.get("z")}
                                 for k, v in r["cats"].items()},
                    }
                    for r in rows[-TRAIL_WEEKS:]
                ]
            dc = asset["default_contract"]
            print("    %-9s %s %5d 週，最新未平倉 %s 口"
                  % (b["zh"], dc, len(history[skey][bk][dc]),
                     f"{latest[skey][bk][dc]['oi']:,}"))
    return history, latest, trail


def build_ratio(asset_key: str, raw: dict) -> dict | None:
    """M5 比價模組的資料。raw 是 build_vol() 抓到的完整價格序列（2006 年起）。

    圖只畫近三年（跟 M3 的部位軌跡、M4 的波動度同一個時間尺度，整頁好對照），
    但**百分位用 2006 年起的全樣本**——金銀比是長週期的東西，
    2020 年衝到 100 以上、2011 年低到 30 出頭，只看三年會把極端讀成常態。
    """
    spec = RATIO_SPEC.get(asset_key)
    if not spec:
        return None

    full = derive.ratio_series(raw[spec["numer"]], raw[spec["denom"]])
    if not full:
        raise RuntimeError(f"{asset_key} 的{spec['zh']}算不出任何一天——兩條價格序列沒有共同日期")

    vals = [v for _, v in full]
    now = vals[-1]
    window = [v for d, v in full[-VOL_DAYS:]]
    return {
        "zh": spec["zh"], "en": spec["en"], "note": spec["note"],
        "color": spec["color"], "overlay": spec["overlay"],
        "series": full[-VOL_DAYS:],
        "now": now,
        "lo": min(window), "hi": max(window),
        "pctile": round(derive._percentile_rank(vals, now), 1),
        "since": full[0][0],
        "n": len(full),
    }


def build_vol(asset_key: str) -> tuple[dict, str, dict]:
    """某一資產分頁的波動度序列，回傳（序列表, 資料涵蓋到哪一天, 比價模組或 None）。"""
    vol = {}
    if asset_key == "ust":
        raw = fred.fetch_all()
        specs = [{"key": s["key"], "zh": s["zh"], "unit": "%"} for s in fred.SERIES]
        rv_fn = derive.realised_vol
    else:
        raw = prices.fetch_asset(asset_key)
        specs = [{"key": s["key"], "zh": s["zh"], "unit": s["unit"]}
                 for s in prices.SERIES[asset_key]]
        rv_fn = derive.realised_vol_price

    for s in specs:
        rv = rv_fn(raw[s["key"]])
        vol[s["key"]] = {w: v[-VOL_DAYS:] for w, v in rv.items()}
        vol[s["key"]]["level"] = raw[s["key"]][-VOL_DAYS:]
        vol[s["key"]]["zh"] = s["zh"]
        vol[s["key"]]["unit"] = s["unit"]
        vol[s["key"]]["color"] = VOL_COLORS.get(s["key"], "var(--c-nonrept)")
        print("    %-6s 已實現波動 20d=%s %s"
              % (s["key"], rv["rv20"][-1][1], VOL_SPEC[asset_key]["unit"]))

    ratio = build_ratio(asset_key, raw)
    if ratio:
        print("    %-6s %s（%s 起 %d 天樣本，百分位 %s）"
              % (ratio["zh"], ratio["now"], ratio["since"], ratio["n"], ratio["pctile"]))
    return vol, max(v[-1][0] for v in raw.values()), ratio


def build() -> dict:
    latest, trail, vols, price_dates, ratios = {}, {}, {}, {}, {}

    for asset in cftc.ASSETS:
        print("【%s】" % asset["zh"])
        history, latest[asset["key"]], trail[asset["key"]] = build_positions(asset)
        n = write_history_csv(asset["key"], history)
        print("  歷史檔 cot_history_%s.csv %s 列" % (asset["key"], f"{n:,}"))

        print("  抓取價格／殖利率…")
        (vols[asset["key"]], price_dates[asset["key"]],
         ratios[asset["key"]]) = build_vol(asset["key"])

    # 各分頁的報告日理論上相同（同一份 COT），但仍逐一記錄：
    # CFTC 曾對個別合約補發修正，屆時分頁之間會短暫不同步，記下來才看得出來。
    report_dates = {}
    for asset in cftc.ASSETS:
        skey = asset["schemes"][0]
        report_dates[asset["key"]] = {
            c["key"]: latest[asset["key"]][skey]["combined"][c["key"]]["date"]
            for c in asset["contracts"]
        }
    newest = max(d for by_c in report_dates.values() for d in by_c.values())

    # 刻意不放「本次建置時間」這種欄位。
    #
    # 建置時間每跑一次就變，會讓沒有新資料的那幾次排程也產生 latest.json 差異，
    # git 每次都提交一個內容毫無意義的 commit。改成記錄兩份資料各自涵蓋到哪一天——
    # 這對讀者也更有用（他要知道的是「數字新到哪」，不是「機器幾點跑的」），
    # 而且沒有新資料時檔案完全相同，「沒變更」在 git 層面就是真的沒變更。
    assets_meta = []
    for a in cftc.ASSETS:
        assets_meta.append({
            "key": a["key"], "zh": a["zh"], "sub": a["sub"],
            "schemes": a["schemes"], "contracts": a["contracts"],
            "default_contract": a["default_contract"],
            "vol": {
                **VOL_SPEC[a["key"]],
                "plot": VOL_PLOT[a["key"]],
                "date": price_dates[a["key"]],
            },
            "source_note": SOURCE_NOTES[a["key"]],
        })
        # 只有定義了 RATIO_SPEC 的分頁才帶這個鍵，前端據此決定要不要顯示 M5，
        # 不必知道哪一頁是貴金屬。
        if ratios[a["key"]]:
            assets_meta[-1]["ratio"] = ratios[a["key"]]

    payload = {
        "meta": {
            "report_date": newest,
            "next_release": next_release(date.fromisoformat(newest)).isoformat(),
            "report_dates": report_dates,
            "source": "CFTC Commitments of Traders",
            "repo": os.environ.get("GITHUB_REPOSITORY") or REPO,
        },
        "assets": assets_meta,
        "bases": cftc.BASES,
        "schemes": {k: {"zh": v["zh"], "en": v["en"], "note": v["note"],
                        "datasets": v["datasets"],
                        "categories": [{kk: c[kk] for kk in ("key", "zh", "en")}
                                       for c in v["categories"]]}
                    for k, v in cftc.SCHEMES.items()},
        "scheme_notes": {f"{s}|{a}": t for (s, a), t in cftc.SCHEME_NOTES.items()},
        "latest": latest,
        "trail": trail,
        "vol": vols,
    }

    write_json(DATA / "latest.json", payload, compact=True)
    return payload

if __name__ == "__main__":
    try:
        p = build()
    except Exception as e:  # noqa: BLE001
        print("建置失敗：%s" % e, file=sys.stderr)
        raise
    print("\n完成。報告日 %s，下期預定 %s" % (p["meta"]["report_date"], p["meta"]["next_release"]))
