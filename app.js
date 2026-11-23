/* toto勝敗予想システム 閲覧専用サイト（GitHub Pages / Mac電源オフ対応）
 * データ: Mac側で LightGBM 等が計算した最新予測JSON（data.json）を読み込む。
 * マイAI育成データは各ブラウザの localStorage のみ（キー: toto_my_ai_v1）。
 */
"use strict";

const LS_KEY = "toto_my_ai_v1";
const OUTCOMES = ["1", "0", "2"];
const OUTCOME_LABEL = { "1": "ホーム勝ち", "0": "引き分け", "2": "アウェイ勝ち" };
const TICKET_UNIT = 100;
const GOAL_CATS = ["0", "1", "2", "3+"];
const GOAL_LABEL = { "0": "0点", "1": "1点", "2": "2点", "3+": "3点以上" };

const $ = (s) => document.querySelector(s);

/* ---------------- 外部情報取得（CORSプロキシ） ----------------
 * 第1候補: 専用Cloudflare Workerプロキシ。失敗時は無料プロキシ→直接取得の順。
 */
const DEDICATED_PROXY = "https://sparkling-firefly-4550.mopfyui99201.workers.dev?url=";
const PROXY_CHAIN = [
  (u) => DEDICATED_PROXY + encodeURIComponent(u),
  (u) => "https://api.allorigins.win/raw?url=" + encodeURIComponent(u),
  (u) => "https://corsproxy.io/?url=" + encodeURIComponent(u),
  (u) => "https://api.codetabs.com/v1/proxy?quest=" + encodeURIComponent(u),
];
async function fetchExternal(url, opts = {}) {
  let lastErr = null;
  for (const wrap of PROXY_CHAIN) {
    try {
      const r = await fetch(wrap(url), { ...opts, signal: AbortSignal.timeout(12000) });
      if (r.ok) return await r.text();
      lastErr = new Error(`HTTP ${r.status}`);
    } catch (e) { lastErr = e; }
  }
  try {
    const r = await fetch(url, { ...opts, signal: AbortSignal.timeout(12000) });
    if (r.ok) return await r.text();
    throw new Error(`HTTP ${r.status}`);
  } catch (e) { throw lastErr || e; }
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let DATA = null;          // data.json
let BUILD_TS = "";
let CUR_GAME = null;      // 選択中のくじ種キー
let PROFILE = null;       // マイAIプロフィール

/* ---------------- 🤖 自動収集（結果・ニュース・怪我情報） ----------------
 * localStorage キー: toto_auto_v1。30分以上経過で起動時に裏側取得。
 */
const AUTO_KEY = "toto_auto_v1";
const AUTO_INTERVAL = 30 * 60 * 1000;
const RESULT_LIST_URL = "https://store.toto-dream.com/dcs/subos/screen/pi04/spin011/PGSPIN01101InitLotResultLsttoto.form";
const RESULT_DETAIL_URL = "https://store.toto-dream.com/dcs/subos/screen/pi04/spin011/PGSPIN01101LnkHoldCntLotResultLsttoto.form";
const INFO_URL = "https://store.toto-dream.com/dcs/subos/screen/pi01/spin000/PGSPIN00001DisptotoLotInfo.form";
const NEWS_URLS = [
  "https://www.jleague.jp/news/",
  "https://www.toto-dream.com/toto/",
];
const AUTO_RE = /第\s*(\d+)\s*回/;
const HOLD_RE = /holdCntId=(\d+)/;
const SCORE_RE = /(\d+)\s*-\s*(\d+)/;

let AUTO = { enabled: true, lastRun: 0, status: "未取得", results: {}, news: [],
             official: null, newer: false };
try {
  const raw = localStorage.getItem(AUTO_KEY);
  if (raw) AUTO = { ...AUTO, ...JSON.parse(raw) };
} catch (e) { /* 初期値のまま */ }
function saveAuto() { localStorage.setItem(AUTO_KEY, JSON.stringify(AUTO)); }

function _doc(html) { return new DOMParser().parseFromString(html, "text/html"); }
function _text(el) { return (el.textContent || "").replace(/\s+/g, " ").trim(); }

function listCompletedRounds(html) {
  const doc = _doc(html), out = [];
  doc.querySelectorAll("a[href]").forEach((a) => {
    const m = HOLD_RE.exec(a.getAttribute("href") || "");
    if (!m) return;
    const label = _text(a);
    const rm = AUTO_RE.exec(label);
    out.push({ label, round_no: rm ? `第${rm[1]}回` : label, hold_cnt_id: m[1],
               url: `${RESULT_DETAIL_URL}?holdCntId=${m[1]}` });
  });
  return out;
}

function parseResultHtml(html) {
  const doc = _doc(html);
  const rm = AUTO_RE.exec(_text(doc.body));
  const round_no = rm ? `第${rm[1]}回` : "";
  const matches = [];
  doc.querySelectorAll("table").forEach((table) => {
    const head = _text(table);
    if (!head.includes("試合結果") || !head.includes("くじ結果")) return;
    table.querySelectorAll("tr").forEach((tr) => {
      const cells = [...tr.querySelectorAll("td,th")].map(_text);
      if (cells.length < 7 || !/^\d+$/.test(cells[2])) return;
      const sm = SCORE_RE.exec(cells[4]);
      matches.push({ match_no: parseInt(cells[2], 10), date: cells[0], stadium: cells[1],
        home: cells[3], away: cells[5], score: cells[4],
        home_goals: sm ? +sm[1] : null, away_goals: sm ? +sm[2] : null,
        result: ["1", "0", "2"].includes(cells[6]) ? cells[6] : "" });
    });
  });
  return { round_no, matches };
}

async function fetchNewsItems() {
  const items = [];
  for (const url of NEWS_URLS) {
    try {
      const html = await fetchExternal(url);
      const doc = _doc(html);
      doc.querySelectorAll("a[href]").forEach((a) => {
        const t = _text(a);
        if (t.length < 8 || t.length > 90) return;
        if (!/怪我|負傷|出場停止|監督|戦術|ニュース|J1|J2|試合|移籍|契約/.test(t)) return;
        const href = a.getAttribute("href") || "";
        if (!href || href.startsWith("#") || href.startsWith("javascript")) return;
        const abs = href.startsWith("http") ? href : new URL(href, url).href;
        if (!items.some((i) => i.title === t)) items.push({ title: t, url: abs });
      });
      if (items.length >= 8) break;
    } catch (e) { /* 個別失敗は無視 */ }
  }
  return items.slice(0, 8);
}

/* 公式「totoくじ情報」ページを取得し、最新の開催回番号・開催日を検出する。
 * 公開データ（data.json）より新しい回が出ていれば AUTO.newer=true にして通知する。 */
async function checkOfficialRound() {
  try {
    const html = await fetchExternal(INFO_URL);
    const doc = _doc(html);
    const text = _text(doc.body);
    const rounds = [...text.matchAll(/第\s*(\d+)\s*回/g)].map((m) => +m[1]);
    if (!rounds.length) return;
    const dm = /(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日/.exec(text);
    const held = dm ? `${dm[1]}-${String(+dm[2]).padStart(2, "0")}-${String(+dm[3]).padStart(2, "0")}` : "";
    const latest = Math.max(...rounds);
    AUTO.official = { round_no: `第${latest}回`, held_date: held, checked_at: Date.now() };
    const cur = parseInt((AUTO_RE.exec(DATA ? DATA.round_no : "") || [])[1] || "0", 10);
    AUTO.newer = latest > cur;
    saveAuto();
  } catch (e) { /* 公式ページ取得失敗は表示を変えず無視 */ }
}

async function autoCollect() {
  AUTO.status = "取得中…"; updateAutobar();
  checkOfficialRound();
  try {
    const listHtml = await fetchExternal(RESULT_LIST_URL);
    const rounds = listCompletedRounds(listHtml);
    if (!rounds.length) throw new Error("結果一覧を解析できませんでした");
    const target = rounds[0];
    const detailHtml = await fetchExternal(target.url);
    const parsed = parseResultHtml(detailHtml);
    const filled = parsed.matches.filter((m) => m.result);
    if (!filled.length) throw new Error("試合結果を解析できませんでした");
    const rkey = parsed.round_no || target.round_no;
    AUTO.results[rkey] = {};
    filled.forEach((m) => {
      AUTO.results[rkey][m.match_no] = { result: m.result, home: m.home, away: m.away };
    });
    AUTO.news = await fetchNewsItems();
    AUTO.lastRun = Date.now();
    AUTO.status = `取得完了：${rkey} ${filled.length}試合`;
  } catch (e) {
    AUTO.lastRun = Date.now();
    AUTO.status = `失敗：${e.message}`;
  }
  saveAuto(); updateAutobar();
  if (DATA) render();  // 結果反映のため再描画
}

function _roundNum(key) {
  const m = AUTO_RE.exec(String(key || ""));
  return m ? `第${m[1]}回` : "";
}
function _normTeam(s) {
  return String(s || "").replace(/[Ｆｆ][ＣＣ]|FC/gi, "").replace(/[\s・\-ー]/g, "");
}
function _teamMatch(a, b) {
  const na = _normTeam(a), nb = _normTeam(b);
  return na && nb && (na.includes(nb) || nb.includes(na));
}
/* 自動取得結果＋手動入力を data.json の結果へマージして返す */
function resolveResults(rkey, rows) {
  const out = {};
  for (const gm of Object.values(DATA.games || {})) {
    if ((gm.round_key || DATA.round_no) === rkey && gm.results) {
      for (const [k, v] of Object.entries(gm.results)) out[k] = v;
    }
  }
  const rnum = _roundNum(rkey);
  const auto = rnum && AUTO.results[rnum];
  if (auto) {
    (rows || []).forEach((m) => {
      const hit = auto[m.match_no];
      // チーム名が一致する場合のみ採用（別カード誤爆防止）
      if (hit && _teamMatch(hit.home, m.home) && _teamMatch(hit.away, m.away)) {
        out[m.match_no] = hit.result;
      }
    });
  }
  const manual = (PROFILE.manual_results || {})[rkey] || {};
  for (const [k, v] of Object.entries(manual)) if (v) out[k] = v;
  return out;
}

function updateAutobar() {
  const el = $("#autobar");
  if (!el) return;
  const last = AUTO.lastRun
    ? new Date(AUTO.lastRun).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })
    : "—";
  const newerHtml = AUTO.newer && AUTO.official
    ? `<div class="warn" style="flex-basis:100%;font-size:.8rem">
         🔔 公式サイトに <b>${esc(AUTO.official.round_no)}</b>${AUTO.official.held_date ? `（${esc(AUTO.official.held_date)}）` : ""} の情報があります。
         現在の公開データは <b>${esc(DATA ? DATA.round_no : "")}</b> です。
         Mac側で「最新情報を取得」→「固定URLへ最新予想を反映・公開」を実行すると自動で切り替わります。</div>`
    : "";
  el.innerHTML =
    `<button class="btn sub" id="auto-toggle" style="padding:6px 12px;font-size:.78rem">
       🤖 自動収集：${AUTO.enabled ? "ON" : "OFF"}</button>
     <span class="st">${esc(AUTO.status)}／最終更新：${last}</span>
     <button class="btn sub" id="auto-now" style="padding:6px 12px;font-size:.78rem">🔄 今すぐ収集</button>` +
    (AUTO.news && AUTO.news.length
      ? `<details><summary>📰 自動収集ニュース・怪我情報（${AUTO.news.length}件）</summary>` +
        AUTO.news.map((n) => `<p style="margin:4px 0;font-size:.78rem"><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a></p>`).join("") +
        `</details>` : "") + newerHtml;
  $("#auto-toggle").onclick = () => { AUTO.enabled = !AUTO.enabled; saveAuto(); updateAutobar(); };
  $("#auto-now").onclick = () => autoCollect();
}

