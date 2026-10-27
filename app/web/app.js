'use strict';
/* 店家營運中樞（示範）：核心（登入、導覽、路由、彈出視窗、表單、複製、推播）。
   CSP 不允許行內 onclick，所有按鈕都用 data-act / data-go，由下面的事件委派處理。 */
let S = null, ME = null, VIEW = '', PARAM = '', BUSY = false;
const ACT = {}, PAGES = {};
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const isRedo = o => !!(o && (o.parent_order_id || /^補清/.test(o.title || '')));      // 補清的工單要一眼看得出來
const redoTag = o => isRedo(o) ? '<span class="pill warn">🔁 補清</span> ' : '';
const money = n => n === null || n === undefined || n === '' ? '—' : (n < 0 ? '-$' : '$') + Math.abs(Math.round(n / 100)).toLocaleString('zh-TW');
const WD = '日一二三四五六';
const dOf = s => new Date(s + 'T12:00:00+08:00');
// 盤點：資料庫有兩種時間格式——「08:42:30」是 UTC（dispatch_log、audit、trash），「T16:42:02+08:00」是台灣時間；畫面一律顯示台灣時間
const loc = s => { if (!s) return ''; if (s.includes('T')) return s.slice(5, 16).replace('T', ' '); const d = new Date(s.replace(' ', 'T') + 'Z'); if (isNaN(d)) return s; const p = n => String(n).padStart(2, '0'); return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; };
const md = s => s ? (+s.slice(5, 7)) + '/' + (+s.slice(8, 10)) + '(' + WD[dOf(s).getDay()] + ')' : '日期待定';
const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const addDays = (s, n) => { const d = dOf(s); d.setDate(d.getDate() + n); return iso(d); };
const mins = (a, b) => { if (!a || !b) return null; const [h1, m1] = a.split(':').map(Number), [h2, m2] = b.split(':').map(Number); return h2 * 60 + m2 - h1 * 60 - m1; };
const hrs = m => m === null || m === undefined ? '—' : (Math.round(m / 6) / 10) + ' 小時';
const LBL = { review: '待核對', draft: '草稿', confirmed: '已排定', completed: '已完工', cancelled: '已取消', archived: '歷史', active: '可派工', paused: '暫停', invited: '邀請中', applied: '已報名', accepted: '已接單', declined: '婉拒', issued: '待收款', paid: '已結清', open: '待處理', resolved: '已結案', pending: '待確認', verified: '已對帳', disputed: '有爭議', new: '新需求', contacted: '已聯絡', quoted: '已報價', booked: '已建單', lost: '未成交', spam: '無效', overdue: '逾期', to_bill: '待開帳', billed: '待收款', in_service: '服務中' };
const st = (s, txt) => `<span class="st ${esc(s)}">${esc(txt || LBL[s] || s)}</span>`;
const today = () => S?.today || iso(new Date());

function toast(msg, err) {
  const t = document.createElement('div'); t.className = 'toast' + (err ? ' err' : ''); t.textContent = msg; t.setAttribute('role', 'status');
  document.body.appendChild(t); setTimeout(() => t.remove(), err ? 6000 : 3200);
}
async function api(path, data, method) {
  const opt = data === undefined && !method ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': ME?.csrf || '' }, body: JSON.stringify(data || {}) };
  const r = await fetch(path, opt); let v = {};
  try { v = await r.json(); } catch (e) { }
  if (r.status === 401 && v.login) { showLogin(); throw new Error('請重新登入'); }
  if (!r.ok) throw new Error(v.error || '操作失敗');
  return v;
}
/* ───── Google 地圖直接導航（正確入口 → 國土測繪精準座標 → 地址） ───── */
function navUrl(s) {
  s = s || {};
  if ((s.navigation || '').startsWith('http')) return s.navigation;
  const exact = s.lat && s.lng && (s.geo_addr || '') === (s.address || '');
  return exact ? `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lng}` : s.address ? 'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(s.address) : '';
}
const navHow = s => (s.navigation || '').startsWith('http') ? '管理層標的正確入口' : s.lat && (s.geo_addr || '') === (s.address || '') ? (s.geo_level === '路段' ? '國土測繪路段定位（門牌查不到）' : '國土測繪門牌精準定位') : '用地址搜尋（精準定位查詢中）';
// card＝放在外層卡片上（不要跟著打開工單）
const navBtn = (s, label = '🧭 導航', card = false) => { const u = navUrl(s); return u ? `<a class="btn sm navbtn" href="${esc(u)}" target="_blank" rel="noopener"${card ? ' data-act="navGo"' : ''}>${label}</a>` : ''; };
ACT.navGo = () => { };
ACT.copyAddr = d => copy(d.t, '地址');
async function copy(text, what) {
  try { await navigator.clipboard.writeText(text); toast((what || '文字') + '已複製，可以貼到 LINE'); }
  catch (e) { sheet('複製文字', `<p class="small">請長按下方文字全選複製。</p><div class="copybox" tabindex="0">${esc(text)}</div>`); }
}
function wait(btn, on) { if (btn) { btn.disabled = on; if (on) btn.dataset.txt = btn.textContent, btn.textContent = '處理中…'; else if (btn.dataset.txt) btn.textContent = btn.dataset.txt; } }

