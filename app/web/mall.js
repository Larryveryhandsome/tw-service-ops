'use strict';
/* 示範商城網站（2026-10-12）：照「服務問題」找商品 → 商品說明（只顯示有填的欄位）→ 購物車 → 免註冊下單 → 訂單進度。
   夥伴從 LINE 帶 ?w= 進來看到夥伴價；客戶帶 ?t= 自動帶入姓名電話。價格、庫存一律以伺服器為準。 */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const money = n => 'NT$ ' + Number(n || 0).toLocaleString('zh-TW');
const Q = new URLSearchParams(location.search), T = Q.get('t') || '', W = Q.get('w') || '';
const L = (() => { const v = Q.get('l') || (() => { try { return sessionStorage.getItem('mall-l') || ''; } catch (e) { return ''; } })();      // l＝從 LINE 打開：訂單直接連到這個 LINE
  if (Q.get('l')) { try { sessionStorage.setItem('mall-l', v); } catch (e) { /* 私密視窗 */ } Q.delete('l'); history.replaceState(null, '', location.pathname + (Q.toString() ? '?' + Q : '') + location.hash); }      // 稽核 F3：網址列拿掉，分享出去不會帶到
  return v; })();
const MOBILE = /Android|iPhone|iPad|iPod|Mobile|Line\//i.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && /Macintosh/.test(navigator.userAgent));
const KEY = 'demoshop-mall-cart';
const LABEL = { solves: '解決什麼問題', suitable: '適用範圍', avoid: '不適用／注意事項', usage: '使用方法', spec: '規格與內容物', warning: '成分與警語', faq: '常見問題' };
const ICON = { '浴室水垢': '🚿', '浴室馬桶': '🚽', '廚房油污': '🍳', '磁磚地面': '🧱', '發霉黴斑': '🫧', '地板傢俱': '🧹', '玻璃鏡面': '🪟', '工具耗材': '🧽' };
let V = null, FILTER = '', CART = load(), QTY = {};

function load() { try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { return {}; } }
function save() { try { localStorage.setItem(KEY, JSON.stringify(CART)); } catch (e) { /* 私密視窗：購物車只留在這一頁 */ } }
const P = id => (V.products || []).find(p => p.id === id);
const count = () => Object.values(CART).reduce((n, q) => n + q, 0);
const ev = (pid, kind) => fetch('/api/public/mall/ev', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pid, kind }) }).catch(() => {});
const keep = u => { const k = W ? 'w=' + encodeURIComponent(W) : T ? 't=' + encodeURIComponent(T) : ''; return u + (k ? (u.includes('?') ? '&' : '?') + k : ''); };      // l 不帶進連結（存在這個分頁）

function pic(p, big) {
  return p.image ? `<img src="${esc(p.image)}" alt="${esc(p.name)}" loading="lazy">` : `<span aria-hidden="true">${ICON[(p.problems || [])[0]] || '🧴'}</span>`;
}
function stockText(p) {
  if (p.stock === null || p.stock === undefined) return '<span class="stock">有貨</span>';
  if (p.stock <= 0) return '<span class="stock out">暫時缺貨</span>';
  return p.stock <= 5 ? `<span class="stock low">剩 ${p.stock} 瓶</span>` : '<span class="stock">有貨</span>';
}

