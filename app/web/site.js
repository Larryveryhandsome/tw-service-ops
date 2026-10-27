'use strict';
/* 已綁定客戶補案場資料（從 LINE 綁定完成卡／我的預約打開）。 */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const T = new URLSearchParams(location.search).get('t') || '';
const AREAS = ['大安區', '板橋區', '中壢區', '東區', '壯圍鄉', '西屯區', '三民區', '三星鄉', '蘇澳鎮', '東區', '大同鄉', '南澳鄉', '臺北以外'];
let P = null, REG = null;
async function init() {
  const r = await fetch('/api/public/prefill?t=' + encodeURIComponent(T)); const v = await r.json().catch(() => ({}));
  if (!r.ok) { $('#bad').hidden = false; $('#bad').innerHTML = `<h2 style="margin-top:0">${esc(v.error || '連結無效')}</h2><p>請回到 LINE，按選單「我的預約」重新打開。</p>`; return; }
  P = v; $('#hi').textContent = `${v.name} 您好！留一次，之後預約、服務人員到場都更順。`;
  REG = regionSingle($('#county'), $('#area'), '臺北市'); brandHead(v.brand, v.brand_name);
  $('#site').innerHTML = v.sites.map(s => `<option value="${esc(s.id)}">${esc(s.name || s.address)}</option>`).join('') + '<option value="">＋ 新增一個地點</option>';
  $('#pickBox').hidden = !v.sites.length; fill(); $('#site').addEventListener('change', fill);
  $('#bookGo').href = `/book?b=${v.brand}&t=${encodeURIComponent(T)}`; $('#f').hidden = false;
  window.DRAFT = Draft.attach('site:' + T, $('#f')); DRAFT.restore();      // 填到一半離開不會不見
}
function fill() {
  const s = P.sites.find(x => x.id === $('#site').value) || {};
  $('#sname').value = s.name || ''; $('#address').value = s.address || ''; REG = regionSingle($('#county'), $('#area'), s.area || '臺北市');
}
$('#f').addEventListener('submit', async e => {
  e.preventDefault(); $('#err').textContent = ''; $('#go').disabled = true;
  const body = { t: T, site_id: $('#site').value || '', name: $('#sname').value, area: REG.get(), address: $('#address').value, kind: $('#kind').value, size: $('#size').value,
    rooms: $('#rooms').value, baths: $('#baths').value, floors: $('#floors').value, nolift: $('#nolift').checked, pets: $('#pets').checked,
    entry_notes: $('#entry').value, parking: $('#parking').value, notes: $('#notes').value,
    contact_name: $('#cname').value, contact_phone: $('#cphone').value, contact_role: $('#crole').value };
  try {
    const r = await postRetry('/api/public/site', body);
    const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '儲存失敗');
    window.DRAFT && DRAFT.clear(); $('#f').hidden = true; $('#ok').hidden = false;
  } catch (x) { $('#err').textContent = x.message; $('#go').disabled = false; }
});
init();
