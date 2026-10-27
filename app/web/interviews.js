'use strict';
/* 夥伴面試：應徵者在 LINE 自己選面試時間、系統自動提醒；面試完在這裡填評分表 → 錄取（自動改可派工、傳歡迎＋系統介紹）／不錄取／再考慮／沒出現。 */
const IV_ITEMS = [['ontime', '守時'], ['manner', '溝通禮貌'], ['exp', '服務經驗'], ['detail', '細心程度'], ['avail', '可排時間與交通'], ['fit', '對工作內容的理解']];
const IV_ST = { check: '背景查核（良民證）', trial: '實作', training: '訓練', probation: '試派案', screening: '待看資料', reject_pending: '婉拒（等文案確認）', invited: '等他選時間', proposed: '等你挑面試時間', need_time: '等排面試時間', scheduled: '已約好', hired: '正式夥伴', rejected: '不錄取', hold: '再考慮', no_show: '沒出現' };
const ivWhen = s => { if (!s) return ''; const d = new Date(s); return `${d.getMonth() + 1}/${d.getDate()}（${'日一二三四五六'[d.getDay()]}）${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
PAGES.interviews = () => {
  const all = S.interviews || [], mode = SEG.iv || 'todo', open = x => ['screening', 'invited', 'proposed', 'need_time', 'scheduled', 'hold', 'check', 'trial', 'training', 'probation'].includes(x.status);
  const list = all.filter(x => mode === 'all' || (mode === 'todo' ? open(x) : !open(x)));
  let h = phead('夥伴招募', '找到對的人，服務才會好。', '招募六關（店長營運手冊）：①審資料／履歷（官方 LINE 應徵表單，派單管理群收到資料，管理層先審過，按「OK，邀請電話面試」才往下）→ ②電話面試（我們選時間、系統 LINE 通知他並自動提醒；也可以讓他自己點時間）→ ③背景查核：良民證（自己傳照片，或請示範服務店代辦、規費 LINE Pay／現場付）→ ④實作 → ⑤訓練 → ⑥試派案（這關開始可派工，系統自動傳歡迎和系統介紹）→ 轉正式。不適合隨時可以婉拒（文案由店長確認）。');
  h += segctl('iv', [['todo', '進行中', all.filter(open).length], ['done', '有結果'], ['all', '全部']], mode);
  const cand = (S.workers || []).filter(w => w.status === 'review' && !all.some(x => x.worker_id === w.id && open(x)));
  if (cand.length) h += `<div class="card"><label for="ivNew">幫舊夥伴／電話應徵的人約面試（有綁 LINE 的會收到選時間的訊息）</label><select id="ivNew">${cand.map(w => `<option value="${w.id}">${esc(w.name)} ${esc(w.phone || '')}</option>`).join('')}</select><div class="btns">${btn('約面試', 'ivInvite')}</div></div>`;
  h += '<div class="card">' + (list.map(x => `<div class="item"><div class="t click" data-act="ivForm" data-id="${x.id}"><div class="n">${esc(x.name)}　${esc(x.phone || '')}</div>
    <div class="m">${IV_ST[x.status] || x.status}${x.slot ? '・' + ivWhen(x.slot) : ''}${x.want ? '・想約：' + esc(x.want) : ''}・${esc((x.regions || '').slice(0, 20))}${x.result_by ? '・面試官 ' + esc(x.result_by) : ''}</div></div>
    <div class="a">${btn(open(x) ? '填結果' : '看評分', 'ivForm', { id: x.id }, open(x) ? '' : 'gray')}</div></div>`).join('') || '<div class="empty">目前沒有。</div>') + '</div>';
  return h;
};
ACT.ivInvite = async () => { const id = $('#ivNew').value; if (!id) throw new Error('請選一位'); await api('/api/interview/invite', { worker_id: id }); await refresh(); return '已約面試（有 LINE 的會收到選時間的訊息）'; };
ACT.ivForm = d => {
  const x = (S.interviews || []).find(i => i.id === d.id); if (!x) return toast('找不到這筆面試（請重新整理）', true);
  const sc = JSON.parse(x.scores || '{}'), opt = v => [['', '—'], ['5', '5 很好'], ['4', '4 好'], ['3', '3 普通'], ['2', '2 待加強'], ['1', '1 不適合']].map(([k, t]) => `<option value="${k}"${String(v || '') === k ? ' selected' : ''}>${t}</option>`).join('');
  const kinds = ['一般到府服務', '店家服務', '年度保養／細清', '大型案場服務', '店面／辦公室', '搬遷前後服務'], have = (x.skills || x.wskills || '');
  const app = JSON.parse(x.application || '{}'), AL = [['days', '每週可做'], ['avail', '可做時段'], ['start', '可以開始'], ['exp', '經驗'], ['exp_types', '做過'], ['abilities', '可以接受'], ['transport', '交通'], ['iv_time', '面試方便'], ['intro', '自我介紹']];
  const fl = (x.files || '').split(',').filter(Boolean).map(s => s.split(':')), pfs = fl.filter(f => f[1] === 'portfolio'), pol = fl.filter(f => f[1] === 'police'), fbs = fl.filter(f => f[1] === 'feedback');
  // 完整版應徵表（apply_form.py）：照段落列出全部答案
  const appHtml = (x.app_view || []).map(([sec, rows]) => `<h3 style="margin:16px 0 4px">${esc(sec)}</h3><dl class="kv">${rows.map(([t, v]) => `<dt>${esc(t)}</dt><dd style="white-space:pre-wrap">${esc(v)}</dd>`).join('')}</dl>`).join('');
  const pics = list => list.map(([id]) => `<a href="/api/worker-file?id=${esc(id)}" target="_blank" rel="noopener"><img src="/api/worker-file?id=${esc(id)}" alt="照片" loading="lazy" style="width:84px;height:84px;object-fit:cover;border-radius:10px"></a>`).join('');
  const pfHtml = (pfs.length ? `<h3>作品集照片 ${pfs.length}</h3><div style="display:flex;gap:6px;flex-wrap:wrap">${pics(pfs)}</div>` : '')
    + (fbs.length ? `<h3>客戶好評截圖 ${fbs.length}</h3><div style="display:flex;gap:6px;flex-wrap:wrap">${pics(fbs)}</div>` : '');
  const moreBtn = x.app_complete ? '' : btn('請他補填完整應徵表', 'ivMore', { id: x.id });
  const warn = x.app_complete ? '' : '<p class="small" style="color:var(--red)">⚠️ 資料不完整（沒填完整版應徵表），建議先請他補填，再決定要不要面試。</p>';
  if (['check', 'trial', 'training', 'probation'].includes(x.status)) {
    const P = JSON.parse(x.police || '{}'), isAdmin = S.role === 'admin';
    const polHtml = `<h3>③ 良民證</h3><dl class="kv"><dt>方式</dt><dd>${P.mode === 'agent' ? '示範服務店代辦' : P.mode === 'self' ? '自己辦、傳照片' : '還沒選'}</dd><dt>規費</dt><dd>${P.paid_at ? '已收（' + esc(P.paid_how || '') + '・' + esc(P.paid_by || '') + '）' : P.mode === 'agent' ? '還沒收' : '—'}</dd>
      <dt>送件</dt><dd>${P.submitted_at ? esc(P.submitted_at.slice(5, 16).replace('T', ' ')) : '—'}</dd><dt>結果</dt><dd>${P.ok ? '✅ 通過（' + esc(P.ok_by || '') + '）' : '還沒確認'}</dd></dl>
      ${pol.length ? (isAdmin ? `<div style="display:flex;gap:6px;flex-wrap:wrap">${pics(pol)}</div>` : '<p class="small">已上傳（只有管理員看得到照片）</p>') : ''}
      ${x.status === 'check' ? `<div class="btns">${btn('良民證確認通過', 'polMark', { id: x.id, w: 'ok', confirm: '確認良民證：3 個月內、本人、沒有紀錄？' })}${P.mode === 'agent' && !P.paid_at ? btn('已收規費（現場／匯款）', 'polMark', { id: x.id, w: 'paid' }, 'sec') : ''}${P.mode === 'agent' && !P.submitted_at ? btn('已送件', 'polMark', { id: x.id, w: 'sub' }, 'sec') : ''}${btn('請他重傳', 'polMark', { id: x.id, w: 'redo' }, 'gray')}</div>
        ${isAdmin ? `<label class="photobtn" style="margin-top:8px">＋ 上傳代辦拿到的良民證照片<input type="file" accept="image/*" hidden data-polup="${x.id}"></label>` : ''}` : ''}`;
    const nextLabel = { trial: '實作完成（沒經驗的下一關訓練；有經驗的直接試派案）', training: '訓練完成，下一關：試派案（開始可派工）', probation: '試派案沒問題，轉正式夥伴' }[x.status];
    return sheet(`招募：${esc(x.name)}・${IV_ST[x.status]}`, `<dl class="kv"><dt>應徵</dt><dd>${esc(x.role || '服務人員')}</dd><dt>電話</dt><dd>${esc(x.phone || '—')}</dd><dt>地區</dt><dd>${esc(x.regions || '—')}</dd><dt>面試官</dt><dd>${esc(x.result_by || '—')}</dd><dt>評語</dt><dd>${esc(x.note || '—')}</dd></dl>`
      + polHtml + pfHtml + `<div class="btns">${nextLabel ? btn(nextLabel, 'rcNext', { id: x.id, confirm: nextLabel + '？' }) : ''}${btn('不錄取', 'ivScreen', { id: x.id, ok: 0, confirm: `${x.name} 不錄取？會改成暫停，婉拒文案由店長確認後才傳。` }, 'gray')}</div>`);
  }
  if (['rejected', 'reject_pending'].includes(x.status)) return sheet(`應徵：${esc(x.name)}・${IV_ST[x.status]}`, `<dl class="kv"><dt>電話</dt><dd>${esc(x.phone || '—')}</dd><dt>婉拒</dt><dd>${esc(x.result_by || '—')}</dd></dl>${appHtml}${pfHtml}
    <div class="btns">${btn('按錯了，改回待看資料', 'ivUndoRej', { id: x.id, confirm: `把 ${x.name} 改回「待看資料」？（已傳出的婉拒訊息收不回，改回後要通過請再跟他說明）` })}</div>`);      // 婉拒按錯要能改
  if (x.status === 'screening') return sheet(`應徵資料：${esc(x.name)}`, `${warn}<dl class="kv"><dt>電話</dt><dd>${esc(x.phone || '—')}</dd><dt>地區</dt><dd>${esc(x.regions || '—')}</dd><dt>想做</dt><dd>${esc(x.wskills || '—')}</dd></dl>
    ${appHtml}${pfHtml}${ivPick(x, app)}<div class="btns">${moreBtn}${btn('讓他自己在 LINE 選時間', 'ivScreen', { id: x.id, ok: 1, sp: 1 }, 'sec')}${btn('直接通過（免面試，例：老夥伴回鍋）', 'ivSkip', { id: x.id, confirm: `${x.name} 不用面試、直接通過？下一關會照職缺和經驗自動決定（服務人員免良民證、有經驗免訓練）。` }, 'sec')}${btn('不適合，婉拒', 'ivScreen', { id: x.id, ok: 0, confirm: `婉拒 ${x.name}？婉拒文案由店長確認過才會自動傳給他。` }, 'gray')}</div>`);
  sheet(`面試：${esc(x.name)}`, `<dl class="kv"><dt>電話</dt><dd>${esc(x.phone || '—')}</dd><dt>時間</dt><dd>${x.slot ? ivWhen(x.slot) : (x.want ? '想約：' + esc(x.want) : '還沒約')}　${esc(x.mode || '')}</dd>
    <dt>區域</dt><dd>${esc(x.regions || '—')}</dd><dt>可排</dt><dd>${esc(x.availability || '—')}</dd>${appHtml ? '' : `<dt>應徵填的</dt><dd class="small">${esc(x.wnotes || '—')}</dd>`}</dl>
    ${appHtml ? `<details><summary class="small">看完整應徵資料</summary>${appHtml}</details>` : ''}${pfHtml}
    ${x.status === 'proposed' ? `<h3>他選的時段（挑一個）</h3><div class="btns">${JSON.parse(x.opts || '[]').map(t => btn('選 ' + ivWhen(t), 'ivConf', { id: x.id, t: t.slice(0, 16) })).join('')}</div>` : ''}
    ${['invited', 'proposed', 'need_time', 'no_show'].includes(x.status) ? ivPick(x, app, 'ivSlot') : ''}
    ${['invited', 'proposed', 'scheduled', 'need_time', 'no_show'].includes(x.status) ? `<div class="btns">${btn('請他重新選時間（LINE 再傳一次面試時段）', 'ivReinvite', { id: x.id }, 'gray')}</div>` : ''}
    <h3>評分</h3>${IV_ITEMS.map(([k, t]) => `<label for="iv-${k}">${t}</label><select id="iv-${k}" class="ivsc" data-k="${k}">${opt(sc[k])}</select>`).join('')}
    <label>會做的項目</label><div class="chips">${kinds.map(k => `<label><input type="checkbox" class="ivsk" value="${k}"${have.includes(k.slice(0, 2)) ? ' checked' : ''}> ${k}</label>`).join('')}</div>
    <label for="ivNote">備註（給之後派工參考）</label><textarea id="ivNote" rows="3">${esc(x.note || '')}</textarea>
    <div class="btns">${btn('面試通過', 'ivDecide', { id: x.id, r: 'hired', confirm: `${x.name} 面試通過？下一關照職缺和經驗自動決定（陪玩員才要良民證；有經驗的免訓練）。` })}${btn('直接通過（免面試）', 'ivSkip', { id: x.id, confirm: `${x.name} 不用面試、直接通過（例：老夥伴回鍋）？` }, 'sec')}${btn('再考慮', 'ivDecide', { id: x.id, r: 'hold' }, 'sec')}${btn('不錄取', 'ivDecide', { id: x.id, r: 'rejected', confirm: `${x.name} 不錄取？會改成暫停（不會派工）。` }, 'gray')}${btn('沒出現', 'ivDecide', { id: x.id, r: 'no_show' }, 'gray')}</div>`);
};
ACT.ivReinvite = async d => { if (!confirm('請他重新選面試時間？系統會用 LINE 再傳一次可選的時段。')) return; const r = await api('/api/interview/reinvite', { id: d.id }); closeSheet(); await refresh(); return r.message; };      // 跟 LINE 同一個
ACT.ivSkip = async d => { const r = await api('/api/interview/decide', { id: d.id, result: 'hired', scores: {}, skills: '', note: '免面試（直接通過）' }); closeSheet(); await refresh(); return '已直接通過：' + r.result; };
ACT.ivMore = async d => { const r = await api('/api/interview/more', { id: d.id }); closeSheet(); await refresh(); return r.message; };
ACT.rcNext = async d => { const r = await api('/api/recruit/advance', { id: d.id }); closeSheet(); await refresh(); return r.message; };
ACT.polMark = async d => { const r = await api('/api/recruit/police', { id: d.id, what: d.w }); closeSheet(); await refresh(); return r.message; };
document.addEventListener('change', async e => {            // 管理員上傳代辦拿到的良民證
  const inp = e.target.closest('input[data-polup]'); if (!inp || !inp.files[0]) return;
  try { await api('/api/recruit/upload', { id: inp.dataset.polup, content: await shrink(inp.files[0]) }); toast('已存進良民證（只有管理員看得到）'); closeSheet(); await refresh(); } catch (x) { toast(x.message, true); }
});
ACT.ivScreen = async d => { const r = await api('/api/interview/screen', { id: d.id, ok: !!+d.ok, self_pick: !!+d.sp }); closeSheet(); await refresh(); return r.message; };
/* 電話面試時間由我們選 → 建議時段一點就排定（LINE 通知他），或自己填時間 */
const ivPick = (x, app, act = 'ivBook') => `<h3>電話面試時間（我們選，系統會 LINE 通知他）</h3>${app.iv_time ? `<p class="small">他填的方便時間：${esc(app.iv_time)}</p>` : ''}
  <div class="btns">${(S.iv_slots || []).slice(0, 9).map(t => btn(ivWhen(t), act, { id: x.id, t })).join('')}</div>
  <label for="ivSlot">或自己填時間</label><input id="ivSlot" type="datetime-local"><div class="btns">${btn('排定這個時間', act, { id: x.id })}</div>`;
ACT.ivUndoRej = async d => { const r = await api('/api/interview/undo-reject', { id: d.id }); closeSheet(); await refresh(); return r.message; };
ACT.ivBook = async d => { const v = d.t || $('#ivSlot').value; if (!v) throw new Error('請選或填一個時間'); const r = await api('/api/interview/screen', { id: d.id, ok: true, slot: v }); closeSheet(); await refresh(); return r.message; };
ACT.ivConf = async d => { const r = await api('/api/interview/slot', { id: d.id, slot: d.t }); closeSheet(); await refresh(); return r.message; };      // 挑應徵者選的時段
ACT.ivSlot = async d => { const v = d.t || $('#ivSlot').value; if (!v) throw new Error('請填時間'); const r = await api('/api/interview/slot', { id: d.id, slot: v }); closeSheet(); await refresh(); return r.message.split('\n')[0]; };
ACT.ivDecide = async d => {
  const scores = {}; $$('.ivsc').forEach(s => { if (s.value) scores[s.dataset.k] = +s.value; });
  const r = await api('/api/interview/decide', { id: d.id, result: d.r, scores, skills: $$('.ivsk:checked').map(x => x.value).join('、'), note: $('#ivNote').value });
  closeSheet(); await refresh(); return '已記錄：' + r.result;
};