/* ───── 登入 ───── */
function showLogin() { $('#app').hidden = true; $('#setpwView').hidden = true; $('#loginView').hidden = false; setTimeout(() => $('#lu').focus(), 50); }
$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault(); $('#lerr').textContent = '';
  try { await api('/api/login', { username: $('#lu').value, password: $('#lp').value }); $('#lp').value = ''; boot(); }
  catch (err) { $('#lerr').textContent = err.message; }
});
$('#setpwForm').addEventListener('submit', async e => {
  e.preventDefault(); $('#sperr').textContent = '';
  if ($('#sp1').value !== $('#sp2').value) return $('#sperr').textContent = '兩次輸入不一樣';
  try { await api('/api/password', { new: $('#sp1').value }); boot(); } catch (err) { $('#sperr').textContent = err.message; }
});
async function boot() {
  // 「結帳完全不能看」：頁面剛好在系統重啟那幾秒載入，某個程式檔沒載到 → 自動重新整理一次（同一分頁 1 分鐘內只試一次）
  const need = ['billing', 'quotes', 'payhist', 'pairing', 'marketing', 'ledger'];
  if (need.some(k => typeof PAGES[k] !== 'function')) {
    let t = 0; try { t = +sessionStorage.getItem('reloadAt') || 0; } catch (e) { }
    if (Date.now() - t > 60000) { try { sessionStorage.setItem('reloadAt', Date.now()); } catch (e) { } location.reload(); return; }
  }
  let me;
  try { const r = await fetch('/api/me'); me = await r.json(); if (!r.ok) return showLogin(); } catch (e) { return showLogin(); }
  ME = me;
  if (me.must_change) { $('#loginView').hidden = true; $('#app').hidden = true; $('#setpwView').hidden = false; return; }
  $('#loginView').hidden = true; $('#setpwView').hidden = true; $('#app').hidden = false;
  if (typeof homeTodos !== 'function') {          // 伺服器重啟那幾秒 pages.js 沒載到 → 自動重新整理一次
    try { if (!sessionStorage.getItem('reload_once')) { sessionStorage.setItem('reload_once', '1'); return location.reload(); } } catch (e) { }
  } else { try { sessionStorage.removeItem('reload_once'); } catch (e) { } }
  await load(); route();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => { });
}
async function load() {
  const r = await fetch('/api/state'); const v = await r.json();
  if (!r.ok) { if (v.login) return showLogin(); throw new Error(v.error); }
  S = v; ME = { ...ME, ...v.me, csrf: v.csrf, tours: v.tours || [] };
  if (S.role !== 'cleaner') index();
}
async function refresh() { await load(); route(true); }

