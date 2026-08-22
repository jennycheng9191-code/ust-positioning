"""設定網頁密碼。

用法：
    python scripts/set_password.py

在本機輸入密碼（不會顯示、不會存檔、不進版控），
本腳本只把 PBKDF2-SHA256 的鹽與雜湊寫回 assets/gate.js。

提醒：這是前端軟鎖。repo 是 public、data/*.json 可直接用網址開啟，
所以它擋的是「隨手點進來的人」，不是有心人。
"""

import getpass
import hashlib
import pathlib
import re
import secrets
import sys

GATE = pathlib.Path(__file__).resolve().parent.parent / "assets" / "gate.js"
ITERATIONS = 200_000


def prompt(label: str) -> str:
    """在終端機隱藏輸入；被管線餵資料時改讀 stdin（Windows 的 getpass 只認主控台，
    否則會在沒有 tty 的環境卡住不動）。"""
    if sys.stdin.isatty():
        return getpass.getpass(label)
    print(label, end="", flush=True)
    return sys.stdin.readline().rstrip("\r\n")


def main() -> int:
    if not GATE.exists():
        print(f"找不到 {GATE}", file=sys.stderr)
        return 1

    src = GATE.read_text(encoding="utf-8")
    m = re.search(r"iterations:\s*(\d+)", src)
    iterations = int(m.group(1)) if m else ITERATIONS

    pw = prompt("新密碼：")
    if len(pw) < 6:
        print("密碼太短，至少 6 個字元。", file=sys.stderr)
        return 1
    if pw != prompt("再輸入一次："):
        print("兩次輸入不一致。", file=sys.stderr)
        return 1

    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", pw.encode("utf-8"), salt.encode("utf-8"), iterations, dklen=32
    ).hex()

    src, n_salt = re.subn(r"salt:\s*'[^']*'", f"salt: '{salt}'", src, count=1)
    src, n_hash = re.subn(r"hash:\s*'[^']*'", f"hash: '{digest}'", src, count=1)
    if not (n_salt and n_hash):
        print("gate.js 格式不符預期，沒有寫入。", file=sys.stderr)
        return 1

    GATE.write_text(src, encoding="utf-8")
    print(f"已寫入 {GATE.name}（迭代 {iterations:,} 次）。")
    print("接著執行：git add -A && git commit -m '加上密碼閘門' && git push")
    print("舊的解鎖憑證會自動失效，所有裝置都要重新輸入密碼。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
