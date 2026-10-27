'use strict';
/* 合作夥伴應徵表（2026-10-12 完整版）：題目由伺服器 apply_form.py 給（唯一的題目設定），這裡照著畫；
   依應徵職缺顯示專屬題；作品集照片、好評截圖先上傳；必填由伺服器最後把關。 */
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const T = new URLSearchParams(location.search).get('t') || '';
let FORM = [], RM = null, CNT = { portfolio: 0, feedback: 0 };

const val = k => { const el = $$(`[name="${k}"]`); if (!el.length) return ''; if (el[0].type === 'radio') return (el.find(x => x.checked) || {}).value || ''; if (el[0].type === 'checkbox') return el.filter(x => x.checked).map(x => x.value); return el[0].value.trim(); };
const role = () => val('role');

function qHtml(q) {
  const id = q.k, need = q.req ? '<span class="req">必填</span>' : '';
  const head = `<label class="ql">${q.no ? esc(q.no) + '｜' : ''}${esc(q.label)}${need}</label>${q.hint ? `<p class="hint">${esc(q.hint)}</p>` : ''}`;
  let body = '';
  if (q.type === 'one' || q.type === 'many') {
    const t = q.type === 'one' ? 'radio' : 'checkbox';
    body = `<div class="chips">${q.opts.map(o => `<label><input type="${t}" name="${esc(id)}" value="${esc(o)}"> ${esc(o)}</label>`).join('')}</div>`;
    if (q.other) body += `<input type="text" class="other" name="${esc(id)}_other" placeholder="${esc(q.other_label || '請說明')}" maxlength="200" hidden>`;
    if (q.photos_if) body += `<div class="other" data-photos="${esc(q.kind)}" hidden>${upHtml(q.kind, '＋ 上傳截圖（可多張）')}</div>`;
  } else if (q.type === 'long') body = `<textarea name="${esc(id)}" rows="3" maxlength="1500"></textarea>`;
  else if (q.type === 'regions') body = `<div id="regions"></div>`;
  else if (q.type === 'photos') body = upHtml(q.kind, '＋ 上傳作品照片（可多張，最多 8 張）') + `<input type="url" name="${esc(q.link)}" placeholder="或貼雲端作品集連結（例如 Google 相簿、IG）" style="margin-top:8px">`;
  else body = `<input type="${q.type === 'tel' ? 'tel' : 'text'}" name="${esc(id)}" ${q.type === 'tel' ? 'inputmode="tel" autocomplete="tel"' : q.k === 'name' ? 'autocomplete="name"' : ''} maxlength="40">`;
  return `<div class="q" data-q="${esc(id)}">${head}${body}</div>`;
}
function upHtml(kind, label) { return `<label class="photobtn">${label}<input type="file" accept="image/*" multiple hidden data-kind="${esc(kind)}"></label><p class="small" data-count="${esc(kind)}"></p>`; }

function render(prefill) {
  $('#qs').innerHTML = FORM.map(s => `<section class="sec" data-roles="${esc((s.role || []).join(','))}"><h2>${esc(s.title)}</h2>${s.qs.map(qHtml).join('')}
    ${s.title.includes('良民證') ? '' : ''}</section>`).join('');
  RM = regionMulti($('#regions'), prefill.regions ? prefill.regions.split('、') : []);
  if (prefill.name) $('[name="name"]').value = prefill.name;
  if (prefill.phone) $('[name="phone"]').value = prefill.phone;
  sync();
}
function sync() {
  const r = role();
  $$('section.sec').forEach(s => { const rs = s.dataset.roles; s.hidden = !!rs && !rs.split(',').includes(r); });
  for (const s of FORM) for (const q of s.qs) {
    if (q.other) { const o = $(`[name="${q.k}_other"]`); const v = val(q.k); if (o) o.hidden = !(Array.isArray(v) ? v.includes(q.other) : v === q.other); }
    if (q.photos_if) { const box = $(`[data-photos="${q.kind}"]`); if (box) box.hidden = val(q.k) !== q.photos_if; }
  }
}

