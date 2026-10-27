'use strict';
/* LINE 配對（2026-10-14 負責人）：偵測到還沒配對的聊天室（1 對 1、群組、歷史對話、證據）都列出來，管理層可以自己配對；
   業主／夥伴這邊也能從「全部還沒配對的 LINE」裡挑，不是只有系統猜的那幾個；配錯可以解除。
   「配對的東西太亂了，全部請店長上去一個一個確認，不要再推撥」→ 群組不再推配對卡，這頁預設「一個一個來」。 */
let PR = null, PR_LOADING = false, PR_I = 0;
async function prLoad() {
  if (PR_LOADING) return; PR_LOADING = true;
  try { PR = await api('/api/pairing'); } catch (e) { PR = { error: e.message }; } finally { PR_LOADING = false; }
  if (VIEW === 'pairing') route(true);
}
const PR_BRAND = { demoshop: '示範服務店', bizline: '企業線', homeline: '生活線' };
function prPickOpts(acc, sel) {          // 下拉：同品牌還沒對上 LINE 的客戶＋（示範服務店）夥伴
  const cs = (PR.pick_c || []).filter(x => x.acc === acc), ws = PR.pick_w || [];      // 夥伴不分品牌，三個官方 LINE 都能配夥伴
  return `<option value="">— 選客戶或夥伴（全部還沒對上 LINE 的）—</option>`
    + (cs.length ? `<optgroup label="客戶（${cs.length}）">${cs.map(x => `<option value="c:${esc(x.id)}"${sel === 'c:' + x.id ? ' selected' : ''}>${esc(x.name)}${x.tail ? '（…' + esc(x.tail) + '）' : ''}</option>`).join('')}</optgroup>` : '')
    + (ws.length ? `<optgroup label="夥伴（${ws.length}）">${ws.map(x => `<option value="w:${esc(x.id)}">${esc(x.name)}${x.tail ? '（…' + esc(x.tail) + '）' : ''}</option>`).join('')}</optgroup>` : '');
}
function prLineOpts(acc) {               // 下拉：同品牌全部還沒配對的 LINE
  return `<option value="">— 選 LINE（全部還沒配對的）—</option>` + (PR.lines || []).filter(l => l.acc === acc && l.skip !== 'no')
    .map(l => `<option value="${esc(l.uid)}">${esc(l.name)}　${esc(l.last_seen.slice(5, 10))}${l.n1 ? '・' + l.n1 + ' 則' : ''}</option>`).join('');
}
const prToday = () => new Date().toLocaleDateString('sv-SE');
const prLater = l => !!l.skip && l.skip !== 'no' && l.skip > prToday();      // 按過「先跳過 7 天」、還沒到期
const prBest = l => Math.max(0, ...l.cands.map(x => x.score));
function prQueue() {                     // 一個一個來的順序：系統有比對到的先（分數高的先），其他照最後訊息；先跳過的不列
  return (PR.lines || []).filter(l => l.skip !== 'no' && !prLater(l) && !(l.silent && !l.cands.length))      // 只加好友、沒講過話、沒證據的不排進來（講話後自動出現）
    .sort((a, b) => (prBest(b) - prBest(a)) || (b.last_seen > a.last_seen ? 1 : b.last_seen < a.last_seen ? -1 : 0));
}
function prCard(l, one) {                // 一個聊天室：對話、群組、歷史、系統比對到的人、下拉自己選
  return `<div class="card" style="margin-bottom:12px${one ? ';border:2px solid #0A84FF' : ''}"><div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:6px">
      <div><b style="font-size:${one ? 20 : 17}px">${esc(l.name)}</b> <span class="pill">${PR_BRAND[l.acc] || l.acc}</span>${prLater(l) ? ' <span class="pill">先跳過</span>' : ''}</div><span class="small">最後訊息 ${esc(l.last_seen)}</span></div>
    <p class="small" style="margin:6px 0">1 對 1：${l.n1} 則${l.groups.length ? '　群組：' + l.groups.map(esc).join('、') : ''}${l.history ? `　歷史對話：「${esc(l.history)}」` : ''}</p>
    ${l.last.length ? `<div class="small" style="background:var(--bg2,#f5f5f7);border-radius:10px;padding:8px 10px;margin-bottom:8px">${l.last.map(t => '・' + esc(t)).join('<br>')}</div>` : ''}
    ${l.cands.length ? '<div class="small" style="margin-bottom:6px">系統比對到（請確認）：</div>' + l.cands.map(x => `<div class="row" style="gap:8px;margin-bottom:6px;flex-wrap:wrap"><span><b>${esc(x.label)}</b>　<span class="small">${esc(x.why)}（${x.score} 分）</span></span>${btn('是這位', 'prPair', { acc: l.acc, uid: l.uid, kind: x.kind, id: x.id }, x.score >= 80 ? '' : 'sec')}</div>`).join('')
      : '<p class="small" style="color:#8A5A00">系統找不到證據，認得的話從下面選。</p>'}
    <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:6px"><select id="prs-${esc(l.uid)}" style="flex:1;min-width:200px">${prPickOpts(l.acc)}</select>${btn('配對', 'prPick', { acc: l.acc, uid: l.uid })}</div>
    <div class="btns" style="margin-top:8px">${btn('不是客戶也不是夥伴', 'prSkip', { acc: l.acc, uid: l.uid, v: 'no' }, 'gray')}${btn('先跳過 7 天', 'prSkip', { acc: l.acc, uid: l.uid, v: 'later' }, 'gray')}</div></div>`;
}
function prBinds() {                     // 自己填了電話、對到既有客戶／夥伴，等確認是不是本人（以前推到群組，起只在這裡和首頁）
  const br = (typeof S !== 'undefined' && S && S.bind_requests) || [];
  if (!br.length) return '';
  return `<div class="card" style="margin-bottom:12px;border:2px solid #C47F00"><b>📝 自己填了電話、等確認是不是本人（${br.length}）</b>
    <p class="small" style="margin:4px 0 8px">對方填的電話對到既有客戶／夥伴，系統先不綁（避免冒用）。</p>`
    + br.map(r => `<div class="row" style="gap:8px;flex-wrap:wrap;margin-bottom:8px"><span style="flex:1;min-width:200px"><b>${esc(r.name || '—')}</b> ${esc(r.phone || '')} → ${r.kind === 'worker' ? '夥伴' : '客戶'} <b>${esc(r.target || '—')}</b>　<span class="small">LINE：${esc(r.line_name || '—')}・${PR_BRAND[r.account] || esc(r.account || '')}</span></span>${btn('是本人，綁定', 'prBind', { id: r.id, ok: 1 })}${btn('不是本人', 'prBind', { id: r.id, ok: 0 }, 'gray')}</div>`).join('')
    + '</div>';
}
PAGES.pairing = () => {
  if (!PR) { prLoad(); return phead('LINE 配對', '誰是誰。', '') + '<div class="empty">比對中（表單、對話、歷史對話、群組）…</div>'; }
  if (PR.error) return phead('LINE 配對', '誰是誰。', '') + `<div class="banner warn">${esc(PR.error)}</div>`;
  const mode = SEG.pr || 'one', lines = (PR.lines || []).filter(l => l.skip !== 'no'), q = prQueue();
  let h = phead('LINE 配對', '誰是誰。', `系統看得到的 LINE 聊天室（三個品牌、1 對 1 和群組）都在這裡。系統比對表單、對話裡的電話和地址、${PR.history_threads} 個歷史對話、群組發言，列出可能是誰，由店長（或管理層）一個一個確認；群組不再推播配對卡。配對後會傳歡迎訊息給對方，之後提醒、派工、完成照片都會直接傳。`,
    btn('重新比對', 'prReload', {}, 'sec'));
  h += prBinds();
  const nSilent = (PR.lines || []).filter(l => l.skip !== 'no' && l.silent && !l.cands.length).length;
  if (nSilent) h += `<p class="small" style="margin:0 0 8px">另有 ${nSilent} 個只加了好友、從沒講過話，系統沒證據可以比對，先不用處理（對方一講話就會自動排進來）。在「全部列表」看得到。</p>`;
  h += segctl('pr', [['one', '一個一個來', q.length], ['lines', '全部列表', lines.length], ['need', '依人找', (PR.need_c || []).length + (PR.need_w || []).length], ['paired', '已配對', (PR.paired || []).length]], mode);
  if (mode === 'one') {
    if (!q.length) return h + `<div class="empty">都確認完了 🎉${lines.length ? `（另有 ${lines.length} 個按過「先跳過」的，在「全部列表」）` : ''}</div>`;
    PR_I = Math.min(Math.max(0, PR_I), q.length - 1);
    const l = q[PR_I];
    h += `<div class="row" style="justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin:4px 0 8px"><b>第 ${PR_I + 1} 個／共 ${q.length} 個</b>
      <span class="btns">${PR_I > 0 ? btn('‹ 上一個', 'prNav', { d: -1 }, 'gray') : ''}${PR_I < q.length - 1 ? btn('下一個（先不決定）›', 'prNav', { d: 1 }, 'gray') : ''}</span></div>
      <div style="height:6px;background:var(--bg2,#eee);border-radius:3px;margin-bottom:10px"><div style="height:6px;width:${Math.round((PR_I + 1) * 100 / q.length)}%;background:#0A84FF;border-radius:3px"></div></div>`;
    h += prCard(l, true);
    h += '<p class="small">按「是這位」「配對」「不是客戶也不是夥伴」「先跳過 7 天」後，自動換下一個。</p>';
  } else if (mode === 'lines') {
    h += lines.length ? lines.map(l => prCard(l, false)).join('') : '<div class="empty">都配對好了 🎉</div>';
  } else if (mode === 'need') {
    const one = (kind, x) => {
      const acc = kind === 'c' ? ((PR.pick_c || []).find(p => p.id === x.id) || {}).acc || 'demoshop' : 'demoshop';
      const hint = (PR.lines || []).flatMap(l => l.cands.filter(c => c.kind === kind && c.id === x.id).map(c => `${esc(l.name)}（${esc(c.why)}）`));
      return `<div class="card" style="margin-bottom:12px"><b>${esc(x.name)}</b> <span class="pill">${kind === 'c' ? '業主・' + (PR_BRAND[acc] || acc) : '夥伴'}</span><p class="small" style="margin:6px 0">${esc(x.why)}</p>
        ${hint.length ? `<p class="small">系統比對到：${hint.join('、')}</p>` : '<p class="small" style="color:#8A5A00">系統找不到證據：從全部還沒配對的 LINE 裡選，或他可能還沒加官方 LINE。</p>'}
        <div class="row" style="gap:8px;flex-wrap:wrap"><select id="prl-${esc(x.id)}" style="flex:1;min-width:200px">${prLineOpts(acc)}</select>${btn('配對', 'prPickLine', { acc, kind, id: x.id })}${kind === 'c' ? btn('他沒加官方 LINE', 'pcfNone', { id: x.id }, 'gray') : btn('他沒加官方 LINE（複製綁定訊息）', 'pwfNone', { id: x.id }, 'gray')}</div></div>`;
    };
    h += ((PR.need_c || []).map(x => one('c', x)).join('') + (PR.need_w || []).map(x => one('w', x)).join('')) || '<div class="empty">最近有事要傳的業主、夥伴都對上 LINE 了 🎉</div>';
  } else {
    h += '<div class="card">' + tbl(['LINE 名稱', '品牌', '對到', ''], (PR.paired || []).map(p => `<tr>${td('LINE 名稱', esc(p.name))}${td('品牌', PR_BRAND[p.acc] || p.acc)}${td('對到', (p.kind === 'c' ? '客戶：' : '夥伴：') + esc(p.who))}${td('', btn('解除', 'prUnpair', { acc: p.acc, uid: p.uid }, 'gray'))}</tr>`)) + '</div>';
  }
  return h;
};
ACT.prReload = async () => { PR = null; route(true); };
ACT.prNav = d => { PR_I += +d.d || 0; route(true); };
async function prDo(path, body, ask) {
  if (ask && !confirm(ask)) return;
  const r = await api(path, body); PR = null; await refresh(); return r.message || '已更新';      // refresh：首頁「還剩幾個」、綁定待確認一起更新
}
ACT.prPair = d => prDo('/api/pairing/pair', { acc: d.acc, uid: d.uid, kind: d.kind, id: d.id }, '配對後會傳歡迎訊息給這個 LINE，確定是他嗎？');
ACT.prPick = d => {
  const v = ($('#prs-' + CSS.escape(d.uid)) || {}).value || ''; if (!v) return '請先從下拉選客戶或夥伴';
  const [kind, id] = v.split(':'); return prDo('/api/pairing/pair', { acc: d.acc, uid: d.uid, kind, id }, '配對後會傳歡迎訊息給這個 LINE，確定是他嗎？');
};
ACT.prPickLine = d => {
  const uid = ($('#prl-' + CSS.escape(d.id)) || {}).value || ''; if (!uid) return '請先從下拉選 LINE';
  return prDo('/api/pairing/pair', { acc: d.acc, uid, kind: d.kind, id: d.id }, '配對後會傳歡迎訊息給這個 LINE，確定是他嗎？');
};
ACT.prSkip = d => prDo('/api/pairing/skip', { acc: d.acc, uid: d.uid, v: d.v === 'no' ? 'no' : 'later' });
ACT.pwfNone = async d => { await api('/api/pairing/none-worker', { id: d.id }); PR = null; await refresh(); return ACT.wBindMsg({ id: d.id }); };      // 跟 LINE「他沒加官方 LINE」同一個
ACT.prUnpair = d => prDo('/api/pairing/unpair', { acc: d.acc, uid: d.uid }, '解除後提醒、完成照片就不會再傳給這個 LINE（不會通知對方），確定？');
ACT.prBind = async d => {
  if (+d.ok && !confirm('確認是本人？綁定後系統會傳「綁定完成」給他。')) return;
  const r = await api('/api/bind-request/decide', { id: d.id, ok: !!+d.ok }); PR = null; await refresh(); return r.message;
};