/* ---------------- マイAIプロフィール（localStorage） ---------------- */
function defaultProfile() {
  return { enabled: true, draw_bias: 0, team_adj: {}, conditions: [],
           notes: [], entries: [], stakes: {}, favorites: [], manual_results: {} };
}
function loadProfile() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    const d = raw ? JSON.parse(raw) : {};
    const p = defaultProfile();
    for (const k of Object.keys(p)) if (d[k] !== undefined) p[k] = d[k];
    return p;
  } catch (e) { return defaultProfile(); }
}
function saveProfile() {
  localStorage.setItem(LS_KEY, JSON.stringify(PROFILE));
  toast("この端末に保存しました");
}
PROFILE = loadProfile();

/* ---------------- マイAI補正の適用（Python版と同一ロジック） ---------------- */
function applyCorrections(rows) {
  if (!PROFILE.enabled) return rows;
  const bias = Number(PROFILE.draw_bias || 0);
  return rows.map((a) => {
    const h = PROFILE.team_adj[a.home] || {};
    const w = PROFILE.team_adj[a.away] || {};
    const d0 = bias + ((h.draw || 0) + (w.draw || 0)) / 2;
    const d1 = (h.win || 0) + (w.lose || 0);
    const d2 = (w.win || 0) + (h.lose || 0);
    const raw = { "1": a.p1 + d1, "0": a.p0 + d0, "2": a.p2 + d2 };
    let vals = OUTCOMES.map((o) => Math.max(0.5, raw[o]));
    const sum = vals.reduce((x, y) => x + y, 0);
    vals = vals.map((v) => Math.round(v / sum * 1000) / 10);
    const diff = Math.round((100 - vals.reduce((x, y) => x + y, 0)) * 10) / 10;
    const top = vals.indexOf(Math.max(...vals));
    vals[top] = Math.round((vals[top] + diff) * 10) / 10;
    const p = { "1": vals[0], "0": vals[1], "2": vals[2] };
    const votes = { "1": a.vote_1 || 0, "0": a.vote_0 || 0, "2": a.vote_2 || 0 };
    const b = { ...a, p1: p["1"], p0: p["0"], p2: p["2"], myai: true };
    b.predicted = OUTCOMES.reduce((m, o) => (p[o] > p[m] ? o : m), "1");
    b.gaps = {};
    for (const o of OUTCOMES) b.gaps[o] = Math.round((p[o] - votes[o]) * 10) / 10;
    return b;
  });
}

