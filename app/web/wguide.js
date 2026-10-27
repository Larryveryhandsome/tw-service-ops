'use strict';
/* 夥伴使用教學（/wguide）：只做兩件事——①讀網址 ?b= 換品牌文字（預設示範服務店；?b=homeline＝生活小幫手）②目錄、選單格子點了平滑捲動。
   不打任何 API、不存任何資料。頁面上要換的字用 data-b 標記：name／full／title／contact。 */
(() => {
  const BRANDS = {
    demoshop: { name: '示範服務店', full: '示範服務店', title: 'DEMO 示範服務店', contact: '聯絡示範服務店' },
    homeline: { name: '生活小幫手', full: '生活小幫手', title: '生活小幫手', contact: '聯絡我們' },      // richmenu2.worker_cells：生活線版「聯絡示範服務店」改「聯絡我們」
  };
  let id = 'demoshop';
  try { id = (new URLSearchParams(location.search).get('b') || 'demoshop').toLowerCase(); } catch (e) { id = 'demoshop'; }
  if (!Object.prototype.hasOwnProperty.call(BRANDS, id)) id = 'demoshop';
  const B = BRANDS[id];
  document.documentElement.dataset.brand = id;
  document.querySelectorAll('[data-b]').forEach(el => { const v = B[el.dataset.b]; if (v) el.textContent = v; });
  document.querySelectorAll('[data-only]').forEach(el => { el.hidden = el.dataset.only !== id; });
  document.title = '夥伴使用教學｜' + B.name;

  const still = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    const el = document.getElementById(decodeURIComponent(a.getAttribute('href').slice(1)));
    if (!el) return;
    e.preventDefault();
    el.scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: 'start' });
    try { history.replaceState(null, '', '#' + el.id); } catch (x) { /* 舊瀏覽器：不改網址也沒關係 */ }      // 保留 ?b=，重新整理還是同一個品牌
  });
})();