function render() {
  const b = V.buyer || {};
  let h = `<div class="nav"><a class="logo" href="${esc(keep('/mall'))}"><img src="/brand-wordmark.png" alt="示範">商城</a>
    <div class="right"><a class="pillbtn" href="${esc(V.line)}" rel="noopener">LINE 問我們</a><button class="pillbtn cartbtn" type="button" data-open="cart">購物車 <span class="badge" id="cnt">${count()}</span></button></div></div>
    <div class="hero"><h1>服務團隊在用的耗材</h1><p>照問題找，買對、用對。不確定材質或不想自己處理？也可以直接請我們到府服務。</p>
    ${b.kind === 'worker' ? `<span class="who">👋 ${esc(b.name)}，你看到的是夥伴價</span>` : b.kind === 'customer' ? `<span class="who">👋 ${esc(b.name)}，已帶入您的資料</span>` : ''}</div>`;
  const probs = (V.problems || []).filter(x => V.products.some(p => (p.problems || []).includes(x)));
  h += `<div class="chips" role="group" aria-label="依問題找">${[['', '全部'], ...probs.map(x => [x, (ICON[x] || '') + ' ' + x])].map(([k, t]) => `<button class="chip" type="button" data-f="${esc(k)}" aria-pressed="${FILTER === k}">${esc(t)}</button>`).join('')}</div>`;
  const list = V.products.filter(p => !FILTER || (p.problems || []).includes(FILTER));
  h += list.length ? `<div class="grid">${list.map(p => {
    const q = QTY[p.id] || 1, out = p.stock !== null && p.stock !== undefined && p.stock <= 0;
    return `<article class="card"><button class="pic" type="button" data-detail="${esc(p.id)}" aria-label="看 ${esc(p.name)} 說明">${pic(p)}</button>
      <div class="body"><div class="tags">${(p.problems || []).map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>
      <h3>${esc(p.name)} <span class="note">${esc(p.size)}</span></h3><p class="solves">${esc(p.solves || '')}</p>
      <div class="row" style="justify-content:space-between"><span class="price">${money(p.price)}${p.worker_price ? '<small>夥伴價</small>' : ''}</span>${stockText(p)}</div>
      <button class="link" type="button" data-detail="${esc(p.id)}">看說明・用法 ›</button>
      <div class="row"><span class="step"><button type="button" data-q="${esc(p.id)}" data-d="-1" aria-label="減少">−</button><span>${q}</span><button type="button" data-q="${esc(p.id)}" data-d="1" aria-label="增加">＋</button></span>
      <button class="add" type="button" data-add="${esc(p.id)}"${out ? ' disabled' : ''}>${out ? '缺貨' : '加入購物車'}</button></div></div></article>`;
  }).join('')}</div>` : `<p class="empty">${V.products.length ? '這個分類還沒有商品。' : '商城準備中，很快就開賣 😊'}</p>`;
  h += `<section class="info"><h2>取貨、付款與售後</h2><div class="box"><p>${esc(V.terms || '下單後專人會在 LINE 跟您確認取貨與付款方式。')}</p>
    <p>商品有任何問題（例如破損、漏液、不知道怎麼用），直接在官方 LINE 告訴我們，附上照片我們會盡快處理。</p>
    <p>不確定材質能不能用、或不想自己處理？<a href="${esc(keep('/book?b=demoshop'))}">預約到府服務</a>，由服務團隊幫您。</p></div></section>`;
  h += `<footer><b>${esc(V.brand_name)}</b>｜${esc(V.address || '')}<br><a href="/home?b=demoshop">官方網站</a>　・　<a href="/store?b=demoshop">商店資訊</a>　・　<a href="/privacy?b=demoshop">隱私權政策</a>　・　<a href="${esc(V.line)}" rel="noopener">官方 LINE</a></footer>`;
  $('#app').innerHTML = h;
}

function detail(id) {
  const p = P(id); if (!p) return;
  ev(id, 'view');
  const fields = Object.keys(LABEL).filter(k => p[k]);
  $('#detail').innerHTML = `<button class="x" type="button" data-close="detail" aria-label="關閉">✕</button><h2>${esc(p.name)} ${esc(p.size)}</h2>
    <div class="pic" style="border-radius:16px;overflow:hidden;max-width:360px">${pic(p, true)}</div>
    <p class="price" style="margin:12px 0 0">${money(p.price)}${p.worker_price ? '<small>夥伴價</small>' : ''}　${stockText(p)}</p>
    <dl class="kv">${fields.map(k => `<dt>${LABEL[k]}</dt><dd>${esc(p[k])}</dd>`).join('')}</dl>
    ${fields.length < 3 ? '<p class="note" style="margin-top:12px">完整用法與注意事項請以瓶身標示為準；有疑問直接在官方 LINE 問我們。</p>' : ''}
    <div class="row" style="margin-top:16px"><button class="add" type="button" data-add="${esc(p.id)}"${p.stock !== null && p.stock !== undefined && p.stock <= 0 ? ' disabled' : ''}>加入購物車</button></div>
    <p class="note" style="margin-top:14px">不確定材質或不想自己處理？<a href="${esc(keep('/book?b=demoshop'))}">預約到府服務</a></p>`;
  open('detail');
}

function cart() {
  const ids = Object.keys(CART).filter(id => P(id) && CART[id] > 0);
  const total = ids.reduce((n, id) => n + P(id).price * CART[id], 0);
  $('#cartItems').innerHTML = ids.length ? ids.map(id => { const p = P(id); return `<div class="line"><div><b>${esc(p.name)} ${esc(p.size)}</b><div class="note">${money(p.price)} × ${CART[id]}</div></div>
    <span class="step"><button type="button" data-c="${esc(id)}" data-d="-1" aria-label="減少">−</button><span>${CART[id]}</span><button type="button" data-c="${esc(id)}" data-d="1" aria-label="增加">＋</button></span></div>`; }).join('')
    + `<div class="total"><span>合計</span><span>${money(total)}</span></div><p class="note">不收運費；取貨方式選下面一種。</p>` : '<p class="empty">購物車是空的。</p>';
  $('#co').hidden = !ids.length;
  $('#dlv').innerHTML = '<p class="field" style="margin:6px 0 0">要怎麼拿？</p>' + (V.delivery || []).map((d, i) => `<label class="opt"><input type="radio" name="dl" value="${esc(d.id)}"${i === 0 ? ' checked' : ''}> ${esc(d.label)}</label>`).join('');
  const b = V.buyer || {};
  if (b.name && !$('#nm').value) $('#nm').value = b.name;
  if (b.phone && !$('#ph').value) $('#ph').value = b.phone;
  $('#terms').textContent = (V.terms ? '取貨付款：' + V.terms : '送出後專人會在 LINE 跟您確認取貨與付款方式。')
    + (b.line ? '' : '　送出後請連上示範服務店官方 LINE（手機一鍵、電腦用手機掃 QR code），訂單才會成立；取貨、付款、備好了都在 LINE 通知。');
  const n = $('#cnt'); if (n) n.textContent = count();
}

function open(id) { $('#bg').hidden = false; $('#' + id).hidden = false; if (id === 'cart') cart(); }
function close() { $('#bg').hidden = true; $('#detail').hidden = true; $('#cart').hidden = true; }

function addToCart(id, q) {
  const p = P(id); if (!p) return;
  const want = Math.min(20, (CART[id] || 0) + q);
  if (p.stock !== null && p.stock !== undefined && want > p.stock) { alert(`${p.name} 只剩 ${Math.max(0, p.stock)} 瓶`); return; }
  CART[id] = want; save(); ev(id, 'add');
  const n = $('#cnt'); if (n) n.textContent = count();
  close(); open('cart');
}

document.addEventListener('click', e => {
  const t = e.target.closest('button, [data-open], .sheet-bg'); if (!t) return;
  if (t.dataset.copy !== undefined) { const ok = () => { t.textContent = '已複製 ✓'; }; return navigator.clipboard ? navigator.clipboard.writeText(t.dataset.copy).then(ok, () => {}) : null; }
  if (t.id === 'bg' || t.dataset.close) return close();
  if (t.dataset.open === 'cart') return open('cart');
  if (t.dataset.f !== undefined) { FILTER = t.dataset.f; return render(); }
  if (t.dataset.detail) return detail(t.dataset.detail);
  if (t.dataset.q) { QTY[t.dataset.q] = Math.max(1, Math.min(20, (QTY[t.dataset.q] || 1) + Number(t.dataset.d))); return render(); }
  if (t.dataset.add) return addToCart(t.dataset.add, t.closest('#detail') ? 1 : (QTY[t.dataset.add] || 1));
  if (t.dataset.c) { CART[t.dataset.c] = Math.max(0, Math.min(20, (CART[t.dataset.c] || 0) + Number(t.dataset.d))); if (!CART[t.dataset.c]) delete CART[t.dataset.c]; save(); return cart(); }
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });

const CO_DRAFT = Draft.attach('mall:co', $('#co')); CO_DRAFT.restore();      // 填到一半離開不會不見
$('#co').addEventListener('submit', async e => {
  e.preventDefault(); $('#err').textContent = '';
  const items = Object.keys(CART).filter(id => P(id) && CART[id] > 0).map(pid => ({ pid, qty: CART[pid] }));
  const dl = (document.querySelector('input[name="dl"]:checked') || {}).value;
  if (!items.length) return;
  if (!dl) return $('#err').textContent = '請選取貨方式';
  $('#go').disabled = true;
  try {
    const r = await fetch('/api/public/mall/order', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ t: T, w: W, l: L, items, delivery: dl, name: $('#nm').value, phone: $('#ph').value, note: $('#nt').value }) });
    const v = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(v.error || '送出失敗，請稍後再試，或直接在官方 LINE 留言');
    CART = {}; save(); close(); CO_DRAFT.clear();
    location.href = keep('/mall/o/' + v.token);
  } catch (err) { $('#err').textContent = err.message; $('#go').disabled = false; }
});