/* 方便查找 */
let IDX = {};
function index() {
  IDX = {};
  for (const t of ['customers', 'sites', 'workers', 'orders', 'plans', 'invoices', 'users', 'inquiries']) IDX[t] = Object.fromEntries((S[t] || []).map(x => [x.id, x]));
  IDX.byOrder = {}; for (const a of S.assignments || []) (IDX.byOrder[a.order_id] = IDX.byOrder[a.order_id] || []).push(a);
  IDX.sitesOf = {}; for (const s of S.sites || []) (IDX.sitesOf[s.customer_id] = IDX.sitesOf[s.customer_id] || []).push(s);
  for (const c of S.customers || []) c.dn = c.name + (c.ln ? `（LINE：${c.ln}）` : '');      // 顯示用：名字＋LINE 名稱（存檔一律用 name）
  IDX.photos = {}; for (const p of S.attachments || []) (IDX.photos[p.order_id] = IDX.photos[p.order_id] || []).push(p);
  IDX.invoiced = new Set((S.invoice_lines || []).map(l => l.order_id).filter(Boolean));
}
const cust = id => IDX.customers?.[id] || {};
const site = id => IDX.sites?.[id] || {};
const wname = id => IDX.workers?.[id]?.name || '（已移除）';
const crew = (oid, sts = ['accepted']) => (IDX.byOrder[oid] || []).filter(a => sts.includes(a.status));
const accepted = o => crew(o.id).length;
const short = o => ['review', 'draft', 'confirmed'].includes(o.status) && accepted(o) < o.required_workers;
/* 人數進度條（Apple 風：一位一格膠囊；接了填色、到齊整條變綠；超過 8 位改連續條） */
function crewMeter(o) {
  const need = Math.max(1, o.required_workers || 1), got = Math.min(accepted(o), need), miss = need - got, full = !miss;
  const bar = need <= 8 ? `<span class="segs">${Array.from({ length: need }, (_, i) => `<i class="${i < got ? 'on' : ''}"></i>`).join('')}</span>`
    : `<span class="bar"><i style="width:${Math.round(got / need * 100)}%"></i></span>`;
  return `<span class="crewm${full ? ' full' : ''}" role="meter" aria-valuemin="0" aria-valuemax="${need}" aria-valuenow="${got}" aria-label="已接 ${got} 位，要 ${need} 位">${bar}<span class="lab"><b>${got}</b>/${need} 人${full ? '<em class="ok">到齊</em>' : `<em>缺 ${miss}</em>`}</span></span>`;
}
const can = c => ({ admin: ['ops', 'money', 'admin'], staff: ['ops'], finance: ['ops', 'money'], cleaner: [] }[S?.role || ME?.role] || []).includes(c);