/* ---------------- 推しクラブへの冷静アドバイス ---------------- */
function favoriteAdvice(a) {
  const favs = PROFILE.favorites || [];
  const out = [];
  for (const [team, isHome] of [[a.home, true], [a.away, false]]) {
    if (!favs.includes(team)) continue;
    const pWin = isHome ? a.p1 : a.p2;
    const vWin = isHome ? (a.vote_1 || 0) : (a.vote_2 || 0);
    const venue = isHome ? "ホーム" : "アウェイ";
    let txt;
    if (pWin >= vWin + 10) txt = `【${team}】AI勝率${pWin}%は投票率${vWin}%を上回り、客観的には${venue}有利と評価。ただし応援目線を排して判断を。`;
    else if (pWin <= vWin - 10) txt = `【${team}】世間の評価(${vWin}%)よりAI評価(${pWin}%)が低め。波乱・不安材料を確認して。`;
    else txt = `【${team}】AI評価${pWin}%と投票率${vWin}%は拮抗。五分の予想。`;
    if ((a.p0 || 0) >= 28) txt += ` 引き分け確率${a.p0}%と高め→油断注意。`;
    if (!isHome) txt += "（アウェイ戦）";
    out.push(txt);
  }
  return out;
}

/* ---------------- 買い目最適化（optimizer.py の貪欲法をJS移植） ---------------- */
function utility(a, o, mode) {
  const p = { "1": a.p1, "0": a.p0, "2": a.p2 }[o] / 100;
  if (mode === "upset") {
    const v = { "1": a.vote_1 || 0, "0": a.vote_0 || 0, "2": a.vote_2 || 0 }[o] / 100;
    return p + 0.5 * Math.max(0, p - v);
  }
  return p;
}
function optimizeGreedy(rows, budget, mode) {
  const maxLines = Math.max(1, Math.floor(budget / TICKET_UNIT));
  const counts = {}; rows.forEach((a) => (counts[a.match_no] = 1));
  let lines = 1;
  const coverage = (a, k) => {
    const scored = [...OUTCOMES].sort((x, y) => utility(a, y, mode) - utility(a, x, mode)).slice(0, k);
    return scored.reduce((s, o) => s + { "1": a.p1, "0": a.p0, "2": a.p2 }[o], 0) / 100;
  };
  for (;;) {
    let best = null;
    for (const a of rows) {
      const no = a.match_no, k = counts[no];
      if (k >= 3) continue;
      const newLines = Math.floor(lines / k) * (k + 1);
      if (newLines > maxLines) continue;
      const gain = coverage(a, k + 1) - coverage(a, k);
      const eff = gain / (newLines - lines);
      if (!best || eff > best.eff) best = { eff, no, k, newLines };
    }
    if (!best) break;
    counts[best.no] = best.k + 1;
    lines = best.newLines;
  }
  const marks = rows.map((a) => {
    const no = a.match_no, k = counts[no];
    const scored = [...OUTCOMES].sort((x, y) => utility(a, y, mode) - utility(a, x, mode)).slice(0, k);
    const cov = scored.reduce((s, o) => s + { "1": a.p1, "0": a.p0, "2": a.p2 }[o], 0) / 100;
    return { match_no: no, home: a.home, away: a.away,
             outcomes: [...scored].sort(), type: { 1: "シングル", 2: "ダブル", 3: "トリプル" }[k], _cov: cov };
  });
  const total = marks.reduce((s, m) => s * m.outcomes.length, 1);
  const coverageAll = marks.reduce((s, m) => s * m._cov, 1);
  marks.forEach((m) => delete m._cov);
  return { marks, lines: total, cost: total * TICKET_UNIT, coverage: Math.round(coverageAll * 10000) / 100 };
}

/* ---------------- マークシートHTML生成 ---------------- */
function markSheetHTML(marks, roundLabel) {
  const cell = (o, picked) => picked.includes(o)
    ? `<td class="cell"><span class="mark m${o}">${o}</span></td>`
    : `<td class="cell"><span class="empty">・</span></td>`;
  const rows = marks.map((m) =>
    `<tr><td class="ts-no">${m.match_no}</td>` +
    `<td class="ts-match"><b>${esc(m.home)}</b> vs ${esc(m.away)}<br>` +
    `<span class="type-tag">${m.type} ${m.outcomes.map((o) => OUTCOME_LABEL[o]).join("・")}</span></td>` +
    OUTCOMES.map((o) => cell(o, m.outcomes)).join("") + "</tr>").join("");
  return `<div class="card msheet"><h3>${esc(roundLabel)} 購入用マークシート</h3>
    <table><tr><th></th><th>対戦カード</th><th>1</th><th>0</th><th>2</th></tr>${rows}</table>
    <p class="muted">凡例：<span class="mark m1">1</span>シングル <span class="mark m0">0</span>ダブル <span class="mark m2">2</span>トリプル</p></div>`;
}
function goalSheetHTML(marks, roundLabel) {
  const rows = marks.map((m) =>
    `<tr><td class="ts-no">${m.match_no}</td>` +
    `<td class="ts-match"><b>${esc(m.home)}</b> vs ${esc(m.away)}<br>` +
    `<span class="type-tag">確信度 ${m.confidence}%</span></td>` +
    `<td>${GOAL_LABEL[m.home_pick]}</td><td>${GOAL_LABEL[m.away_pick]}</td></tr>`).join("");
  return `<div class="card msheet"><h3>${esc(roundLabel)} 得点予想マークシート</h3>
    <table><tr><th></th><th>対戦カード</th><th>ホーム得点</th><th>アウェイ得点</th></tr>${rows}</table></div>`;
}

