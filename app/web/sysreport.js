'use strict';
/* 功能回報：系統問題、想要的功能集中在這裡（LINE 打「回報：…」也會進來；程式錯誤自動記錄）。每天晚上 22:00 左右統一讀取、修正，修好會通知回報的人。 */
PAGES.sysreport = () => {
  const all = S.sys_reports || [], mode = SEG.iss || 'open', isOpen = t => ['open', 'need_decision'].includes(t.status);
  const list = all.filter(t => mode === 'all' || (mode === 'open' ? isOpen(t) : !isOpen(t)));
  const ST = { open: '待處理', need_decision: '要決定', fixed: '已處理', wontfix: '不處理' }, KIND = { bug: '問題', feature: '想要的功能', question: '詢問' };
  let h = phead('功能回報', '哪裡怪怪的，寫下來就好。', '系統有問題、想要新功能，都寫在這裡（可以 Ctrl+V 貼截圖）；每天晚上 22:00 左右統一讀取、修正，處理好會標記。系統自己抓到的錯誤（標「自動」）也會出現在這裡。在 LINE 打「回報：…」也可以。');
  h += `<div class="card"><label for="isKind">類型</label><select id="isKind"><option value="bug">問題（哪裡壞了、怪怪的）</option><option value="feature">想要的功能</option></select>
    <label for="isText">說明（在哪一頁、按了什麼、看到什麼）</label><textarea id="isText" rows="3" placeholder="例：待整理按「保留」後跳出紅色錯誤"></textarea>
    <div id="isDrop" class="small" style="border:1.5px dashed var(--line,#d2d2d7);border-radius:12px;padding:10px 12px;margin-top:10px">📎 截圖：在上面框框裡按 <b>Ctrl+V</b> 直接貼上（先用 Win+Shift+S 或 PrtSc 截圖），或把圖片拖進來，或 <label class="btn sm sec" style="display:inline-block;cursor:pointer;margin:0 4px">選圖片<input id="isFile" type="file" accept="image/*" multiple hidden></label>（最多 6 張）
      <div id="isThumbs" class="thumbs">${ISS_IMGS.map((x, i) => `<div class="thumb"><img src="data:image/jpeg;base64,${x}" alt="截圖 ${i + 1}"><button type="button" data-act="issueImgDel" data-i="${i}" aria-label="移除">×</button></div>`).join('')}</div></div>
    <div class="btns">${btn('送出回報', 'issueAdd')}</div></div>`;
  h += segctl('iss', [['open', '待處理', all.filter(isOpen).length], ['done', '已處理'], ['all', '全部']], mode);
  h += '<div class="card">' + (list.map(t => `<div class="item"><div class="t"><div class="n">${esc(t.title)}</div>
    <div class="m">${KIND[t.kind] || ''}・${t.source === 'auto' ? '自動' : esc(t.who)}・${esc((t.created_at || '').slice(5, 16).replace('T', ' '))}${t.count > 1 ? `・出現 ${t.count} 次` : ''}・<b>${ST[t.status] || t.status}</b></div>
    ${t.fix_note ? `<div class="m">處理：${esc(t.fix_note)}</div>` : ''}${issFiles(t.id)}</div></div>`).join('') || '<div class="empty">沒有。</div>') + '</div>';
  return h;
};
let ISS_IMGS = [];                         // 回報附的截圖（base64 JPEG，已縮小）
function issFiles(rid) {
  const ids = (S.sys_report_files || {})[rid] || []; if (!ids.length) return '';
  return `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">${ids.map((id, i) => `<button type="button" class="thumb" data-act="galIds" data-k="r" data-cap="回報截圖" data-i="${i}" data-ids="${esc(ids.join(','))}" style="width:72px;height:72px"><img src="/api/issue/file?id=${esc(id)}" alt="截圖 ${i + 1}" loading="lazy"></button>`).join('')}</div>`;
}
async function issAddFiles(files) {
  const imgs = [...files].filter(f => f && f.type && f.type.startsWith('image/'));
  if (!imgs.length) return false;
  for (const f of imgs) { if (ISS_IMGS.length >= 6) { toast('最多 6 張', true); break; } ISS_IMGS.push(await shrink(f)); }
  const keep = $('#isText') ? $('#isText').value : ''; route(true); if ($('#isText')) $('#isText').value = keep;
  return true;
}
document.addEventListener('paste', e => {          // Ctrl+V 貼上截圖（在功能回報頁）
  if (VIEW !== 'sysreport') return;
  const files = [...((e.clipboardData && e.clipboardData.items) || [])].filter(x => x.kind === 'file').map(x => x.getAsFile());
  if (files.some(f => f && f.type.startsWith('image/'))) { e.preventDefault(); issAddFiles(files); }
});
document.addEventListener('change', e => { if (e.target && e.target.id === 'isFile') issAddFiles(e.target.files); });
document.addEventListener('dragover', e => { if (VIEW === 'sysreport' && e.target.closest && e.target.closest('#isDrop, #isText')) e.preventDefault(); });
document.addEventListener('drop', e => { if (VIEW === 'sysreport' && e.target.closest && e.target.closest('#isDrop, #isText') && e.dataTransfer && e.dataTransfer.files.length) { e.preventDefault(); issAddFiles(e.dataTransfer.files); } });
ACT.issueImgDel = d => { const keep = $('#isText').value; ISS_IMGS.splice(+d.i, 1); route(true); $('#isText').value = keep; };
ACT.issueAdd = async () => {
  const text = $('#isText').value.trim(); if (!text) throw new Error('請寫一下說明（截圖可以一起貼上）');
  const r = await api('/api/issue/report', { kind: $('#isKind').value, title: text.split('\n')[0].slice(0, 80), detail: text, page: location.hash.slice(0, 60), images: ISS_IMGS });
  ISS_IMGS = []; await refresh(); return '收到！' + (r.files ? `（附 ${r.files} 張截圖）` : '') + '每天晚上統一處理';
};
