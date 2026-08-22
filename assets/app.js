/* 美債部位與波動追蹤 — 前端渲染。
   不引任何外部圖表庫：資料量小、圖形單純，inline SVG 自己畫就夠，
   也省掉一個離線／CSP 會壞掉的相依。 */
'use strict';

const CAT_COLOR = {
  // TFF 五類
  dealer: 'var(--c-dealer)', asset_mgr: 'var(--c-asset)', lev_money: 'var(--c-lev)',
  other_rept: 'var(--c-other)', nonrept: 'var(--c-nonrept)',
  // Legacy 三類。投機沿用槓桿基金的紫、避險沿用資產管理的金——
  // 兩套分類法性質相近的角色給同一色，切換時視覺上比較好接。
  noncomm: 'var(--c-lev)', comm: 'var(--c-asset)'
};

let DATA = null;
let current = 'ust10y';
let basis = 'combined';   // combined / futonly / options
let scheme = 'tff';       // tff / legacy

// 兩套分類法 × 三種口徑，看的都是同一批部位的不同切面。
const L = () => DATA.latest[scheme][basis][current];
const T = () => DATA.trail[scheme][basis][current];
const S = () => DATA.schemes.find(x => x.key === scheme);
const CATS = () => S().categories;

const $ = id => document.getElementById(id);
const num = n => (n === null || n === undefined) ? '—' : n.toLocaleString('en-US');
const signed = n => (n === null || n === undefined) ? '—' : (n > 0 ? '+' : '') + n.toLocaleString('en-US');
const wan = n => (n === null || n === undefined) ? '—'
  : (Math.abs(n) >= 10000 ? (n / 10000).toFixed(1) + ' 萬' : n.toLocaleString('en-US'));
// 淨多＝押債價漲＝利多債市＝紅；淨空＝綠。與另外兩個站的語義一致。
const dir = n => n > 0 ? 'bull' : (n < 0 ? 'bear' : 'dim');

function fmtDate(s) {
  const d = new Date(s + 'T00:00:00');
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

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

  const repo = m.repo;
  const runUrl = `https://github.com/${repo}/actions/workflows/update.yml`;

  $('status').innerHTML = `
    <div class="stat">部位日期 <b>${m.report_date}</b>（週二收盤）</div>
    <div class="stat${state === 'ok' ? '' : ' warn'}">${label}</div>
    <a class="btn${state === 'stale' ? ' btn-warn' : ''}" href="${runUrl}"
       target="_blank" rel="noopener"
       title="開啟 GitHub Actions，在該頁右上角按 Run workflow 手動觸發一次更新">手動更新 ↗</a>`;

  $('srcline').textContent = `本期部位為 ${m.report_date}（週二）收盤，於當週五 15:30 ET 公布；`
    + `殖利率與波動度資料涵蓋至 ${m.yield_date}。`;
}

/* ── 合約切換 ────────────────────────────────────────── */
function renderChips() {
  $('chips').innerHTML = DATA.contracts.map(c => {
    const oi = DATA.latest[scheme][basis][c.key].oi;
    return `<button class="chip${c.key === current ? ' on' : ''}" data-k="${c.key}">
      ${c.zh}<span class="sm">OI ${wan(oi)}</span></button>`;
  }).join('');
  $('chips').querySelectorAll('.chip').forEach(b =>
    b.onclick = () => { current = b.dataset.k; render(); });
}

