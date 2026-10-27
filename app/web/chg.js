'use strict';
/* 業主申請改時間：選新日期、開始時間（時數照原本）、要調整的事 → 送出，我們確認服務人員時間後在 LINE 通知。手機、電腦都能用。 */
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const Q = new URLSearchParams(location.search), O = Q.get('o') || '', S = Q.get('s') || '';
const app = document.getElementById('app');
const WD = '日一二三四五六';
const md = d => { const x = new Date(d + 'T00:00:00'); return `${x.getMonth() + 1}/${x.getDate()}（${WD[x.getDay()]}）`; };
const addMin = (t, m) => { const [h, mm] = t.split(':').map(Number), x = Math.min(h * 60 + mm + m, 22 * 60); return `${String(Math.floor(x / 60)).padStart(2, '0')}:${String(x % 60).padStart(2, '0')}`; };
let V = null;
(async () => {
  const r = await fetch(`/api/public/chg?o=${encodeURIComponent(O)}&s=${encodeURIComponent(S)}`);
  V = await r.json().catch(() => ({}));
  if (!r.ok) { app.innerHTML = `<div class="card"><h2 style="margin-top:0">${esc(V.error || '連結無效')}</h2><p class="small">請回到 LINE 直接告訴我們想改的時間 🙏</p><div id="lqc"></div></div>`; lqContact('#lqc', 'demoshop', '💬 LINE 告訴我們想改的時間'); return; }
  document.title = `申請改時間｜${V.brand_name}`;
  draw();
})();
function draw() {
  const hrs = V.minutes ? Math.round(V.minutes / 6) / 10 : '';
  let h = `<p class="small" style="margin:0">${esc(V.brand_name)}</p><h1 class="headline" style="font-size:30px;margin-top:4px">申請改時間</h1>`;
  h += `<div class="card"><div class="small">目前的預約</div><div class="now"><div class="big">${esc(V.when)}</div></div>${hrs ? `<div class="small">約 ${hrs} 小時</div>` : ''}</div>`;
  if (V.pending) h += `<div class="card" style="border:2px solid #0A84FF"><b>已送出：想改到 ${md(V.pending.date)} ${esc(V.pending.start)}–${esc(V.pending.end)}</b><div class="small">我們確認中，確認後會在 LINE 通知您。要改成別的時間，直接在下面重新選、送出就好。</div></div>`;
  h += `<div class="rule">${esc(V.rule)}<br>送出後我們會確認服務人員的時間，確認好會在 LINE 通知您；<b>確認之前原本的預約仍有效</b>。</div>`;
  h += `<form id="f" class="card" novalidate>
    <label for="d">新的日期</label><input id="d" type="date" min="${V.min_date}" max="${V.max_date}" value="${V.pending ? V.pending.date : ''}" required>
    <label style="margin-top:14px;display:block">開始時間</label><div class="times">${V.times.map(t => `<label><input type="radio" name="t" value="${t}"${V.pending && V.pending.start === t ? ' checked' : ''}><span>${t}</span></label>`).join('')}</div>
    <div class="end" id="end"></div>
    <label for="n" style="margin-top:14px;display:block">其他要調整的（選填）</label><textarea id="n" rows="3" placeholder="例如：想多加陽台、改成 2 位、那天家裡有人">${esc(V.pending ? V.pending.note : '')}</textarea>
    <div class="err" id="err" role="alert" style="color:#C62828;margin-top:8px"></div>
    <button class="btn" type="submit" style="width:100%;margin-top:12px">送出改時間申請</button></form>`;
  app.innerHTML = h;
  const showEnd = () => { const t = (document.querySelector('[name=t]:checked') || {}).value; document.getElementById('end').textContent = t && V.minutes ? `大約 ${t}–${addMin(t, V.minutes)}（時數跟原本一樣）` : ''; };
  document.querySelectorAll('[name=t]').forEach(x => x.addEventListener('change', showEnd)); showEnd();
  document.getElementById('f').addEventListener('submit', async e => {
    e.preventDefault(); const err = document.getElementById('err'); err.textContent = '';
    const date = document.getElementById('d').value, start = (document.querySelector('[name=t]:checked') || {}).value, note = document.getElementById('n').value.trim();
    if (!date) return err.textContent = '請選新的日期';
    if (!start) return err.textContent = '請選開始時間';
    const b = e.submitter || document.querySelector('#f button'); b.disabled = true; b.textContent = '送出中…';
    try {
      const r = await fetch('/api/public/chg', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ o: O, s: S, date, start, note }) });
      const v = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(v.error || '送出失敗，請再試一次');
      app.innerHTML = `<div class="card ok"><div class="ic">📅</div><h2>已收到您的申請</h2><p>想改到 <b>${md(date)} ${start}</b></p><p class="small">我們確認服務人員的時間後，會在 LINE 通知您。<br>確認之前原本的預約仍有效。</p><p class="small">可以關掉這一頁，回到 LINE。</p></div><div id="lqc"></div>`; lqContact('#lqc', V.brand, '💬 回到 LINE');
    } catch (x) { err.textContent = x.message; b.disabled = false; b.textContent = '送出改時間申請'; }
  });
}