/* 電腦按「用 LINE 接收通知」打開的是 LINE 網站 → 訂單沒連上，備好貨聯絡不到（幽靈單）。
   沒連 LINE 的訂單「還差一步」：手機一鍵打開 LINE（訊息已打好）；電腦用手機 LINE 掃 QR code；也可以搜尋帳號傳那段文字。連上後頁面自動更新。 */
function lineManual(o) {
  return `<p class="note">也可以自己在 LINE 搜尋 <b>${esc(o.line_id)}</b>（示範服務店官方帳號），傳這段文字：</p>
    <p><code class="lcode">${esc(o.line_code)}</code> <button type="button" class="pillbtn" data-copy="${esc(o.line_code)}">複製</button></p>`;
}
function lineBox(o) {
  if (!o.line_wait) return '';
  return `<div class="box linebox" id="linebox"><p class="lb-t">還差一步：連上示範服務店官方 LINE，訂單才成立</p>
    <p>取貨、付款、「備好了」都只在 LINE 通知您。<b>${esc(o.expires)}</b> 前沒連上，訂單會自動取消（商品釋出給其他人）。</p>
    ${MOBILE ? `<p><a class="btn-line" href="${esc(o.line_link)}">打開 LINE 連上</a></p><p class="note">訊息已經幫您打好，按「傳送」就完成。</p>
      <details><summary class="note">按了沒反應？</summary>${lineManual(o)}</details>`
    : `<div class="qrwrap"><img class="qr" src="${esc(o.line_qr)}" alt="用手機 LINE 掃描，連上這張訂單" width="200" height="200">
      <div><p><b>拿出手機，掃這個 QR code</b>（手機相機或 LINE 的「掃描行動條碼」都可以）→ 會打開示範服務店官方 LINE、訊息已經打好 → 按「傳送」就完成。</p>${lineManual(o)}</div></div>`}
    <p class="note">連上之後這一頁會自動更新 ✅</p></div>`;
}
let POLL = null;
async function orderPage(token) {
  let o;
  try { const r = await fetch('/api/public/mall/o/' + encodeURIComponent(token)); o = await r.json(); if (!r.ok) throw new Error(o.error || '找不到訂單'); }
  catch (e) { $('#app').innerHTML = `<p class="empty">${esc(e.message)}</p><p style="text-align:center"><a href="${esc(keep('/mall'))}">回商城</a></p>`; return; }
  const st = o.status, idx = o.line_wait ? -1 : { new: 0, ready: 1, delivered: 2 }[st];
  $('#app').innerHTML = `<div class="nav"><a class="logo" href="${esc(keep('/mall'))}"><img src="/brand-wordmark.png" alt="示範">商城</a></div>
    <div class="hero"><h1>${st === 'cancelled' ? '訂單已取消' : o.line_wait ? '還差一步 👇' : '謝謝您的訂購 🧴'}</h1><p>訂單編號 <b>${esc(o.id)}</b>　${esc(o.created_at)}</p></div>
    <div class="steps">${st === 'cancelled' ? '<div class="cancel" style="grid-column:1/-1">已取消</div>' : [o.line_wait ? '等您連上 LINE' : '已收到訂單', '備好了，等您取貨', '已交付'].map((t, i) => `<div class="${i <= idx ? 'on' : ''}">${t}</div>`).join('')}</div>
    ${o.auto_cancelled ? '<p class="note" style="margin:10px 0">這張訂單沒有連上官方 LINE，系統已自動取消。想買的話請重新下單，下單後記得連上 LINE 😊</p>' : ''}
    ${lineBox(o)}
    <div class="box">${o.items.map(i => `<div class="line"><span>${esc(i.name)} × ${i.qty}</span><span>${money(i.price * i.qty)}</span></div>`).join('')}
      <div class="total"><span>合計</span><span>${money(o.total)}</span></div>
      <p>取貨：${esc(o.delivery)}</p><p>付款：${o.paid ? '已付款' + (o.paid_how ? '（' + esc(o.paid_how) + '）' : '') : '還沒付款'}</p>
      ${o.terms ? `<p class="note">${esc(o.terms)}</p>` : '<p class="note">專人會在 LINE 跟您確認取貨與付款方式。</p>'}
      ${o.bank && o.bank.account ? `<div class="note" style="background:#FFF7E6;border-radius:10px;padding:10px 12px"><b>匯款資訊</b><br>${esc(o.bank.bank)}<br>戶名：${esc(o.bank.holder)}<br>帳號：<b>${esc(o.bank.account)}</b><br>金額 NT$ ${Number(o.total).toLocaleString('zh-TW')}；匯好請在官方 LINE 傳「帳號末五碼」，確認後就幫您備貨。</div>` : ''}
      ${o.pay_url ? `<p><a class="btn-line" href="${esc(o.pay_url)}">用 LINE Pay 付款</a></p>` : ''}</div>
    ${o.line_on ? '<p class="note" style="margin-top:12px">✅ 已連上示範服務店官方 LINE：取貨、付款、備好了都會在 LINE 通知您。</p>' : ''}
    <p style="margin-top:20px"><a class="pillbtn" href="${esc(keep('/mall'))}">繼續逛商城</a></p>
    <p class="note">這個網址只有您知道，可以收藏起來隨時看進度；有問題直接在官方 LINE 告訴我們。</p>`;
  if (o.line_wait && !POLL) {             // 等客戶用手機傳出那則訊息 → 連上就自動換畫面（最多等 30 分鐘；切回這個分頁也會馬上看一次）
    let n = 0;
    const check = async () => { try { const r = await fetch('/api/public/mall/o/' + encodeURIComponent(token)); const v = await r.json(); if (r.ok && !v.line_wait) { clearInterval(POLL); orderPage(token); } } catch (e) { /* 網路不穩：下一輪再看 */ } };
    POLL = setInterval(() => { if (++n > 360) return clearInterval(POLL); check(); }, 5000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  }
}

(async () => {
  const m = location.pathname.match(/^\/mall\/o\/([A-Za-z0-9_-]+)$/);
  if (m) return orderPage(m[1]);
  try { const r = await fetch('/api/public/mall?' + new URLSearchParams({ t: T, w: W, l: L })); V = await r.json(); if (!r.ok) throw new Error(V.error || ''); }
  catch (e) { $('#app').innerHTML = '<p class="empty">商城暫時打不開，請稍後再試，或直接在官方 LINE 留言。</p>'; return; }
  for (const id of Object.keys(CART)) if (!P(id)) delete CART[id];
  render();
  const pid = Q.get('p'); if (pid && P(pid)) detail(pid);
})();
