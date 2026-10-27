'use strict';
/* 待整理區＋回收桶：系統判斷不了的資料交給負責人點選決定。刪除先進回收桶（可還原），回收桶裡才能永久刪除。 */
let TRI = null;
const ENT = { customers: '客戶', workers: '夥伴', orders: '工單', inquiries: '預約需求' };
async function loadTri() { TRI = await api('/api/triage'); }

PAGES.triage = () => {
  if (!TRI) { loadTri().then(() => route(true)).catch(e => toast(e.message, true)); return phead('待整理', '載入中…', ''); }
  const mode = SEG.tri || 'open';
  let h = phead('待整理', '這些交給你決定。', '系統和同事拿不準的客戶、夥伴、班表都放在這裡。可以一筆一筆點，也可以勾選多筆一起處理。刪除會先放進回收桶，隨時可以還原。');
  h += segctl('tri', [['open', '待決定', TRI.items.length], ['trash', '回收桶', TRI.trash.length], ['log', '處理紀錄']], mode);
  if (mode === 'open') {
    if (!TRI.items.length) return h + '<div class="banner ok">✓ 全部處理完了。</div>';
    const groups = {};
    for (const it of TRI.items) { const k = it.grp + '（' + ENT[it.entity] + '）'; (groups[k] = groups[k] || []).push(it); }
    Object.entries(groups).forEach(([g, list], gi) => {
      const ent = list[0].entity, moves = list[0].moves;
      h += `<details class="fold" ${gi === 0 ? 'open' : ''} data-tour="tri-group"><summary>${esc(g.replace(/^\d+\.\s*/, ''))}<small>${list.length} 筆</small></summary><div class="in">
        <p class="small">${esc(list[0].suggestion.startsWith('merge:') ? '名字很像。是同一位就按「合併」，資料、服務紀錄都會併在一起。' : list[0].suggestion)}</p>
        <div class="btns" style="margin:4px 0 10px"><label class="chk" style="margin:0;font-size:15px"><input type="checkbox" data-act="triAll" data-g="${gi}"> 全選</label>
          ${btn('勾選的保留', 'triBulk', { g: gi, a: 'keep' }, 'gray')}<select class="trimove" data-g="${gi}" aria-label="移到">${moves.map(([k, t]) => `<option value="${k}">移到：${t}</option>`).join('')}</select>${btn('勾選的移過去', 'triBulk', { g: gi, a: 'move' }, 'sec')}${btn('勾選的刪除', 'triBulk', { g: gi, a: 'trash', confirm: '勾選的資料會移到回收桶（可以還原），確定？' }, 'red')}</div>`;
      h += list.map(it => {
        const merge = it.suggestion.startsWith('merge:') ? it.suggestion.slice(6) : '';
        const target = merge && (it.entity === 'customers' ? IDX.customers : IDX.workers)[merge];
        return `<div class="item"><input type="checkbox" class="trisel" data-g="${gi}" value="${it.id}" aria-label="選取" style="width:22px;height:22px;flex:none">
          <div class="t ${it.entity === 'orders' ? 'click' : ''}" ${it.entity === 'orders' ? `data-act="order" data-id="${it.ref_id}"` : it.entity === 'customers' && IDX.customers[it.ref_id] ? `data-act="customer" data-id="${it.ref_id}"` : it.entity === 'workers' && IDX.workers[it.ref_id] ? `data-act="worker" data-id="${it.ref_id}"` : ''}>
          <div class="n">${esc(it.label)} <span class="small">${ENT[it.entity]}</span></div><div class="m">${esc(it.reason)}</div><div class="m">${esc(it.info)}</div></div>
          <div class="a">${target ? btn('合併到「' + target.name + '」', 'triDo', { id: it.id, a: 'merge', target: merge }) : ''}${btn('修改', 'triEdit', { id: it.id }, 'sec')}${btn('保留', 'triDo', { id: it.id, a: 'keep' }, 'gray')}${btn('移到…', 'triMove', { id: it.id })}${['customers', 'workers'].includes(it.entity) ? btn('合併到…', 'triMerge', { id: it.id }, 'gray') : ''}${btn('刪除', 'triDo', { id: it.id, a: 'trash', confirm: '移到回收桶（可以還原）？' }, 'red')}</div></div>`;
      }).join('') + '</div></details>';
    });
  } else if (mode === 'trash') {
    h += '<div class="banner blue">回收桶裡的資料已經從系統拿掉，但完整保留，按「還原」就回到原本的樣子（含地點、別名、歷史服務紀錄）。「永久刪除」之後就救不回來了。</div>';
    h += '<div class="card">' + tbl(['資料', '刪除時間', '誰刪的', ''], TRI.trash.map(t => `<tr>${td('資料', esc(t.label) + `<span class="s">${esc(t.reason)}（含 ${t.rows} 筆關聯資料）</span>`)}${td('時間', esc(loc(t.deleted_at)))}${td('誰', esc(t.deleted_by))}${td('', btn('還原', 'trRestore', { id: t.id }, 'sec') + (S.role === 'admin' ? btn('永久刪除', 'trPurge', { id: t.id, confirm: `永久刪除「${t.label}」？刪了就救不回來。` }, 'red') : ''))}</tr>`)) + '</div>';
    if (TRI.trash.length && S.role === 'admin') h += `<div class="btns">${btn('回收桶全部永久刪除', 'trPurgeAll', { confirm: `回收桶 ${TRI.trash.length} 筆全部永久刪除？刪了就救不回來。` }, 'red')}</div>`;
  } else {
    h += '<p class="small">按「保留」的資料原封不動留在系統裡，只是從待決定拿掉、記在這裡。點資料可以打開；按錯了按「放回待決定」。</p>';
    h += '<div class="card">' + tbl(['時間', '資料', '決定', '誰', ''], TRI.history.map(t => `<tr>${td('時間', esc(loc(t.decided_at)))}${td('資料', `<a data-act="triOpen" data-id="${t.id}">${ENT[t.entity]}：${esc(t.label || '')}</a><span class="s">${esc(t.reason.slice(0, 40))}</span>`)}${td('決定', esc(t.decision))}${td('誰', esc(t.decided_by))}${td('', btn('修改', 'triEdit', { id: t.id }, 'gray') + btn('放回待決定', 'triDo', { id: t.id, a: 'reopen' }, 'gray'))}</tr>`)) + '</div>';
  }
  return h;
};
async function triRun(body) {
  const r = await api('/api/triage/decide', body); await loadTri(); await refresh();
  if (r.errors.length) sheet('有些沒辦法處理', r.errors.map(e => `<div class="item"><div class="t"><div class="m">${esc(e.error)}</div></div></div>`).join(''));
  return `完成 ${r.done} 筆`;
}
ACT.triDo = d => triRun({ id: d.id, action: d.a, target: d.target });
ACT.triAll = (d, el) => $$(`.trisel[data-g="${d.g}"]`).forEach(x => x.checked = el.checked);
ACT.triBulk = d => {
  const ids = $$(`.trisel[data-g="${d.g}"]:checked`).map(x => x.value);
  if (!ids.length) throw new Error('請先勾選');
  return triRun({ ids, action: d.a, to: $(`.trimove[data-g="${d.g}"]`).value });
};
const triItem = id => { const it = (TRI.items || []).find(x => x.id === id) || (TRI.history || []).find(x => x.id === id); if (!it) { route(true); throw new Error('這筆已經處理過了，畫面已更新'); } return it; };
ACT.triOpen = d => { const it = triItem(d.id); return ({ orders: ACT.order, customers: ACT.customer, workers: ACT.worker, inquiries: ACT.inquiry }[it.entity] || (() => {}))({ id: it.ref_id }); };
ACT.triEdit = d => {      // 待整理「修改」：補資料（管理人員都能改）；工單補完客戶就從待決定拿掉
  const it = triItem(d.id);
  if (it.entity === 'customers') { if (!IDX.customers[it.ref_id]) throw new Error('找不到這位客戶（可能已合併或在回收桶）'); return ACT.editCustomer({ id: it.ref_id }); }
  if (it.entity === 'workers') return ACT.editWorker({ id: it.ref_id });
  if (it.entity === 'inquiries') return ACT.inquiry({ id: it.ref_id });
  const o = IDX.orders[it.ref_id]; if (!o) throw new Error('找不到這筆工單（可能已在回收桶）');
  const sites = S.sites.slice().sort((a, b) => (cust(a.customer_id).name || '').localeCompare(cust(b.customer_id).name || ''));
  sheet('補資料', `<p class="small">${esc(it.label || o.title)}<br>${esc(it.info || it.reason || '')}</p>`
    + pickFld('site_id', '客戶／案場', o.site_id, siteOpts(), { hint: '上面打字就會篩選；還沒有地點的客戶也能選（用客戶名建地點）。' })      // 
    + fld('title', '服務名稱', o.title) + fld('notes', '備註', o.notes, 'textarea', { rows: 3 })
    + (o.status === 'archived' ? '<p class="small">這是歷史紀錄：只能補客戶／案場、名稱、備註；時間、金額、人員不能改。</p>' : ''),
    async f => {
      if (!f.site_id) throw new Error('請選客戶／案場');
      f.site_id = await ensureSite(f.site_id);
      await api('/api/save/orders', { id: o.id, version: o.version, site_id: f.site_id, title: f.title || site(f.site_id).name || o.title, notes: f.notes || '' });
      if (it.status !== 'done') await api('/api/triage/decide', { id: it.id, action: 'fixed', note: cust(site(f.site_id).customer_id).name || '' });
      await loadTri(); await refresh(); return '已補好資料' + (it.status !== 'done' ? '，已從待決定拿掉' : '');
    }, '儲存');
};
ACT.triMove = d => {
  const it = triItem(d.id);
  sheet('移到…', `<p>${esc(it.label)}</p>` + fld('to', '移到哪裡', it.moves[0][0], 'select', { options: it.moves }) + (it.entity === 'orders' ? fld('note', '原因（取消時記錄）', '') : ''),
    async f => { const r = await triRun({ id: it.id, action: 'move', to: f.to, note: f.note }); return r; }, '移過去');
};
ACT.triMerge = d => {
  const it = triItem(d.id), list = (it.entity === 'customers' ? S.customers : S.workers).filter(x => x.id !== it.ref_id).sort((a, b) => a.name.localeCompare(b.name));
  sheet('合併到…', `<p class="small">「${esc(it.label)}」的資料、服務紀錄、別名會併到你選的那一位，然後這筆消失（名字會留成別名，之後搜得到）。</p>` + fld('target', '合併到', '', 'select', { options: optsOf(list) }),
    async f => { if (!f.target) throw new Error('請選一位'); return triRun({ id: it.id, action: 'merge', target: f.target }); }, '合併');
};
ACT.trRestore = async d => { const r = await api('/api/trash/restore', { id: d.id }); await loadTri(); await refresh(); return '已還原：' + r.restored; };
ACT.trPurge = async d => { await api('/api/trash/purge', { id: d.id }); await loadTri(); route(true); return '已永久刪除'; };
ACT.trPurgeAll = async () => { for (const t of TRI.trash) await api('/api/trash/purge', { id: t.id }); await loadTri(); route(true); return '回收桶已清空'; };
ACT.toTriage = async d => { await api('/api/triage/flag', { entity: d.e, ref_id: d.id, grp: '0. 同事轉交', reason: prompt('為什麼要交給負責人決定？（可空白）') || `${ME.name} 轉交處理` }); closeSheet(); TRI = null; await refresh(); return '已移到待整理'; };
