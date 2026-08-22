"""共用工具：路徑、HTTP、日期。

資料源都是公開免金鑰的：
- CFTC Socrata（publicreporting.cftc.gov）：美國聯邦政府公開報告
- FRED fredgraph.csv：不需 API key 的 CSV 端點
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

# Windows 主控台預設 cp950，印到中文以外的符號（✓ ✗ →）會直接丟 UnicodeEncodeError
# 把腳本炸掉。Actions 跑在 Linux／UTF-8 沒這問題，但本機一定會踩到，統一在這裡處理。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ust-positioning/1.0"


def get(url: str, params: dict | None = None, retries: int = 4, timeout: int = 45):
    """帶重試的 GET。CFTC 偶有 5xx，退避重試即可。"""
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": UA})
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"取得失敗 {url}: {last}")


def get_json(url: str, params: dict | None = None):
    return get(url, params).json()


def get_impersonated(url: str, params: dict | None = None, retries: int = 4, timeout: int = 90):
    """模擬 Chrome 的 TLS 指紋來抓。

    FRED 會對一般 HTTP 客戶端重置連線（本機是 ConnectionReset，
    GitHub runner 上表現為讀取逾時），curl_cffi 偽裝指紋後 2.8 秒就抓完 20 年日資料。
    這是 CME、cftc.gov 主站、AOFM 都用的同一類防護。

    CFTC 的 Socrata API（publicreporting.cftc.gov）沒有這層，用一般 requests 即可，
    所以不把整個專案都換過去——多一層偽裝就多一個會壞的地方。
    """
    from curl_cffi import requests as cr

    last = None
    for attempt in range(retries):
        try:
            r = cr.get(url, params=params, timeout=timeout, impersonate="chrome")
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"取得失敗（偽裝指紋）{url}: {last}")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj, compact: bool = False) -> None:
    """寫檔。歷史資料用 compact 省空間，給人看的用縮排。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(obj, ensure_ascii=False, indent=1)
    path.write_text(text + "\n", encoding="utf-8")


def to_date(s: str) -> date:
    """CFTC 回傳 '2026-08-18T00:00:00.000'，FRED 回傳 '2026-08-20'。"""
    return datetime.fromisoformat(s.replace("Z", "")).date()


def iso(d: date) -> str:
    return d.isoformat()
