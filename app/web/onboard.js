'use strict';
/* LINE 首次加入：我要應徵（apply）／我要詢問（hi）。 */
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
let RM = null, REG = null;
const KIND = document.body.dataset.kind, T = new URLSearchParams(location.search).get('t') || '';
const AREAS = ['大安區', '板橋區', '中壢區', '東區', '壯圍鄉', '西屯區', '三民區', '三星鄉', '蘇澳鎮', '東區', '大同鄉', '南澳鄉'];
const chips = (id, list) => { $('#' + id).innerHTML = list.map(x => `<label><input type="checkbox" name="${id}" value="${x}"> ${x}</label>`).join(''); };
const pick = n => $$(`[name="${n}"]:checked`).map(x => x.value).join('、');
const radios = (id, list) => { $('#' + id).innerHTML = list.map((x, i) => `<label><input type="radio" name="${id}" value="${x}"${i === 0 ? ' checked' : ''}> ${x}</label>`).join(''); };
async function init() {
  const r = await fetch(`/api/public/onboard?kind=${KIND}&t=${encodeURIComponent(T)}`); const v = await r.json().catch(() => ({}));
  if (!r.ok) { $('#bad').hidden = false; $('#bad').innerHTML = `<h2 style="margin-top:0">${esc(v.error || '連結無效')}</h2><p>請回到 LINE 重新點選。</p>`; return; }
  if (v.display_name) $('#hi').textContent = `嗨 ${v.display_name}！` + $('#hi').textContent;
  if (KIND === 'apply') {      // 盡量用點的：地區、技能、時間可複選；交通、經驗單選
    // 客服 應徵資料表（01–09）
    radios('role', ['服務人員', '家庭管家', '陪玩員']);
    RM = regionMulti($('#regions'), []); chips('skills', ['一般到府服務', '年度保養／細清', '進階專案', '搬遷服務', '定期駐點服務', '店面／辦公室', '收納整理']);
    radios('transport', ['機車', '汽車', '大眾運輸', '其他']); radios('has_exp', ['有', '無']);
    radios('years', ['無經驗', '未滿 1 年', '1～3 年', '3～5 年', '5 年以上']); radios('cert', ['無', '有']);
    chips('avail', ['平日上午', '平日下午', '假日上午', '假日下午', '晚上', '都可以']);
    chips('iv_time', ['平日白天', '平日晚上', '假日白天', '假日晚上']);
    const sync = () => { $('#cleanBox').hidden = pick('role') !== '服務人員'; $('#cert_name').hidden = pick('cert') !== '有'; };
    $('#f').addEventListener('change', sync); sync();
    $('#pf').addEventListener('change', async e => {        // 作品集照片：縮小後先上傳（最多 8 張）
      const fs = [...e.target.files]; $('#err').textContent = '';
      for (let i = 0; i < fs.length; i++) {
        $('#pfn').textContent = `上傳中 ${i + 1}/${fs.length}…`;
        try { const r = await postRetry('/api/public/apply/photo', { t: T, content: await shrinkImg(fs[i]) }); const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '上傳失敗'); $('#pfn').textContent = `已上傳 ${v.count} 張`; }
        catch (x) { $('#err').textContent = x.message; break; }
      }
      e.target.value = '';
    });
    if (v.display_name && !$('#name').value) $('#name').value = v.display_name;
  }
  else {
    REG = regionSingle($('#county'), $('#area'), '臺北市'); if (v.display_name) $('#name').value = v.display_name;
    const chk = () => { const c = REG.county(), out = !(v.service_counties || ['臺北市']).includes(c); $('#outside').hidden = !out; if (out) $('#outside').textContent = outsideNote(c, v.service_counties, v.brand_name); };
    $('#county').addEventListener('change', chk); chk();
  }
  brandHead(v.brand, v.brand_name); window.HIB = v.brand;
  const bl = document.getElementById('bookLink'); if (bl && v.brand) bl.href = '/book?b=' + encodeURIComponent(v.brand);      // 稽核：企業線／生活線不要跑到示範服務店
  $('#f').hidden = false;
  window.DRAFT = Draft.attach('onboard:' + KIND + ':' + T, $('#f')); DRAFT.restore();      // 填到一半離開不會不見
}
$('#f').addEventListener('submit', async e => {
  e.preventDefault(); $('#err').textContent = '';
  if (KIND === 'apply' && !RM.get().length) return $('#err').textContent = '請至少點一個可接案地區';
  if (KIND === 'apply' && pick('role') === '服務人員' && !pick('skills')) return $('#err').textContent = '請至少點一個想做的服務項目';
  $('#go').disabled = true;
  const body = KIND === 'apply'
    ? { t: T, role: pick('role'), name: $('#name').value, phone: $('#phone').value, regions: RM.get().join('、'), skills: pick('role') === '服務人員' ? pick('skills') : '',
        transport: pick('transport'), has_exp: pick('has_exp'), years: pick('years'), cert: pick('cert'), cert_name: $('#cert_name').value, experience: [pick('has_exp') && '相關經驗：' + pick('has_exp'), pick('years')].filter(Boolean).join('・'),
        portfolio_link: $('#portfolio_link').value, portfolio_text: $('#portfolio_text').value, exp_desc: $('#exp_desc').value, served: $('#served').value, ability: $('#ability').value,
        feedback: $('#feedback').value, specialty: $('#specialty').value, availability: pick('avail'), iv_time: pick('iv_time') }
    : { t: T, name: $('#name').value, phone: $('#phone').value, area: REG.get(), need: $('#need').value };
  try {
    const r = await postRetry(KIND === 'apply' ? '/api/public/apply' : '/api/public/hello', body);
    const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '送出失敗');
    window.DRAFT && DRAFT.clear(); $('#f').hidden = true; $('#ok').hidden = false; if (typeof lqContact === 'function') lqContact('#lqc', (window.HIB || 'demoshop'), '💬 回到 LINE');      // 
    if (v.pending || v.existing) $('#ok').querySelector('p').textContent = '資料收到了！系統找到您之前的資料，專人確認是本人後就會完成綁定，請稍候 🙏';
  } catch (x) { $('#err').textContent = x.message; $('#go').disabled = false; }
});
function shrinkImg(file) {          // 手機照片縮到長邊 1600、JPEG 0.82
  return new Promise((ok, bad) => { const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => { const k = Math.min(1, 1600 / Math.max(img.width, img.height)), cv = document.createElement('canvas'); cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k); cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height); URL.revokeObjectURL(url); ok(cv.toDataURL('image/jpeg', 0.82)); };
    img.onerror = () => { URL.revokeObjectURL(url); bad(new Error('照片讀不到，請換一張')); }; img.src = url; });
}
init();