/* ───── 導覽列與路由 ───── */
const ICON = {
  home: '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>', schedule: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  dispatch: '<path d="M4 12h12M12 6l6 6-6 6"/><circle cx="20" cy="12" r="1.5"/>', customers: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20c-.4-2.4-1.7-4-3.5-4.8"/>',
  billing: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M7 15h4"/>', workers: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4.5 4.2-7 8-7s7 2.5 8 7"/>',
  more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>', jobs: '<path d="M4 7h16v12H4zM9 7V5h6v2"/>',
  mine: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4M8 15l2.5 2.5L16 13"/>', avail: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>', me: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4.5 4.2-7 8-7s7 2.5 8 7"/>'
};
const svg = k => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICON[k] || ICON.more}</svg>`;
function navItems() {
  if (S.role === 'cleaner') return [['jobs', '接單'], ['mine', '我的班'], ['avail', '可排時間'], ['me', '我']];
  const n = [['home', '今天'], ['schedule', '排班'], ['quotes', '報價'], ['dispatch', '派工'], ['customers', '客戶']];
  if (can('money')) n.push(['billing', '結帳']);
  n.push(['workers', '夥伴'], ['more', '更多']);
  return n;
}
/* 還剩幾件沒處理（管理群卡片＋要決定的題目＋沒結案的待辦、客訴）→ 外層「更多」、「管理群待處理」都顯示數字 */
const sqCount = Q => new Set((Q || []).map(x => ['lvd', 'lvh'].includes(x.kind) ? 'lv:' + x.id : x.kind + ':' + x.id)).size;      // 同一個請假的①②算一件
const asksLeft = () => !S || S.role === 'cleaner' ? 0 : sqCount(S.staffq) + (S.asks || []).length + [...(S.tasks || []), ...(S.service_cases || [])].filter(t => t.status === 'open').length;
function badgeFor(k) {
  if (!S || S.role === 'cleaner') return k === 'jobs' ? (S?.mine || []).filter(x => x.assignment_status === 'invited').length : 0;
  if (k === 'home') return typeof homeTodos === 'function' ? homeTodos().reduce((n, g) => n + (g.red ? g.items.length : 0), 0) : 0;      // pages.js 沒載到（重啟那幾秒）不要整頁壞掉
  if (k === 'dispatch') return (S.orders || []).filter(o => o.date >= today() && short(o) && o.status !== 'cancelled').length;
  if (k === 'quotes') return S.quotes_pending || 0;
  if (k === 'more') return asksLeft() + (S.inquiries || []).filter(q => q.status === 'new').length + (S.triage_open || 0) + (matchMedia('(max-width: 640px)').matches ? (S.quotes_pending || 0) : 0);
  return 0;
}
function drawNav() {
  const items = navItems();
  $('#gnav').innerHTML = items.map(([k, t]) => { const b = badgeFor(k); return `<a data-go="${k}" data-nav="${k}" class="${VIEW === k || (k === 'more' && MORE.some(m => m[0] === VIEW)) ? 'on' : ''}">${t}${b ? `<span class="badge">${b}</span>` : ''}</a>`; }).join('');
  const tabs = S.role === 'cleaner' ? items : items.filter(([k]) => ['home', 'schedule', 'dispatch', can('money') ? 'billing' : 'customers', 'more'].includes(k));
  $('#tabbar').innerHTML = tabs.map(([k, t]) => { const b = badgeFor(k); return `<button data-go="${k}" data-tab="${k}" class="${VIEW === k || (k === 'more' && !tabs.some(x => x[0] === VIEW)) ? 'on' : ''}">${svg(k)}${t}${b ? `<span class="badge">${b}</span>` : ''}</button>`; }).join('');
  $('#gme').innerHTML = `<span class="av">${esc((ME.name || '?').slice(0, 1))}</span><span class="nm">${esc(ME.name)}</span>`;
}
const MORE = [['ledger', '記帳', '示範公司（示範服務店／生活線／企業線）每月收支、LINE 拍收據自動記、每月記帳士包'], ['asks', '管理群待處理', '群組裡要按的卡片（題目、現場回報、請假、人員審核、開門方式…），在這裡也能處理'], ['marketing', '行銷', '待審貼文（全文、圖片、平台時間，直接核准）、發布排程、成效漏斗'], ['pairing', 'LINE 配對', '偵測到還沒配對的聊天室：比對證據、自己配對、解除'], ['map', '地區分布地圖', '各鄉鎮客戶分布與可支援夥伴，一眼看出哪裡缺人'], ['quotes', '報價審核', '客戶估價需求：看照片、確認金額後傳給客戶'], ['manual', '操作手冊', '每天怎麼做、派工、結帳、待整理，一步一步'], ['triage', '待整理', '交給負責人決定：保留、移動、合併、刪除（回收桶可還原）'], ['testimonials', '客戶見證', '滿意客戶的話＋服務前後照片；到官方 LINE 回覆謝謝'], ['interviews', '夥伴招募', '招募六關：審資料（履歷）→ 電話面試 → 良民證 → 實作 → 訓練 → 試派案；資料審核過才約面試'], ['shop', '示範商城', '水垢、油垢、磁磚耗材 500ML；業主、夥伴在 LINE 打「商城」點選購買'], ['flows', '工作流圖', '15 條工作流＋總覽：誰觸發、誰收到、LINE 往哪走（聚光燈導覽）'], ['sysreport', '功能回報', '系統問題、想要的功能都寫這裡（LINE 打「回報：…」也可以）；每天晚上統一修'], ['inquiries', '預約需求', '客戶從官方 LINE 填的表單'], ['plans', '方案與次數', '月方案、套票、剩餘次數'], ['receipts', '收款對帳', '先對銀行，再分攤到帳單'], ['payhist', '收款紀錄', '每一筆進來的錢：LINE Pay、匯款、商城、帳單收款（管理群打「收款紀錄」也看得到）'],
  ['tasks', '待辦紀錄（含已結案）', '人工待辦、客訴、補掃的全部紀錄；要處理的都在「管理群待處理」'], ['chats', '對話檔案', '示範服務店官方 LINE 匯出的歷史對話'], ['importdata', '資料匯入', '上傳 Excel／CSV 一次帶入客戶、夥伴、工單（先預覽再匯入）'], ['review', '資料核對', 'Excel 匯入結果、待補資料'], ['brands', '品牌與業務', '新增業務線、接官方 LINE、客戶選單（示範服務店／企業線／生活線⋯）'], ['line', 'LINE 自動化', '派工卡、承接卡、前一天提醒自動發送'], ['settings', '設定與帳號', '帳號權限、匯款資訊、日曆、LINE']];
function go(v, p) { location.hash = v + (p ? '/' + p : ''); }
function route(keep) {
  const [v, p] = (location.hash.slice(1) || '').split('/');
  if (v === 'new' && p && S.role !== 'cleaner') {
    const go2 = { order: ['schedule', 'newOrder'], customer: ['customers', 'newCustomer'], worker: ['workers', 'newWorker'], quote: ['quotes', 'quoteNew'] }[p];
    if (go2) { history.replaceState(null, '', '#' + go2[0]); route(); setTimeout(() => ACT[go2[1]] && ACT[go2[1]]({}), 80); return; }
  }
  if (['interviews', 'inq', 'q', 'w', 'c', 'o'].includes(v) && p) window.DEEP = true;      // 從 LINE 點進來看某一筆 → 不跳導覽蓋住
  if (v === 'inq' && p && S.role !== 'cleaner') { history.replaceState(null, '', '#inquiries'); route(); setTimeout(() => IDX.inquiries && IDX.inquiries[p] ? ACT.inquiry({ id: p }) : toast('找不到這筆預約需求'), 50); return; }
  if (v === 'interviews' && p && S.role !== 'cleaner') { history.replaceState(null, '', '#interviews'); route(); setTimeout(() => ACT.ivForm({ id: p }), 80); return; }
  if (v === 'q' && p && S.role !== 'cleaner') { history.replaceState(null, '', '#quotes'); route(); const tryQ = (n = 0) => ACT.quoteOpen ? ACT.quoteOpen({ id: p }) : n < 50 ? setTimeout(() => tryQ(n + 1), 100) : toast('報價單還在載入，請重新整理'); setTimeout(tryQ, 80); return; }      // 手機網路慢，quotes.js 還沒載完
  if (v === 'w' && p && S.role !== 'cleaner') { history.replaceState(null, '', '#workers'); route(); setTimeout(() => IDX.workers[p] ? ACT.worker({ id: p }) : toast('找不到這位夥伴（可能已合併或刪除）'), 50); return; }
  if (v === 'c' && p && S.role !== 'cleaner') { history.replaceState(null, '', '#customers'); route(); setTimeout(() => IDX.customers[p] ? ACT.customer({ id: p }) : toast('找不到這位客戶（可能已合併或刪除）'), 50); return; }
  if (v === 'o' && p && S.role !== 'cleaner') { history.replaceState(null, '', '#home'); route(); setTimeout(() => IDX.orders[p] ? ACT.order({ id: p }) : toast('找不到這張工單'), 50); return; }
  const home = S.role === 'cleaner' ? 'jobs' : 'home';
  const next = v && PAGES[v] ? v : home;
  const changed = next !== VIEW;
  VIEW = next; PARAM = decodeURIComponent(p || '');
  drawNav();
  const main = $('#main'); const y = scrollY;
  main.className = 'main' + (['schedule', 'billing', 'dispatch'].includes(VIEW) ? ' wide' : '') + (changed ? ' enter' : '');
  try { main.innerHTML = PAGES[VIEW](PARAM) || ''; } catch (e) { console.error(e); main.innerHTML = `<div class="banner red">這頁出了問題：${esc(e.message)}</div>`; }
  if (PAGES[VIEW].after) PAGES[VIEW].after(PARAM);
  if (keep) scrollTo(0, y); else if (changed) scrollTo(0, 0);
  if (window.FOCUS) { const el = document.querySelector(window.FOCUS); if (el) { window.FOCUS = null; el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 2600); } }      // 點了直接到那一筆
  if (changed && typeof maybeTour === 'function') maybeTour(VIEW);
}
addEventListener('hashchange', () => S && route());
document.addEventListener('visibilitychange', () => { if (!document.hidden && S && $('#sheet').hidden) refresh().catch(() => { }); });

/* ───── 頁首、表格、空狀態 ───── */
function phead(eyebrow, title, sub, act = '') { return `<div class="phead"><div class="tx"><div class="eyebrow">${eyebrow}</div><h1 class="headline">${title}</h1>${sub ? `<p class="sub">${sub}</p>` : ''}</div>${act ? `<div class="act">${act}</div>` : ''}</div>`; }
function segctl(name, opts, cur) { return `<div class="segctl" style="--segn:${Math.min(opts.length, 4)}">${opts.map(([k, t, n]) => `<button data-act="seg" data-seg="${name}" data-val="${k}" class="${cur === k ? 'on' : ''}">${t}${n ? `<span class="n">${n}</span>` : ''}</button>`).join('')}</div>`; }
const SEG = {};
ACT.seg = d => { SEG[d.seg] = d.val; route(true); };
function tbl(head, rows, cls = '') {
  if (!rows.length) return '<div class="empty">目前沒有資料。</div>';
  return `<div class="tbl"><table class="${cls}"><thead><tr>${head.map(h => `<th${h.startsWith('>') ? ' class="r"' : ''}>${h.replace('>', '')}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}
function td(label, html, cls = '') { return `<td data-l="${esc(label)}"${cls ? ` class="${cls}"` : ''}>${html}</td>`; }
const btn = (t, act, data = {}, cls = '') => `<button type="button" class="btn sm ${cls}" data-act="${act}" ${Object.entries(data).map(([k, v]) => `data-${k}="${esc(v)}"`).join(' ')}>${t}</button>`;

/* ───── 彈出視窗（手機從下方、電腦置中）＋表單 ───── */
let SHEET_SUBMIT = null;
let SHEET_BACK = [], CUR_SHEET = null, NEXT_SHEET = null;      // 「點下去要直接到相對應的地方」：從工單點進子表單，存完回到同一張工單
function sheet(title, body, submit, label = '儲存', extra = '') {
  if (!$('#sheet').hidden && CUR_SHEET && CUR_SHEET !== NEXT_SHEET) SHEET_BACK.push(CUR_SHEET);
  CUR_SHEET = NEXT_SHEET; NEXT_SHEET = null; if (SHEET_BACK.length > 5) SHEET_BACK.shift();
  $('#sheetBody').innerHTML = `<div class="grab"></div><div class="shead">${SHEET_BACK.length ? '<button class="btn sm gray" data-act="sheetBack">‹ 返回</button>' : ''}<h3>${title}</h3><button class="btn sm gray" data-act="closeSheet">關閉</button></div>
    <form id="sheetForm" novalidate>${body}${submit ? `<div class="err" id="sheetErr" role="alert"></div><div class="btns">${extra}<button class="btn" type="submit" id="sheetOk">${label}</button></div>` : ''}</form>`;
  $('#sheet').hidden = false; SHEET_SUBMIT = submit; SHEET_DIRTY = false; SHEET_DRAFT = null;
  document.body.style.overflow = 'hidden';
  if (submit && !$('#q-date')) {          // 填到一半關掉、跳出也不會不見（報價編輯有自己的暫存）。暫存名＝標題＋哪顆按鈕哪一筆＋一開始的內容，不會帶到別筆
    const k0 = String(title).replace(/<[^>]+>/g, '') + '|' + LAST_ACT + '|' + hashStr(JSON.stringify(form()));
    SHEET_DRAFT = Draft.attach('sheet:' + hashStr(k0), $('#sheetForm'), { onClear: () => { closeSheet(); toast('已清除暫存，請重新打開這張表單'); } });
    SHEET_DRAFT.restore();
  }
  const f = $('#sheetBody input:not([type=hidden]):not([readonly]),#sheetBody select,#sheetBody textarea'); if (f && innerWidth > 800) f.focus();
}
let SHEET_DRAFT = null, LAST_ACT = '';
const hashStr = s => { let h = 5381; for (let i = 0; i < s.length; i++) h = (h * 33 + s.charCodeAt(i)) >>> 0; return h.toString(36); };
/* ───── 手機底部視窗往下滑就關（從上方把手、標題列；內容捲到最上面時也可以） ───── */
(() => {
  let y0 = null, dy = 0, t0 = 0, head = false;
  const pane = () => $('#sheetBody');
  document.addEventListener('touchstart', e => {
    const p = pane(); y0 = null;
    if (!p || $('#sheet').hidden || !p.contains(e.target) || e.touches.length !== 1) return;
    head = !!e.target.closest('.grab,.shead');
    if (!head && (p.scrollTop > 0 || e.target.closest('input,textarea,select,[contenteditable],.chips'))) return;
    y0 = e.touches[0].clientY; dy = 0; t0 = Date.now();
  }, { passive: true });
  document.addEventListener('touchmove', e => {
    if (y0 === null) return;
    const p = pane(); dy = e.touches[0].clientY - y0;
    if (dy <= 0 || (!head && p.scrollTop > 0)) { if (dy < -6 || p.scrollTop > 0) { y0 = null; p.style.transform = ''; } return; }      // 往上＝捲內容
    e.preventDefault(); p.style.transition = 'none'; p.style.transform = `translateY(${dy}px)`;
  }, { passive: false });
  document.addEventListener('touchend', () => {
    if (y0 === null) return;
    const p = pane(); y0 = null;
    p.style.transition = 'transform .2s ease';
    if (dy > 110 || (dy > 40 && Date.now() - t0 < 250)) {
      p.style.transform = 'translateY(100%)';
      setTimeout(() => { p.style.transform = ''; p.style.transition = ''; tryCloseSheet(); }, 180);
    } else {
      p.style.transform = ''; setTimeout(() => { p.style.transition = ''; }, 220);
    }
  });
})();
function closeSheet() { $('#sheet').hidden = true; SHEET_SUBMIT = null; SHEET_DIRTY = false; document.body.style.overflow = ''; SHEET_BACK = []; CUR_SHEET = null; }
ACT.sheetBack = () => { const f = SHEET_BACK.pop(); const keep = SHEET_BACK; $('#sheet').hidden = true; CUR_SHEET = null; SHEET_BACK = keep; if (f) f(); };
/* 改報價時會一直跳出去、改的不見 → 在框裡選字拖到外面放開不會再關掉；有改過要先確認才關 */
let SHEET_DIRTY = false, DOWN_BG = false;
function tryCloseSheet() { if (SHEET_DIRTY && SHEET_DRAFT) { SHEET_DRAFT.save(); toast('還沒儲存的內容已暫存，下次打開這張會自動帶回'); } else if (SHEET_DIRTY && !confirm('剛剛修改的內容還沒儲存，確定要關閉嗎？')) return; closeSheet(); }
ACT.closeSheet = tryCloseSheet;
$('#sheetBody').addEventListener('input', () => { SHEET_DIRTY = true; });
$('#sheet').addEventListener('pointerdown', e => { DOWN_BG = e.target.id === 'sheet'; });
$('#sheet').addEventListener('click', e => { if (e.target.id === 'sheet' && DOWN_BG) tryCloseSheet(); DOWN_BG = false; });
addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#sheet').hidden) tryCloseSheet(); });
$('#sheetBody').addEventListener('submit', async e => {
  e.preventDefault(); if (!SHEET_SUBMIT || BUSY) return;
  BUSY = true; const ok = $('#sheetOk'); wait(ok, true); $('#sheetErr').textContent = '';
  try { const keep = await SHEET_SUBMIT(form(), e.submitter); if (SHEET_DRAFT) SHEET_DRAFT.clear(); let back = null, rest = [];
    if (keep !== 'keep') { back = SHEET_BACK.pop(); rest = SHEET_BACK; closeSheet(); } await refresh(); if (typeof keep === 'string' && keep !== 'keep') toast(keep);
    if (back) { SHEET_BACK = rest; try { back(); } catch (x) { } } }      // 存完回到原本那張（工單、客戶、夥伴）
  catch (err) { $('#sheetErr').textContent = err.message; }
  finally { BUSY = false; wait(ok, false); }
});
function form() {
  const o = {};
  for (const el of $$('#sheetForm [name]')) {
    if (el.type === 'checkbox') { if (el.dataset.multi) { (o[el.name] = o[el.name] || []); if (el.checked) o[el.name].push(el.value); } else o[el.name] = el.checked ? 1 : 0; }
    else if (el.type === 'radio') { if (el.checked) o[el.name] = el.value; }
    else o[el.name] = el.value.trim();
  }
  return o;
}
function fld(name, label, val = '', type = 'text', opt = {}) {
  const id = 'f-' + name, v = val ?? '';
  let input;
  if (type === 'select') {      // 稽核：舊資料的值不在選項裡（例：客戶來源「歷史」、案場類型「日常房務…」）→ 保留原值，不要存檔時被改成第一個選項
    const ops = String(v) !== '' && !opt.options.some(([k]) => String(k) === String(v)) ? [[v, `${v}（原本的值）`], ...opt.options] : opt.options;
    input = `<select id="${id}" name="${name}">${ops.map(([k, t]) => `<option value="${esc(k)}"${String(k) === String(v) ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
  }
  else if (type === 'textarea') input = `<textarea id="${id}" name="${name}" rows="${opt.rows || 3}" placeholder="${esc(opt.ph || '')}">${esc(v)}</textarea>`;
  else if (type === 'check') return `<label class="chk"><input type="checkbox" id="${id}" name="${name}"${v ? ' checked' : ''}> ${label}</label>${opt.hint ? `<div class="fieldnote">${opt.hint}</div>` : ''}`;
  else input = `<input id="${id}" name="${name}" type="${type}" value="${esc(v)}" placeholder="${esc(opt.ph || '')}"${opt.step ? ` step="${opt.step}"` : ''}${type === 'number' ? ' inputmode="decimal"' : ''}${opt.ro ? ' readonly' : ''}>`;
  return `<label for="${id}">${label}</label>${input}${opt.hint ? `<div class="fieldnote">${opt.hint}</div>` : ''}`;
}
const g2 = (...x) => `<div class="g2">${x.map(v => `<div>${v}</div>`).join('')}</div>`;
const optsOf = (list, label = x => x.name, empty = '請選擇') => [['', empty], ...list.map(x => [x.id, label(x)])];
const toCents = v => v === '' || v === null || v === undefined ? null : Math.round(Number(v) * 100);

/* ───── 程式錯誤自動記到「功能回報」（同一種只記一筆；每次開頁最多送 5 筆） ───── */
function reportJs(msg, stack) {
  if (/Load failed|Failed to fetch|NetworkError|網路|network/i.test(String(msg || ''))) return;      // 每日回報 斷線（系統重啟、手機沒訊號）不是程式錯誤
  if (!ME || (reportJs.n = (reportJs.n || 0) + 1) > 5) return;
  api('/api/issue/js', { msg: String(msg || '').slice(0, 300), stack: String(stack || '').slice(0, 1500), page: location.hash.slice(0, 30) }).catch(() => { });
}
window.addEventListener('error', e => {           // 記檔名、行號、瀏覽器（語法錯誤沒有 stack）；外掛、別的網站的錯誤不算
  if ((e.filename && !e.filename.startsWith(location.origin)) || e.message === 'Script error.') return;
  reportJs(e.message, ((e.error && e.error.stack) || '') + `\n@ ${(e.filename || '').replace(location.origin, '')}:${e.lineno || 0}:${e.colno || 0}\n${navigator.userAgent}`.slice(0, 400));
});
window.addEventListener('unhandledrejection', e => { const r = e.reason || {}; if (r.name && r.name !== 'Error') reportJs(r.message, r.stack); });
/* ───── 事件委派 ───── */
document.addEventListener('click', async e => {
  const g = e.target.closest('[data-go]');
  if (g) { e.preventDefault(); closeSheet(); return go(g.dataset.go, g.dataset.p || ''); }
  const a = e.target.closest('[data-act]');
  if (!a || !ACT[a.dataset.act]) return;
  if (a.tagName === 'TR' || a.classList.contains('ocard')) { if (e.target.closest('input,select,textarea,label,button,a') && e.target.closest('input,select,textarea,label,button,a') !== a) return; }      // 整列可點，但勾選框、按鈕各做各的
  if (a.closest('form') && a.getAttribute('type') !== 'submit') e.preventDefault();      // 沒寫 type 的按鈕也不能送出表單
  if (a.dataset.confirm && !confirm(a.dataset.confirm)) return;
  LAST_ACT = a.dataset.act + ':' + (a.dataset.id || a.dataset.o || a.dataset.c || a.dataset.w || a.dataset.q || a.dataset.k || '');      // 表單暫存用：哪顆按鈕、哪一筆
  try { const r = await ACT[a.dataset.act]({ ...a.dataset }, a); if (typeof r === 'string') toast(r); }
  catch (err) { toast(err.message, true); if (err.name !== 'Error') reportJs(err.message, err.stack); }      // 程式錯誤（不是操作提示）→ 自動功能回報
});
document.addEventListener('change', e => { const a = e.target.closest('[data-change]'); if (a && ACT[a.dataset.change]) ACT[a.dataset.change]({ ...a.dataset, value: a.value }, a); });
// 中文輸入法選字中（注音／拼音還在組字）不要觸發搜尋重畫，選完字（compositionend）再處理，不然會打斷選字
const onInput = e => { const a = e.target.closest('[data-input]'); if (a && ACT[a.dataset.input]) ACT[a.dataset.input]({ ...a.dataset, value: a.value }, a); };
document.addEventListener('input', e => { if (!e.isComposing) onInput(e); });
document.addEventListener('compositionend', onInput);
ACT.logout = async () => { await api('/api/logout', {}); location.hash = ''; location.reload(); };

/* ───── 手機通知 ───── */
ACT.enablePush = async () => {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) throw new Error('這支手機的瀏覽器不支援通知。iPhone 請先用 Safari「分享 › 加入主畫面」，再從主畫面打開。');
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') throw new Error('沒有允許通知，請到瀏覽器設定打開');
  const reg = await navigator.serviceWorker.register('/sw.js'); await navigator.serviceWorker.ready;
  const { key } = await api('/api/push/key');
  const raw = atob(key.replace(/-/g, '+').replace(/_/g, '/') + '=='.slice(0, (4 - key.length % 4) % 4));
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: Uint8Array.from(raw, c => c.charCodeAt(0)) });
  const j = sub.toJSON();
  await api('/api/push/subscribe', { endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth, ua: navigator.userAgent.slice(0, 150) });
  await refresh(); return '這支手機會收到通知了';
};

boot();
