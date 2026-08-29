/* 部位與波動追蹤 — 前端渲染。
   不引任何外部圖表庫：資料量小、圖形單純，inline SVG 自己畫就夠，
   也省掉一個離線／CSP 會壞掉的相依。

   三個資產分頁（美債／原油／貴金屬）共用同一套模組，差異全部由 latest.json 決定——
   哪些合約、哪幾套分類法、波動度用什麼單位、頁尾寫什麼來源，前端一律照 payload 渲染，
   不在這裡寫任何 if (asset === 'oil')。加第四個分頁時只要動 scripts/cftc.py。 */
'use strict';

const CAT_COLOR = {
  // TFF 五類（美債）
  dealer: 'var(--c-dealer)', asset_mgr: 'var(--c-asset)', lev_money: 'var(--c-lev)',
  other_rept: 'var(--c-other)', nonrept: 'var(--c-nonrept)',
  // Disagg 五類（商品）。跨分類法用「角色相同給同一色」對齊：
  //   投機資金   槓桿基金 → 管理基金 → 非商業   全部用紫
  //   避險／實需 資產管理 → 生產商   → 商業     全部用金
  //   中介       交易商   → 交換商             全部用藍
  // 切分頁或切分類法時，同一個角色的線不會換色，眼睛不用重新對照圖例。
  prod_merc: 'var(--c-asset)', swap: 'var(--c-dealer)', m_money: 'var(--c-lev)',
  // Legacy 三類（共用）
  noncomm: 'var(--c-lev)', comm: 'var(--c-asset)'
};

let DATA = null;
let asset = 'ust';
let current = 'ust10y';
let basis = 'combined';   // combined / futonly / options
let scheme = 'tff';       // tff / disagg / legacy

const A = () => DATA.assets.find(x => x.key === asset);
const L = () => DATA.latest[asset][scheme][basis][current];
const T = () => DATA.trail[asset][scheme][basis][current];
const S = () => DATA.schemes[scheme];
const CATS = () => S().categories;
const CONTRACT = () => A().contracts.find(x => x.key === current);

const $ = id => document.getElementById(id);
const num = n => (n === null || n === undefined) ? '—' : n.toLocaleString('en-US');
const signed = n => (n === null || n === undefined) ? '—' : (n > 0 ? '+' : '') + n.toLocaleString('en-US');
const wan = n => (n === null || n === undefined) ? '—'
  : (Math.abs(n) >= 10000 ? (n / 10000).toFixed(1) + ' 萬' : n.toLocaleString('en-US'));
// 淨多＝押標的價格上漲＝紅；淨空＝綠。美債頁的「債價漲」就是利多債市，
// 商品頁的「油價／金價漲」語義相同，所以三個分頁共用同一組顏色，不必換。
const dir = n => n > 0 ? 'bull' : (n < 0 ? 'bear' : 'dim');

/* ── 狀態列 ──────────────────────────────────────────── */
function renderStatus() {
  const m = DATA.meta;

  // 判定基準是「下一期該發布了沒」，不是「這份資料幾天前的」。
  //
  // 用資料年齡判斷會晚很多：COT 報的是週二部位、週五才發，正常情況下站上永遠是
  // 三到十天前的數字，看年齡分不出「本來就這樣」和「更新掛了」。改看下期發布日
  // 有沒有被跨過，自動更新沒起的隔天就看得出來。
  //
  // 寬限四天：CFTC 遇美國假日會把發布順延到下週一（+3 天），加上排程本身可能晚幾小時。
  const daysPastDue = Math.floor(
    (Date.now() - new Date(m.next_release + 'T00:00:00')) / 86400000);
  const state = daysPastDue < 0 ? 'ok' : (daysPastDue <= 4 ? 'due' : 'stale');

  const label = {
    ok: `下期預定 <b>${m.next_release}</b>`,
    due: `新一期應已發布，等待更新（預定 ${m.next_release}）`,
    stale: `⚠ 已逾預定發布日 ${daysPastDue} 天仍未更新`
  }[state];

  const runUrl = `https://github.com/${m.repo}/actions/workflows/update.yml`;

  $('status').innerHTML = `
    <div class="stat">部位日期 <b>${m.report_date}</b>（週二收盤）</div>
    <div class="stat${state === 'ok' ? '' : ' warn'}">${label}</div>
    <a class="btn${state === 'stale' ? ' btn-warn' : ''}" href="${runUrl}"
       target="_blank" rel="noopener"
       title="開啟 GitHub Actions，在該頁右上角按 Run workflow 手動觸發一次更新">手動更新 ↗</a>`;
}

