'use strict';
/* 商家通用版預約：選服務項目（單價＋時長）× 數量 → 日期時段 → 地址 → 送出；系統依單價試算，店家確認後成為正式報價單。 */
const $ = s => document.querySelector(s);
const fmt = n => 'NT$ ' + Number(n).toLocaleString('zh-TW');
const locDay = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const acc = new URLSearchParams(location.search).get('b') || 'demoshop';
let OPT = null, REG = null, QTY = {}, CREATED = null, timer = null;

function drawSvcs() {
  $('#svcs').innerHTML = (OPT.generic || []).map(s => `<div class="svc${QTY[s.code] ? ' on' : ''}" data-code="${s.code}">
    <div class="t"><div class="n">${s.name}</div><div class="m">${s.desc}・每${s.unit}約 ${s.mins} 分鐘</div></div>
    <div class="p">${fmt(s.price)}<span class="small">／${s.unit}</span></div>
    <div class="qty"><button type="button" data-d="-1" aria-label="減少 ${s.name}">−</button><b>${QTY[s.code] || 0}</b><button type="button" data-d="1" aria-label="增加 ${s.name}">＋</button></div></div>`).join('');
}
const items = () => Object.entries(QTY).filter(([, q]) => q > 0).map(([code, qty]) => ({ code, qty }));
function form() {
  return { account: acc, svc_items: items(), date: $('#date').value, slot: $('#slot').value, area: REG ? REG.get() : '',
    address: ((REG ? REG.get() : '') + $('#address').value.trim()), name: $('#name').value.trim(), phone: $('#phone').value.trim(),
    notes: $('#notes').value.trim(), website: $('#website').value, photo_via: 'upload' };
}
function refresh() {
  clearTimeout(timer); const box = $('#est');
  if (!items().length) { box.hidden = true; return; }
  timer = setTimeout(async () => {
    const r = await fetch('/api/public/estimate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form()) });
    const e = await r.json().catch(() => ({}));
    if (!r.ok || !e.total) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<div class="estt">系統試算</div>` + e.items.map(i => `<div class="estrow"><span>${i.label}</span><span>${fmt(i.amount)}</span></div>`).join('')
      + `<div class="estrow total"><span>合計（約 ${e.hours} 小時）</span><span>${fmt(e.total)}</span></div><p class="small" style="margin-bottom:0">${e.note || ''}</p>`;
  }, 250);
}
$('#svcs').addEventListener('click', e => {
  const b = e.target.closest('button[data-d]'); if (!b) return;
  const code = b.closest('.svc').dataset.code; QTY[code] = Math.max(0, Math.min(20, (QTY[code] || 0) + +b.dataset.d));
  drawSvcs(); refresh();
});
$('#bk').addEventListener('submit', async e => {
  e.preventDefault(); $('#err').textContent = '';
  const f = form();
  if (!f.svc_items.length) return $('#err').textContent = '請至少選一個服務項目（按＋）';
  if (!f.date) return $('#err').textContent = '請選日期';
  if (!$('#address').value.trim()) return $('#err').textContent = '請填地址（到店服務可填「到店」）';
  if (!f.name) return $('#err').textContent = '請留下稱呼';
  if (f.phone.replace(/\D/g, '').length < 8) return $('#err').textContent = '請留下手機';
  if (!$('#agree').checked) return $('#err').textContent = '請勾選同意使用資料';
  const go = $('#go'); go.disabled = true; go.textContent = '送出中…';
  try {
    const post = async (url, body) => { const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '送出失敗，請稍後再試'); return v; };
    const q = CREATED = CREATED || await post('/api/public/quote', f);
    await post(`/api/public/quote/${q.token}/submit`, {});
    $('#bk').hidden = true; $('#ok').hidden = false; scrollTo(0, 0);
    $('#statusLink').href = '/q/' + q.token;
    $('#okText').innerHTML = `預約編號 <b>${q.no}</b>。<b>店家會先確認時段與金額</b>，確認後正式報價單會傳給您。`;
    $('#okSum').innerHTML = $('#est').hidden ? '' : `<div class="estbox">${$('#est').innerHTML}</div>`;
  } catch (err) { $('#err').textContent = err.message; go.disabled = false; go.textContent = '送出預約'; }
});
async function init() {
  OPT = await (await fetch('/api/public/options?b=' + encodeURIComponent(acc))).json();
  $('#slot').innerHTML = (OPT.slots || []).map(s => `<option>${s}</option>`).join('');
  const d = new Date(); d.setDate(d.getDate() + 1); $('#date').min = locDay(d); d.setDate(d.getDate() + 2); $('#date').value = locDay(d);
  REG = regionSingle($('#county'), $('#area'), '臺北市大安區');
  drawSvcs();
}
init();
