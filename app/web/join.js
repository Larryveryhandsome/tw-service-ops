'use strict';
/* 夥伴報到：既有夥伴選名字＋手機驗證；新夥伴自動建檔（待店長確認）。 */
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const t = new URLSearchParams(location.search).get('t') || ''; let RM = null, V = null;
const chips = (id, list) => { $('#' + id).innerHTML = list.map(x => `<label><input type="checkbox" name="${id}" value="${x}"> ${x}</label>`).join(''); };
const pick = n => $$(`[name="${n}"]:checked`).map(x => x.value).join('、');
async function init() {
  const r = await fetch('/api/public/join?t=' + encodeURIComponent(t));
  const v = await r.json().catch(() => ({})); V = v;
  if (!r.ok) { $('#bad').hidden = false; $('#bad').innerHTML = `<h2 style="margin-top:0">${esc(v.error || '連結無效')}</h2><p>請回到 LINE 群組，再按一次「我是示範服務店夥伴，報到」。</p>`; return; }
  if (v.brand) brandHead(v.brand, v.brand_name);
  if (v.display_name) $('#hi').textContent = `嗨 ${v.display_name}！綁定一次，之後業主指定你、新的工作、前一天提醒都會自動傳到你的 LINE。`;
  $('#who').innerHTML = '<option value="">— 請選一個 —</option><option value="phone">我是示範服務店老夥伴（用手機號碼找我）</option>' + (v.workers || []).map(w => `<option value="${esc(w.id)}">${esc(w.name)}</option>`).join('') + '<option value="new">我是新夥伴（第一次來）</option>';
  RM = regionMulti($('#regions'), []);
  chips('skills', ['一般到府服務', '年度保養／細清', '搬遷服務', '店家服務', '店面／辦公室', '收納整理', '洗衣摺衣']);
  chips('avail', ['平日上午', '平日下午', '假日上午', '假日下午', '晚上', '都可以']);
  $('#jf').hidden = false;
  window.DRAFT = Draft.attach('join:' + t, $('#jf')); DRAFT.restore();      // 填到一半離開不會不見
}
$('#who').addEventListener('change', () => {           // 新夥伴：改填完整應徵表
  const isNew = $('#who').value === 'new'; $('#newBox').hidden = true; $('#go').hidden = isNew;
  let a = $('#applyGo'); if (!a) { $('#go').insertAdjacentHTML('beforebegin', '<a class="btn full" id="applyGo" style="margin-top:18px" hidden>填應徵表（約 5 分鐘）</a>'); a = $('#applyGo'); }
  a.hidden = !isNew; if (V && V.apply_url) a.href = V.apply_url;
});
$('#jf').addEventListener('submit', async e => {
  e.preventDefault(); $('#err').textContent = '';
  const who = $('#who').value;
  if (!who) return $('#err').textContent = '請選「老夥伴」或「新夥伴」';
  const body = { t, phone: $('#phone').value, regions: RM.get().join('、'), skills: pick('skills'), availability: pick('avail') };
  if (who === 'new') body.name = $('#name').value; else body.worker_id = who;
  $('#go').disabled = true;
  try {
    const r = await postRetry('/api/public/join', body);
    const v = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(v.error || '報到失敗，請稍後再試');
    window.DRAFT && DRAFT.clear(); $('#jf').hidden = true; $('#ok').hidden = false;
    $('#okT').textContent = `${v.name}，報到完成 🎉`;
    $('#okP').textContent = v.pending ? '為了安全，主管確認是你本人後就會完成綁定（通常很快），之後的派工、提醒都會傳到你的 LINE。' : v.new ? `你是新夥伴，${v.brand_name || '示範服務店'}確認資料後就會開始派工給你。` : '之後有指定你的工作、新的派工、前一天提醒，都會直接傳到你的 LINE。';
  } catch (err) { $('#err').textContent = err.message; $('#go').disabled = false; }
});
init();