/* ---------------- 共通UI部品 ---------------- */
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2200);
}
function probCell(p, top) {
  return `<td>${p}%<div class="bar"><i style="width:${p}%"></i></div></td>`;
}
function card(html) { return `<div class="card">${html}</div>`; }

/* ---------------- くじ種切替 ---------------- */
function renderTabs() {
  const nav = $("#gametabs");
  nav.innerHTML = "";
  for (const [key, g] of Object.entries(DATA.games)) {
    const b = document.createElement("button");
    b.textContent = g.label || key;
    b.className = key === CUR_GAME ? "on" : "";
    b.onclick = () => { CUR_GAME = key; location.hash = "g=" + encodeURIComponent(key); render(); };
    nav.appendChild(b);
  }
}

/* ---------------- 勝敗予想ビュー（toto / mini toto） ---------------- */
function renderWinDraw(g) {
  const rows = applyCorrections(g.rows || []);
  const custom = PROFILE.enabled && rows.some((r) => r.myai);
  let html = "";
  if (custom) html += `<div class="card" style="border-color:#2f6fed">🧠 <b>マイAI補正ON</b>：あなたのチーム別評価・引き分け補正をベースAI勝率に上乗せしています（この端末のみ）。</div>`;

  // 予想テーブル
  html += card(`<h3 style="margin-top:0">🎯 勝敗予想</h3><div style="overflow-x:auto"><table>
    <tr><th>試合</th><th class="l">対戦カード</th><th>1 ホーム勝ち</th><th>0 引き分け</th><th>2 アウェイ勝ち</th><th>AI本命</th><th>接戦度</th><th>引分危険度</th><th>波乱</th></tr>` +
    rows.map((a) => `<tr>
      <td>${a.match_no}</td>
      <td class="l">${esc(a.home)} vs ${esc(a.away)}<br><span class="muted">${esc(a.date)} ${esc(a.time)}</span></td>
      ${probCell(a.p1)}${probCell(a.p0)}${probCell(a.p2)}
      <td><b>${OUTCOME_LABEL[a.predicted]}</b></td>
      <td>${(() => { const s = [a.p1, a.p0, a.p2].sort((x, y) => y - x); return `${Math.round((s[0] - s[1]) * 10) / 10}pt`; })()}</td>
      <td>${a.draw_risk ?? "-"}</td>
      <td>${a.upset ? `<span class="warn">⚡波乱狙い</span>` : ""}</td></tr>`).join("") +
    `</table></div>`);

  // 推しクラブへの冷静アドバイス
  const advices = rows.flatMap((a) => favoriteAdvice(a));
  if (advices.length) {
    html += card(`<h3 style="margin-top:0">⭐ 推しクラブへの冷静アドバイス</h3>` +
      advices.map((t) => `<p style="margin:6px 0">💡 ${esc(t)}</p>`).join("") + "</div>");
  }

  // ギャップ分析
  const maxP = Math.max(...rows.map((a) => Math.max(a.p1, a.p0, a.p2, a.vote_1 || 0, a.vote_0 || 0, a.vote_2 || 0)), 1);
  html += card(`<h3 style="margin-top:0">📈 AI本命勝率 vs 世間投票率（推定）</h3>` +
    rows.map((a) => {
      const ai = { "1": a.p1, "0": a.p0, "2": a.p2 }[a.predicted];
      const vt = { "1": a.vote_1 || 0, "0": a.vote_0 || 0, "2": a.vote_2 || 0 }[a.predicted];
      return `<div class="row" style="margin:4px 0">
        <span class="muted" style="width:110px">${a.match_no}. ${esc(a.home).slice(0, 8)}</span>
        <div class="bar" style="flex:1"><i style="width:${ai / maxP * 100}%"></i></div>
        <span style="width:42px;text-align:right">${ai}%</span>
        <div class="bar g" style="flex:1"><i style="width:${vt / maxP * 100}%"></i></div>
        <span style="width:42px;text-align:right">${vt}%</span></div>`;
    }).join("") + `<p class="muted">青=AI本命勝率／グレー=世間投票率（推定）</p>`);

  // 波乱・引分アラート
  const alerts = rows.filter((a) => a.upset || (a.draw_risk || 0) >= 70);
  if (alerts.length) {
    html += card(`<h3 style="margin-top:0">⚡ 波乱・引分アラート</h3>` +
      alerts.map((a) => `<p>⚡ <b>第${a.match_no}試合 ${esc(a.home)} vs ${esc(a.away)}</b><br>
        <span class="muted">${esc(a.upset_note || "")} 引分危険度 ${a.draw_risk ?? "-"}</span></p>`).join("") + "</div>");
  }

  // 買い目最適化
  html += `<div class="card"><h3 style="margin-top:0">💴 買い目自動提案</h3>
    <div class="row">
      <label class="muted">予算(円)<br><input type="number" id="myai_budget" value="1000" step="100" min="100" style="width:110px"></label>
      <label class="muted">狙い方<br><select id="myai_mode" style="width:150px">
        <option value="steady">本命堅実</option><option value="upset">波乱狙い</option></select></label>
      <button class="btn" id="btn-opt">🎯 買い目を最適化</button>
    </div><div id="plan-out"></div></div>`;

  // マイAI育成タブ内容（win_drawのみ）
  html += `<div class="card"><h3 style="margin-top:0">🧠 マイAI育成・カスタム学習（この端末専用）</h3>
    <div id="myai-panel"></div></div>`;

  // 情報元
  const okSources = (DATA.sources || []).filter((s) => s.ok !== false);
  if (okSources.length) {
    html += card(`<h3 style="margin-top:0">🔗 情報元</h3>` +
      okSources.map((s) => `<p style="margin:4px 0"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a></p>`).join("") +
      `<p style="margin-top:8px"><button class="btn sub" id="btn-extcheck">📡 外部情報をプロキシ経由で確認</button>
       <span id="extcheck-out" class="muted" style="margin-left:8px"></span></p>`);
  }
  $("#app").innerHTML = html;

  const extBtn = $("#btn-extcheck");
  if (extBtn) extBtn.onclick = async () => {
    const out = $("#extcheck-out");
    out.textContent = "取得中…（専用プロキシ優先）";
    try {
      const target = okSources[0].url;
      const text = await fetchExternal(target);
      const title = (text.match(/<title[^>]*>([^<]+)<\/title>/i) || [])[1] || "";
      out.textContent = `✅ 取得成功（${text.length.toLocaleString()} bytes）${title ? "／" + title.trim().slice(0, 40) : ""}`;
    } catch (e) {
      out.textContent = `❌ 取得失敗: ${e.message}`;
    }
  };

  // 買い目最適化の配線
  $("#btn-opt").onclick = () => {
    const budget = parseInt($("#myai_budget").value || "1000", 10);
    const mode = $("#myai_mode").value;
    const plan = optimizeGreedy(rows, budget, mode);
    const marks = plan.marks.map((m) =>
      `<tr><td>${m.match_no}</td><td class="l">${esc(m.home)} vs ${esc(m.away)}</td>
       <td>${m.outcomes.map((o) => `<span class="mark m${o}">${o}</span>`).join(" ")}</td><td>${m.type}</td></tr>`).join("");
    $("#plan-out").innerHTML =
      `<p><b>${plan.lines}口・${plan.cost.toLocaleString()}円・全的中期待度 ${plan.coverage}%</b></p>
       <div style="overflow-x:auto"><table><tr><th>試合</th><th class="l">対戦カード</th><th>買い目</th><th>種類</th></tr>${marks}</table></div>
       <div id="msheet"></div>
       <details style="margin-top:10px"><summary class="btn sub" style="display:inline-block;padding:8px 14px;border-radius:10px">📱 1試合ずつマーク確認モード</summary>
         <div id="markcheck" style="margin-top:10px"></div></details>
       <p><button class="btn sub" id="btn-dl">⬇ マークシートを保存</button></p>`;
    $("#msheet").innerHTML = markSheetHTML(plan.marks, g.round_key || DATA.round_no);
    $("#btn-dl").onclick = () => {
      const blob = new Blob([markSheetHTML(plan.marks, g.round_key || DATA.round_no)], { type: "text/html" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "toto_mark_sheet.html"; a.click();
      URL.revokeObjectURL(a.href);
    };
    renderMarkCheck(plan.marks, g.round_key || DATA.round_no);
  };

  renderMyAiPanel(g);
}

/* ---------------- 得点予想ビュー（totoGOAL3 / totoGOAL2） ---------------- */
function renderGoal(g) {
  const rows = g.rows || [];
  let html = card(`<h3 style="margin-top:0">🎯 得点予想</h3><div style="overflow-x:auto"><table>
    <tr><th>試合</th><th class="l">対戦カード</th><th>日時</th><th>ホーム予想</th><th>アウェイ予想</th><th>確信度</th></tr>` +
    rows.map((a) => `<tr><td>${a.match_no}</td>
      <td class="l">${esc(a.home)} vs ${esc(a.away)}</td>
      <td>${esc(a.date)} ${esc(a.time)}</td>
      <td><b>${GOAL_LABEL[a.home_pick]}</b> <span class="muted">(${a.home_dist[a.home_pick]}%)</span></td>
      <td><b>${GOAL_LABEL[a.away_pick]}</b> <span class="muted">(${a.away_dist[a.away_pick]}%)</span></td>
      <td>${a.confidence}%</td></tr>`).join("") + `</table></div>`);

  html += card(`<h3 style="margin-top:0">📊 得点カテゴリ別確率</h3>` +
    rows.map((a) => `<p class="muted" style="margin:8px 0 2px"><b>${a.match_no}. ${esc(a.home)} vs ${esc(a.away)}</b></p>
      <div class="row">
        <span class="muted" style="width:36px">H</span>
        ${GOAL_CATS.map((c) => `<div class="bar" style="flex:1" title="${esc(a.home)} ${GOAL_LABEL[c]} ${a.home_dist[c]}%"><i style="width:${a.home_dist[c]}%"></i></div>`).join("")}
      </div>
      <div class="row">
        <span class="muted" style="width:36px">A</span>
        ${GOAL_CATS.map((c) => `<div class="bar" style="flex:1" title="${esc(a.away)} ${GOAL_LABEL[c]} ${a.away_dist[c]}%"><i style="width:${a.away_dist[c]}%"></i></div>`).join("")}
      </div>`).join("") +
    `<p class="muted">${GOAL_CATS.map((c) => GOAL_LABEL[c]).join(" / ")}</p>`);

  const t = g.ticket;
  if (t && t.marks) {
    html += card(`<h3 style="margin-top:0">📝 得点買い目＆マークシート</h3>
      <p>${t.teams}チーム対象・平均確信度 ${t.confidence}%</p>
      <div id="goal-msheet">${goalSheetHTML(t.marks, DATA.round_no)}</div></div>`);
  }
  $("#app").innerHTML = html;
}

/* ---------------- 1試合ずつマーク確認モード ---------------- */
function renderMarkCheck(marks, roundLabel) {
  const el = $("#markcheck");
  if (!el) return;
  let idx = 0;
  const picks = marks.map((m) => new Set(m.outcomes));
  const draw = () => {
    const m = marks[idx];
    el.innerHTML = `
      <div class="row" style="justify-content:space-between;margin-bottom:8px">
        <button class="btn sub" id="mc-prev" ${idx === 0 ? "disabled" : ""}>◀ 前へ</button>
        <b>${idx + 1} / ${marks.length} 試合目</b>
        <button class="btn sub" id="mc-next" ${idx === marks.length - 1 ? "disabled" : ""}>次へ ▶</button>
      </div>
      <div class="card" style="text-align:center">
        <div class="muted">第${m.match_no}試合</div>
        <div style="font-size:1.3rem;font-weight:800;margin:6px 0">${esc(m.home)}<br>vs ${esc(m.away)}</div>
        <div class="muted">AI推奨：${m.outcomes.map((o) => `${o} ${OUTCOME_LABEL[o]}`).join("・")}（${m.type}）</div>
        <div class="row" style="justify-content:center;margin-top:12px">
          ${OUTCOMES.map((o) => `<button class="btn ${picks[idx].has(o) ? "" : "sub"}" data-o="${o}" style="font-size:1.1rem;padding:14px 10px;min-width:96px">${o}<br><small>${OUTCOME_LABEL[o]}</small></button>`).join("")}
        </div>
        <p class="muted">タップでON/OFF（複数選択可・最低1つは残ります）</p>
      </div>
      <div id="mc-done"></div>`;
    $("#mc-prev").onclick = () => { if (idx > 0) { idx--; draw(); } };
    $("#mc-next").onclick = () => { if (idx < marks.length - 1) { idx++; draw(); } };
    el.querySelectorAll("[data-o]").forEach((b) => b.onclick = () => {
      const o = b.dataset.o;
      if (picks[idx].has(o)) { if (picks[idx].size > 1) picks[idx].delete(o); }
      else picks[idx].add(o);
      draw();
    });
    if (idx === marks.length - 1) {
      $("#mc-done").innerHTML = `<p style="text-align:center"><button class="btn" id="mc-apply">✅ 確認完了・この内容でマークシートを再生成</button></p>`;
      $("#mc-apply").onclick = () => {
        const custom = marks.map((mm, i) => {
          const sel = [...picks[i]].sort((a, b) => OUTCOMES.indexOf(a) - OUTCOMES.indexOf(b));
          return { match_no: mm.match_no, home: mm.home, away: mm.away, outcomes: sel,
                   type: { 1: "シングル", 2: "ダブル", 3: "トリプル" }[sel.length] || "シングル" };
        });
        $("#msheet").innerHTML = markSheetHTML(custom, roundLabel + "（マイマーク確認版）");
        toast("確認済みマークシートを再生成しました");
      };
    }
  };
  draw();
}

/* ---------------- マイAI育成パネル ---------------- */
function renderMyAiPanel(g) {
  const el = $("#myai-panel");
  if (!el) return;
  const teams = [...new Set((g.rows || []).flatMap((r) => [r.home, r.away]))].sort();
  const p = PROFILE;

  el.innerHTML = `
    <div class="row" style="margin-bottom:8px">
      <label class="muted"><input type="checkbox" id="myai-en" ${p.enabled ? "checked" : ""}> マイAI補正を有効化</label>
      <label class="muted">引き分け全体補正(pt)
        <input type="number" id="myai-bias" value="${p.draw_bias}" min="-30" max="30" step="5" style="width:70px"></label>
      <button class="btn sub" id="myai-save">💾 保存</button>
    </div>
    <p class="muted">チーム別補正（-50〜+50pt）：「勝ち」=そのチームが勝つ方向、「負け」=相手が勝つ方向</p>
    <div id="team-adj"></div>
    <h4>⭐ 推しクラブ登録（冷静アドバイス用）</h4>
    <details><summary class="muted">推しクラブを選択（${(p.favorites || []).length}件選択中）</summary>
      <div class="in" id="fav-list" style="max-height:220px;overflow-y:auto"></div></details>
    <h4>📝 戦術メモ・ケガ人・監督交代メモ</h4>
    <textarea id="myai-note" placeholder="例：川崎は主力DF出場停止の噂"></textarea>
    <p><button class="btn sub" id="myai-addnote">➕ メモ追加</button></p>
    <div id="myai-notes"></div>
    <h4>🎯 この開催回の自分の予想記録</h4>
    <div id="myai-record"></div>
    <h4>📈 過去の答え合わせ・育成レベル</h4>
    <div id="myai-history"></div>`;

  // チーム別補正スライダー
  const tadj = $("#team-adj");
  for (const t of teams) {
    const cur = p.team_adj[t] || {};
    const det = document.createElement("details");
    det.innerHTML = `<summary>⚽ ${esc(t)}（勝${cur.win || 0} / 分${cur.draw || 0} / 負${cur.lose || 0}）</summary>
      <div class="in grid2">
        ${[["win", "勝ち補正"], ["draw", "引き分け補正"], ["lose", "負け補正"]].map(([k, label]) =>
          `<label class="muted">${label}<br><input type="range" min="-50" max="50" step="5"
             data-team="${esc(t)}" data-kind="${k}" value="${cur[k] || 0}"></label>`).join("")}
      </div>`;
    tadj.appendChild(det);
  }
  tadj.oninput = (e) => {
    const t = e.target.dataset.team, k = e.target.dataset.kind;
    if (!t || !k) return;
    p.team_adj[t] = p.team_adj[t] || {};
    p.team_adj[t][k] = parseInt(e.target.value, 10);
  };
  $("#myai-en").onchange = (e) => { p.enabled = e.target.checked; saveProfile(); render(); };
  $("#myai-bias").onchange = (e) => { p.draw_bias = parseInt(e.target.value || "0", 10); saveProfile(); render(); };
  $("#myai-save").onclick = saveProfile;

  // 推しクラブ（即時保存）
  p.favorites = p.favorites || [];
  const favList = $("#fav-list");
  favList.innerHTML = teams.map((t) =>
    `<label class="muted" style="display:block;padding:3px 0">
      <input type="checkbox" data-fav="${esc(t)}" ${p.favorites.includes(t) ? "checked" : ""}> ${esc(t)}</label>`).join("");
  favList.onchange = (e) => {
    if (!e.target.dataset.fav) return;
    const t = e.target.dataset.fav;
    p.favorites = e.target.checked ? [...new Set([...p.favorites, t])] : p.favorites.filter((x) => x !== t);
    saveProfile();
    // アドバイスを即時反映するため再描画（スクロール位置は維持されないが許容）
    render();
  };

  // メモ
  const drawNotes = () => {
    $("#myai-notes").innerHTML = (p.notes || []).slice().reverse().map((n) =>
      `<p class="muted">📝 ${esc(n.created)} ${esc(n.text)}
       <button class="btn sub" style="padding:2px 8px;font-size:.75rem" data-del="${n.id}">削除</button></p>`).join("") ||
      `<p class="muted">メモはまだありません。</p>`;
    document.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => {
      p.notes = p.notes.filter((n) => n.id !== b.dataset.del); saveProfile(); render();
    });
  };
  $("#myai-addnote").onclick = () => {
    const v = $("#myai-note").value.trim();
    if (!v) return;
    p.notes.push({ id: Date.now().toString(36), text: v,
                   created: new Date().toISOString().slice(0, 16).replace("T", " ") });
    saveProfile(); render();
  };
  drawNotes();

  // 予想記録
  const rkey = g.round_key || DATA.round_no;
  const existing = {};
  (p.entries || []).filter((e) => e.round_key === rkey).forEach((e) => (existing[e.match_no] = e.pick));
  $("#myai-record").innerHTML = (g.rows || []).map((m) =>
    `<div class="row" style="margin:4px 0">
      <span class="muted" style="width:120px">第${m.match_no}試合</span>
      <span style="flex:1">${esc(m.home)} vs ${esc(m.away)}</span>
      <select data-rec="${m.match_no}" style="width:130px">
        <option value="">未記入</option>
        ${OUTCOMES.map((o) => `<option value="${o}" ${existing[m.match_no] === o ? "selected" : ""}>${o}：${OUTCOME_LABEL[o]}</option>`).join("")}
      </select></div>`).join("");
  const saveBtn = document.createElement("button");
  saveBtn.className = "btn sub"; saveBtn.textContent = "📝 予想をこの端末に記録";
  saveBtn.style.marginTop = "8px";
  saveBtn.onclick = () => {
    const today = new Date().toISOString().slice(0, 10);
    p.entries = (p.entries || []).filter((e) => e.round_key !== rkey);
    document.querySelectorAll("[data-rec]").forEach((s) => {
      if (!s.value) return;
      const m = g.rows.find((r) => r.match_no === parseInt(s.dataset.rec, 10));
      if (m) p.entries.push({ round_key: rkey, round_label: rkey, match_no: m.match_no,
        home: m.home, away: m.away, pick: s.value, date: today });
    });
    saveProfile(); toast("予想を記録しました");
  };
  $("#myai-record").appendChild(saveBtn);

  drawHistory(g);
}

/* ---------------- 答え合わせ・育成レベル ---------------- */
function drawHistory(g) {
  const el = $("#myai-history");
  const entries = PROFILE.entries || [];
  if (!entries.length) { el.innerHTML = `<p class="muted">まだ予想記録がありません。</p>`; return; }

  // 結果マップ：data.json + 自動取得 + 手動入力をマージ（round_key -> {match_no: actual}）
  const byRound = {};
  entries.forEach((e) => (byRound[e.round_key] = byRound[e.round_key] || []).push(e));

  let totalHits = 0, totalJudged = 0, judgedRounds = 0;
  const catStats = { "1": [0, 0], "0": [0, 0], "2": [0, 0] };
  let html = "";
  for (const rkey of Object.keys(byRound).sort()) {
    const es = byRound[rkey];
    const results = resolveResults(rkey, es);
    const judged = es.filter((e) => results[e.match_no]);
    const hits = judged.filter((e) => e.pick === results[e.match_no]).length;
    judged.forEach((e) => { catStats[e.pick][1]++; catStats[e.pick][0] += e.pick === results[e.match_no] ? 1 : 0; });
    totalHits += hits; totalJudged += judged.length;
    const acc = judged.length ? Math.round(hits / judged.length * 1000) / 10 : null;
    if (acc !== null) judgedRounds++;
    const stake = (PROFILE.stakes || {})[rkey] || {};
    const recovery = stake.investment && stake.payout
      ? Math.round(stake.payout / stake.investment * 1000) / 10 : null;
    html += `<details><summary>${esc(es[0].round_label || rkey)}：記録${es.length}件・的中${hits}件・的中率 ${acc === null ? "未確定" : acc + "%"}・回収率 ${recovery === null ? "—" : recovery + "%"}</summary>
      <div class="in">
        <div class="row">
          <label class="muted">投資額<input type="number" class="stake-inv" data-rk="${esc(rkey)}" value="${stake.investment || ""}" step="100" style="width:90px"></label>
          <label class="muted">払戻額<input type="number" class="stake-pay" data-rk="${esc(rkey)}" value="${stake.payout || ""}" step="100" style="width:90px"></label>
          <button class="btn sub stake-save" data-rk="${esc(rkey)}">収支保存</button>
        </div>
        <table><tr><th>試合</th><th>対戦</th><th>自分の予想</th><th>実際の結果</th><th>判定</th></tr>` +
      es.map((e) => {
        const a = results[e.match_no];
        const man = ((PROFILE.manual_results || {})[rkey] || {})[e.match_no];
        const resCell = a
          ? OUTCOME_LABEL[a] || a
          : `<select class="res-man" data-rk="${esc(rkey)}" data-no="${e.match_no}" style="width:110px">
               <option value="">未確定（手動入力）</option>
               ${OUTCOMES.map((o) => `<option value="${o}" ${man === o ? "selected" : ""}>${o}：${OUTCOME_LABEL[o]}</option>`).join("")}
             </select>`;
        return `<tr><td>${e.match_no}</td><td class="l">${esc(e.home)} vs ${esc(e.away)}</td>
          <td>${e.pick}</td><td>${resCell}</td>
          <td>${a ? (e.pick === a ? "⭕" : "❌") : "—"}</td></tr>`;
      }).join("") + `</table></div></details>`;
  }
  const level = Math.min(100, totalHits * 10 + judgedRounds * 5);
  const catRows = Object.entries(catStats).filter(([, v]) => v[1] > 0)
    .map(([k, [h, t]]) => `<tr><td>${k}：${OUTCOME_LABEL[k]}</td><td>${Math.round(h / t * 1000) / 10}%</td><td>${t}</td></tr>`).join("");
  html += `<div class="row" style="margin:10px 0">
      <div class="level">Lv.${level}</div><div class="muted">スコア ${level}/100<br>累計的中 ${totalHits}件 / 判定済 ${totalJudged}件</div></div>
    <div class="bar"><i style="width:${level}%"></i></div>
    ${catRows ? `<h4>カテゴリ別得意度</h4><table>${catRows}</table>` : ""}`;
  el.innerHTML = html;

  // 🧠 AI自己学習チューニング（Mac側の答え合わせ履歴から自動学習した補正係数）
  const L = DATA.learning;
  if (L && L.metrics && L.metrics.n) {
    const m = L.metrics, c = L.coefficients || {};
    const keyOf = { "1": "home", "0": "draw", "2": "away" };
    const fmtPt = (v) => `${v >= 0 ? "+" : ""}${Math.round(v * 1000) / 10}pt`;
    el.innerHTML += `<h4>🧠 AI自己学習チューニング（答え合わせ連動・自動更新）</h4>
      <p class="muted">判定済 ${m.n}件・的中率 ${m.accuracy}%・波乱発生率 ${m.upset_rate}%・最終学習 ${esc(L.updated_at || "—")}</p>
      <table><tr><th>結果</th><th>予測平均</th><th>実際頻度</th><th>バイアス</th><th>次回への補正</th></tr>` +
      OUTCOMES.map((o) => `<tr><td>${OUTCOME_LABEL[o]}</td>
        <td>${m.mean_pred ? m.mean_pred[o] : "-"}%</td><td>${m.actual_rate ? m.actual_rate[o] : "-"}%</td>
        <td>${m.bias_pt ? m.bias_pt[o] : "-"}pt</td><td><b>${fmtPt(c[keyOf[o]] || 0)}</b></td></tr>`).join("") +
      `</table><p class="muted">過去の的中／不的中履歴から引き分け・ホーム／アウェイの偏りを自動分析し、
       Mac側の再予測へ補正係数として自動反映しています。</p>`;
    const hist = (L.history || []).slice(-10);
    if (hist.length > 1) {
      el.innerHTML += `<div class="row" style="gap:4px;align-items:flex-end;height:64px;margin-top:6px">` +
        hist.map((h) => `<div title="${esc(h.ts)} 的中率${h.accuracy}%（n=${h.n}）"
             style="flex:1;background:var(--blue);height:${Math.max(4, (h.accuracy || 0) * 0.6)}px;border-radius:3px 3px 0 0"></div>`).join("") +
        `</div><p class="muted">直近の的中率推移（バー）</p>`;
    }
  }

  document.querySelectorAll(".stake-save").forEach((b) => b.onclick = () => {
    const rk = b.dataset.rk;
    const inv = parseInt(document.querySelector(`.stake-inv[data-rk="${rk}"]`).value || "0", 10);
    const pay = parseInt(document.querySelector(`.stake-pay[data-rk="${rk}"]`).value || "0", 10);
    PROFILE.stakes = PROFILE.stakes || {};
    PROFILE.stakes[rk] = { investment: inv, payout: pay };
    saveProfile(); drawHistory(g);
  });
  // 自動取得できなかった試合の手動結果入力
  document.querySelectorAll(".res-man").forEach((s) => s.onchange = () => {
    const rk = s.dataset.rk, no = s.dataset.no;
    PROFILE.manual_results = PROFILE.manual_results || {};
    PROFILE.manual_results[rk] = PROFILE.manual_results[rk] || {};
    if (s.value) PROFILE.manual_results[rk][no] = s.value;
    else delete PROFILE.manual_results[rk][no];
    saveProfile(); drawHistory(g);
  });
}

/* ---------------- 使い方マニュアル ---------------- */
const MANUAL_HTML = `
  <h2 style="margin-top:0">📖 使い方マニュアル</h2>
  <h3>⚽ 初めて使う人向け・基本ステップ</h3>
  <ol>
    <li><b>くじ種を選択</b>：上部タブで toto / mini toto / totoGOAL3 / totoGOAL2 を切替</li>
    <li><b>AI勝敗予測を確認</b>：「🎯 勝敗予想」で各試合の 1/0/2 確率・AI本命・波乱アラートを確認</li>
    <li><b>買い目を作る</b>：「📝 買い目＆マークシート」で予算を入力し「🎯 買い目を最適化」</li>
    <li><b>マークシートを保存</b>：表示されたシートを保存・印刷して購入に利用</li>
  </ol>
  <h3>🧠 マイAI育成・カスタム学習</h3>
  <ul>
    <li>データは<b>あなたのブラウザ（localStorage）にのみ保存</b>。他の閲覧者やMac本体に影響しません</li>
    <li><b>チーム別補正</b>：チームを開き「勝ち/引き分け/負け」へ±pt設定→「💾 保存」でAI勝率へリアルタイム上乗せ</li>
    <li><b>条件別学習・メモ</b>：雨天・ケガ人・監督交代などをメモ登録</li>
    <li><b>答え合わせ学習</b>：自分の予想を記録→結果確定で的中判定。的中率（上限100%）・回収率（100%=プラマイゼロ）・AIレベルが育つ</li>
    <li><b>推しクラブ</b>：登録すると応援チームの試合に「冷静アドバイス」が出ます</li>
  </ul>
  <h3>🔢 「1・0・2」とマルチ買いの見方</h3>
  <ul>
    <li><b>1</b>＝ホーム勝ち／<b>0</b>＝引き分け／<b>2</b>＝アウェイ勝ち</li>
    <li><b>シングル</b>＝1通り／<b>ダブル</b>＝2通り／<b>トリプル</b>＝3通り</li>
    <li>接戦度が高い試合ほど自動でダブル・トリプルが配置されます</li>
    <li>「📱 1試合ずつマーク確認モード」で1試合ずつ大きく確認してから反映できます</li>
  </ul>
  <h3>⚠️ 注意事項</h3>
  <p>本システムの予想は統計・AI分析に基づく<b>参考情報</b>です。的中を保証するものではなく、
  最終的な購入判断とその結果についてはご自身の責任で行ってください。</p>`;
function showManual() {
  $("#manual-body").innerHTML = MANUAL_HTML;
  $("#manual-overlay").style.display = "block";
}
function hideManual() { $("#manual-overlay").style.display = "none"; }

/* ---------------- 起動 ---------------- */
async function boot() {
  $("#btn-manual").onclick = showManual;
  $("#manual-close").onclick = hideManual;
  $("#manual-overlay").onclick = (e) => { if (e.target.id === "manual-overlay") hideManual(); };
  try {
    const b = await (await fetch("build.json", { cache: "no-store" })).json();
    BUILD_TS = b.ts;
    DATA = await (await fetch(`data.json?v=${BUILD_TS}`, { cache: "no-store" })).json();
    const m = await (await fetch("maintenance.json", { cache: "no-store" })).json().catch(() => ({}));
    if (m.maintenance) {
      $("#app").innerHTML = card(`<h3>🔧 メンテナンス中</h3>
        <p>現在サイトはメンテナンス中です。しばらくしてから再度アクセスしてください。</p>`);
      $("#hdr-meta").textContent = "toto勝敗予想システム（閲覧専用）";
      return;
    }
    $("#hdr-meta").textContent =
      `${DATA.round_no}／開催日 ${DATA.held_date}／取得 ${DATA.fetched_at}／公開データ生成 ${DATA.generated_at}`;
    if (DATA.carryover) {
      $("#hdr-meta").innerHTML += `<br><span style="color:#ffd166;font-weight:700">🔥 キャリーオーバー発生中！ ${esc(DATA.carryover)}</span>`;
    }
    const want = new URLSearchParams(location.hash.slice(1)).get("g") || Object.keys(DATA.games)[0];
    CUR_GAME = DATA.games[want] ? want : Object.keys(DATA.games)[0];
    renderTabs();
    render();
    updateAutobar();
    // 30分以上経過していれば裏側で自動収集（画面はブロックしない）
    if (AUTO.enabled && (!AUTO.lastRun || Date.now() - AUTO.lastRun > AUTO_INTERVAL)) {
      autoCollect();
    } else {
      // 自動収集OFF・間隔内でも、公式の最新回だけは軽量チェックして通知する
      checkOfficialRound().then(updateAutobar);
    }
  } catch (e) {
    $("#app").innerHTML = card(`<h3>⚠️ データを読み込めませんでした</h3>
      <p class="muted">${esc(e.message)}<br>公開データ（data.json）がまだ生成されていない可能性があります。</p>`);
  }
}
function render() {
  const g = DATA.games[CUR_GAME];
  if (!g) { $("#app").innerHTML = card(`<p class="muted">このくじ種のデータがありません。</p>`); return; }
  if (g.kind === "goal") renderGoal(g); else renderWinDraw(g);
}
boot();
