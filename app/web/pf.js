'use strict';
/* 客戶看的夥伴作品集（匿名）：不顯示姓名，只有代號、自我介紹、年資、服務場數、夥伴自己挑的公開照片。 */
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const Q = new URLSearchParams(location.search);
(async () => {
  const r = await fetch(`/api/public/pf?o=${encodeURIComponent(Q.get('o') || '')}&s=${encodeURIComponent(Q.get('s') || '')}`);
  const v = await r.json().catch(() => ({}));
  const app = document.getElementById('app');
  if (!r.ok) { app.innerHTML = `<div class="card"><h2 style="margin-top:0">${esc(v.error || '連結無效')}</h2><div id="lqc"></div></div>`; lqContact('#lqc', 'demoshop'); return; }
  document.title = `夥伴作品集｜${v.brand}`;
  let h = `<h1 class="headline" style="font-size:30px">為您服務的夥伴</h1><p class="sub">${esc(v.brand)}・為保護夥伴隱私，這裡不顯示姓名；照片都是夥伴自己挑選、看不出是哪位客戶家的作品。</p>`;
  if (!v.crew.length) h += '<div class="card"><p>人員安排好之後，這裡會顯示夥伴的經歷和作品。</p></div>';
  for (const w of v.crew) {
    h += `<div class="card"><h2 style="margin:0">${esc(w.code)}</h2><div class="stat">
      ${w.years !== '' ? `<div><b>${w.years}</b><span class="small">年經驗</span></div>` : ''}
      <div><b>${w.jobs}</b><span class="small">場服務紀錄</span></div></div>
      ${w.intro ? `<p style="white-space:pre-wrap">${esc(w.intro)}</p>` : '<p class="small">夥伴還沒寫自我介紹。</p>'}
      ${w.photos.length ? `<div class="pfg">${w.photos.map(u => `<img src="${esc(u)}" loading="lazy" alt="作品照片">`).join('')}</div>` : '<p class="small">夥伴還沒放作品照片。</p>'}</div>`;
  }
  app.innerHTML = h;
})();
const z = document.getElementById('zoom');
document.addEventListener('click', e => {
  if (e.target.matches('.pfg img')) { z.querySelector('img').src = e.target.src; z.style.display = 'flex'; }
  else if (e.target.closest('#zoom')) z.style.display = 'none';
});
