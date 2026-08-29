/* 前端密碼閘門
   ------------------------------------------------------------------
   這是「軟鎖」，不是存取控制：擋得住隨手點進網址的人，擋不住懂技術的人。
   原因——本站是公開的 GitHub Pages，repo 也是 public：
     · data/latest.json 與 data/cot_history.csv 可以直接用網址開啟，繞過本頁
     · 整份原始碼在 github.com 上看得到
   要真正鎖住，得改用伺服器端驗證（Cloudflare Access 等）並把 repo 轉 private。

   附帶一提：本站的資料本來就是美國聯邦政府的公開報告，任何人都能自己去 CFTC 下載，
   所以這道閘門擋的是「這個網頁介面」，不是資料本身的機密性。

   密碼本身不會外洩：這裡只存 PBKDF2-SHA256（20 萬次迭代）的雜湊，
   由 scripts/set_password.py 在本機產生，原文不進版控。 */

const GATE = {
  salt: '233cece198304e2a8c6dcbe5fdf20892',            // 由 scripts/set_password.py 填入
  hash: '2228dfdb7b0d041a26f73b715e6accf9539d6a603943cb9e13a40bbad151720a',            // 同上；留空＝尚未設密碼，直接放行
  iterations: 200000,
  ttlDays: 30,         // 解鎖後記住幾天
  storeKey: 'usp.gate',   // 不可與美國站 'umg.gate'、澳洲站 'amg.gate' 相同：
                          // 同一個 github.io 帳號下所有 Pages 站台共用 origin，
                          // localStorage 也共用，同 key 會互相蓋掉解鎖憑證
};

/* ------------------------------------------------------------ 雜湊計算 */
async function derive(pw) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(pw), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt: enc.encode(GATE.salt), iterations: GATE.iterations, hash: 'SHA-256' },
    key, 256);
  return [...new Uint8Array(bits)].map(b => b.toString(16).padStart(2, '0')).join('');
}

/* ------------------------------------------------------- 解鎖狀態的記憶 */
function remembered() {
  try {
    const t = JSON.parse(localStorage.getItem(GATE.storeKey) || 'null');
    // 換過密碼就讓舊憑證失效
    return !!t && t.h === GATE.hash && t.exp > Date.now();
  } catch { return false; }
}

function remember() {
  try {
    localStorage.setItem(GATE.storeKey, JSON.stringify({
      h: GATE.hash, exp: Date.now() + GATE.ttlDays * 864e5
    }));
  } catch { /* 無痕模式等情境下寫不進去，忽略即可 */ }
}

/* --------------------------------------------------------------- 解鎖 */
function unlock() {
  document.documentElement.classList.remove('locked');
  const g = document.getElementById('gate');
  if (g) g.remove();
  const s = document.createElement('script');
  s.src = 'assets/app.js';
  document.body.appendChild(s);
}

/* ------------------------------------------------------------ 閘門畫面 */
function renderGate() {
  const box = document.createElement('div');
  box.id = 'gate';
  box.innerHTML = `
    <form id="gateform" autocomplete="off">
      <div class="gate-title">部位與波動追蹤</div>
      <div class="gate-sub">請輸入密碼</div>
      <input id="gatepw" type="password" autocomplete="current-password"
             autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="密碼">
      <button class="btn btn-primary" id="gatego" type="submit">進入</button>
      <div class="gate-msg" id="gatemsg"></div>
    </form>`;
  document.body.appendChild(box);

  const pw = document.getElementById('gatepw');
  const go = document.getElementById('gatego');
  const msg = document.getElementById('gatemsg');
  pw.focus();

  let tries = 0;

  document.getElementById('gateform').addEventListener('submit', async e => {
    e.preventDefault();
    if (!pw.value) return;
    go.disabled = true; go.textContent = '驗證中…'; msg.textContent = '';
    try {
      if (await derive(pw.value) === GATE.hash) { remember(); unlock(); return; }
      tries++;
      msg.textContent = tries >= 3 ? `密碼錯誤（第 ${tries} 次）` : '密碼錯誤';
      pw.value = ''; pw.focus();
      // 錯越多次等越久，讓暴力嘗試不划算
      await new Promise(r => setTimeout(r, Math.min(tries * 600, 3000)));
    } catch (err) {
      msg.textContent = '無法驗證：請用 https 網址開啟本頁';
      console.error(err);
    } finally {
      go.disabled = false; go.textContent = '進入';
    }
  });
}

/* --------------------------------------------------------------- 啟動 */
if (!GATE.hash) {
  // 還沒設密碼：先跑 scripts/set_password.py，否則等於沒鎖
  console.warn('[gate] 尚未設定密碼，直接放行。請執行 python scripts/set_password.py');
  unlock();
} else if (remembered()) {
  unlock();
} else if (!window.crypto || !crypto.subtle) {
  document.documentElement.classList.remove('locked');
  document.body.innerHTML =
    '<p style="padding:24px;color:#A9A3BC">此瀏覽器環境無法驗證密碼，請改用 https 網址開啟。</p>';
} else {
  renderGate();
}