/* ── 分類法切換 ──────────────────────────────────────── */
function renderSchemes() {
  $('schemes').innerHTML = DATA.schemes.map(s =>
    `<button class="basebtn${s.key === scheme ? ' on' : ''}" data-s="${s.key}"
       title="${s.note}">${s.zh}</button>`).join('');
  $('schemes').querySelectorAll('.basebtn').forEach(b =>
    b.onclick = () => { scheme = b.dataset.s; render(); });
  const s = S();
  const n = L().cats[CATS()[0].key].sample;
  $('schemehint').innerHTML = `${s.note}。本合約此口徑共 ${num(n)} 週樣本。
    <b>兩套分類法不可互相取代</b>——Legacy 的「商業」對金融期貨是大雜燴，
    資產管理與交易商都被歸進去，所以它的「非商業淨空」跟 TFF 的「槓桿基金淨空」
    不是同一件事，數量級也不同。`;
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
  const c = DATA.contracts.find(x => x.key === current);
  const b = DATA.bases.find(x => x.key === basis);

  // 選了「期貨＋選擇權」時順便報出選擇權佔多少——這是拆分口徑最直接的用處。
  const optOI = DATA.latest[scheme].options[current].oi;
  const cbOI = DATA.latest[scheme].combined[current].oi;
  const share = basis === 'combined'
    ? `其中選擇權貢獻 <b>${num(optOI)}</b> 口（${(100 * optOI / cbOI).toFixed(1)}%）。` : '';

  $('m1note').innerHTML = `${c.zh}合約 · <b>${b.zh}</b>，未平倉量 <b>${num(row.oi)}</b> 口，
    週變化 <span class="${dir(row.oi_chg)}">${signed(row.oi_chg)}</span>。${share}
    左綠為空方、右紅為多方，長度以同一把尺；價差（spread）部位是同時持有多空的套利腿，不計入淨額。`;

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
  DATA.contracts.forEach(c => {
    const r = DATA.latest[scheme][basis][c.key];
    CATS().forEach(cat => {
      const v = r.cats[cat.key];
      if (v.net_chg === null || v.net_chg === undefined) return;
      rows.push({ c, cat, v });
    });
  });
  rows.sort((a, b) => Math.abs(b.v.net_chg) - Math.abs(a.v.net_chg));

  const bz = DATA.bases.find(x => x.key === basis).zh;
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
  const c = DATA.contracts.find(x => x.key === current);
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
  // 取該分類法排前三的類別（TFF 是交易商／資產管理／槓桿基金，Legacy 是投機／避險／小戶）。
  // 小戶在 TFF 排最後所以自然被排除，在 Legacy 只有三類就全上。
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
  const map = [
    { k: 'y2', name: '2 年期', color: 'var(--c-dealer)' },
    { k: 'y10', name: '10 年期', color: 'var(--c-asset)' },
    { k: 'y30', name: '30 年期', color: 'var(--c-lev)' }
  ];
  const series = map.map(m => ({
    name: m.name, color: m.color, pts: DATA.vol[m.k].rv20
  }));
  const now = map.map(m => {
    const rv20 = DATA.vol[m.k].rv20, rv60 = DATA.vol[m.k].rv60;
    return `<div class="stat">${m.name} <b>${rv20[rv20.length - 1][1]}</b> bp／年
      <span class="dim">（60 日 ${rv60[rv60.length - 1][1]}）</span></div>`;
  }).join('');

  $('m4').innerHTML = `<div class="statusbar" style="justify-content:flex-start;margin-bottom:10px">${now}</div>`
    + lineChart(series, { fmt: v => v + ' bp' })
    + legend(series)
    + `<div class="note" style="margin:10px 0 0">20 日滾動窗；60 日值列在上方數字後方供對照。
       20 日明顯高於 60 日＝波動正在放大。</div>`;
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
  y0 -= pad; y1 += pad;

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

function legend(series) {
  return `<div class="legend">${series.map(s =>
    `<span><i style="background:${s.color}"></i>${s.name}</span>`).join('')}</div>`;
}

/* ── 進入點 ─────────────────────────────────────────── */
function render() {
  renderChips(); renderSchemes(); renderBases(); renderM1(); renderM2(); renderM3(); renderM4();
}

fetch('data/latest.json')
  .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
  .then(d => { DATA = d; renderStatus(); render(); })
  .catch(e => {
    document.body.insertAdjacentHTML('afterbegin',
      `<div class="card" style="border-color:var(--warn);color:var(--warn);margin-bottom:14px">
       載入 data/latest.json 失敗：${e.message}。請確認網站已完成建置。</div>`);
  });