/* ── 資產分頁 ────────────────────────────────────────── */
function renderAssets() {
  $('assets').innerHTML = DATA.assets.map(a =>
    `<button class="atab${a.key === asset ? ' on' : ''}" data-a="${a.key}">${a.zh}</button>`
  ).join('');
  $('assets').querySelectorAll('.atab').forEach(b =>
    b.onclick = () => switchAsset(b.dataset.a));
  $('sub').textContent = A().sub;
}

function switchAsset(key) {
  asset = key;
  const a = A();
  current = a.default_contract;
  // 分類法不能沿用：美債的 tff 在商品頁不存在，商品的 disagg 在美債頁也不存在。
  // 口徑（combined／futonly／options）三頁通用，故保留使用者原本的選擇。
  scheme = a.schemes[0];
  render();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ── 合約切換 ────────────────────────────────────────── */
function renderChips() {
  $('chips').innerHTML = A().contracts.map(c => {
    const oi = DATA.latest[asset][scheme][basis][c.key].oi;
    return `<button class="chip${c.key === current ? ' on' : ''}" data-k="${c.key}">
      ${c.zh}<span class="sm">OI ${wan(oi)}</span></button>`;
  }).join('');
  $('chips').querySelectorAll('.chip').forEach(b =>
    b.onclick = () => { current = b.dataset.k; render(); });
}

/* ── 分類法切換 ──────────────────────────────────────── */
function renderSchemes() {
  $('schemes').innerHTML = A().schemes.map(k =>
    `<button class="basebtn${k === scheme ? ' on' : ''}" data-s="${k}"
       title="${DATA.schemes[k].note}">${DATA.schemes[k].zh}</button>`).join('');
  $('schemes').querySelectorAll('.basebtn').forEach(b =>
    b.onclick = () => { scheme = b.dataset.s; render(); });

  const n = L().cats[CATS()[0].key].sample;
  // 同一套 Legacy 在美債與商品上的可信度天差地遠，所以提醒文字是
  // 「分類法 × 資產」兩個維度決定的，由 build.py 帶進 payload。
  const note = DATA.scheme_notes[`${scheme}|${asset}`] || '';
  $('schemehint').innerHTML = `${S().note}。本合約此口徑共 ${num(n)} 週樣本。${note}`;
}

/* ── 口徑切換 ────────────────────────────────────────── */
function renderBases() {
  $('bases').innerHTML = DATA.bases.map(b =>
    `<button class="basebtn${b.key === basis ? ' on' : ''}" data-b="${b.key}"
       title="${b.note}">${b.zh}</button>`).join('');
  $('bases').querySelectorAll('.basebtn').forEach(b =>
    b.onclick = () => { basis = b.dataset.b; render(); });
}

/* ── M1 部位結構 ─────────────────────────────────────── */
function renderM1() {
  const row = L();
  const c = CONTRACT();
  const b = DATA.bases.find(x => x.key === basis);

  // 選了「期貨＋選擇權」時順便報出選擇權佔多少——這是拆分口徑最直接的用處。
  const optOI = DATA.latest[asset][scheme].options[current].oi;
  const cbOI = DATA.latest[asset][scheme].combined[current].oi;
  const share = basis === 'combined'
    ? `其中選擇權貢獻 <b>${num(optOI)}</b> 口（${(100 * optOI / cbOI).toFixed(1)}%）。` : '';
  // 合約規格差很多——一口 WTI 是 1,000 桶、一口黃金是 100 盎司，
  // 跨商品比較「口數」沒有意義，把單位擺出來提醒。
  const units = row.units ? `<span class="dim">合約單位 ${row.units}</span>` : '';

  $('m1note').innerHTML = `${c.zh}合約 · <b>${b.zh}</b>，未平倉量 <b>${num(row.oi)}</b> 口，
    週變化 <span class="${dir(row.oi_chg)}">${signed(row.oi_chg)}</span>。${share}
    左綠為空方、右紅為多方，長度以同一把尺；價差（spread）部位是同時持有多空的套利腿，不計入淨額。
    ${units}`;

  const scale = Math.max(...CATS().map(cat => {
    const v = row.cats[cat.key];
    return Math.max(v.long || 0, v.short || 0);
  }));

  $('m1').innerHTML = CATS().map(cat => {
    const v = row.cats[cat.key];
    const lw = 50 * (v.long || 0) / scale, sw = 50 * (v.short || 0) / scale;
    const pctOI = row.oi ? (100 * v.net / row.oi) : 0;
    return `<div class="posrow">
      <div class="who"><i style="background:${CAT_COLOR[cat.key]}"></i>
        <span>${cat.zh}<span class="en">${cat.en}</span></span></div>
      <div class="bar">
        <div class="s" style="width:${sw}%"></div>
        <div class="l" style="width:${lw}%"></div>
        <div class="mid"></div>
      </div>
      <div class="num ${dir(v.net)}">${signed(v.net)}
        <span class="pct">淨部位佔 OI ${pctOI.toFixed(1)}%</span></div>
    </div>`;
  }).join('');
}

/* ── M2 本週誰在加倉 ─────────────────────────────────── */
function judge(v) {
  // 用變化較大的那一腿判定主導行為：淨部位轉多，可能是新增多單，也可能是空單回補，
  // 兩者對後續行情的含義完全不同，不可混為一談。
  const cl = v.chg_long || 0, cs = v.chg_short || 0;
  if (cl === 0 && cs === 0) return '持平';
  if (Math.abs(cl) >= Math.abs(cs)) return cl > 0 ? '增多單' : '減多單';
  return cs > 0 ? '增空單' : '減空單';
}

function renderM2() {
  const rows = [];
  A().contracts.forEach(c => {
    const r = DATA.latest[asset][scheme][basis][c.key];
    CATS().forEach(cat => {
      const v = r.cats[cat.key];
      if (v.net_chg === null || v.net_chg === undefined) return;
      rows.push({ c, cat, v });
    });
  });
  rows.sort((a, b) => Math.abs(b.v.net_chg) - Math.abs(a.v.net_chg));

  const bz = DATA.bases.find(x => x.key === basis).zh;
  // 排行涵蓋「本分頁的所有合約 × 本分類法的所有類別」，數量隨分頁而異，
  // 所以這行字要算出來，不能寫死成「六檔 × 五類」。
  $('m2note').innerHTML = `依「淨部位變化」的絕對值排序，本頁
    ${A().contracts.length} 檔 × ${CATS().length} 類全部納入。
    多方變化與空方變化分開列——同樣是淨部位轉多，「新增多單」和「空單回補」的意義完全不同。`;

  $('m2').innerHTML = `<div style="font-size:12.5px;color:var(--text2);margin-bottom:9px">
      口徑：<b>${bz}</b></div>
    <table>
    <thead><tr>
      <th class="l">合約</th><th class="l">交易人</th>
      <th>多方變化</th><th>空方變化</th><th>淨部位變化</th>
      <th>目前淨部位</th><th class="l">主導行為</th>
    </tr></thead><tbody>
    ${rows.slice(0, 14).map(({ c, cat, v }) => `<tr>
      <td class="l">${c.zh}</td>
      <td class="l"><span style="color:${CAT_COLOR[cat.key]}">●</span> ${cat.zh}</td>
      <td class="${dir(v.chg_long)}">${signed(v.chg_long)}</td>
      <td class="${dir(-(v.chg_short || 0))}">${signed(v.chg_short)}</td>
      <td class="${dir(v.net_chg)}"><b>${signed(v.net_chg)}</b></td>
      <td class="${dir(v.net)}">${signed(v.net)}</td>
      <td class="l"><span class="tag ${dir(v.net_chg)}">${judge(v)}</span></td>
    </tr>`).join('')}
    </tbody></table>`;
}

/* ── M3 極端度 ──────────────────────────────────────── */
function renderM3() {
  const row = L();
  const c = CONTRACT();
  const b = DATA.bases.find(x => x.key === basis);
  const sample = row.cats[CATS()[0].key].sample;
  const firstDate = T()[0].date;
  $('m3note').innerHTML = `<b>${b.zh}</b>口徑。百分位是目前淨部位在<b>該合約該口徑的全歷史</b>中的位置
    （${c.zh}共 ${num(sample)} 週樣本，愈接近 100 代表史上少見的偏多、愈接近 0 代表史上少見的偏空）。
    z 值為近三年的標準差倍數。兩者都用<b>擴張視窗</b>計算，只看該週之前的資料，
    不讓歷史圖上的每一點偷看未來。右圖起點 ${firstDate}。`;

  $('m3gauges').innerHTML = CATS().map(cat => {
    const v = row.cats[cat.key];
    const p = v.pctile;
    return `<div class="gaugerow">
      <div class="who" style="font-size:13px">${cat.zh}</div>
      <div class="gauge">${p === null ? '' : `<div class="pin" style="left:calc(${p}% - 1px)"></div>`}</div>
      <div class="num">${p === null ? '<span class="dim">樣本不足</span>' : p.toFixed(1) + '%'}
        <span class="z">z ${v.z === null || v.z === undefined ? '—' : (v.z > 0 ? '+' : '') + v.z}</span></div>
    </div>`;
  }).join('');

  const trail = T();
  // 取該分類法排前三的類別。TFF 是交易商／資產管理／槓桿基金，
  // Disagg 是生產商／交換商／管理基金，Legacy 只有三類就全上。
  // 兩套五類法的第四、五類（其他可報告戶、小戶）量體小，畫上去只會壓縮縱軸。
  const series = CATS().slice(0, 3).map(cat => ({
    name: cat.zh, color: CAT_COLOR[cat.key],
    pts: trail.map(r => [r.date, r.cats[cat.key].net])
  }));
  $('m3chart').innerHTML =
    `<div style="font-size:12.5px;color:var(--text2);margin-bottom:6px">主要類別淨部位（近三年，口）</div>`
    + lineChart(series, { zero: true, fmt: wan })
    + legend(series);
}

/* ── M4 已實現波動 ──────────────────────────────────── */
function renderM4() {
  const a = A();
  const spec = a.vol;
  const vol = DATA.vol[asset];

  $('m4note').innerHTML = spec.note;

  const series = spec.plot.map(k => ({
    name: vol[k].zh, color: vol[k].color, pts: vol[k].rv20
  }));

  const now = spec.plot.map(k => {
    const s = vol[k];
    const rv20 = s.rv20[s.rv20.length - 1][1];
    const rv60 = s.rv60[s.rv60.length - 1][1];
    // 價格類的分頁順帶報出最新價位——看波動度時第一個會想問的就是「現在多少錢」。
    const level = spec.kind === 'price'
      ? ` <span class="dim">｜ ${s.level[s.level.length - 1][1]} ${s.unit}</span>` : '';
    return `<div class="stat">${s.zh} <b>${rv20}</b> ${spec.unit}
      <span class="dim">（60 日 ${rv60}）</span>${level}</div>`;
  }).join('');

  $('m4').innerHTML = `<div class="statusbar" style="justify-content:flex-start;margin-bottom:10px">${now}</div>`
    + lineChart(series, { fmt: v => v + (spec.kind === 'price' ? '%' : ' bp') })
    + legend(series)
    + `<div class="note" style="margin:10px 0 0">20 日滾動窗；60 日值列在上方數字後方供對照。
       20 日明顯高於 60 日＝波動正在放大。資料涵蓋至 ${spec.date}。</div>`;
}

/* ── M5 配對（金銀比／WTI-Brent 價差） ────────────────── */
function renderM5() {
  const p = A().pair;
  // 沒有配對資料的分頁整段藏起來，連標題都不留——留一個空殼標題比沒有還糟。
  $('m5sec').hidden = !p;
  if (!p) return;

  const price = DATA.vol[asset][p.overlay[current]];
  const u = p.unit ? ' ' + p.unit : '';

  $('m5title').textContent = p.zh;
  $('m5note').innerHTML = p.note;

  // 配對序列同一頁共用，換合約只換右軸疊的那條價格線。
  const pairSeries = { name: `${p.zh}（左）`, color: p.color, pts: p.series };
  const priceSeries = { name: `${price.zh}（右）`, color: price.color, pts: price.level };

  // 價差有正負號，數字本身就帶方向，區間跨零時尤其要看得出來
  // （−25.94 – +2.45 一眼就知道跨過零，寫成 −25.94 – 2.45 就不明顯）。
  // 比值恆為正，加號只會很怪。
  const n = p.op === 'spread' ? signed : (v => v);

  const stats = `
    <div class="stat">目前 <b>${n(p.now)}</b>${u}
      <span class="dim">（${p.hint}）</span></div>
    <div class="stat">近三年區間 <b>${n(p.lo)} – ${n(p.hi)}</b>${u}</div>
    <div class="stat">自 ${p.since} 起的百分位 <b>${p.pctile}%</b>
      <span class="dim">（${num(p.n)} 個交易日）</span></div>`;

  // 換合約才有意義的那句提示，只有多於一檔的分頁才顯示（原油頁只有 WTI）。
  const switchHint = A().contracts.length > 1
    ? `上方切換<b>${A().contracts.map(c => c.zh).join('／')}</b>可換右軸疊的價格。` : '';

  $('m5').innerHTML =
    `<div class="statusbar" style="justify-content:flex-start;margin-bottom:10px">${stats}</div>`
    + dualChart(pairSeries, priceSeries, { zeroLeft: p.zero })
    + legend([pairSeries, priceSeries])
    + `<div class="note" style="margin:10px 0 0">
       圖為近三年，與 M3 部位軌跡、M4 波動度同一個時間尺度；
       百分位則用 ${p.since} 起的全樣本算——這類指標都是長週期的，
       只看三年會把極端讀成常態。兩條線各自縮放，看的是方向關係不是高低。
       ${switchHint}</div>`;
}

/* ── 頁尾來源 ───────────────────────────────────────── */
function renderFooter() {
  const m = DATA.meta;
  $('srcline').textContent = `本期部位為 ${m.report_date}（週二）收盤，於當週五 15:30 ET 公布；`
    + `本頁的價格／殖利率資料涵蓋至 ${A().vol.date}。`;
  $('srcnotes').innerHTML = A().source_note.map(t => `<div>${t}</div>`).join('');
}

/* ── SVG 折線圖 ─────────────────────────────────────── */
function lineChart(series, opt) {
  opt = opt || {};
  const W = 760, H = 210, PL = 54, PR = 8, PT = 10, PB = 22;
  const all = series.flatMap(s => s.pts);
  if (!all.length) return '';
  const xs = all.map(p => new Date(p[0] + 'T00:00:00').getTime());
  const ys = all.map(p => p[1]).filter(v => v !== null && v !== undefined);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (opt.zero) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
  const pad = (y1 - y0) * 0.08 || 1;
  const rawMin = Math.min(...ys);
  y0 -= pad; y1 += pad;
  // 全正的序列（波動度就是）不讓留白把軸推到零以下——刻度出現「−2%」會被讀成
  // 真的有負波動。zero 模式是淨部位圖，本來就要跨零軸，不套這條。
  if (!opt.zero && rawMin >= 0) y0 = Math.max(y0, 0);

  const px = t => PL + (W - PL - PR) * (t - x0) / (x1 - x0 || 1);
  const py = v => PT + (H - PT - PB) * (1 - (v - y0) / (y1 - y0 || 1));
  const fmt = opt.fmt || (v => String(v));

  const paths = series.map(s => {
    const d = s.pts.filter(p => p[1] !== null && p[1] !== undefined)
      .map((p, i) => (i ? 'L' : 'M') + px(new Date(p[0] + 'T00:00:00').getTime()).toFixed(1)
        + ' ' + py(p[1]).toFixed(1)).join(' ');
    return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.6"
      stroke-linejoin="round" stroke-linecap="round"/>`;
  }).join('');

  // Y 軸四格刻度
  const ticks = [0, 1, 2, 3].map(i => {
    const v = y0 + (y1 - y0) * i / 3;
    return `<line class="axis" x1="${PL}" y1="${py(v).toFixed(1)}" x2="${W - PR}" y2="${py(v).toFixed(1)}"
      opacity=".45"/><text x="${PL - 6}" y="${(py(v) + 3.5).toFixed(1)}" text-anchor="end">${fmt(Math.round(v))}</text>`;
  }).join('');

  const zeroLine = (opt.zero && y0 < 0 && y1 > 0)
    ? `<line class="zero" x1="${PL}" y1="${py(0).toFixed(1)}" x2="${W - PR}" y2="${py(0).toFixed(1)}"/>` : '';

  // X 軸：頭、中、尾三個日期
  const xl = [0, 0.5, 1].map(f => {
    const t = x0 + (x1 - x0) * f;
    const iso = new Date(t).toISOString().slice(0, 10);
    const anchor = f === 0 ? 'start' : (f === 1 ? 'end' : 'middle');
    return `<text x="${px(t).toFixed(1)}" y="${H - 6}" text-anchor="${anchor}">${iso.slice(0, 7)}</text>`;
  }).join('');

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">
    ${ticks}${zeroLine}${paths}${xl}</svg>`;
}

/* ── 雙 Y 軸折線圖 ──────────────────────────────────── */
/* 比值與價格差兩個數量級（金銀比 ~80、金價 ~4,500），共用一根軸的話
   比值會被壓成一條直線。所以左右各一根軸、各自縮放——代價是**不能讀高低，
   只能讀方向**，這件事在 M5 的說明文字裡有寫明。 */
function dualChart(left, right, opt) {
  opt = opt || {};
  const W = 760, H = 210, PL = 54, PR = 58, PT = 10, PB = 22;
  const t = s => new Date(s + 'T00:00:00').getTime();
  const clean = s => s.pts.filter(p => p[1] !== null && p[1] !== undefined);
  const lp = clean(left), rp = clean(right);
  if (!lp.length || !rp.length) return '';

  // 兩條線的起訖裁到共同區間，否則其中一條會多出一截沒有對照的尾巴，
  // 讀起來像是那段期間另一個數列缺值。
  const x0 = Math.max(t(lp[0][0]), t(rp[0][0]));
  const x1 = Math.min(t(lp[lp.length - 1][0]), t(rp[rp.length - 1][0]));
  const win = pts => pts.filter(p => t(p[0]) >= x0 && t(p[0]) <= x1);
  const L = win(lp), R = win(rp);
  if (!L.length || !R.length) return '';

  const scale = (pts, forceZero) => {
    const ys = pts.map(p => p[1]);
    let a = Math.min(...ys), b = Math.max(...ys);
    // 價差圖一定要看得到零線——正負號就是它的意義所在，
    // 硬把零軸擠出畫面等於把「現在是折價還是溢價」這件事藏起來。
    if (forceZero) { a = Math.min(a, 0); b = Math.max(b, 0); }
    const pad = (b - a) * 0.08 || 1;
    a -= pad; b += pad;
    // 比值與價格都是正的，別讓留白把軸推到零以下
    if (!forceZero && Math.min(...ys) >= 0) a = Math.max(a, 0);
    return [a, b];
  };
  const [l0, l1] = scale(L, opt.zeroLeft), [r0, r1] = scale(R);

  const px = v => PL + (W - PL - PR) * (v - x0) / (x1 - x0 || 1);
  const py = (v, a, b) => PT + (H - PT - PB) * (1 - (v - a) / (b - a || 1));
  // 4,562 不需要小數，70.26 需要——依數量級決定位數
  const fmt = v => Math.abs(v) >= 200 ? Math.round(v).toLocaleString('en-US') : v.toFixed(1);

  const path = (pts, a, b, color) =>
    `<path d="${pts.map((p, i) => (i ? 'L' : 'M') + px(t(p[0])).toFixed(1)
      + ' ' + py(p[1], a, b).toFixed(1)).join(' ')}"
      fill="none" stroke="${color}" stroke-width="1.6"
      stroke-linejoin="round" stroke-linecap="round"/>`;

  // 四格刻度共用同一批格線，左右各標自己的數字
  const ticks = [0, 1, 2, 3].map(i => {
    const f = i / 3;
    const y = (PT + (H - PT - PB) * (1 - f)).toFixed(1);
    return `<line class="axis" x1="${PL}" y1="${y}" x2="${W - PR}" y2="${y}" opacity=".45"/>
      <text x="${PL - 6}" y="${(+y + 3.5).toFixed(1)}" text-anchor="end"
        fill="${left.color}">${fmt(l0 + (l1 - l0) * f)}</text>
      <text x="${W - PR + 6}" y="${(+y + 3.5).toFixed(1)}" text-anchor="start"
        fill="${right.color}">${fmt(r0 + (r1 - r0) * f)}</text>`;
  }).join('');

  const xl = [0, 0.5, 1].map(f => {
    const v = x0 + (x1 - x0) * f;
    const anchor = f === 0 ? 'start' : (f === 1 ? 'end' : 'middle');
    return `<text x="${px(v).toFixed(1)}" y="${H - 6}" text-anchor="${anchor}">${
      new Date(v).toISOString().slice(0, 7)}</text>`;
  }).join('');

  // 零線只畫左軸的——右軸是價格，價格的零沒有意義。畫在折線底下才不會遮住資料。
  const zeroLine = (opt.zeroLeft && l0 < 0 && l1 > 0)
    ? `<line class="zero" x1="${PL}" y1="${py(0, l0, l1).toFixed(1)}"
        x2="${W - PR}" y2="${py(0, l0, l1).toFixed(1)}"/>` : '';

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">${ticks}${zeroLine}
    ${path(R, r0, r1, right.color)}${path(L, l0, l1, left.color)}${xl}</svg>`;
}

function legend(series) {
  return `<div class="legend">${series.map(s =>
    `<span><i style="background:${s.color}"></i>${s.name}</span>`).join('')}</div>`;
}

/* ── 進入點 ─────────────────────────────────────────── */
function render() {
  renderAssets(); renderChips(); renderSchemes(); renderBases();
  renderM1(); renderM2(); renderM3(); renderM4(); renderM5(); renderFooter();
}

fetch('data/latest.json')
  .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
  .then(d => {
    DATA = d;
    // 預設分頁與預設合約都以 payload 為準，不寫死在這裡——
    // 日後把 ASSETS 的順序調換或改預設合約，前端不用跟著改。
    asset = d.assets[0].key;
    current = d.assets[0].default_contract;
    scheme = d.assets[0].schemes[0];
    renderStatus(); render();
  })
  .catch(e => {
    document.body.insertAdjacentHTML('afterbegin',
      `<div class="card" style="border-color:var(--warn);color:var(--warn);margin-bottom:14px">
       載入 data/latest.json 失敗：${e.message}。請確認網站已完成建置。</div>`);
  });