async function upload(input) {
  const kind = input.dataset.kind, fs = [...input.files], out = $(`[data-count="${kind}"]`); $('#err').textContent = '';
  for (let i = 0; i < fs.length; i++) {
    out.textContent = `上傳中 ${i + 1}/${fs.length}…`;
    try { const r = await postRetry('/api/public/apply/photo', { t: T, kind, content: await shrinkImg(fs[i]) }); const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '上傳失敗'); CNT[kind] = v.count; out.textContent = `已上傳 ${v.count} 張 ✅`; }
    catch (x) { $('#err').textContent = x.message; out.textContent = CNT[kind] ? `已上傳 ${CNT[kind]} 張` : ''; break; }
  }
  input.value = '';
}
function shrinkImg(file) {          // 手機照片縮到長邊 1600、JPEG 0.82
  return new Promise((ok, bad) => { const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => { const k = Math.min(1, 1600 / Math.max(img.width, img.height)), cv = document.createElement('canvas'); cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k); cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height); URL.revokeObjectURL(url); ok(cv.toDataURL('image/jpeg', 0.82)); };
    img.onerror = () => { URL.revokeObjectURL(url); bad(new Error('照片讀不到，請換一張')); }; img.src = url; });
}

function answers() {
  const a = {};
  for (const s of FORM) {
    const sec = $$('section.sec')[FORM.indexOf(s)]; if (sec && sec.hidden) continue;
    for (const q of s.qs) {
      if (q.type === 'photos') { a[q.link] = val(q.link); continue; }
      a[q.k] = q.type === 'regions' ? RM.get().join('、') : val(q.k);
      if (q.other) a[q.k + '_other'] = val(q.k + '_other');
    }
  }
  return a;
}
function firstMissing(a) {           // 只做簡單提示；伺服器會再檢查一次
  for (const s of FORM) {
    const sec = $$('section.sec')[FORM.indexOf(s)]; if (sec && sec.hidden) continue;
    for (const q of s.qs) {
      if (!q.req || q.type === 'photos') continue;
      const v = a[q.k];
      if (!v || (Array.isArray(v) && !v.length)) return q;
      if (q.other && (Array.isArray(v) ? v.includes(q.other) : v === q.other) && !a[q.k + '_other']) return { ...q, label: q.other_label, raw: 1 };
      if (q.photos_if && v === q.photos_if && !CNT[q.kind]) return { ...q, label: '選「有」請上傳好評截圖', raw: 1 };
    }
  }
  return null;
}

$('#f').addEventListener('change', e => { if (e.target.dataset && e.target.dataset.kind) return upload(e.target); sync(); $$('.q.bad').forEach(x => x.classList.remove('bad')); });
$('#f').addEventListener('submit', async e => {
  e.preventDefault(); $('#err').textContent = '';
  const a = answers(), miss = firstMissing(a);
  if (miss) { const el = $(`[data-q="${miss.k}"]`); if (el) { el.classList.add('bad'); el.scrollIntoView({ behavior: 'smooth', block: 'center' }); } return $('#err').textContent = `${miss.no ? miss.no + '｜' : ''}${miss.label}${miss.raw ? '' : '：請填寫'}`; }
  $('#go').disabled = true;
  try {
    const r = await postRetry('/api/public/apply', { t: T, answers: a });
    const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '送出失敗');
    window.DRAFT && DRAFT.clear(); $('#f').hidden = true; $('#ok').hidden = false;
    if (v.existing) $('#ok').querySelector('p').textContent = '資料收到了！系統找到您之前的資料，專人確認是本人後就會完成綁定，請稍候 🙏';
    if (v.updated) { $('#ok').querySelector('p').textContent = `已更新你的應徵資料，${(window.BRANDV && BRANDV.brand_name) || '我們'}看過後會在 LINE 跟你約電話面試的時間 🙏`; if (window.BRANDV) brandHead(BRANDV.brand, BRANDV.brand_name); }
  } catch (x) { $('#err').textContent = x.message; $('#go').disabled = false; }
});

(async () => {
  const r = await fetch(`/api/public/onboard?kind=apply&t=${encodeURIComponent(T)}`); const v = await r.json().catch(() => ({}));
  if (!r.ok) { $('#bad').hidden = false; $('#bad').innerHTML = `<h2 style="margin-top:0">${esc(v.error || '連結無效')}</h2><p>請回到 LINE 重新點「我要應徵」。</p>`; return; }
  FORM = v.form || []; $('#intro').textContent = v.intro || '';
  if (v.display_name) $('#hi').textContent = `嗨 ${v.display_name}！` + $('#hi').textContent;
  render({ name: (v.prefill || {}).name || v.display_name || '', phone: (v.prefill || {}).phone || '', regions: (v.prefill || {}).regions || '' });
  brandHead(v.brand, v.brand_name); window.BRANDV = v;
  $('#f').hidden = false;
  window.DRAFT = Draft.attach('apply:' + T, $('#f')); DRAFT.restore();      // 填到一半離開不會不見
})();
