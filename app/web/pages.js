'use strict';
/* 管理端頁面：今天、排班、派工、客戶、夥伴、更多（預約需求、待辦、對話、資料核對、設定）＋工單詳情與 LINE 文字範本 */

/* ═════════ LINE 文字範本（只產生文字，人複製後自己貼）═════════ */
const TXT = {
  when: o => `${md(o.date)} ${o.start || ''}${o.end ? '–' + o.end : ''}`,
  names: o => crew(o.id).map(a => wname(a.worker_id)).join('、') || '（安排中）',
  dispatch(o, opt = {}) {
    const s = site(o.site_id), h = mins(o.start, o.end), n = o.required_workers - accepted(o);
    const lines = [`${s.name || o.title}｜${s.service_type || '服務'}${n > 0 ? '（缺額）' : ''}`,
      `📅 ${md(o.date).replace('(', '(')}${o.start}-${o.end}*${Math.max(n, 1)}位`, h ? `🕒 預計${Math.round(h / 6) / 10}小時` : '',
      s.area ? `🏠 臺北市${s.area}` : '', s.address ? `📍 ${s.address.replace(/^\d{3,5}/, '').replace(/^臺北市/, '')}` : ''];
    const items = (s.checklist || '').split(/\n+/).map(x => x.replace(/^[\s□☐\-•\d.、)）]+/, '').trim()).filter(Boolean);
    const nums = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'];
    if (items.length) lines.push('', ...items.slice(0, 10).map((x, i) => nums[i] + ' ' + x));
    const notes = (o.dispatch_note || '📌 到達時報備群組並找到業主／管家').split(/\n+/).filter(Boolean).map(x => x.startsWith('📌') ? x : '📌 ' + x);
    lines.push('', ...notes);
    if (opt.pay && IDX.workers) { const r = crew(o.id, ['accepted', 'invited'])[0]?.rate; if (r) lines.push(`💰 時薪 ${r}`); }
    if ((s.navigation || '').startsWith('http')) lines.push('', '地圖連結：', s.navigation);
    lines.push('', '要接的夥伴請在示範服務店官方 LINE 的派工卡按「我要接單」。');
    return lines.filter((x, i, a) => x !== '' || a[i - 1] !== '').join('\n').trim();
  },
  shortage(list) {
    const groups = {};
    for (const o of list) { const s = site(o.site_id); const k = (s.service_type || '服務') + '｜' + (s.area ? '臺北市' + s.area : '') + (s.address ? s.address.replace(/^.*?[鄉鎮市區]/, '').replace(/\d+號.*$/, '') : ''); (groups[k] = groups[k] || []).push(o); }
    const nums = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'];
    return ['請問有時間可以安排嗎？🔥', '', ...Object.entries(groups).map(([k, os], i) => [nums[i] + k, ...os.map(o => `${md(o.date)} ${o.start}-${o.end} ${o.required_workers - accepted(o)}位`), ''].join('\n')),
      `報名：${location.origin}/#jobs`].join('\n').trim();
  },
  confirm(o) {
    const c = cust(o.customer_id), s = site(o.site_id);
    return `${c.line_name || c.name} 您好，這裡是示範服務店 😊\n跟您確認服務時間：\n📅 ${TXT.when(o)}\n📍 ${s.address || s.name}\n👤 服務人員：${TXT.names(o)}\n\n如需調整請直接回覆我們，謝謝您！`;
  },
  remindCustomer(o) {
    const c = cust(o.customer_id);
    return `${c.line_name || c.name} 您好，提醒您明天 ${TXT.when(o)} 示範服務店人員（${TXT.names(o)}）將到府服務。\n若需要開門、停車或有特別交代的地方，請先告訴我們，謝謝 🙏`;
  },
  remindWorker(o) {
    const s = site(o.site_id);
    return `明天的工作提醒 🧹\n📅 ${TXT.when(o)}\n🏠 ${s.name}\n📍 ${s.address}${(s.navigation || '').startsWith('http') ? '\n地圖：' + s.navigation : ''}${s.entry_notes ? '\n📌 ' + s.entry_notes : ''}${s.checklist ? '\n\n服務重點：\n' + s.checklist : ''}\n\n到場請在示範服務店官方 LINE「今天的工作」打卡＋拍服務前照片，做完拍完成照片，謝謝！`;
  },
  done(o) { const c = cust(o.customer_id); return `${c.line_name || c.name} 您好，今天（${md(o.date)}）的服務已完成 ✨\n完成照片已附上，麻煩您確認；有任何需要加強的地方請直接告訴我們，謝謝您！`; },
  care(o) { const c = cust(o.customer_id); return `${c.line_name || c.name} 您好，我是${brandName(c)} 😊\n想關心一下 ${md(o.date)} 的服務您還滿意嗎？有任何建議都歡迎告訴我們，我們會持續調整，謝謝您！`; }
};
ACT.copyTxt = d => { const o = IDX.orders[d.id]; return copy(TXT[d.kind](o), { dispatch: '派工文字', confirm: '客戶確認訊息', remindCustomer: '客戶提醒', remindWorker: '夥伴提醒', done: '完工通知', care: '關心訊息' }[d.kind]); };

/* ═════════ 今天 ═════════ */
ACT.payOk = async d => {
  const x = (S.payment_requests || []).find(r => r.id === d.id);
  if (!confirm(`確認已收到 ${x ? x.customer + ' ' + x.label + ' NT$ ' + (x.amount / 100).toLocaleString() : '這筆款項'}？（請先到網銀核對）`)) return;
  await api('/api/payment/confirm', { id: d.id }); await refresh(); return '已確認收款';
};
ACT.openQ = d => open(`/q/${d.t}`, '_blank', 'noopener');
ACT.bindDecide = async d => { if (+d.ok && !confirm('確認是本人？綁定後系統會傳「綁定完成」給他。')) return; const r = await api('/api/bind-request/decide', { id: d.id, ok: !!+d.ok }); await refresh(); return r.message; };      // 按完要從首頁消失
const BRAND_ID = c => { const ids = (S.brands || []).map(b => b.id); return ids.includes(c.line_account) ? c.line_account : ((S.brands || []).find(b => b.channel && b.channel === c.channel) || {}).id || 'demoshop'; };
const brandName = c => ((S.brands || []).find(b => b.id === BRAND_ID(c || {})) || {}).name || '示範服務店';
const brandPill = c => `<span class="pill ${BRAND_ID(c || {}) === 'demoshop' ? '' : 'warn'}">${esc(brandName(c))}</span>`;
function homeTodos() {
  const t = today(), tm = addDays(t, 1), week = addDays(t, 7), live = (S.orders || []).filter(o => !['cancelled', 'archived'].includes(o.status));
  const groups = [];
  const add = (key, title, hint, items, opts = {}) => items.length && groups.push({ key, title, hint, items, ...opts });
  add('short', '缺人（7 天內）', '還沒有足夠夥伴接單。按「挑人派工」勾選要發給誰（只有勾選的人看得到），系統會依區域、可排時間、服務紀錄排序建議人選。',
    live.filter(o => o.date >= t && o.date <= week && short(o)).sort(by), { red: true, act: o => btn('挑人派工', 'pickWorker', { id: o.id }) + btn('詳情', 'order', { id: o.id }, 'gray') });
  add('custconf', '等客戶確認', '人已到位，複製確認訊息貼給客戶；客戶回「好」之後按「客戶已確認」。',
    live.filter(o => ['review', 'draft'].includes(o.status) && o.date >= t && !o.customer_confirmed && !short(o)).sort(by), { act: o => btn('複製確認訊息', 'copyTxt', { id: o.id, kind: 'confirm' }, 'sec') + btn('客戶已確認', 'custOk', { id: o.id }) });
  add('ready', '可以排定', '人和客戶都確認了，按一下排定（會出現在日曆、夥伴看得到地址）。',
    live.filter(o => ['review', 'draft'].includes(o.status) && o.customer_confirmed && !short(o) && o.date >= t).sort(by), { blue: true, act: o => btn('排定', 'confirmOrder', { id: o.id }), bulk: 'confirm' });
  const BC = new Set((S.line_bound || {}).customers || []), boundC = id => BC.has(id), FS = new Set(S.finish_sent || []);
  const bindBtn = o => o.customer_id ? btn('綁定訊息', 'bindMsg', { id: o.customer_id }, 'sec') : '';
  add('remind', '明天的服務：業主沒綁 LINE', '系統每天 18:00 自動傳明天的提醒給業主和夥伴（夥伴沒綁會轉到派單管理群），有綁的不用你處理。這幾位業主還沒綁 LINE、系統傳不到：複製提醒文字＋綁定訊息傳給他，綁好之後全部自動。',
    live.filter(o => o.date === tm && o.status === 'confirmed' && !o.reminded_at && !boundC(o.customer_id)), { act: o => btn('客戶提醒', 'copyTxt', { id: o.id, kind: 'remindCustomer' }, 'sec') + bindBtn(o) + btn('已提醒', 'bulkOne', { id: o.id, action: 'reminded' }, 'gray') });
  add('finish', '待完工登記', '系統會自動問夥伴（服務結束 30 分鐘、之後每天早上，問 3 次沒回會請派單管理群打電話）。這裡也可以直接登記實際時間，這是月底結帳的依據。',
    live.filter(o => o.status === 'confirmed' && o.date < t).sort(by), { red: true, act: o => (o.field_report ? '<span class="st completed">夥伴已回報</span>' : '') + btn('完工登記', 'quickComplete', { id: o.id }) });
  add('accept', '等業主驗收：系統傳不到', '有綁 LINE 的業主：派單管理群審核完成照片後系統自動傳、隔天自動提醒。這裡只列系統做不到的：業主沒綁 LINE（傳綁定訊息，綁好自動補傳完成照片），或沒有完成照片的舊單（打電話確認後按「業主已確認」）。',
    live.filter(o => o.status === 'completed' && o.acceptance === 'pending' && o.date >= addDays(t, -40) && (!boundC(o.customer_id) || !FS.has(o.id))).sort(by),
    { act: o => (!boundC(o.customer_id) ? bindBtn(o) : '<span class="st pending">沒傳過完成照片</span>') + btn('主管代為驗收', 'acceptOk', { id: o.id }) });
  add('care', '完工後關心：業主沒綁 LINE', '有綁 LINE 的業主，系統在服務後 3 天自動關心，不用你處理。這些沒綁：傳綁定訊息（或複製關心訊息自己傳），處理完按「已關心」。',
    live.filter(o => o.status === 'completed' && !o.followed_up_at && o.date <= addDays(t, -2) && o.date >= addDays(t, -7) && !boundC(o.customer_id)), { green: true, act: o => bindBtn(o) + btn('複製關心訊息', 'copyTxt', { id: o.id, kind: 'care' }, 'gray') + btn('已關心', 'bulkOne', { id: o.id, action: 'followed-up' }, 'gray') });
  const fbLog = id => (S.dispatch_log || []).filter(l => l.order_id === id && ['customer_change', 'customer_leave_choice', 'care_reply'].includes(l.action) && (l.action !== 'care_reply' || l.detail === 'fix') && (l.action !== 'customer_leave_choice' || l.detail !== 'other'));
  const fbWhy = o => [o.acceptance === 'disputed' ? '業主要求加強' : '', o.rating && o.rating <= 3 ? `評分 ${'⭐'.repeat(o.rating)}` : '',
    fbLog(o.id).filter(l => !o.feedback_done || l.at > o.feedback_done).length ? '客戶想改時間／有建議' : ''].filter(Boolean).join('、');
  add('fb', '客戶回饋待處理', '客戶在 LINE 按了「需要改時間」「有地方需要加強」或給了低分。請在官方 LINE 關心客戶、處理完按「已處理」（要求加強的單處理完才能開帳）。',
    (S.orders || []).filter(o => o.date >= addDays(t, -40) && !['archived'].includes(o.status) && fbWhy(o) && (!o.feedback_done || fbLog(o.id).some(l => l.at > o.feedback_done))).sort(by),
    { red: true, act: o => `<span class="st pending">${esc(fbWhy(o))}</span>` + btn('詳情', 'order', { id: o.id }, 'gray') + btn('已處理', 'bulkOne', { id: o.id, action: 'feedback-done' }) });
  const pr = S.payment_requests || [], tday = today();        // 收款（只有看得到金額的人才有這份資料）
  const rep = pr.filter(x => x.status === 'reported');
  if (rep.length) groups.push({ key: 'payrep', title: '待確認收款', hint: '客戶回報已匯款。先到網銀核對入帳（末五碼），確認後按「確認收款」；開工前款項收齊，系統才會排定並把地址傳給夥伴。', items: rep, pay: true, red: true });
  const od = pr.filter(x => x.status === 'pending' && x.due && x.due < tday);
  if (od.length) groups.push({ key: 'payod', title: '逾期未付款', hint: '已過付款期限還沒收到。請聯絡客戶，或決定保留／取消這次服務。', items: od, pay: true, red: true });
  if (can('money')) {          // 盤點：不只看當月——之前月份完工沒開帳、逾期沒收的帳單也要在首頁
    const billed = new Set((S.invoice_lines || []).map(l => l.order_id)), m0 = t.slice(0, 7);
    add('unbilled', '之前月份完工、還沒開帳', '上個月以前已完工但還沒請款的服務。到「結帳」切到那個月份開帳。',
      (S.orders || []).filter(o => o.status === 'completed' && o.date.slice(0, 7) < m0 && !billed.has(o.id)).sort(by), { act: o => btn('去結帳', 'goBill', { m: o.date.slice(0, 7), c: o.customer_id || '' }, 'sec') });
    const odInv = (S.invoices || []).filter(i => i.status === 'issued' && i.balance > 0 && i.due_date && i.due_date < t);
    if (odInv.length) groups.push({ key: 'odinv', title: '帳單逾期未收（所有月份）', hint: '已開帳、過了付款期限還沒收齊。到「結帳」該月份複製催款訊息。', items: odInv, inv: true, red: true });
  }
  const br = S.bind_requests || [];
  if (br.length) groups.push({ key: 'bind', title: 'LINE 綁定待確認', hint: '電話對到既有資料，系統先不綁（避免冒用）。確認是本人就按「是本人」。不推到群組，只在這裡和「更多 › LINE 配對」。', items: br, bind: true, red: true });
  const RECRUIT = new Set((S.interviews || []).filter(i => ['screening', 'invited', 'scheduled', 'need_time', 'hold', 'check', 'trial', 'training'].includes(i.status)).map(i => i.worker_id));
  const nw = (S.workers || []).filter(w => w.status === 'review' && !RECRUIT.has(w.id));
  if (nw.length) groups.push({ key: 'newworker', title: '舊夥伴待找回（待核對）', hint: '舊資料裡的夥伴（從官方 LINE 應徵的在「夥伴招募」，不會在這裡）。打電話問還能不能做：可以的改「可派工」並請他加官方 LINE；LINE 管理選單「找回舊夥伴」也能一位一位點。', items: nw, worker: true, blue: true });
  const inq = (S.inquiries || []).filter(q => q.status === 'new');
  if (inq.length) groups.push({ key: 'inq', title: '新的預約需求', hint: '客戶從官方 LINE 填的表單。', items: inq, inquiry: true, red: true });
  const fix = live.filter(o => ['review', 'draft'].includes(o.status) && /待處理|待確認/.test(o.review_note) && o.date >= t);
  add('fix', '資料待補', '這些工單缺地址、時間不明確或撞期，補好就能排定。', fix.sort(by), { act: o => btn('補資料', 'editOrder', { id: o.id }, 'gray') });
  return groups;
}
const by = (a, b) => (a.date + a.start).localeCompare(b.date + b.start);
// 今天・即時狀況（首頁最上面）：每場一列＝時間地點、步驟點點（綠好了／橘等待／紅要注意／灰還沒到這步）、一句「現在最要注意的」
const LIVE_STAGE = { '已完工': 'completed', '已離場': 'completed', '服務中': 'confirmed', '待開工': 'confirmed', '開工了・還沒到場': 'short', '還沒排定': 'review' };
function liveBox(LV, tomN) {
  const cnt = k => LV.filter(x => x.stage === k).length, bad = LV.reduce((n, x) => n + (x.alerts || 0), 0);
  const sum = [['待開工', cnt('待開工') + cnt('還沒排定')], ['服務中', cnt('服務中') + cnt('開工了・還沒到場')], ['已完工', cnt('已完工') + cnt('已離場')]].filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join('・');
  let h = `<section class="today" id="livebox"><div class="thead"><div><div class="k">今天</div><div class="v">${LV.length} <small>場服務</small></div></div>
    <div class="tsum">${LV.length ? `<span>${esc(sum)}</span>` : ''}${bad ? `<span class="tbad">${bad} 個狀況要注意</span>` : LV.length ? '<span class="tgood">目前都正常</span>' : ''}<span class="small">明天 ${tomN} 場</span></div></div>`;
  if (!LV.length) return h + '<p class="hint" style="margin:8px 0 0">今天沒有服務。</p></section>';
  h += LV.map(x => `<div class="trow" data-act="order" data-id="${esc(x.id)}">
      <div class="twhen"><div class="tm">${esc(x.start)}–${esc(x.end)}</div><div class="tt">${esc(x.title)} <span class="small">${esc(x.area)}</span></div>${navBtn(site((IDX.orders[x.id] || {}).site_id), '🧭 導航', true)}</div>
      <div class="tbody"><div class="tsteps">${x.steps.map(s => `<span class="tstep ${esc(s.s)}" title="${esc(s.k + '：' + s.t)}"><i></i>${esc(s.k)}</span>`).join('')}</div>
        <div class="tfocus ${esc(x.focus.s)}"><b>${esc(x.focus.k)}</b>${esc(x.focus.t)}</div></div>
      <div class="tstage">${st(LIVE_STAGE[x.stage] || 'review', x.stage)}</div></div>`).join('');
  return h + '<p class="tlegend">綠＝好了　橘＝等待中　紅＝要注意　灰＝還沒到這一步・滑鼠移到點點上看細節，點一列打開工單</p></section>';
}
function ocard(o, opt) {          // opt.date：列表跨好幾天時顯示日期；map 傳進來的 index 不算
  const s = site(o.site_id), ws = crew(o.id).map(a => wname(a.worker_id)), dt = opt && opt.date;
  return `<div class="ocard s-${o.status}" data-act="order" data-id="${o.id}"><div class="top"><span class="tm">${dt ? `<span class="od">${md(o.date)}</span> ` : ''}${o.start || '--'}–${o.end || '--'}</span>${short(o) ? crewMeter(o) : st(o.status, '')}</div>
    <div class="ti">${esc(s.name || o.title)}</div><div class="m">${esc(s.area || '')} ${esc(ws.join('、') || '尚未派人')}${o.field_report ? '　· 夥伴已回報' : ''}</div>${['cancelled', 'archived', 'completed'].includes(o.status) ? '' : navBtn(s, '🧭 導航', true)}</div>`;
}
PAGES.home = () => {
  const t = today(), live = S.orders.filter(o => !['cancelled', 'archived'].includes(o.status));
  const todayO = live.filter(o => o.date === t).sort(by), tomO = live.filter(o => o.date === addDays(t, 1)).sort(by);
  const groups = homeTodos(), red = groups.filter(g => g.red).reduce((n, g) => n + g.items.length, 0);
  const unpaid = (S.invoices || []).filter(i => i.status === 'issued').reduce((n, i) => n + i.balance, 0);
  let h = phead(`${md(t)} · ${ME.name}`, '今天，照顧好每一個家。', '需要你處理的事都在這裡；處理完就會消失。', btn('新增工單', 'newOrder') + ' ' + btn('補服務工單', 'newRedo', {}, 'sec') + ' ' + btn('固定排程', 'recurring', {}, 'sec'));
  if (S.admin_group_fail) h += `<div class="banner warn" style="background:#FDECEA;color:#C62828">⚠️ 派單管理群收不到系統訊息（${esc(S.admin_group_fail)}）。可能是機器人被移出群組，請負責人檢查；恢復前請假、派員審核等卡片都送不到。</div>`;
  h += liveBox(S.today_live || [], tomO.length);       // 負責人 最上面就是今天每一場的即時狀況（通知一律在派單管理群，不再用手機推播）
  const RV = can('money') ? (S.staffq || []).filter(x => x.kind === 'rv') : [];      // 行銷審核在今天頁就看得到
  if (RV.length) h += `<section class="todo-group red"><h3><span class="dot"></span>要審核（行銷內容、群發公告）<span class="cnt">${RV.length}</span></h3><p class="hint">看全文、圖片、每則的平台和時間，直接在這裡核准或要修改；跟 LINE 卡片是同一件，一邊按了另一邊就顯示已處理。</p>`
    + RV.map(x => `<div class="item"><div class="t"><div class="n">${esc(x.title)}</div></div><div class="a">${btn('看全文＋圖、審核', 'rvOpen', { id: x.id })}</div></div>`).join('') + '</section>';
  if (S.quotes_pending) h += `<div class="banner warn" data-go="quotes" style="cursor:pointer">🧾 有 <b>${S.quotes_pending}</b> 張報價等你確認（客戶在等正式報價單）　前往確認 ›</div>`;
  const shortN = groups.find(g => g.key === 'short')?.items.length || 0;
  h += `<div class="grid"><div class="tile stat ${red ? 'warn' : 'good'}"><div class="k"><a data-act="homeJump" data-to=".todo-group">要處理 ›</a></div><div class="v">${groups.reduce((n, g) => n + g.items.length, 0)}</div><div class="s">${red ? `其中 ${red} 件較急` : '都處理好了'}</div></div>
    ${can('money') ? `<div class="tile stat"><div class="k">未收款</div><div class="v">${money(unpaid)}</div><div class="s" ><a data-act="goBill" data-m="${today().slice(0, 7)}" data-list="1">看未收帳單 ›</a></div></div>` : ''}
    <div class="tile stat ${shortN ? 'warn' : ''}"><div class="k">${shortN ? '<a data-go="dispatch">7 天內缺人 ›</a>' : '7 天內缺人'}</div><div class="v">${shortN}</div><div class="s">${shortN ? '系統會自動詢問夥伴' : '人都排好了'}</div></div></div>`;
  const nQ = asksLeft();      // 跟「管理群待處理」頁的數字一致
  if (nQ) h += `<div class="banner warn" data-go="asks" style="cursor:pointer">🗳 管理群待處理 <b>${nQ}</b> 件（群組裡要按的卡片，在中控台也能直接處理）　前往 ›</div>`;
  if (S.pairing_todo) h += `<div class="banner blue" data-go="pairing" style="cursor:pointer">🔗 LINE 配對：還有 <b>${S.pairing_todo}</b> 個聊天室不知道是誰（不在群組推播了），到「LINE 配對」一個一個確認 ›</div>`;
  if (S.triage_open) h += `<div class="banner blue" data-go="triage">🗂 待整理 ${S.triage_open} 筆：舊資料裡拿不準的客戶、夥伴、班表，交給負責人點選決定 ›</div>`;
  if (!groups.length) h += '<div class="banner ok">✓ 目前沒有待處理的事。</div>';
  for (const g of groups) {
    h += `<section class="todo-group ${g.red ? 'red' : g.blue ? 'blue' : g.green ? 'green' : ''}" data-tour="todo-${g.key}"><h3><span class="dot"></span>${g.title}<span class="cnt">${g.items.length}</span></h3><p class="hint">${g.hint}</p>`;
    if (g.bulk) h += `<div class="btns" style="margin:0 0 6px">${btn(`全部排定（${g.items.length}）`, 'bulk', { action: g.bulk, ids: g.items.map(o => o.id).join(','), confirm: `排定這 ${g.items.length} 張？夥伴會收到地址。` }, 'sec')}</div>`;
    const lim = (SEG.homeAll || {})[g.key] ? 999 : 12;      // 超過 12 筆按「顯示全部」原地展開
    h += g.items.slice(0, lim).map(x => g.inv
      ? `<div class="item"><div class="t"><div class="n">${esc(cust(x.customer_id).dn || '—')}　${esc(x.period)}　未收 ${money(x.balance)}</div><div class="m">付款期限 ${esc(x.due_date)}</div></div><div class="a">${btn('去結帳', 'goBill', { m: x.period.slice(0, 7), c: x.customer_id }, 'sec')}</div></div>`
      : g.pay
      ? `<div class="item"><div class="t"><div class="n">${esc(x.customer || '—')}　${esc(x.label)}　NT$ ${(x.amount / 100).toLocaleString()}</div><div class="m">期限 ${esc(x.due || '完工後')}${x.ref ? '　末五碼 ' + esc(x.ref) : ''}</div></div><div class="a">${btn('確認收款', 'payOk', { id: x.id })}${btn('改金額', 'payEdit', { id: x.id }, 'gray')}${x.token ? btn('報價單', 'openQ', { t: x.token }, 'gray') : ''}${x.order_id ? btn('工單', 'order', { id: x.order_id }, 'gray') : ''}</div></div>`
      : g.bind
      ? `<div class="item"><div class="t"><div class="n">${x.kind === 'worker' ? '夥伴' : '客戶'}・${esc(x.name)}　${esc(x.phone)}</div><div class="m">LINE「${esc(x.line_name || '—')}」→ 對到${x.kind === 'worker' ? '夥伴' : '客戶'}「${esc(x.target)}」· ${esc(x.created_at.slice(5, 16).replace('T', ' '))}</div></div><div class="a">${btn('是本人，綁定', 'bindDecide', { id: x.id, ok: 1 })}${btn('不是本人', 'bindDecide', { id: x.id, ok: 0 }, 'gray')}</div></div>`
      : g.worker
      ? `<div class="item"><div class="t click" data-act="editWorker" data-id="${x.id}"><div class="n">${esc(x.name)}　${esc(x.phone)}</div><div class="m">${esc((x.regions || '—').slice(0, 40))} · ${esc((x.skills || '').slice(0, 30))}</div></div><div class="a">${btn('打開資料', 'editWorker', { id: x.id })}</div></div>`
      : g.inquiry
      ? `<div class="item"><div class="t" data-act="inquiry" data-id="${x.id}" style="cursor:pointer"><div class="n">${esc(x.name)}　${esc(x.service_type)}</div><div class="m">${esc(x.area)} · ${esc(x.preferred || '')} · ${esc(x.created_at.slice(5, 16))}</div></div><div class="a">${btn('查看', 'inquiry', { id: x.id })}</div></div>`
      : `<div class="item"><div class="t click" data-act="order" data-id="${x.id}"><div class="n">${md(x.date)} ${x.start}–${x.end}　${esc(site(x.site_id).name || x.title)}</div><div class="m">${esc(TXT.names(x))}${g.key === 'fix' ? ' · ' + esc((x.review_note.split('；').filter(s => /待/.test(s)).pop() || '')) : ''}</div></div><div class="a">${g.act(x)}</div></div>`).join('');
    if (g.items.length > lim) h += `<div class="item"><div class="t m">還有 ${g.items.length - lim} 筆</div><div class="a">${btn('顯示全部 ' + g.items.length + ' 筆', 'homeAll', { k: g.key }, 'sec')}</div></div>`;
    h += '</section>';
  }
  h += `<h2>今天 <span class="g">${todayO.length} 場</span></h2>${todayO.map(ocard).join('') || '<div class="empty">今天沒有排服務。</div>'}`;
  h += `<h2>明天 <span class="g">${tomO.length} 場</span></h2>${tomO.map(ocard).join('') || '<div class="empty">明天沒有排服務。</div>'}`;
  return h;
};

/* ═════════ 排班（Apple 行事曆）═════════ */
let CAL = null, WFILTER = '';
PAGES.schedule = () => {
  CAL = CAL || today().slice(0, 7);
  const mode = SEG.sched || 'month';
  let h = phead('排班', '每一場服務，一目了然。', '藍色＝已排定、橘色＝待核對、綠色＝已完工。點一場看詳情；點空白日期新增。', btn('新增工單', 'newOrder') + ' ' + btn('補服務工單', 'newRedo', {}, 'sec') + ' ' + btn('固定排程', 'recurring', {}, 'sec'));
  h += segctl('sched', [['month', '月曆'], ['week', '本週清單'], ['list', '全部清單']], mode);
  const active = S.workers.filter(w => w.status === 'active');
  h += `<div class="pills" style="margin:0 0 16px">${[['', '全部夥伴'], ...active.map(w => [w.id, w.name])].map(([k, t]) => `<button class="pill" aria-pressed="${WFILTER === k}" data-act="wfilter" data-id="${k}">${esc(t)}</button>`).join('')}</div>`;
  const pass = o => !WFILTER || crew(o.id, ['accepted', 'invited']).some(a => a.worker_id === WFILTER);
  const orders = S.orders.filter(o => o.status !== 'archived' && o.date && pass(o));
  if (mode === 'month') {
    const [y, m] = CAL.split('-').map(Number), first = new Date(y, m - 1, 1), start = new Date(first); start.setDate(1 - first.getDay());
    h += `<div class="calbar"><h2 class="ttl" style="margin:0">${y} 年 ${m} 月</h2><div class="paddles"><button class="paddle" data-act="calMove" data-n="-1" aria-label="上個月">‹</button><button class="today" data-act="calMove" data-n="0">今天</button><button class="paddle" data-act="calMove" data-n="1" aria-label="下個月">›</button></div></div>`;
    const byDay = {}; for (const o of orders) (byDay[o.date] = byDay[o.date] || []).push(o);
    const phone = matchMedia('(max-width: 640px)').matches, pick = SEG.day && SEG.day.startsWith(CAL) ? SEG.day : (today().startsWith(CAL) ? today() : CAL + '-01');
    h += '<div class="calwrap" data-tour="cal"><div class="cal">' + [...WD].map(d => `<div class="h">${d}</div>`).join('');
    for (let i = 0; i < 42; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i); const k = iso(d), list = (byDay[k] || []).sort(by);
      if (i >= 35 && d.getMonth() !== m - 1) break;
      if (phone) {   // 手機：照 iPhone 行事曆，格子只放圓點，點日期看下面清單
        h += `<div class="d ${d.getMonth() !== m - 1 ? 'other' : ''} ${k === today() ? 'today' : ''} ${k === pick ? 'picked' : ''}" data-act="dayPick" data-date="${k}"><div class="dn"><span>${d.getDate()}</span></div><div class="dots">${list.slice(0, 5).map(o => `<i class="${short(o) && o.status !== 'cancelled' ? 'review' : o.status}"></i>`).join('')}</div></div>`;
        continue;
      }
      h += `<div class="d edit ${d.getMonth() !== m - 1 ? 'other' : ''} ${k === today() ? 'today' : ''}" data-act="dayNew" data-date="${k}"><div class="dn"><span>${d.getDate()}</span></div>`;
      h += list.slice(0, 4).map(o => `<span class="ev ${short(o) && o.status !== 'cancelled' ? 'review' : o.status}" data-act="order" data-id="${o.id}" title="${esc(site(o.site_id).name)}">${o.start} ${isRedo(o) ? "🔁" : ""}${esc((site(o.site_id).name || o.title).slice(0, 6))}<span class="w"> ${esc(crew(o.id).map(a => wname(a.worker_id).slice(-2)).join('、'))}</span></span>`).join('');
      if (list.length > 4) h += `<span class="more" data-act="dayList" data-date="${k}" role="button">還有 ${list.length - 4} 場 ›</span>`;
      h += '</div>';
    }
    h += '</div></div>';
    if (phone) {
      const dl = (byDay[pick] || []).sort(by);
      h += `<div class="secttl"><h3>${md(pick)}　${dl.length} 場</h3>${btn('這天新增', 'dayNew', { date: pick }, 'sec')}</div>` + (dl.map(ocard).join('') || '<div class="empty">這天沒有排服務。</div>');
    }
  } else {
    const t = today(), list = mode === 'week' ? orders.filter(o => o.date >= t && o.date <= addDays(t, 7)) : orders.filter(o => !SEG.smonth || o.date.startsWith(SEG.smonth));
    if (mode === 'list') h += `<div class="g3" style="margin-bottom:12px"><div><label>月份</label><input type="month" value="${SEG.smonth || ''}" data-change="smonth"></div><div><label>狀態</label><select data-change="sstatus">${[['', '全部'], ['review', '待核對'], ['confirmed', '已排定'], ['completed', '已完工'], ['cancelled', '已取消']].map(([k, t]) => `<option value="${k}"${(SEG.sstatus || '') === k ? ' selected' : ''}>${t}</option>`).join('')}</select></div></div>`;
    const rows = list.filter(o => !SEG.sstatus || o.status === SEG.sstatus).sort(by);
    h += `<div class="btns" style="margin:0 0 10px">${btn('勾選的排定', 'bulkSel', { action: 'confirm', confirm: '排定勾選的工單？夥伴會收到地址。' }, 'sec')}${btn('勾選的客戶已確認', 'bulkSel', { action: 'customer-confirmed' }, 'gray')}${btn('勾選的封存（不要了）', 'bulkSel', { action: 'archive', confirm: '封存勾選的工單？' }, 'gray')}</div>`;
    h += `<div class="card">${tbl(['<input type="checkbox" data-act="selAll" aria-label="全選">', '日期時段', '案場', '夥伴', '狀態', '備註'], rows.map(o => `<tr class="click" data-act="order" data-id="${o.id}">${td('', `<input type="checkbox" class="sel" value="${o.id}" aria-label="選取">`)}${td('日期', `<a data-act="order" data-id="${o.id}">${md(o.date)} ${o.start}–${o.end}</a>`)}${td('案場', redoTag(o) + esc(site(o.site_id).name || o.title))}${td('夥伴', esc(TXT.names(o)))}${td('狀態', st(short(o) && o.status !== 'cancelled' ? 'short' : o.status, short(o) && o.status !== 'cancelled' ? '缺人' : ''))}${td('備註', `<span class="small">${esc((o.review_note || '').split('；').slice(-1)[0])}</span>`)}</tr>`), 'tblsel')}</div>`;
  }
  return h;
};
ACT.wfilter = d => { WFILTER = d.id; route(true); };
ACT.dayList = d => {      // 月曆格子放不下時：點「還有 N 場」看這天全部
  const list = S.orders.filter(o => o.date === d.date && o.status !== 'archived').sort(by);
  sheet(`${md(d.date)}　${list.length} 場`, list.map(ocard).join('') || '<div class="empty">這天沒有排服務。</div>', null);
  $('#sheetBody .shead').insertAdjacentHTML('afterend', `<div class="btns" style="margin:0 0 12px">${btn('這天新增', 'dayNew', { date: d.date }, 'sec')}</div>`);
};
ACT.calMove = d => { if (d.n === '0') CAL = today().slice(0, 7); else { const [y, m] = CAL.split('-').map(Number); const n = new Date(y, m - 1 + Number(d.n), 1); CAL = n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0'); } route(true); };
ACT.smonth = d => { SEG.smonth = d.value; route(true); }; ACT.sstatus = d => { SEG.sstatus = d.value; route(true); };
ACT.dayNew = d => orderForm(null, { date: d.date });
ACT.dayPick = d => { SEG.day = d.date; route(true); };
ACT.selAll = (d, el) => $$('.sel').forEach(x => x.checked = el.checked);
ACT.bulkSel = async d => { const ids = $$('.sel:checked').map(x => x.value); if (!ids.length) throw new Error('請先勾選工單'); return doBulk(d.action, ids); };
ACT.bulk = d => doBulk(d.action, d.ids.split(','));
ACT.bulkOne = d => doBulk(d.action, [d.id]);
async function doBulk(action, ids) {
  const r = await api('/api/orders/bulk', { action, ids }); await refresh();
  if (r.errors.length) sheet('部分沒有完成', `<p>完成 ${r.done} 筆；以下 ${r.errors.length} 筆需要處理：</p>` + r.errors.map(e => `<div class="item"><div class="t"><div class="n">${esc(IDX.orders[e.id] ? md(IDX.orders[e.id].date) + ' ' + (site(IDX.orders[e.id].site_id).name || '') : e.id)}</div><div class="m">${esc(e.error)}</div></div><div class="a">${btn('打開', 'order', { id: e.id }, 'gray')}</div></div>`).join(''));
  return `完成 ${r.done} 筆`;
}

/* ═════════ 工單：詳情、新增／編輯、流程動作 ═════════ */
const FLOW = [['建立', o => true], ['派工', o => accepted(o) > 0], ['客戶確認', o => o.customer_confirmed || ['confirmed', 'completed'].includes(o.status)], ['排定', o => ['confirmed', 'completed'].includes(o.status)], ['完工', o => o.status === 'completed'], ['驗收', o => o.acceptance === 'accepted'], ['請款', o => IDX.invoiced.has(o.id)]];
const OPAY = o => (S.order_pay || {})[o.id] || {};      // 先結的單「收款」在排定前；後結（公司行號、月結）完工後才請款
function flowOf(o) {
  const p = OPAY(o);
  if (p.mode === 'before') return [FLOW[0], FLOW[1], FLOW[2], ['收款', () => !!p.paid], FLOW[3], FLOW[4], FLOW[5]];
  return FLOW.map(f => f[0] === '請款' ? ['請款', o2 => IDX.invoiced.has(o2.id) || !!p.billed] : f);
}
const ORDER_REOPEN = {}, CUST_REOPEN = {}, WORKER_REOPEN = {};
ACT.order = d => orderSheet(d.id);
ACT.crewAllShow = d => { (SEG.crewAll = SEG.crewAll || {})[d.o] = 1; orderSheet(d.o); };
function orderSheet(id) {
  const o = IDX.orders[id]; if (!o) return toast('找不到工單', true);
  NEXT_SHEET = ORDER_REOPEN[id] = ORDER_REOPEN[id] || (() => orderSheet(id));
  const s = site(o.site_id), c = cust(o.customer_id), crewAll = IDX.byOrder[o.id] || [], ph = IDX.photos[o.id] || [];
  const FL = flowOf(o), lastOn = FL.reduce((n, f, i) => f[1](o) ? i : n, 0);
  let h = `<div>${st(short(o) && !['cancelled', 'completed', 'archived'].includes(o.status) ? 'short' : o.status, short(o) && !['cancelled', 'completed', 'archived'].includes(o.status) ? `缺 ${o.required_workers - accepted(o)} 人` : '')} ${o.customer_confirmed ? st('completed', '客戶已確認') : ''} ${o.status === 'completed' ? st(o.acceptance, { accepted: '業主已驗收', pending: '待業主驗收', disputed: '有爭議' }[o.acceptance]) : ''}${o.rating ? ` <span class="st ${o.rating >= 4 ? 'completed' : 'overdue'}">客戶評價 ${'⭐'.repeat(o.rating)}</span>` : ''}</div>`;
  if (o.status !== 'archived') h += `<div class="steps2">${FL.map((f, i) => `<div class="${i <= lastOn ? 'on' : ''} ${i === lastOn + 1 ? 'now' : ''}"><i></i>${f[0]}</div>`).join('')}</div>`;
  h += `<dl class="kv"><dt>時間</dt><dd>${md(o.date)} ${o.start}–${o.end}（${hrs(mins(o.start, o.end))}）${o.actual_start ? `<br><span class="small">實際 ${o.actual_start}–${o.actual_end}</span>` : ''}</dd>
    <dt>客戶</dt><dd>${c.id ? `<a data-act="customer" data-id="${c.id}">${esc(c.dn || c.name)}</a>` : '未指定'}${c.phone ? '　' + esc(c.phone) : ''}${c.id ? '　' + brandPill(c) : ''}</dd>
    <dt>案場</dt><dd>${esc(s.name || '')}<br>${esc(s.address || '地址待補')}${s.navigation && !s.navigation.startsWith('http') ? '<br><span class="small">' + esc(s.navigation) + '</span>' : ''}${s.address || (s.navigation || '').startsWith('http') ? `<div class="navrow">${navBtn(s, '🧭 Google 地圖導航')}${s.address ? btn('複製地址', 'copyAddr', { t: s.address }, 'gray') : ''}</div><div class="small">${esc(navHow(s))}</div>` : ''}</dd>
    <dt>開門</dt><dd>${esc(ENTRY_TXT[o.entry_mode || s.entry_mode] || '還沒確認')}${o.entry_mode ? '（這張工單另外設定）' : ''}</dd>
    ${s.entry_notes ? `<dt>進場</dt><dd>${esc(s.entry_notes)}</dd>` : ''}
    <dt>人數</dt><dd>${o.required_workers} 位</dd>
    ${can('money') ? `<dt>收費</dt><dd>${o.approved_amount != null ? money(o.approved_amount) + '（已核定）' : s.visit_price != null ? money(s.visit_price) + '／次（案場價）' : o.rate ? '$' + o.rate + '／小時' : '未設定'}${o.adjustment ? `，加減價 ${money(o.adjustment)}（${esc(o.adjustment_reason)}）` : ''}</dd>` : ''}
    ${((S.order_media || {})[o.id] || []).length ? `<dt>業主傳的</dt><dd style="display:flex;gap:6px;flex-wrap:wrap">${S.order_media[o.id].map(([mid, mm]) => `<span style="text-align:center"><a href="/api/order-media/${mid}" target="_blank" rel="noopener">${mm.startsWith('video') ? '🎬 影片' : `<img src="/api/order-media/${mid}" style="width:64px;height:64px;object-fit:cover;border-radius:8px">`}</a><br>${btn('刪', 'omDel', { id: mid }, 'gray')}</span>`).join('')}<span class="small" style="flex-basis:100%">業主在 LINE 傳的現場照片／影片，派工卡「看業主傳的現場照片」看得到</span></dd>` : ''}
    <dt>現場工具</dt><dd>${s.tools ? esc({ full: '業主工具齊全（用業主的）', basic: '業主只有基本工具，其他自備', none: '業主沒有工具，全部自備' }[s.tools] || s.tools) + (s.tools_note ? '　<span class="small">' + esc(s.tools_note) + '</span>' : '') : `${st('short', '還沒設定')} ${btn('直接設定工具', 'toolsSet', { id: o.site_id }, 'sec')} ${btn('用 LINE 問業主', 'toolsAsk', { id: o.id }, 'gray')}`}${s.tools ? ' ' + btn('改', 'toolsSet', { id: o.site_id }, 'gray') : ''}</dd>
    ${o.scope ? `<dt>服務範圍</dt><dd style="white-space:pre-wrap">${esc(o.scope)}</dd>` : ''}
    ${o.notes ? `<dt>內部備註</dt><dd style="white-space:pre-wrap">${esc(o.notes)}<div class="small">夥伴看不到</div></dd>` : ''}${s.notes ? `<dt>案場備註</dt><dd class="small">${esc(s.notes)}<br>夥伴看不到</dd>` : ''}
    ${o.scope ? `<dt>夥伴看得到嗎</dt><dd>${o.scope_hold ? `${st('short', '⏳ 待管理層審核，夥伴還看不到')} ${btn('審核通過，給夥伴看', 'scopeOk', { id: o.id })} ${btn('修改', 'editOrder', { id: o.id }, 'sec')}` : o.scope_ok ? `<span class="small">✅ ${esc(o.scope_ok)} 審核，夥伴看得到</span>` : '<span class="small">夥伴看得到</span>'}</dd>` : ''}
    ${(o.review_note || '').match(/報價單 ([A-Z0-9]+)/) ? `<dt>報價單</dt><dd>${btn('看報價單 ' + o.review_note.match(/報價單 ([A-Z0-9]+)/)[1], 'quoteOpen', { id: o.review_note.match(/報價單 ([A-Z0-9]+)/)[1] }, 'sec')}</dd>` : ''}${Object.entries(JSON.parse(o.extra || '{}')).map(([k, v]) => `<dt>${esc(((EXTRA.find(e => e[0] === k) || [])[1] || k).replace(/（.*）/, ''))}</dt><dd>${esc(v)}</dd>`).join('')}${o.review_note ? `<dt>核對</dt><dd class="small">${esc(o.review_note)}</dd>` : ''}
    ${o.field_report ? `<dt>夥伴回報</dt><dd>${esc(o.field_report)}</dd>` : ''}</dl>`;
  const held = (S.holds || {})[o.id];               // 找人中，先不要自動派
  if (held) h += `<div class="banner warn">⏸ ${esc(held)}：系統先不自動問夥伴（找到人按「挑人派工 › 直接排入」）。</div>`;
  h += `<div class="secttl"><h3>派工</h3><div>${['review', 'draft'].includes(o.status) ? btn('挑人派工', 'pickWorker', { id: o.id }) + ' ' + btn('系統自動派', 'autoDispatch', { id: o.id }, 'sec') + ' ' + btn(held ? '恢復自動派' : '找人中，先不要自動派', 'holdToggle', { id: o.id, on: held ? '' : '1' }, 'gray') : ['confirmed'].includes(o.status) ? btn('換人／加人', 'pickWorker', { id: o.id }, 'sec') : ''}${['review', 'draft', 'confirmed'].includes(o.status) && (o.required_workers || 1) > 1 ? ' ' + btn('減少人數', 'peopleCut', { id: o.id }, 'gray') : ''}</div></div>`;
  const fullCrew = crewAll.filter(a => a.status === 'accepted').length >= (o.required_workers || 1), hideOther = fullCrew && !(SEG.crewAll || {})[o.id];      // 派單完畢 → 只顯示已接單的，婉拒／邀請中的收起來
  const crewShow = hideOther ? crewAll.filter(a => a.status === 'accepted') : crewAll, crewHid = crewAll.length - crewShow.length;
  h += crewShow.length ? crewShow.map(a => `<div class="item"><div class="t"><div class="n">${esc(wname(a.worker_id))} ${st(a.status)}</div><div class="m">${!can('money') ? '' : a.rate ? '時薪 ' + esc(a.rate) : a.amount != null ? '固定 ' + money(a.amount) : a.pay_mode === 'fixed' ? '<b style="color:var(--orange)">案件金額待填（按「計酬」）</b>' : ''}　${esc(a.notes || '')}${a.status === 'accepted' ? ckLine(a) : ''}${seenLine(a)}</div></div><div class="a">${a.status === 'accepted' && ['confirmed', 'completed'].includes(o.status) ? btn('改打卡時間', 'setTimes', { id: a.id, o: o.id }, 'gray') : ''}${a.status === 'applied' || a.status === 'invited' ? btn('選他', 'setAssign', { id: a.id, status: 'accepted' }) + btn('取消邀請', 'uninvite', { id: a.id, o: o.id }, 'gray') : ''}${a.status === 'accepted' && o.status !== 'completed' ? btn('取消此人', 'setAssign', { id: a.id, status: 'cancelled', confirm: '確定取消這位夥伴？' }, 'gray') : ''}${a.status === 'accepted' && o.customer_id && !(cust(o.customer_id).preferred_workers || '').split(',').includes(a.worker_id) ? btn('設為固定夥伴', 'setPreferred', { c: o.customer_id, w: a.worker_id, o: o.id }, 'gray') : ''}${can('money') ? btn('計酬', 'editAssign', { id: a.id }, 'gray') : ''}</div></div>`).join('') : '<div class="empty">還沒有人。按「挑人派工」勾選要發給誰（只有被勾的人看得到）。</div>';
  if (crewHid) h += `<div class="item"><div class="t m">另外 ${crewHid} 位（婉拒、邀請中、取消）已收起來</div><div class="a">${btn('顯示', 'crewAllShow', { o: o.id }, 'gray')}</div></div>`;
  if (can('money') && S.vendors) {          // 配合廠商（窗簾、高空、清運⋯）外包成本
    const vc = (S.vendor_costs || []).filter(x => x.order_id === o.id);
    h += `<div class="secttl"><h3>配合廠商（外包成本）</h3>${btn('新增', 'vcForm', { oid: o.id }, 'gray')}</div>` + (vc.map(x => `<div class="item"><div class="t"><div class="n">${esc(x.vendor || '')}・${esc(x.item)}</div><div class="m">${money(x.amount)} · ${x.status === 'paid' ? '已付 ' + esc(x.paid_ref || '') : '未付'}</div></div><div class="a">${x.status === 'paid' ? '' : btn('刪除', 'vcDel', { id: x.id }, 'gray')}</div></div>`).join('') || '<div class="empty">沒有外包</div>');
  }
  h += `<div class="secttl"><h3>LINE 文字</h3></div><div class="btns" style="margin-top:0">${btn('派工文字', 'copyTxt', { id: o.id, kind: 'dispatch' }, 'gray')}${btn('客戶確認', 'copyTxt', { id: o.id, kind: 'confirm' }, 'gray')}${btn('客戶提醒', 'copyTxt', { id: o.id, kind: 'remindCustomer' }, 'gray')}${btn('夥伴提醒', 'copyTxt', { id: o.id, kind: 'remindWorker' }, 'gray')}${btn('完工通知', 'copyTxt', { id: o.id, kind: 'done' }, 'gray')}${btn('關心', 'copyTxt', { id: o.id, kind: 'care' }, 'gray')}</div>`;
  if (ph.length) h += `<div class="secttl"><h3>照片 ${ph.length}</h3>${btn('放大看', 'gallery', { o: o.id, i: 0 }, 'gray')}</div>` + photoStrip(o.id);      // 直接看照片、左右滑
  const acts = [];
  if (o.status === 'archived') acts.push('<p class="small">這是 10 月以前的歷史紀錄，只供查詢。</p>');
  else {
    if (['review', 'draft', 'confirmed'].includes(o.status)) acts.push(btn('編輯／改期', 'editOrder', { id: o.id }, 'sec'));
    if (o.status === 'completed') acts.push(btn('改時間／收費（已完工）', 'editOrder', { id: o.id }, 'sec'));
    if (['review', 'draft', 'confirmed'].includes(o.status) && o.date >= today()) acts.push(btn('🚨 緊急加派一位', 'urgAdd', { id: o.id }, 'red'));      // // 延時、照片上傳後也能改
    if (['review', 'draft'].includes(o.status) && !o.customer_confirmed) acts.push(btn('客戶已確認', 'custOk', { id: o.id }, 'sec'));
    if (['review', 'draft'].includes(o.status)) acts.push(btn('排定', 'confirmOrder', { id: o.id }));
    if (o.status === 'confirmed') acts.push(btn('完工登記', 'quickComplete', { id: o.id }));
    if (o.status === 'completed' && o.acceptance !== 'accepted') acts.push(btn('主管代為驗收', 'acceptOk', { id: o.id }, 'sec'));
    if (o.status === 'completed' && !(site(o.site_id) || {}).no_photo && !(S.nophoto_jobs || []).includes(o.id)) acts.push(btn('請夥伴補拍', 'photoRedo', { id: o.id }, 'gray'));      // 跟 LINE 完工卡同一個
    if (['review', 'draft', 'confirmed'].includes(o.status)) acts.push(btn('補充提醒夥伴', 'workerNote', { id: o.id }, 'gray'));
    acts.push(btn('照片', 'photos', { id: o.id }, 'gray'), btn('客訴／補掃', 'newCase', { id: o.id }, 'gray'), btn('補服務工單', 'newRedo', { id: o.id }, 'gray'), btn('紀錄', 'orderLog', { id: o.id }, 'gray'), btn('移到待整理', 'toTriage', { id: o.id, e: 'orders' }, 'gray'));
    if (['review', 'draft', 'confirmed'].includes(o.status)) acts.push(btn('取消', 'cancelOrder', { id: o.id }, 'red'));
    if (/^垃圾代丟/.test(o.title || '')) acts.push(btn('停止垃圾代丟', 'ddStop', { id: o.id }, 'gray'));      // 
  }
  h += `<div class="btns">${acts.join('')}</div>`;
  sheet(redoTag(o) + esc(s.name || o.title), (isRedo(o) ? `<div class="banner warn">🔁 這是補服務工單${o.parent_order_id && IDX.orders[o.parent_order_id] ? `（補 <a data-act="order" data-id="${o.parent_order_id}">${md(IDX.orders[o.parent_order_id].date)} 那一場</a>）` : ''}</div>` : '') + h);
}
function orderForm(o, preset = {}) {
  o = o || { required_workers: 1, billing_mode: 'fixed', ...preset };
  OF_OPTS = siteOpts();                           // 還沒有地點的客戶也能選
  const body = pickFld('site_id', '案場（客戶）', o.site_id, OF_OPTS, { hint: '上面打字就會篩選；沒有的客戶先到「客戶」新增。' })
    + g2(fld('date', '日期', o.date, 'date'), fld('required_workers', '需要幾位', o.required_workers, 'number'))
    + g2(fld('start', '開始', o.start, 'time'), fld('end', '結束', o.end, 'time'))
    + fld('title', '服務名稱（空白＝用案場名）', o.title)
    + (can('money') ? g2(fld('approved_amount', '這次收費（元，空白＝依案場價或時薪）', o.approved_amount == null ? '' : o.approved_amount / 100, 'number'), fld('rate', '每小時費率（元）', o.rate ?? '', 'number')) + g2(fld('adjustment', '加減價（元）', (o.adjustment || 0) / 100, 'number'), fld('adjustment_reason', '加減價原因', o.adjustment_reason)) : '')
    + fld('notes', '內部備註（夥伴看不到，可以寫特殊資料；要交代夥伴的寫在下面「派工注意事項」）', o.notes, 'textarea')
    + fld('dispatch_note', '派工注意事項（📌 會放進派工文字）', o.dispatch_note, 'textarea', { ph: '到達時報備群組並找到管家', rows: 2 })
    + fld('scope', o.scope_hold ? '服務範圍（審核後夥伴才看得到；改了＝審核通過；一行一項）' : '服務範圍（夥伴看得到，不含價格；一行一項）', o.scope, 'textarea', { ph: '客廳、房間地面吸拖\n浴室馬桶、洗手台', rows: 5 })
    + `<details class="fold"${o.extra && o.extra !== '{}' ? ' open' : ''}><summary>陪玩／管家額外資料（這兩類服務才要填，排定後夥伴看得到）</summary><div class="in">${EXTRA.map(([k, t, ph]) => fld('x_' + k, t, (JSON.parse(o.extra || '{}'))[k] || '', 'text', { ph })).join('')}</div></details>`
    + fld('entry_mode', '開門方式（這張工單；空白＝沿用案場設定）', o.entry_mode || '', 'select', { options: [['', '沿用案場設定'], ...ENTRY] })
    + fld('customer_confirmed', '客戶已確認這個時間', o.customer_confirmed, 'check')
    + (o.id ? '' : pickFld('assign_w', '指定夥伴（選填，打字找）', '', workerPickOpts(), { q: '找夥伴（打名字、地區、電話）', qph: '例：阿芬、板橋、0912', hint: '選了：只邀請他（他按接單就成立；30 分鐘沒回會通知你）。沒選：發布後照 A／B／C 級自動找。' }))      // 
    + (o.id && o.status === 'confirmed' ? fld('review_note', '改期原因（已排定的工單改時間要寫）', '', 'textarea', { rows: 2 }) : '');
  sheet(o.id ? '編輯工單' : '新增工單', body, async f => {
    if (!f.site_id) throw new Error('請選客戶／案場（上面可以打字找）');
    f.site_id = await ensureSite(f.site_id);         // 還沒有地點的客戶：先用客戶名建一個地點
    const s = site(f.site_id) || {};
    const p = { ...f, title: f.title || s.name || '服務', required_workers: Number(f.required_workers || 1) };
    const ex = {}; EXTRA.forEach(([k]) => { if ((f['x_' + k] || '').trim()) ex[k] = f['x_' + k].trim(); delete p['x_' + k]; }); p.extra = Object.keys(ex).length ? JSON.stringify(ex) : '';
    if (can('money')) { p.approved_amount = toCents(f.approved_amount); p.adjustment = toCents(f.adjustment) || 0; }
    if (o.id) { p.id = o.id; p.version = o.version; if (o.status === 'confirmed' && !f.review_note) delete p.review_note; else if (f.review_note) p.review_note = (o.review_note ? o.review_note + '；' : '') + '改期：' + f.review_note; }
    else p.review_note = '後台建立';
    const aw = p.assign_w; delete p.assign_w;
    const r = await api('/api/save/orders', p);
    if (!o.id && aw && r && r.id) { const d = await api('/api/dispatch/auto', { order_id: r.id, worker_ids: [aw], exclusive: true }); return '已新增工單，' + d.message + '（只有他看得到）'; }
    if (r && r.crew_ask) return r.crew_ask;
    return o.id ? '已儲存' + (o.status === 'confirmed' && r.status === 'review' ? '，已改回待核對，請重新確認' : '') : '已新增工單';
  });
}
ACT.newOrder = () => orderForm(null, { date: today() });
/* 補服務工單 —— 客戶／地點、服務人員都能打字找；時間可指定，或勾「早上／下午／晚上前往」彈性時段；
   補掃不一定付夥伴薪水（主管判斷）；補掃項目用簡答。建立後只邀請選的那位夥伴（他按接單就成立）。客戶這次不收費。 */
const REDO_T = { am: ['早上', '08:00', '12:00'], pm: ['下午', '13:00', '17:00'], eve: ['晚上', '18:00', '21:00'] };
ACT.newRedo = d => {
  const src = d && d.id ? IDX.orders[d.id] : null;
  const done = (S.orders || []).filter(o => o.status === 'completed' && (!src || o.customer_id === src.customer_id)).sort(by).reverse().slice(0, 60);
  OF_OPTS = siteOpts();
  const body = pickFld('site_id', '客戶｜地點', src ? src.site_id : '', OF_OPTS, { hint: '上面打字就會篩選。' })
    + fld('parent_order_id', '原本那一場（選填，補哪一次的服務）', src ? src.id : '', 'select', { options: [['', '不指定'], ...done.map(o => [o.id, `${md(o.date)} ${site(o.site_id).name || o.title}`])] })
    + pickFld('assign_w', '服務人員', '', workerPickOpts(), { q: '找服務人員（打名字、地區、電話）', qph: '例：阿芬、板橋、0912', empty: '先不指定（之後再派）', hint: '選了：只邀請他，他按接單就成立。' })
    + fld('date', '日期', today(), 'date')
    + fld('tmode', '時間', 'exact', 'select', { options: [['exact', '指定時間（填下面開始／結束）'], ['am', '早上前往（08:00–12:00 之間到）'], ['pm', '下午前往（13:00–17:00 之間到）'], ['eve', '晚上前往（18:00–21:00 之間到）']] })
    + g2(fld('start', '開始', src ? src.start : '', 'time'), fld('end', '結束', src ? src.end : '', 'time'))
    + fld('scope', '補掃項目（簡答，夥伴看得到）', '', 'textarea', { ph: '例：浴室玻璃水垢、廚房抽油煙機外殼', rows: 3 })
    + (can('money') ? fld('paid', '這次補掃要付夥伴薪水（主管判斷；不勾＝不支薪）', '', 'check') : '<p class="small">支薪與否由主管在工單「計酬」決定。</p>')
    + fld('notes', '內部備註（夥伴看不到，例：客訴原因）', '', 'textarea', { rows: 2 });
  sheet('補服務工單', body, async f => {
    if (!f.site_id) throw new Error('請選客戶／地點（上面可以打字找）');
    if (!(f.scope || '').trim()) throw new Error('請寫補掃項目');
    const flex = REDO_T[f.tmode];
    if (flex) { f.start = flex[1]; f.end = flex[2]; } else if (!f.start || !f.end) throw new Error('請填開始和結束時間，或選「早上／下午／晚上前往」');
    f.site_id = await ensureSite(f.site_id);
    const s = site(f.site_id) || {}, par = f.parent_order_id ? IDX.orders[f.parent_order_id] : null;
    if (par && par.customer_id !== s.customer_id) throw new Error('原本那一場不是這位客戶的，請重選或改「不指定」');
    const p = { site_id: f.site_id, date: f.date, start: f.start, end: f.end, required_workers: 1, title: '補服務｜' + (s.name || ''), scope: f.scope.trim(),
      parent_order_id: f.parent_order_id || null, billing_mode: 'fixed', approved_amount: 0,
      dispatch_note: (flex ? `🕘 彈性時段：${flex[0]}前往（${flex[1]}–${flex[2]} 之間到就可以）\n` : '') + '🔁 補服務：照上面補掃項目處理',
      notes: [!can('money') ? '補掃：支薪待主管決定' : f.paid ? '補掃：支薪' : '補掃：不支薪（主管判斷）', f.notes].filter(Boolean).join('；'), review_note: '後台建立（補服務）' };
    const r = await api('/api/save/orders', p), aw = f.assign_w;
    if (!aw || !r || !r.id) return '已建立補服務工單' + (aw ? '' : '（還沒指定服務人員，到派工挑人）');
    const dd = await api('/api/dispatch/auto', { order_id: r.id, worker_ids: [aw], exclusive: true, unpaid: can('money') && !f.paid });      // 不支薪在發卡前就設好
    return '已建立補服務工單，' + dd.message + (can('money') && !f.paid ? '（不支薪）' : '');
  }, '建立補服務工單');
};
function workerPickOpts() {                       // 可派工的夥伴（打字找名字、地區）
  return (S.workers || []).filter(w => w.status === 'active' && !(w.name || '').startsWith('（示範）')).sort((a, b) => (a.name || '').localeCompare(b.name || ''))
    .map(w => [w.id, `${w.name}${w.regions ? '｜' + w.regions : ''}`, `${w.name} ${w.regions || ''} ${w.phone || ''}`.toLowerCase()]);
}
let OF_OPTS = [];                                 // 新增工單的客戶／案場選項：[值, 顯示, 搜尋用文字]
/* 客戶已綁定、建工單卻選不到 → 所有選客戶／案場的地方都能打字找；沒有地點的客戶也能選（送出時用客戶名建地點） */
const PICK = {};                                  // 每個下拉的選項 {opts: [值, 顯示, 搜尋字], empty}
function siteOpts() {
  const sites = S.sites.slice().sort((a, b) => (cust(a.customer_id).name || '').localeCompare(cust(b.customer_id).name || ''));
  const noSite = (S.customers || []).filter(c => ['active', 'paused', 'lead'].includes(c.status) && !(IDX.sitesOf[c.id] || []).length);
  return [...sites.map(s => [s.id, `${cust(s.customer_id).dn}｜${s.name}`, ofKey(cust(s.customer_id), s)]), ...noSite.map(c => ['c:' + c.id, `${c.dn}｜（還沒有地點，送出時用客戶名建立）`, ofKey(c)])];
}
function custOpts(keep) {                         // 歷史客戶只在「目前就是他」時列出
  return (S.customers || []).filter(c => c.status !== 'ended' || c.id === keep).sort((a, b) => (a.name || '').localeCompare(b.name || ''))
    .map(c => [c.id, c.dn + (c.status === 'ended' ? '（歷史）' : c.status === 'lead' ? '（潛在）' : ''), ofKey(c) + (IDX.sitesOf[c.id] || []).map(s => (s.name + s.address).toLowerCase()).join(' ')]);
}
function pickFld(name, label, value, opts, o = {}) {
  PICK[name] = { opts, empty: o.empty || '請選擇' };
  return `<label for="f-${name}_q">${o.q || '找客戶（打名字、電話、地址、LINE 名稱）'}</label><input id="f-${name}_q" data-input="pickQ" data-for="${name}" autocomplete="off" placeholder="${o.qph || '例：巧琳、0972、三星、清洲'}" style="margin-bottom:6px">`
    + fld(name, label, value, 'select', { options: [['', o.empty || '請選擇'], ...opts.map(([v, t]) => [v, t])], hint: o.hint || '上面打字就會篩選。' });
}
ACT.pickQ = d => {                                // 打字篩選（iOS 不支援藏 option，所以整個重建）
  const P = PICK[d.for], sel = $('#f-' + d.for); if (!P || !sel) return;
  setTimeout(() => sel.dispatchEvent(new Event('change')), 0);          // 打字選到 → 跟手動選一樣（報價單會自動帶入客戶資料）
  const q = (d.value || '').toLowerCase().replace(/[-\s]/g, ''), hit = q ? P.opts.filter(o => o[2].includes(q)) : P.opts;
  sel.innerHTML = (q ? '' : `<option value="">${esc(P.empty)}</option>`) + hit.slice(0, 80).map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join('') + (hit.length ? '' : '<option value="">找不到：換個字試試，或先到「客戶」新增</option>');
  if (q && hit.length) sel.value = hit[0][0];
};
async function ensureSite(v) {                    // 選到「還沒有地點的客戶」→ 先用客戶名建一個地點
  if (!v || !v.startsWith('c:')) return v;
  const cu = IDX.customers[v.slice(2)];
  const ns = await api('/api/save/sites', { customer_id: cu.id, name: cu.name, address: '' }); await refresh(); return ns.id;
}
function ofKey(c, s) { return [c.name, c.ln, c.line_name, c.phone, c.code, s && s.name, s && s.address, ...(S.customer_aliases || []).filter(a => a.customer_id === c.id).map(a => a.alias)].join(' ').toLowerCase().replace(/[-\s]/g, ''); }
ACT.siteQ = d => ACT.pickQ({ ...d, for: 'site_id' });      // 舊名稱（相容）
ACT.siteQ0 = d => {                                // 打字篩選（iOS 不支援藏 option，所以整個重建）
  const q = (d.value || '').toLowerCase().replace(/[-\s]/g, ''), sel = $('#f-site_id'); if (!sel) return;
  const hit = q ? OF_OPTS.filter(o => o[2].includes(q)) : OF_OPTS;
  sel.innerHTML = (q ? '' : '<option value="">請選擇</option>') + hit.slice(0, 80).map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join('') + (hit.length ? '' : '<option value="">找不到：換個字試試，或先到「客戶」新增</option>');
  if (q && hit.length) sel.value = hit[0][0];
};
ACT.setPreferred = async d => {      // 這位客戶之後派工先邀請這位夥伴（客戶資料「指定夥伴」）
  const cu = IDX.customers[d.c], ids = (cu.preferred_workers || '').split(',').filter(Boolean);
  if (!ids.includes(d.w)) await api('/api/save/customers', { id: cu.id, version: cu.version, preferred_workers: [...ids, d.w].join(',') });
  await refresh(); orderSheet(d.o); return `已設為 ${cu.name} 的固定夥伴：之後派工先只邀請 ${wname(d.w)}`;
};
ACT.editOrder = d => orderForm(IDX.orders[d.id]);
ACT.custOk = async d => { const o = IDX.orders[d.id]; await api('/api/save/orders', { id: o.id, version: o.version, customer_confirmed: 1 }); await refresh(); orderSheet(d.id); return '已記錄客戶確認'; };
ACT.confirmOrder = async d => {
  const o = IDX.orders[d.id];
  try { await api(`/api/order/${o.id}/confirm`, { version: o.version }); }
  catch (e) {
    if (!/先排定的原因/.test(e.message)) throw e;          // 款項還沒收到：要先排定得寫原因（會記在核對說明）
    const why = prompt(e.message + '\n\n先排定的原因（例如：熟客月結、已收現金）：', '');
    if (!why || !why.trim()) return '沒有排定（款項還沒確認收到）';
    await api(`/api/order/${o.id}/confirm`, { version: o.version, unpaid_reason: why.trim() });
  }
  closeSheet(); await refresh(); return '已排定，夥伴會收到通知';
};
ACT.toolsAsk = async d => { const r = await api('/api/order/tools-ask', { id: d.id }); return r.message; };   // 現場工具
ACT.scopeOk = async d => { const r = await api('/api/order/scope-ok', { id: d.id }); await refresh(); if (IDX.orders[d.id]) ACT.order({ id: d.id }); return r.message || '已審核'; };   // 詳細需求審核
ACT.acceptOk = async d => { const o = IDX.orders[d.id]; await api('/api/workflow/acceptance', { id: o.id, version: o.version, acceptance: 'accepted', reason: '主管代為驗收（' + ME.name + ' 記錄）' }); await refresh(); orderSheet(d.id); return '已記錄業主驗收'; };
ACT.quickComplete = d => {
  const o = IDX.orders[d.id], ts = crew(o.id).map(a => (S.timesheets || []).find(t => t.assignment_id === a.id)).filter(Boolean);
  const multi = crew(o.id).length > 1;
  sheet('完工登記', `<p class="small">${md(o.date)} ${esc(site(o.site_id).name)}　預排 ${o.start}–${o.end}</p>`
    + (ts.length ? `<div class="banner ok">夥伴已打卡：${ts.map(t => `${t.start}–${t.end}`).join('、')}</div>` : '')
    + (multi && ts.length < crew(o.id).length ? `<div class="banner warn">多人服務：還沒打卡的人，在下面填實際時間（預設＝工單實際時間），登記完工時一起記工時。</div>`
      + crew(o.id).filter(a => !(S.timesheets || []).some(t => t.assignment_id === a.id)).map(a => g2(fld('cs_' + a.id, esc(wname(a.worker_id)) + '：開始', o.actual_start || o.start, 'time'), fld('ce_' + a.id, esc(wname(a.worker_id)) + '：結束', o.actual_end || o.end, 'time'))).join('') : '')
    + g2(fld('actual_start', '實際開始', ts[0]?.start || o.actual_start || o.start, 'time'), fld('actual_end', '實際結束', ts[0]?.end || o.actual_end || o.end, 'time'))
    + fld('break_minutes', '休息分鐘', o.break_minutes || 0, 'number')
    + fld('report', '完工回報', o.field_report || '依排班完成', 'textarea')
    + fld('acceptance', '業主驗收', 'pending', 'select', { options: [['pending', '還沒確認'], ['accepted', '業主已確認'], ['disputed', '有意見（之後開客訴）']] }),
    async f => { const ct = {}; Object.keys(f).filter(k => k.startsWith('cs_')).forEach(k => { const id = k.slice(3); ct[id] = [f[k], f['ce_' + id]]; delete f[k]; delete f['ce_' + id]; });
      await api('/api/order/quick-complete', { id: o.id, ...f, crew_times: ct, break_minutes: Number(f.break_minutes || 0) }); return '已登記完工'; }, '登記完工');
};
ACT.cancelOrder = d => {
  const o = IDX.orders[d.id];
  sheet('取消工單', fld('kind', '原因', '客戶取消', 'select', { options: [['客戶取消', '客戶取消'], ['客戶改期（另建新單）', '客戶改期（另建新單）'], ['夥伴無法出勤且找不到人', '夥伴無法出勤且找不到人'], ['天候', '天候（颱風、豪雨）'], ['到場無法進入', '到場無法進入（客戶不在、鎖門）'], ['其他', '其他']] })
    + fld('reason', '說明', '', 'textarea') + (can('money') && crew(o.id).length ? fld('comp', '給夥伴的車馬費補貼（元，沒有填 0）', 0, 'number', { hint: '例如到場被鎖在外面，補貼 300。會記在夥伴計酬裡。' }) : ''),
    async f => {
      if (Number(f.comp) > 0) for (const a of crew(o.id)) await api('/api/save/assignments', { id: a.id, version: a.version, pay_mode: 'fixed', amount: Math.round(Number(f.comp) * 100), notes: (a.notes ? a.notes + '；' : '') + '取消補貼車馬費' });
      await api(`/api/order/${o.id}/cancel`, { version: IDX.orders[o.id].version + (Number(f.comp) > 0 ? 0 : 0), reason: f.kind + (f.reason ? '：' + f.reason : '') }).catch(async e => { await refresh(); const o2 = IDX.orders[o.id]; return api(`/api/order/${o.id}/cancel`, { version: o2.version, reason: f.kind + (f.reason ? '：' + f.reason : '') }); });
      return '已取消，夥伴會收到通知';
    }, '確定取消', '');
};
ACT.orderLog = async d => {
  const log = await api('/api/order-log?id=' + encodeURIComponent(d.id));
  sheet('工單紀錄', log.map(l => `<div class="item"><div class="t"><div class="n">${esc({ time_fix: '更正打卡時間', door_auto: '打卡＝已進門', publish: '發布派工', unpublish: '收回派工', invite: '邀請', apply: '報名', accepted: '接受', declined: '婉拒', filled: '額滿', escalate: '逾時改邀', arrive: '到場打卡', leave: '離場打卡', field_report: '夥伴回報', reminded: '已提醒', followed_up: '已關心', withdraw: '撤回報名' }[l.action] || l.action)}</div><div class="m">${esc(loc(l.at))} · ${esc(l.actor)} ${esc(l.detail)}</div></div></div>`).join('') || '<div class="empty">還沒有紀錄。</div>');
};
ACT.photoDel = async d => {
  if (!confirm('確定刪除這張照片？（重複或拍錯的才刪；誤刪可以請負責人救回）')) throw new Error('已取消');
  const r = await api('/api/photo/delete', { id: d.id }); await refresh(); ACT.photos({ id: d.o }); return r.message;
};
/* ───── 照片：縮圖一排（手指左右滑）＋點開大圖（左右滑、鍵盤 ← →、Esc 關）── ───── */
const PH_LABEL = { before: '服務前', after: '完工', incident: '異常' };
function photoOrder(oid) { const ph = IDX.photos[oid] || []; return ['before', 'after', 'incident'].flatMap(l => ph.filter(p => p.label === l)).concat(ph.filter(p => !PH_LABEL[p.label])); }
function phRetry(img) { const n = +(img.dataset.r || 0); if (n < 3) { img.dataset.r = n + 1; setTimeout(() => { img.src = img.src.replace(/&r=\d+$/, '') + '&r=' + (n + 1); }, 800 * (n + 1)); } }      // 載不到自動再試 3 次
function photoStrip(oid) {
  const ph = photoOrder(oid);
  return ['before', 'after', 'incident'].map(l => { const g = ph.filter(p => p.label === l); if (!g.length) return '';
    const days = [...new Set(g.map(p => (p.service_date || '').slice(5)))].filter(Boolean).join('、');
    return `<div class="phgrp"><div class="small"><b>${PH_LABEL[l]}</b> ${g.length} 張${days ? '・' + esc(days) : ''}</div><div class="phstrip">${g.map(p => `<button type="button" class="phth" data-act="gallery" data-o="${esc(oid)}" data-i="${ph.indexOf(p)}" aria-label="${PH_LABEL[l]}照片"><img loading="lazy" src="/api/photo/${esc(p.id)}?t=1" alt="" onerror="phRetry(this)"></button>`).join('')}</div></div>`; }).join('');
}
ACT.gallery = d => {
  const ph = photoOrder(d.o); if (!ph.length) return;
  galOpen(ph.map(p => ({ src: `/api/photo/${p.id}`, cap: `${PH_LABEL[p.label] || p.label} ${(p.service_date || '').slice(5)}` })), d.i);
};
ACT.galIds = d => {            // 報價現況照片、客戶見證也能左右滑（data-k＝q 報價照片／p 工單照片）
  const ids = (d.ids || '').split(',').filter(Boolean); if (!ids.length) return;
  galOpen(ids.map(id => ({ src: (d.k === 'q' ? '/api/quote-photo?id=' : d.k === 'r' ? '/api/issue/file?id=' : '/api/photo/') + encodeURIComponent(id), cap: d.cap || '' })), d.i);
};
function galOpen(ph, start) {   // ph：[{src, cap}]；左右滑、鍵盤 ← →、Esc 關
  if (!ph.length) return;
  let box = $('#gal');
  if (!box) {             // 只建一次：事件用 addEventListener（電腦沒有觸控時 ontouchstart 不會生效）
    box = document.createElement('div'); box.id = 'gal'; box.setAttribute('role', 'dialog'); document.body.appendChild(box); let sx = null;
    box.addEventListener('click', e => { if (e.target.closest('.gal-x') || e.target === box) box._close(); else if (e.target.closest('.gal-prev')) box._move(-1); else if (e.target.closest('.gal-next')) box._move(1); });
    box.addEventListener('touchstart', e => { sx = e.touches[0].clientX; }, { passive: true });
    box.addEventListener('touchend', e => { if (sx === null) return; const dx = e.changedTouches[0].clientX - sx; sx = null; if (Math.abs(dx) > 40) box._move(dx < 0 ? 1 : -1); });
    document.addEventListener('keydown', e => { if (box.hidden) return; if (e.key === 'ArrowLeft') box._move(-1); else if (e.key === 'ArrowRight') box._move(1); else if (e.key === 'Escape') { e.stopImmediatePropagation(); box._close(); } }, true);
  }
  let i = Math.max(0, Math.min(ph.length - 1, Number(start) || 0));
  const show = () => { const p = ph[i]; box.innerHTML = `<div class="gal-top"><span>${esc(p.cap || '')}${p.cap ? '・' : ''}${i + 1} / ${ph.length}</span><a href="${esc(p.src)}" target="_blank" rel="noopener">原圖</a><button type="button" class="gal-x" aria-label="關閉">✕</button></div>`
    + `<button type="button" class="gal-prev" aria-label="上一張"${i ? '' : ' disabled'}>‹</button><img src="${esc(p.src)}" alt=""><button type="button" class="gal-next" aria-label="下一張"${i < ph.length - 1 ? '' : ' disabled'}>›</button>`;
    if (ph[i + 1]) new Image().src = ph[i + 1].src; };          // 先載下一張，滑過去比較快
  box._move = k => { const n = i + k; if (n >= 0 && n < ph.length) { i = n; show(); } };
  box._close = () => { box.hidden = true; if ($('#sheet').hidden) document.body.style.overflow = ''; };
  box.hidden = false; document.body.style.overflow = 'hidden'; show();
}
ACT.photos = d => {
  const o = IDX.orders[d.id];
  sheet('服務照片', photoStrip(o.id) + `${(IDX.photos[o.id] || []).map(p => `<div style="display:flex;gap:8px;align-items:center;margin:4px 0"><a href="/api/photo/${p.id}" target="_blank" rel="noopener" style="flex:1">${esc(p.service_date)} ${esc({ before: '服務前', after: '完工', incident: '異常' }[p.label] || p.label)} ${esc(p.name)}</a>${can('admin') ? btn('刪除', 'photoDel', { id: p.id, o: o.id }, 'gray') : ''}</div>`).join('') || '<div class="empty">還沒有照片。</div>'}`
    + fld('label', '用途', 'after', 'select', { options: [['before', '服務前'], ['after', '完工'], ['incident', '異常／損壞']] }) + '<label for="f-file">選照片（可多張，JPG/PNG）</label><input id="f-file" type="file" accept="image/jpeg,image/png" multiple>',
    async f => { const files = [...$('#f-file').files]; if (!files.length) throw new Error('請選照片'); for (const file of files) await api('/api/photo', { order_id: o.id, label: f.label, service_date: o.date, name: file.name, content: await shrink(file) }); return `已上傳 ${files.length} 張`; }, '上傳');
};
async function shrink(file) {   /* 手機照片太大：縮到長邊 1800、JPEG 0.82，再轉 base64 */
  const img = await createImageBitmap(file); const k = Math.min(1, 1800 / Math.max(img.width, img.height));
  const cv = document.createElement('canvas'); cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k); cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
  return (cv.toDataURL('image/jpeg', .82)).split(',')[1];
}
ACT.setAssign = async d => { const a = (S.assignments || []).find(x => x.id === d.id); await api('/api/save/assignments', { id: a.id, version: a.version, status: d.status }); await refresh(); orderSheet(a.order_id); return d.status === 'accepted' ? '已選定' : '已更新'; };
ACT.editAssign = d => {
  const a = S.assignments.find(x => x.id === d.id), t = (S.timesheets || []).find(x => x.assignment_id === a.id), o = IDX.orders[a.order_id];
  sheet(`${esc(wname(a.worker_id))} 的計酬`, (can('money') ? g2(fld('pay_mode', '計酬方式', a.pay_mode, 'select', { options: [['hourly', '時薪'], ['fixed', '固定金額（單次、補貼）']] }), fld('rate', '時薪（元）', a.rate ?? '', 'number'))
    + g2(fld('amount', '固定金額（元）', a.amount == null ? '' : a.amount / 100, 'number'), fld('approved_minutes', '核定分鐘（空白＝依打卡）', a.approved_minutes ?? '', 'number')) : '')
    + fld('notes', '備註', a.notes) + (o.status === 'confirmed' ? `<h3 style="margin-top:20px">實際工時</h3>` + g2(fld('ts_start', '開始', t?.start || o.start, 'time'), fld('ts_end', '結束', t?.end || o.end, 'time')) + fld('ts_pause', '休息分鐘', t?.pause || 0, 'number') : ''),
    async f => {
      const p = { id: a.id, version: a.version, notes: f.notes };
      if (can('money')) Object.assign(p, { pay_mode: f.pay_mode, rate: f.rate, amount: toCents(f.amount), approved_minutes: f.approved_minutes === '' ? null : Number(f.approved_minutes) });
      await api('/api/save/assignments', p);
      if (f.ts_start && o.status === 'confirmed' && a.status === 'accepted') await api('/api/workflow/timesheet', { assignment_id: a.id, start: f.ts_start, end: f.ts_end, pause: Number(f.ts_pause || 0), notes: '後台登記（' + ME.name + '）', version: t?.version });
      return '已儲存';
    });
};
ACT.autoDispatch = async d => { const r = await api('/api/dispatch/auto', { order_id: d.id }); await refresh(); if (!$('#sheet').hidden) orderSheet(d.id); return r.message; };
ACT.pickWorker = async d => {
  const list = await api('/api/candidates?id=' + encodeURIComponent(d.id)), o = IDX.orders[d.id];
  sheet('挑選夥伴派工', `<p class="small">勾選要邀請的夥伴，按「只發給勾選的人」：<b>只有被勾選的人</b>收得到派工卡、看得到這筆工作，先按先得；30 分鐘沒人回會通知你改派，不會自動發給別人。分數依區域、可排時間、服務過這位客戶、本週負荷計算。</p>
    ${o && o.status === 'confirmed' ? '<div class="banner warn">這筆已排定：換人或加人請直接按該夥伴的「直接排入」（會立刻通知他）。</div>' : ''}${o && o.scope ? `<div class="banner">夥伴會看到的範圍：${esc(o.scope.split(/\n/).join('、'))}</div>` : ''}<div class="cands">`
    + (list.map(c => `<label class="item" style="cursor:${c.blocked ? 'default' : 'pointer'}"><input type="checkbox" class="pickw" value="${c.worker_id}"${c.blocked ? ' disabled' : ''} style="width:22px;height:22px;flex:none">
        <div class="score">${c.score}</div><div class="t"><div class="n">${esc(c.name)}${c.blocked ? `<small>${esc(c.blocked)}</small>` : ''}</div><div class="m">${esc(c.why.join('、') || '—')}</div></div>
        <div class="a">${btn('直接排入', 'assignDirect', { id: d.id, w: c.worker_id }, 'gray')}</div></label>`).join('') || '<div class="empty">沒有可派工的夥伴，請到「夥伴」把人設為可派工。</div>') + '</div>',
    o && o.status === 'confirmed' ? null : async () => {
      const ids = $$('.pickw:checked').map(x => x.value);
      if (!ids.length) throw new Error('請至少勾選一位');
      const r = await api('/api/dispatch/auto', { order_id: d.id, worker_ids: ids, exclusive: true });
      return r.message + '（只有他們看得到）';
    }, '只發給勾選的人');
};
ACT.peopleCut = d => {           // 減少人數（拿掉的不補人）
  const o = IDX.orders[d.id], crew = (S.assignments || []).filter(a => a.order_id === o.id && a.status === 'accepted'), n0 = o.required_workers || 1;
  sheet('減少人數', `<p class="small">現在 ${n0} 位、已接 ${crew.length} 位。拿掉的人系統會通知他不用前往，<b>不會另外補人</b>。報價／收款是照原本人數算的，金額要調整請到報價或結帳改。</p>`
    + fld('n', '改成幾位', n0 - 1, 'select', { options: Array.from({ length: n0 - 1 }, (_, i) => [n0 - 1 - i, `${n0 - 1 - i} 位`]) })
    + (crew.length ? `<label>拿掉誰（已接的比新人數多才需要勾）</label>` + crew.map(a => `<label class="chk"><input type="checkbox" name="drop" value="${a.worker_id}" data-multi="1"> ${esc(wname(a.worker_id))}</label>`).join('') : ''),
    async f => { const r = await api('/api/order/people', { id: o.id, n: Number(f.n), drop: f.drop || [] }); return r.message; }, '確定減少');
};
ACT.holdToggle = async d => { const r = await api('/api/order/hold', { id: d.id, on: !!d.on }); await refresh(); orderSheet(d.id); return r.message; };
ACT.uninvite = async d => { const r = await api('/api/assignment/uninvite', { id: d.id }); await refresh(); orderSheet(d.o); return r.message; };      // 可以取消邀請
ACT.inviteOne = async d => { const r = await api('/api/dispatch/auto', { order_id: d.id, worker_ids: [d.w] }); await refresh(); orderSheet(d.id); return r.message; };
ACT.assignDirect = async d => {
  const w = IDX.workers[d.w], vendor = WROLES(w).includes('外包廠商');
  const body = vendor ? { order_id: d.id, worker_id: d.w, status: 'accepted', pay_mode: 'fixed', notes: '外包廠商，案件計酬（金額請看得到金額的人在「計酬」填）・' + ME.name }      // 
    : { order_id: d.id, worker_id: d.w, status: 'accepted', pay_mode: 'hourly', rate: w.rate, notes: '後台直接排入（' + ME.name + '）' };
  try { await api('/api/save/assignments', body); }
  catch (e) {          // 排班時間寫太長造成的「重疊」：確認實際時間錯開就放行（記在這筆派工上，之後確認排程也不再擋）
    if (!/時間重疊|時間太近/.test(e.message) || !confirm(e.message + '\n\n如果實際時間是錯開的、或確定趕得過去（例如一場早上、一場下午），按「確定」仍要排入。')) throw e;
    await api('/api/save/assignments', { ...body, overlap_ok: 1, notes: body.notes + '；確認時間錯開' });
  }
  await refresh(); orderSheet(d.id); return '已排入 ' + w.name;
};
ACT.recurring = () => {
  sheet('固定排程（一次排多週）', pickFld('site_id', '案場', '', siteOpts())
    + `<label>星期</label><div class="chips">${[1, 2, 3, 4, 5, 6, 0].map(i => `<label><input type="checkbox" name="weekdays" data-multi="1" value="${(i + 6) % 7}"> 週${WD[i]}</label>`).join('')}</div>`
    + g2(fld('start', '開始', '09:00', 'time'), fld('end', '結束', '13:00', 'time')) + g2(fld('from', '從', today(), 'date'), fld('to', '到', addDays(today(), 27), 'date'))
    + g2(fld('every_weeks', '每幾週一次', 1, 'number', { hint: '雙週填 2' }), fld('required_workers', '人數', 1, 'number'))
    + fld('worker_id', '固定夥伴（選填）', '', 'select', { options: optsOf(S.workers.filter(w => w.status === 'active'), w => w.name, '先不指定（之後派工）') })
    + fld('customer_confirmed', '客戶已同意這個固定時段', 0, 'check') + fld('confirm', '有指定夥伴就直接排定', 0, 'check'),
    async f => { if (!f.site_id) throw new Error('請選客戶／案場（上面可以打字找）'); f.site_id = await ensureSite(f.site_id); const r = await api('/api/orders/recurring', { ...f, weekdays: f.weekdays.map(Number), required_workers: Number(f.required_workers || 1), every_weeks: Number(f.every_weeks || 1) }); return `已建立 ${r.created} 張工單` + (r.skipped.length ? `，${r.skipped.length} 天已有排班略過` : ''); }, '建立');
};

/* ═════════ 派工看板 ═════════ */
PAGES.dispatch = () => {
  const t = today(), live = S.orders.filter(o => ['review', 'draft', 'confirmed'].includes(o.status) && o.date >= t).sort(by);
  const need = live.filter(o => short(o) && o.dispatch !== 'open'), recruiting = live.filter(o => short(o) && o.dispatch === 'open'), wait = live.filter(o => !short(o) && o.status !== 'confirmed');
  let h = phead('派工', '缺人的工作，你決定派給誰。', '「挑人派工」：系統依區域、可排時間、服務紀錄排序建議，你勾選後只有那幾位看得到、接得到；30 分鐘沒人回會通知你改派。「系統自動派」才會自動換下一批。', btn('複製缺額總表', 'copyShortage', {}, 'sec'));
  const lane = (title, list, act, tour) => `<div data-tour="${tour}"><div class="lanehead">${title}<span class="cnt">${list.length}</span></div>${list.map(o => {
    const people = IDX.byOrder[o.id] || [];
    return `<div class="ocard s-${o.status}" data-act="order" data-id="${o.id}"><div class="top" data-act="order" data-id="${o.id}"><span class="tm">${md(o.date)} ${o.start}</span>${crewMeter(o)}</div>
      <div class="ti" data-act="order" data-id="${o.id}">${redoTag(o) + esc(site(o.site_id).name || o.title)}</div><div class="m">${esc(site(o.site_id).area || '')} · ${o.start}–${o.end}</div>
      <div>${people.filter(a => a.status !== 'cancelled').map(a => `<span class="who ${a.status}">${esc(wname(a.worker_id))}·${LBL[a.status]}</span>`).join('')}</div><div class="btns" style="margin-top:10px">${act(o)}</div></div>`;
  }).join('') || '<div class="empty">沒有</div>'}</div>`;
  h += '<div class="kanban">' + lane('待派工', need, o => btn('挑人派工', 'pickWorker', { id: o.id }) + btn('系統自動派', 'autoDispatch', { id: o.id }, 'sec') + btn('派工文字', 'copyTxt', { id: o.id, kind: 'dispatch' }, 'gray'), 'lane-need')
    + lane('招募中', recruiting, o => (IDX.byOrder[o.id] || []).filter(a => a.status === 'applied').map(a => btn('選 ' + wname(a.worker_id), 'setAssign', { id: a.id, status: 'accepted' })).join('') + btn('再挑人', 'pickWorker', { id: o.id }, 'sec') + btn('派工文字', 'copyTxt', { id: o.id, kind: 'dispatch' }, 'gray'), 'lane-open')
    + lane('人到齊・待確認', wait, o => (o.customer_confirmed ? btn('排定', 'confirmOrder', { id: o.id }) : btn('複製確認訊息', 'copyTxt', { id: o.id, kind: 'confirm' }, 'sec') + btn('客戶已確認', 'custOk', { id: o.id })), 'lane-wait') + '</div>';
  const active = S.workers.filter(w => w.status === 'active'), month = t.slice(0, 7);
  h += `<h2>夥伴本月回報 <span class="g">${month}</span></h2><div class="card">${tbl(['夥伴', '可排天數', '不能排', '本月已排'], active.map(w => {
    const av = (S.availability || []).filter(a => a.worker_id === w.id && a.date.startsWith(month)), n = S.assignments.filter(a => a.worker_id === w.id && a.status === 'accepted' && (IDX.orders[a.order_id]?.date || '').startsWith(month) && IDX.orders[a.order_id].status !== 'cancelled').length;
    return `<tr class="click" data-act="worker" data-id="${w.id}">${td('夥伴', esc(w.name))}${td('可排', av.filter(a => a.kind === 'free').length || '<span class="small">未回報</span>')}${td('不能排', av.filter(a => a.kind === 'busy').length)}${td('已排', n)}</tr>`;
  }))}<p class="small">取代每月逐一私訊「這個月可以排哪幾天」：夥伴在官方 LINE 主控板「可排時間」點日期回報，自動派工會優先找有空的人。</p></div>`;
  return h;
};
ACT.copyShortage = () => { const list = S.orders.filter(o => ['review', 'draft', 'confirmed'].includes(o.status) && o.date >= today() && o.date <= addDays(today(), 14) && short(o)).sort(by); if (!list.length) throw new Error('兩週內沒有缺額'); return copy(TXT.shortage(list), '缺額總表'); };

/* ═════════ 客戶 ═════════ */
const CH_LABEL = { 賴群組: 'LINE 群組', 店長LINE: '店長個人 LINE', 關係企業: '關係企業／內部', 示範表單: '示範服務店表單', 歷史: '歷史資料' };
const chLabel = c => (S.brands || []).some(b => b.channel && b.channel === c.channel) ? '官方 LINE' : (CH_LABEL[c.channel] || c.channel || '');
const isBound = c => !!(c.bound && c.bound.friend && c.bound.acc === BRAND_ID(c));
function linePill(c) {          // 哪個客戶已經綁定 LINE，視覺上一眼看得出來
  const b = c.bound;
  if (b && b.acc !== BRAND_ID(c)) return `<span class="pill bad">⚠️ 綁在${esc(({ demoshop: '示範服務店', bizline: '企業線', homeline: '生活線' })[b.acc] || b.acc)}官方 LINE，傳不到</span>`;     // 稽核 B1：品牌不一致
  if (b && b.friend) return `<span class="pill ok">✅ LINE 已綁定</span>${b.name ? `<span class="s">${esc(b.name)}</span>` : ''}`;
  if (b) return '<span class="pill bad">已封鎖／刪好友</span>';
  return `<span class="pill">未綁定</span>${c.channel === '賴群組' ? '<span class="s">在 LINE 群組聯絡</span>' : ''}`;
}
PAGES.customers = () => {
  const q = (SEG.cq || '').toLowerCase(), t = today(), cb = SEG.cb || '';
  const act = c => ['active', 'paused'].includes(c.status);
  const cmode = SEG.cs || 'active', CS = { active: act, unbound: c => act(c) && !isBound(c), mis: c => !!(c.bound && c.bound.acc !== BRAND_ID(c)), lead: c => c.status === 'lead', ended: c => c.status === 'ended', all: () => true };
  const isMis = c => !!(c.bound && c.bound.acc !== BRAND_ID(c)), nMis = S.customers.filter(isMis).length, misMode = cb === 'mis';      // 「品牌和 LINE 不一致」放品牌下拉（分頁只放狀態）
  const inBrand = S.customers.filter(c => misMode ? isMis(c) : (!cb || BRAND_ID(c) === cb));            // 品牌（下拉）和狀態（分頁）分開篩，不混在一起
  const list = inBrand.filter(misMode ? CS.all : (CS[cmode] || CS.active)).filter(c => !q || [c.name, c.line_name, c.ln, c.phone, c.code, ...(IDX.sitesOf[c.id] || []).map(s => s.name + s.address), ...(S.customer_aliases || []).filter(a => a.customer_id === c.id).map(a => a.alias)].join(' ').toLowerCase().includes(q));
  let h = phead('客戶', '熟悉每一位客戶。', `${S.customers.length} 位客戶、${S.sites.length} 個服務地點。上面選狀態、下拉選品牌；搜尋可用姓名、LINE 名稱、電話、地址或排班表上的別名。`, btn('新增客戶', 'newCustomer') + ' ' + btn('地區分布地圖', 'goMap', {}, 'sec'));
  h += '<div class="segscroll">' + segctl('cs', [['active', '服務中', inBrand.filter(CS.active).length], ['unbound', '未綁定 LINE', inBrand.filter(CS.unbound).length], ['lead', '潛在客戶'], ['ended', '歷史客戶'], ['all', '全部']], misMode ? '' : cmode === 'mis' ? 'active' : cmode) + '</div>';
  if (misMode) h += '<div class="banner warn"><div>這幾位客戶的<b>品牌</b>跟他<b>綁定的官方 LINE</b> 不一樣：系統會用品牌的帳號傳提醒、完成照片 → 傳不到他。<br>看哪一個才對：品牌選錯了 → 按「品牌改成…」；LINE 綁錯人或綁錯帳號 → 按「解除這個 LINE」（不會通知對方，之後他在正確的官方 LINE 傳訊息就會重新配對）。</div></div>';
  h += `<div class="row" style="gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px"><select data-change="cbrand" aria-label="品牌" style="flex:0 0 auto;width:auto;min-width:180px">${[['', '全部品牌'], ...(S.brands || []).map(b => [b.id, b.name]), ...(nMis || misMode ? [['mis', `⚠️ 品牌和 LINE 不一致（${nMis}）`]] : [])].map(([k, n]) => `<option value="${esc(k)}"${k === cb ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select>`
    + `<input type="search" placeholder="搜尋客戶" value="${esc(SEG.cq || '')}" data-input="cq" aria-label="搜尋客戶" style="flex:1;min-width:200px;margin:0"></div>`;
  if (cmode === 'unbound') {
    if (!UH && !UH_LOADING) uhLoad();
    const BN = { demoshop: '示範服務店', bizline: '企業線', homeline: '生活線' }, fr = (UH && UH.friends) || {};
    h += `<div class="banner"><div>服務中、還沒綁官方 LINE 的客戶：系統傳不到提醒、完成照片。<br><b>為什麼查不到對話：</b>系統只認得「在官方 LINE 傳過訊息」的人（LINE 只有這時才給系統他的身分）。目前認得：${Object.keys(BN).map(k => `${BN[k]} ${fr[k] || 0} 位`).join('、')}。`
      + `歷史對話匯出檔只有名字和文字，沒有 LINE 身分，可以幫忙認人，但不能拿來傳訊息。在 LINE 群組、店長個人 LINE 聯絡的客戶，官方 LINE 裡本來就沒有他的聊天室。<br>`
      + `<b>怎麼對上：</b>請他加${'官方 LINE'}好友、傳一則訊息（或點「複製綁定訊息」的連結），系統就會自動列進「更多 › LINE 配對」；確定他不會加的按「他沒加官方 LINE」，每天 10:00 會列進打電話清單。<br>`
      + `<b>⚠️ 每一列的「複製綁定訊息」只給那一位客戶</b>（第一個人送出就綁成那位客戶，之後失效）。<b>要一次傳給很多客戶，請用下面的通用綁定訊息：</b>客戶送出後，系統用 LINE 名稱或電話找他的舊資料，對到電話的會列進「LINE 配對」等你確認。</div></div>`
      + `<div class="row" style="gap:8px;flex-wrap:wrap;margin:-6px 0 16px">${(S.brands || []).filter(b => ['demoshop', 'homeline', 'bizline'].includes(b.id)).map(b => btn('複製' + ({ demoshop: '示範服務店', bizline: '企業線', homeline: '生活線' })[b.id] + '通用綁定訊息', 'genBind', { b: b.id }, 'sec')).join(' ')}</div>`;
  }
  h += '<div class="card">' + tbl(['客戶', '品牌', 'LINE', '服務地點', '下次服務', '結帳方式', can('money') ? '>未收' : ''], list.sort((a, b) => (a.code || 'Z').localeCompare(b.code || 'Z') || a.name.localeCompare(b.name)).map(c => {
    const next = S.orders.filter(o => o.customer_id === c.id && o.date >= t && ['review', 'confirmed'].includes(o.status)).sort(by)[0];
    const bal = (S.invoices || []).filter(i => i.customer_id === c.id && i.status === 'issued').reduce((n, i) => n + i.balance, 0);
    return `<tr class="click" data-act="customer" data-id="${c.id}">${td('客戶', `<b>${esc(c.name)}</b>${c.code ? ` <span class="small">${esc(c.code)}</span>` : ''}<span class="s">${esc(c.line_name && c.line_name !== c.name ? 'LINE：' + c.line_name : '')}${c.phone ? ' · ' + esc(c.phone) : ''}</span>`)}${td('品牌', brandPill(c) + (chLabel(c) && chLabel(c) !== '官方 LINE' ? `<span class="s">${esc(chLabel(c))}</span>` : ''))}${td('LINE', linePill(c) + (misMode && c.bound ? ' ' + btn(`品牌改成${({ demoshop: '示範服務店', bizline: '企業線', homeline: '生活線' })[c.bound.acc] || c.bound.acc}`, 'brandFix', { id: c.id, acc: c.bound.acc }, 'sec') + ' ' + btn('解除這個 LINE', 'prUnpair', { acc: c.bound.acc, uid: c.bound.uid }, 'gray') : '') + (cmode === 'unbound' ? uhLine(c) + ' ' + btn('複製綁定訊息', 'bindMsg', { id: c.id, list: 1 }, 'sec') + (UH && UH.hints[c.id] && !UH.hints[c.id].none ? ' ' + btn('他沒加官方 LINE', 'pcfNone', { id: c.id }, 'gray') : '') : ''))}${td('地點', (IDX.sitesOf[c.id] || []).map(s => esc(s.name)).join('、'))}${td('下次', next ? md(next.date) + ' ' + next.start : '—')}${td('結帳', `<span class="small">${esc((c.billing_rule || '').slice(0, 24))}</span>`)}${can('money') ? td('未收', bal ? `<b style="color:var(--orange)">${money(bal)}</b>` : '—', 'r') : ''}</tr>`;
  })) + '</div>';
  return h;
};
ACT.wBindSent = async d => { const r = await api('/api/worker/bind-sent', { id: d.id }); await refresh(); return r.message; };      // 每天 10:00 不再列他
ACT.wBindMsg = async d => { const r = await api('/api/worker/bind-code', { id: d.id, tpl: d.tpl || '' }); if (r.linked) return '他已經綁定官方 LINE 了 👍'; await copy(r.text, d.tpl === 'play' ? '陪玩招募訊息' : '夥伴綁定訊息'); };      // tpl=play＝店長的陪玩招募範本
ACT.cbrand = d => { SEG.cb = d.value; route(true); };
ACT.brandFix = async d => {                       //：品牌改成他實際綁定的官方 LINE 帳號
  const c = IDX.customers[d.id], nm = ({ demoshop: '示範服務店', bizline: '企業服務線', homeline: '生活小幫手' })[d.acc] || d.acc;
  if (!confirm(`「${c.name}」的品牌改成「${nm}」？之後提醒、報價卡都用${nm}的官方 LINE 傳。`)) return;
  await api('/api/save/customers', { id: c.id, version: c.version, line_account: d.acc }); await refresh(); return `已改成${nm}`;
};
let UH = null, UH_LOADING = false;           // 未綁定的為什麼查不到對話
async function uhLoad() { UH_LOADING = true; try { UH = await api('/api/customers/unbound-hints'); } catch (e) { UH = { hints: {}, friends: {}, error: e.message }; } finally { UH_LOADING = false; } if (VIEW === 'customers') route(true); }
function uhLine(c) {
  if (!UH) return '<span class="s">查詢中…</span>';
  const x = UH.hints[c.id]; if (!x) return '';
  const BN = { demoshop: '示範服務店', bizline: '企業線', homeline: '生活線' }[BRAND_ID(c)] || '';
  if (x.cands.length) return `<span class="s" style="color:#248a3d">系統猜到：${x.cands.map(k => `${esc(k.name)}（${k.score} 分，${esc(k.why)}）`).join('、')} → 到「更多 › LINE 配對」確認</span>`;
  if (x.none) return '<span class="s">已標記：他沒加官方 LINE（每天 10:00 列進打電話清單）</span>';
  if (x.hist.length) return `<span class="s">有歷史對話「${esc(x.hist[0])}」，但他還沒在${BN}官方 LINE 傳過訊息，系統拿不到他的 LINE 身分</span>`;
  return `<span class="s">${BN}官方 LINE 裡沒有他的聊天室（多半在 LINE 群組或個人 LINE 聯絡）</span>`;
}
ACT.pcfNone = async d => { if (!confirm('確定他沒加官方 LINE？之後每天 10:00 會列進打電話清單。')) return; const r = await api('/api/pairing/none', { id: d.id }); UH = null; PR = null; await refresh(); return r.message; };
ACT.wq = d => { SEG.wq = d.value; clearTimeout(ACT.wq.t); ACT.wq.t = setTimeout(() => { const i0 = $('[data-input=wq]'); if (i0 && i0.dataset.composing) return; route(true); const i = $('[data-input=wq]'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }, 350); };
ACT.cq = d => { SEG.cq = d.value; clearTimeout(ACT.cq.t); ACT.cq.t = setTimeout(() => { const i0 = $('[data-input=cq]'); if (i0 && i0.dataset.composing) return; route(true); const i = $('[data-input=cq]'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }, 350); };
document.addEventListener('compositionstart', e => { if (e.target.dataset) e.target.dataset.composing = '1'; });
document.addEventListener('compositionend', e => { if (e.target.dataset) delete e.target.dataset.composing; });
ACT.urgAdd = d => sheet('🚨 緊急加派一位', '<p>人數多一位，照分級先問 A 級、沒人接每 10 分鐘問下一級，先按先得；原本的人照常做。有人接管理群會收到通知，新接的人直接可以打卡。</p>'
  + fld('why', '原因（會寫在派工卡注意事項）', '業主改時間／現場需要多一位', 'text'), async f => (await api('/api/order/urgent-add', { id: d.id, why: f.why })).message, '加派');
ACT.ddStop = d => sheet('停止垃圾代丟', '<p>這位業主的垃圾代丟月方案停止：明天以後還沒做的都取消（不通知業主，請在 LINE 跟業主說明），固定夥伴收到「不用再去」。</p>'
  + fld('reason', '原因', '業主不續約', 'text'), async f => (await api('/api/daidiu/stop', { id: d.id, reason: f.reason })).message, '停止');          // 
ACT.delSite = d => {          // 業主自己新增的地點（重複、打錯）可以刪；有工單的合併到正確的地點
  const s = IDX.sites[d.id]; if (!s) return;
  const n = S.orders.filter(o => o.site_id === s.id).length, others = (IDX.sitesOf[s.customer_id] || []).filter(x => x.id !== s.id);
  if (!n) return sheet(`刪除地點：${esc(s.name)}`, `<p>${esc(s.address || '（沒有地址）')}</p><p class="small">這個地點沒有工單，刪了會放到回收桶（「更多 › 待整理 › 回收桶」可以還原）。</p>`,
    async () => (await api('/api/site/delete', { id: s.id })).message, '刪除這個地點');
  if (!others.length) return toast(`這個地點有 ${n} 張工單，不能刪；請按「編輯」改成正確的地址`, true);
  return sheet(`合併地點：${esc(s.name)}`, `<p>${esc(s.address || '（沒有地址）')}</p><p class="small">這個地點有 ${n} 張工單，不能直接刪。選正確的地點：工單會移過去（地址、進場方式空白的會補過去），這個地點再放回收桶。</p>`
    + fld('target', '合併到', others[0].id, 'select', { options: others.map(x => [x.id, `${x.name}｜${x.address || '地址待補'}`]) }),
    async f => (await api('/api/site/merge', { id: s.id, target: f.target })).message, '合併並刪除');
};
ACT.customer = d => {
  NEXT_SHEET = CUST_REOPEN[d.id] = CUST_REOPEN[d.id] || (() => ACT.customer({ id: d.id }));
  const c = IDX.customers[d.id]; if (!c) return;
  const sites = IDX.sitesOf[c.id] || [], orders = S.orders.filter(o => o.customer_id === c.id && o.status !== 'archived').sort(by), t = today();
  const hist = S.orders.filter(o => o.customer_id === c.id).length, aliases = (S.customer_aliases || []).filter(a => a.customer_id === c.id);
  let h = `<dl class="kv"><dt>品牌</dt><dd>${brandPill(c)}　<span class="small">聯絡：${esc(chLabel(c) || '—')}</span></dd><dt>編號</dt><dd>${esc(c.code || '—')}</dd><dt>LINE</dt><dd>${linePill(c)}${c.line_name ? `　<span class="small">${esc(c.line_name)}</span>` : ''}</dd><dt>電話</dt><dd>${c.phone ? `<a href="tel:${esc(c.phone)}">${esc(c.phone)}</a>` : '—'}</dd>
    <dt>結帳</dt><dd>${esc(({ '': '先結（預設）', '全額預付': '先結：全額預付', '訂金尾款': '先收訂金 30%＋尾款', '完工後付款': '後結：完工後付款', '每月月結': '每月月結' })[c.pay_method || ''] || c.pay_method)}${c.billing_rule ? '・' + esc(c.billing_rule) : ''}</dd>${S.customer_tiers ? `<dt>客戶等級</dt><dd>${custTierSel(c)} <span class="small">A＝要培養黏著度（先問 A 級夥伴）；C＝利潤低（先問 C 級）。第一次來的客人一律先問 A 級。${S.tier_route ? '' : '（管理群同意新派工規則後生效）'}</span></dd>` : ''}${c.notes ? `<dt>重要提醒</dt><dd>${esc(c.notes)}</dd>` : ''}<dt>別名</dt><dd>${aliases.map(a => esc(a.alias)).join('、') || '—'}</dd></dl>`;
  h += `<div class="btns" style="margin-top:0">${btn('複製 LINE 綁定訊息', 'bindMsg', { id: c.id })}${btn('編輯客戶', 'editCustomer', { id: c.id }, 'sec')}${btn('新增地點', 'editSite', { cid: c.id }, 'sec')}${btn('加別名', 'addAlias', { id: c.id }, 'gray')}${btn('新增工單', 'newOrderFor', { id: c.id }, 'gray')}${can('ops') ? btn('開報價單', 'quoteNew', { cid: c.id }, 'gray') : ''}${btn('合併重複', 'mergeInto', { id: c.id, t: 'customers' }, 'gray')}${btn('移到待整理', 'toTriage', { id: c.id, e: 'customers' }, 'gray')}</div>`;
  h += `<div class="secttl"><h3>服務地點</h3></div>` + sites.map(s => `<div class="item"><div class="t"><div class="n">${esc(s.name)} <span class="small">${esc(s.service_type || '')}</span></div><div class="m">${esc(s.address || '地址待補')}${s.visit_price != null && can('money') ? ' · 每次 ' + money(s.visit_price) : ''}${s.rate && can('money') ? ' · 時薪 $' + esc(s.rate) : ''}</div>${s.entry_notes ? `<div class="m">📌 ${esc(s.entry_notes)}</div>` : ''}${s.checklist ? `<div class="m" style="white-space:pre-wrap">🧹 每次重點：${esc(s.checklist)}</div>` : ''}${s.worker_info ? `<div class="m" style="white-space:pre-wrap">📋 須知：${esc(s.worker_info)}</div>` : ''}${((S.site_routines || {})[s.id] || []).map(r => `<div class="m">🔁 ${esc(r.title)}（${esc(RT_EVERY[r.every] || '')}）：${esc((r.tasks || '').split('\n').join('、'))}　<span class="small">上次做：${esc(r.last_done || '還沒記錄')}</span></div>`).join('')}${((S.site_photos || {})[s.id] || []).length ? `<div class="m" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">${S.site_photos[s.id].map(([pid, cap]) => `<a href="/api/site-photo/${pid}" target="_blank" rel="noopener" title="${esc(cap || '')}"><img src="/api/site-photo/${pid}?t=1" style="width:64px;height:64px;object-fit:cover;border-radius:8px"></a>`).join('')}</div>` : ''}</div><div class="m">🚪 ${esc(ENTRY_TXT[s.entry_mode] || '開門方式還沒確認')}${s.entry_ok ? '（已確認）' : ''}${s.no_photo ? '・📵 場內不能拍照' : ''}</div></div><div class="a">${btn('🚪 開門方式', 'entrySet', { id: s.id }, 'sec')}${btn('🧹 工具', 'toolsSet', { id: s.id }, 'sec')}${btn('編輯', 'editSite', { id: s.id }, 'gray')}${btn('須知照片', 'sitePhotos', { id: s.id }, 'gray')}${btn('🔁 定期項目', 'routineSet', { id: s.id }, 'gray')}${btn('刪除／合併', 'delSite', { id: s.id }, 'gray')}</div></div>`).join('');
  const cts = (S.contacts || []).filter(x => x.customer_id === c.id);      // 盤點：現場聯絡人（家人、管理員、代管）——夥伴排定後在工作卡看得到，到場前可以先聯絡
  h += `<div class="secttl"><h3>現場聯絡人</h3>${btn('新增', 'contactForm', { cid: c.id }, 'gray')}</div>` + (cts.map(x => `<div class="item"><div class="t"><div class="n">${esc(x.name)} <span class="small">${esc(x.role || '')}</span></div><div class="m">${x.phone ? `<a href="tel:${esc(x.phone)}">${esc(x.phone)}</a>` : '—'}${x.notes ? ' · ' + esc(x.notes) : ''}</div></div><div class="a">${btn('編輯', 'contactForm', { id: x.id }, 'gray')}</div></div>`).join('') || '<div class="empty">沒有（家人、大樓管理員、代管窗口可以加在這裡，夥伴排定後看得到）</div>');
  const up = orders.filter(o => o.date >= t && o.status !== 'cancelled'), past = orders.filter(o => o.date < t || o.status === 'cancelled').reverse();
  h += `<div class="secttl"><h3>接下來 ${up.length} 場</h3></div>` + (up.slice(0, 8).map(o => ocard(o, { date: true })).join('') || '<div class="empty">沒有排程</div>');
  h += `<div class="secttl"><h3>最近服務</h3><span class="small">含歷史共 ${hist} 筆</span></div>` + (past.slice(0, 6).map(ocard).join('') || '<div class="empty">沒有紀錄</div>');
  if (can('money')) { const inv = (S.invoices || []).filter(i => i.customer_id === c.id); if (inv.length) h += `<div class="secttl"><h3>帳單</h3></div>` + inv.map(i => `<div class="item click" data-act="invoiceDetail" data-id="${i.id}" style="cursor:pointer"><div class="t"><div class="n">${esc(i.period)} ${st(i.status)} ›</div><div class="m">應收 ${money(i.total)} · 已收 ${money(i.paid)} · 未收 ${money(i.balance)}</div></div></div>`).join(''); }
  const threads = (S.chat_threads || []).filter(t => t.customer_id === c.id);
  if (threads.length) h += `<div class="secttl"><h3>LINE 對話</h3></div>` + threads.map(t => `<div class="item"><div class="t click" data-act="openThread" data-id="${t.id}"><div class="n">${esc(t.source)}</div><div class="m">${esc(t.first_at.slice(0, 10))}–${esc(t.last_at.slice(0, 10))} · ${t.count} 則</div></div></div>`).join('');
  sheet(esc(c.name), h);
};
ACT.newOrderFor = d => { const s = (IDX.sitesOf[d.id] || [])[0]; orderForm(null, { site_id: s ? s.id : 'c:' + d.id, date: today() }); };      // 帶入這位客戶（沒有地點也帶入）
function customerForm(c) {
  c = c || { status: 'active', line_account: SEG.cb || 'demoshop' };
  const oa = (S.brands || []).some(b => b.channel && b.channel === c.channel), chVal = !c.id || oa ? 'OA' : (c.channel || '');
  const brandF = isBound(c) ? `<div><label>品牌</label><p class="small" style="margin:8px 0 0">${brandPill(c)} 已綁定這個品牌的官方 LINE（品牌跟著綁定的帳號；要換請先到「更多 › LINE 配對」解除）</p></div>`
    : fld('line_account', '品牌（用哪個官方 LINE 聯絡、報價單抬頭）', BRAND_ID(c), 'select', { options: (S.brands || []).map(b => [b.id, b.name]) });
  sheet(c.id ? '編輯客戶' : '新增客戶', g2(fld('name', '客戶名稱（店名／社區名／姓名）', c.name), fld('code', '編號', c.code)) + g2(fld('line_name', 'LINE 名稱', c.line_name), fld('phone', '電話', c.phone))
    + g2(brandF, fld('channel', '聯絡管道（跟品牌分開選）', chVal, 'select', { options: [['OA', '官方 LINE'], ['賴群組', 'LINE 群組'], ['店長LINE', '店長個人 LINE'], ['關係企業', '關係企業／內部'], ['', '其他']] }))
    + fld('status', '狀態', c.status, 'select', { options: [['active', '服務中'], ['paused', '暫停'], ['lead', '潛在客戶（詢問未成交）'], ['ended', '歷史／已結束']] })
    + (can('money') ? fld('pay_method', '結帳方式（建工單後的收款；客戶不能自己選）', c.pay_method || '', 'select', { options: [['', '先結（預設：一萬以下全額、一萬以上訂金 30%）'], ['全額預付', '先結：全額預付'], ['訂金尾款', '先收訂金 30%＋尾款'], ['完工後付款', '後結：完工後付款（公司行號、特殊客戶）'], ['每月月結', '每月月結（次月 5 號前）'], ['當日結算', '當日結算（完工當天，例：企業線店家）']] })
      + fld('billing_rule', '結帳備註（例：需發票、統編、每次價）', c.billing_rule, 'textarea', { rows: 2 }) : '') + fld('notes', '重要提醒（客戶偏好、指定人員…；夥伴看不到）', c.notes, 'textarea')
    + `<label>指定夥伴（派工時先只邀請這些人，時間可以跟業主協調；60 分鐘沒人接才開放給其他人）</label><div class="chips">${S.workers.filter(w => w.status === 'active' || (c.preferred_workers || '').includes(w.id)).sort((a, b) => a.name.localeCompare(b.name)).map(w => `<label><input type="checkbox" class="pwk" value="${w.id}"${(c.preferred_workers || '').split(',').includes(w.id) ? ' checked' : ''}> ${esc(w.name)}</label>`).join('')}</div>`
    + (c.id ? '' : `<h3 style="margin-top:22px">第一個服務地點</h3>` + fld('s_name', '地點名稱（空白＝同客戶名）', '') + fld('s_address', '地址', '') + g2(fld('s_type', '服務類型', '一般到府服務', 'select', { options: SVC.map(x => [x, x]) }), fld('s_price', '每次價（元）', '', 'number'))),
    async f => {
      const bid = f.line_account || BRAND_ID(c);            // 聯絡管道選「官方 LINE」＝存成那個品牌的管道（舊資料、品牌判斷相容）
      const channel = f.channel === 'OA' ? (((S.brands || []).find(b => b.id === bid) || {}).channel || '示範') : f.channel;
      const r = await api('/api/save/customers', { ...f, channel, preferred_workers: $$('.pwk:checked').map(x => x.value).join(','), ...(c.id ? { id: c.id, version: c.version } : {}) });
      if (!c.id) await api('/api/save/sites', { customer_id: r.id, name: f.s_name || f.name, address: f.s_address, service_type: f.s_type, visit_price: toCents(f.s_price) });
      return '已儲存';
    });
}
const SVC = ['一般到府服務', '店家服務', '年度保養／細清', '大型案場服務', '店面／辦公室服務', '搬遷前後服務', '交屋前整理', '全室服務', '陪玩姊姊', '私人管家（家務）', '私人管家（生活管理）', '其他'];
const EXTRA = [['kid_ages', '兒童年齡（陪玩）', '例：5 歲、8 歲'], ['allergy', '過敏及活動限制（陪玩）', '例：花生過敏、不能跑跳'], ['parent', '在場家長（陪玩，必填才可服務）', '例：生活線全程在家'],
  ['emergency', '緊急聯絡（陪玩）', '姓名＋電話'], ['tasks', '授權項目（管家）', '例：代購、收包裹、維修預約'], ['buy_limit', '單筆採買上限（管家）', '例：1000 元'], ['keys', '鑰匙與財物交接（管家）', '例：鑰匙放管理室，交接時拍照']];
ACT.newCustomer = () => customerForm();
ACT.genBind = async d => {                        // 一條連結傳給很多客戶
  const r = await api('/api/customer/bind-generic', { brand: d.b });
  await copy(r.text, ({ demoshop: '示範服務店', bizline: '企業線', homeline: '生活線' })[d.b] + '通用綁定訊息（可以傳給很多客戶）');
};
ACT.bindMsg = async d => {          // 新／舊客戶兩款綁定訊息＋引導圖
  if (!d.kind && !d.list) { const c = IDX.customers[d.id] || {};
    return sheet(`綁定訊息：${esc(c.name || '')}`, `<p class="small">選一款複製，貼到 LINE 傳給客戶；再把引導圖存下來一起傳。</p><div class="btns">${btn('複製「新客戶」版（短）', 'bindMsg', { id: d.id, kind: 'new' })}${btn('複製「老客戶」版', 'bindMsg', { id: d.id, kind: 'old' }, 'sec')}</div>
      <p class="small" style="margin-top:14px">引導圖（長按／右鍵存下來）：</p><img src="/bind-guide-${esc(BRAND_ID(c))}.png" alt="LINE 綁定引導圖" style="width:100%;max-width:360px;border-radius:12px;border:1px solid var(--hair)">`); }
  const r = await api('/api/customer/bind-code', { id: d.id, kind: d.kind || 'old' });
  if (r.linked) return toast('這位客戶已經綁定 LINE 了');
  await copy(r.text, { demoshop: '示範服務店', bizline: '企業線', homeline: '生活線' }[r.brand] + (d.kind === 'new' ? '新客戶' : '老客戶') + '綁定訊息');
  if (d.list) { const b = document.querySelector(`[data-act="bindMsg"][data-id="${d.id}"]`); if (b) b.textContent = '已複製 ✓'; }
};
ACT.unbound = async () => {
  const list = await api('/api/customers/unbound');
  sheet(`還沒綁 LINE 的客戶 <span class="g">${list.length}</span>`, `<p class="small">在 LINE 官方帳號後台跟客戶一對一聊天時，按「複製綁定訊息」貼給他。<b>只傳給你確定的客戶</b>；一對一聊天不算推播額度。客戶點連結送出就完成，資料不用重填。</p>`
    + list.map(c => `<div class="item"><div class="t"><div class="n">${esc(c.name)} <span class="small">${esc(c.channel || '')}</span></div><div class="m">${esc(c.line_name ? 'LINE：' + c.line_name : '')} ${esc(c.phone || '')}</div></div><div class="a">${btn('複製綁定訊息', 'bindMsg', { id: c.id, list: 1 }, 'sec')}</div></div>`).join(''));
};
ACT.editCustomer = d => customerForm(IDX.customers[d.id]);
ACT.vcForm = d => {
  if (!(S.vendors || []).some(v => v.status === 'active')) return ACT.vendorForm({ then: d.oid });
  sheet('新增外包成本', fld('vendor_id', '廠商', '', 'select', { options: S.vendors.filter(v => v.status === 'active').map(v => [v.id, `${v.name}（${v.category || '其他'}）`]) })
    + fld('item', '項目（例：窗簾拆洗 6 片）', '') + fld('amount', '金額（元，付給廠商）', '', 'number') + `<p class="small">沒有的廠商請先到「結帳 › 配合廠商」新增。</p>`,
    async f => { await api('/api/vendor-cost/save', { ...f, order_id: d.oid, amount: toCents(f.amount) }); await refresh(); return '已記錄'; });
};
ACT.vcDel = async d => { if (!confirm('刪除這筆外包成本？')) return; await api('/api/vendor-cost/delete', { id: d.id }); await refresh(); return '已刪除'; };
ACT.vendorForm = d => {
  const v = d.id ? (S.vendors || []).find(x => x.id === d.id) : {};
  sheet(d.id ? '編輯廠商' : '新增配合廠商', fld('name', '廠商名稱', v.name || '') + fld('category', '類別', v.category || '其他', 'select', { options: (S.vendor_categories || ['其他']).map(x => [x, x]) })
    + fld('phone', '電話', v.phone || '') + fld('bank', '匯款帳號（銀行、帳號、戶名）', v.bank || '') + fld('notes', '備註（報價方式、配合條件）', v.notes || '', 'textarea')
    + fld('status', '狀態', v.status || 'active', 'select', { options: [['active', '配合中'], ['paused', '暫停']] }),
    async f => { await api('/api/vendor/save', { ...f, ...(d.id ? { id: d.id } : {}) }); await refresh(); BILL = null; return '已儲存'; });
};
ACT.vendorPaid = async d => {
  const ref = prompt('匯款日期／帳號末五碼／備註：', ''); if (ref === null) return;
  await api('/api/vendor-cost/paid', { ids: d.ids.split(','), ref }); await refresh(); return '已登記付款';
};
ACT.contactForm = d => {
  const x = d.id ? (S.contacts || []).find(y => y.id === d.id) : { customer_id: d.cid };
  sheet(d.id ? '編輯現場聯絡人' : '新增現場聯絡人', fld('name', '姓名／稱呼', x.name || '') + fld('role', '關係', x.role || '家人', 'select', { options: [['家人', '家人'], ['大樓管理員', '大樓管理員'], ['代管／房東', '代管／房東'], ['公司窗口', '公司窗口'], ['請款窗口', '請款窗口（夥伴看不到）'], ['其他', '其他']] })
    + fld('phone', '電話', x.phone || '') + fld('notes', '注意事項（例：只有平日在、要先打電話）', x.notes || '', 'textarea'),
    async f => { await api('/api/save/contacts', { ...f, customer_id: x.customer_id, ...(x.id ? { id: x.id, version: x.version } : {}) }); await refresh(); return '已儲存'; });
};
ACT.sitePhotos = d => {      // 案場須知照片（鑰匙、備品櫃位置…），接單後夥伴看得到
  const s = IDX.sites[d.id], ph = (S.site_photos || {})[d.id] || [];
  sheet(`須知照片：${esc(s.name)}`, (ph.length ? `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">${ph.map(([pid, cap]) => `<div style="text-align:center"><a href="/api/site-photo/${pid}" target="_blank" rel="noopener"><img src="/api/site-photo/${pid}?t=1" style="width:96px;height:96px;object-fit:cover;border-radius:10px"></a><div class="small">${esc(cap || '')}</div>${btn('刪除', 'sitePhotoDel', { id: pid }, 'gray')}</div>`).join('')}</div>` : '<p class="small">還沒有須知照片。</p>')
    + fld('caption', '照片說明（例：備品櫃鑰匙在這裡）', '') + '<label for="f-file">選照片（可多張）</label><input id="f-file" type="file" accept="image/jpeg,image/png" multiple>',
    async f => { const files = [...$('#f-file').files]; if (!files.length) throw new Error('請選照片'); for (const file of files) await api('/api/site/photo', { site_id: d.id, caption: f.caption, content: await shrink(file) }); await refresh(); return `已上傳 ${files.length} 張（夥伴接單後看得到）`; }, '上傳');
};
const RT_EVERY = { 1: '每月', 2: '每兩個月', 3: '每季', 6: '每半年' };
ACT.routineSet = d => {      // 客戶01：定期特殊服務（這一期還沒做 → 派工卡、提醒卡會帶，夥伴按「做完了」才算）
  const s = IDX.sites[d.id], rs = (S.site_routines || {})[d.id] || [], r = d.rid ? rs.find(x => x.id === d.rid) || {} : {};
  sheet(`定期項目：${esc(s.name)}`, (rs.length ? rs.map(x => `<div class="item"><div class="t"><div class="n">${esc(x.title)}（${esc(RT_EVERY[x.every])}）</div><div class="m" style="white-space:pre-wrap">${esc(x.tasks)}</div><div class="m">上次做：${esc(x.last_done || '還沒記錄（下一場就會帶）')}</div></div><div class="a">${btn('改', 'routineSet', { id: d.id, rid: x.id }, 'gray')}${btn('刪除', 'routineDel', { id: x.id }, 'gray')}</div></div>`).join('') : '<p class="small">還沒有定期項目。例：每兩個月拆洗冷氣濾網、電梯、抽油煙機；每月樓梯扶手、床底深層吸拖。</p>')
    + `<h3 style="margin-top:14px">${r.id ? '修改' : '新增'}一組</h3>`
    + g2(fld('every', '多久做一次', r.every || 2, 'select', { options: Object.entries(RT_EVERY) }), fld('title', '名稱（例：設備＆浴廁清洗）', r.title || ''))
    + fld('tasks', '要做哪些（一行一項）', r.tasks || '', 'textarea', { rows: 4 })
    + fld('last_done', '上次做的日期（不知道就空白＝下一場就做）', r.last_done || '', 'date'),
    async f => { await api('/api/site/routine', { ...f, site_id: d.id, ...(r.id ? { id: r.id } : {}) }); await refresh(); return '已儲存（這一期還沒做的，派工卡、提醒卡會帶）'; });
};
ACT.routineDel = async d => { if (!confirm('刪除這組定期項目？')) return; await api('/api/site/routine-del', { id: d.id }); await refresh(); toast('已刪除'); };
ACT.omDel = async d => { if (!confirm('刪除這個業主傳的照片／影片？（派工卡就看不到了）')) return; await api('/api/order-media/del', { id: d.id }); await refresh(); toast('已刪除'); };
ACT.sitePhotoDel = async d => { if (!confirm('刪除這張須知照片？')) return; await api('/api/site/photo-del', { id: d.id }); await refresh(); toast('已刪除'); };
ACT.editSite = d => {
  const s = d.id ? IDX.sites[d.id] : { customer_id: d.cid };
  sheet(s.id ? '編輯服務地點' : '新增服務地點', fld('name', '地點名稱', s.name) + fld('address', '地址', s.address) + `<div class="g2"><div><label>縣市</label><select id="s-county"></select></div><div><label>鄉鎮區</label><select id="s-town"></select></div></div><input type="hidden" name="area" value="${esc(s.area || '')}">` + g2('', fld('service_type', '服務類型', s.service_type || '一般到府服務', 'select', { options: SVC.map(x => [x, x]) }))
    + (can('money') ? g2(fld('visit_price', '每次價（元）', s.visit_price == null ? '' : s.visit_price / 100, 'number'), fld('rate', '時薪計費（元／小時）', s.rate ?? '', 'number')) + fld('worker_rate', '夥伴時薪（這個案場固定；空白＝照夥伴本人時薪）', s.worker_rate ?? '', 'number') + fld('pricing_note', '報價說明（方案、稅）', s.pricing_note) : '')
    + fld('entry_mode', '開門方式（只有「業主親自開門」會通知業主；其他到場打卡＝已進入）', s.entry_mode || '', 'select', { options: [['', '還沒確認'], ...ENTRY] })
    + fld('navigation', 'Google 地圖連結／正確入口', s.navigation) + fld('entry_notes', '進場方式（鑰匙、門禁、停車；只給接單夥伴看）', s.entry_notes, 'textarea', { rows: 2, hint: '密碼建議用完就改，不要寫在派工文字。' })
    + g2(fld('tools', '現場工具（夥伴要不要自備）', s.tools || '', 'select', { options: [['', '還沒問業主'], ['full', '業主工具齊全（用業主的）'], ['basic', '業主只有基本工具，其他自備'], ['none', '業主沒有工具，全部自備']] }), fld('tools_note', '工具說明（選填）', s.tools_note || ''))
    + fld('checklist', '每次服務重點（一行一項；派工卡、工作頁都看得到）', s.checklist, 'textarea', { rows: 4 })
    + fld('worker_info', '給夥伴的現場須知（備品櫃鑰匙放哪、注意事項…；接單後才給夥伴看）', s.worker_info || '', 'textarea', { rows: 3 }) + fld('notes', '內部備註（夥伴看不到，可以寫特殊資料）', s.notes, 'textarea', { rows: 2 })
    + fld('signin_photo', '要拍簽到表（例：客戶02）：夥伴工作頁多一個「簽到表照片」，沒拍不能離場打卡', s.signin_photo, 'check')
    + fld('trash_mode', '服務完垃圾怎麼處理（業主在 LINE 選的，也可以這裡改）', s.trash_mode || '', 'select', { options: [['', '還沒問'], ['pack', '打包好放門口，業主自己丟'], ['bin', '拿到社區／大樓垃圾集中處'], ['none', '沒有要處理的垃圾']] })
    + fld('no_photo', '場內不能拍照（業主規定，例：客戶03）：夥伴不用拍服務前／完成照片，薪資照常，業主收沒有照片的完工卡', s.no_photo, 'check'),
    async f => { const p = { ...f, customer_id: s.customer_id }; if (can('money')) p.visit_price = toCents(f.visit_price); await api('/api/save/sites', s.id ? { ...p, id: s.id, version: s.version } : p); return '已儲存'; });
  { const R = regionSingle($('#s-county'), $('#s-town'), s.area || '臺北市'), sync = () => { $('#sheetForm [name=area]').value = R.get(); };
    $('#s-county').addEventListener('change', sync); $('#s-town').addEventListener('change', sync); if (!s.area) sync(); }
};
ACT.toolsSet = d => {                 // 舊客戶的工具管理層直接填（不問業主）
  const s = IDX.sites[d.id]; if (!s) return toast('找不到地點', true);
  const have = ((s.tools_note || '').match(/現場有：([^；]*)/) || [, ''])[1].split('、').filter(Boolean), rest = (s.tools_note || '').replace(/現場有：[^；]*；?/, '');
  const items = ['基本工具組', '專業工具', '量測工具', '延長線', '手機／平板', '耗材', '防護用品', '推車', '收納箱'];
  sheet(`現場工具：${esc(s.name)}`, '<p class="small">設定後夥伴的派工卡、工作頁照這個寫，系統不會再用 LINE 問業主。</p>'
    + fld('tools', '業主現場的工具', s.tools || 'basic', 'select', { options: [['full', '業主工具齊全（用業主的）'], ['basic', '業主只有基本工具，其他自備'], ['none', '業主沒有工具，全部自備']] })
    + `<label>現場有哪些（可複選）</label><div class="chips" id="tHave">${items.map(t => `<label><input type="checkbox" value="${t}"${have.includes(t) ? ' checked' : ''}> ${t}</label>`).join('')}</div>`
    + fld('note', '說明（選填，例：吸塵器在陽台）', rest),
    async f => { const r = await api('/api/site/tools', { site_id: d.id, tools: f.tools, note: f.note, have: [...document.querySelectorAll('#tHave input:checked')].map(x => x.value) }); await refresh(); return r.message; });
};
ACT.entrySet = d => {                 // 舊客戶的開門方式，管理層直接按（設定後＝已確認，不會再問業主）
  const s = IDX.sites[d.id];
  sheet(`開門方式：${esc(s.name)}`, '<p class="small">設定後系統記成「已確認」，不會再用 LINE 問業主；「業主在家開門」的，前一天會請業主按「會去開門」。</p>'
    + fld('entry_mode', '開門方式', s.entry_mode || '', 'select', { options: [['', '還沒確認'], ...ENTRY] })
    + fld('entry_notes', '說明（密碼、鑰匙位置、管理員電話；只給當天接單的夥伴看）', s.entry_notes, 'textarea', { rows: 3, ph: '例：密碼鎖＋大門鑰匙（鑰匙在信箱）' }),
    async f => { if (!f.entry_mode) throw new Error('請選開門方式'); await api('/api/save/sites', { id: s.id, version: s.version, entry_mode: f.entry_mode, entry_notes: f.entry_notes }); await refresh(); return '已設定（已確認）'; });
};
ACT.addAlias = d => sheet('加別名', '<p class="small">排班表、LINE 上對這位客戶的其他叫法（例：客戶15、客戶14-客戶15）。以後匯入或搜尋都對得到。</p>' + fld('alias', '別名', '') + fld('site_id', '對應地點', '', 'select', { options: optsOf(IDX.sitesOf[d.id] || [], s => s.name, '不指定') }), async f => { await api('/api/alias', { customer_id: d.id, ...f }); return '已加入'; });

/* ═════════ 夥伴 ═════════ */
PAGES.workers = () => {
  const BL = S.worker_blacklist || {}, isBL = w => !!BL[w.id], q = (SEG.wq || '').trim().toLowerCase();
  const WB = w => ((S.line_bound || {}).workers || []).includes(w.id), WSENT = w => (S.worker_bind_sent || {})[w.id];
  const WS = { active: w => w.status === 'active' && !isBL(w), nobind: w => ['active', 'review'].includes(w.status) && !isBL(w) && !WB(w), review: w => w.status === 'review' && !isBL(w), paused: w => w.status === 'paused' && !isBL(w), black: isBL, all: () => true };
  const mode = SEG.wk || 'active', list = S.workers.filter(WS[mode] || WS.all).filter(w => !q || [w.name, w.phone, w.regions, w.skills, w.availability, w.notes, ((S.worker_line || {})[w.id] || {}).name].join(' ').toLowerCase().includes(q));
  const user = w => (S.users || []).find(u => u.worker_id === w.id);
  let h = phead('夥伴', '一起，把服務做好。', '只有「可派工」的夥伴會出現在挑人名單。夥伴綁定示範服務店官方 LINE 後，接單、可排時間、打卡、拍照、請假、收入都在 LINE 完成，不用裝 App。', btn('新增夥伴', 'newWorker') + ' ' + btn('可排時間總表', 'avTable', {}, 'sec') + ' ' + btn('地區分布地圖', 'goMap', {}, 'sec'));
  h += segctl('wk', [['active', '可派工', S.workers.filter(WS.active).length], ['nobind', 'LINE 還沒綁', S.workers.filter(WS.nobind).length], ['review', '待核對'], ['paused', '暫停'], ['black', '黑名單', Object.keys(BL).length || ''], ['all', '全部']], mode);
  const wt = SEG.wt || '', wr = SEG.wr || '', TIER = w => ((S.worker_tiers || {})[w.id] || 'B'), list2 = list.filter(w => (!wt || TIER(w) === wt) && (!wr || WROLES(w).includes(wr)));      // 派工分級（沒排的算 B）、職類
  h += `<div class="row" style="gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px"><select data-change="wTierFilter" aria-label="派工分級" style="flex:0 0 auto;width:auto;min-width:150px">${[['', '全部分級'], ['A', 'A 級（先問）'], ['B', 'B 級'], ['C', 'C 級（最後問）']].map(([k, t]) => `<option value="${k}"${k === wt ? ' selected' : ''}>${t}${k ? `（${list.filter(w => TIER(w) === k).length}）` : ''}</option>`).join('')}</select>`
    + `<select data-change="wRoleFilter" aria-label="職類" style="flex:0 0 auto;width:auto;min-width:150px">${[['', '全部職類'], ...W_ROLES.map(r => [r, r])].map(([k, t]) => `<option value="${k}"${k === wr ? ' selected' : ''}>${t}${k ? `（${list.filter(w => WROLES(w).includes(k)).length}）` : ''}</option>`).join('')}</select>`
    + `<input type="search" placeholder="搜尋夥伴（姓名、LINE 名稱、電話、區域、會做的項目）" value="${esc(SEG.wq || '')}" data-input="wq" aria-label="搜尋夥伴" style="flex:1;min-width:200px;margin:0"></div>`;
  if (mode === 'nobind') h += '<div class="banner"><div>還沒綁官方 LINE 的夥伴：派工、提醒都會轉到派單管理群代問。按「複製綁定訊息」貼給他，傳了按「已傳給他了」——每天 10:00 的群組名單就不會再列他（綁好自動消失；傳過 14 天還沒綁才再提醒一次）。</div></div>';
  if (mode === 'active') h += '<p class="small">派工分級：新單缺人時先問業主指定／常配合 → A 級 → B 級 → C 級（離開工越久每級等越久）。在「分級」欄直接改，馬上生效；沒排的算 B。只有管理層看得到。</p>';
  h += '<div class="card">' + tbl(['夥伴', '分級', '區域', '可約時間', can('money') ? '時薪' : '', '官方 LINE', '本月'], list2.map(w => {
    const n = S.assignments.filter(a => a.worker_id === w.id && a.status === 'accepted' && (IDX.orders[a.order_id]?.date || '').startsWith(today().slice(0, 7)) && IDX.orders[a.order_id].status !== 'cancelled').length, u = user(w);
    return `<tr class="click" data-act="worker" data-id="${w.id}">${td('夥伴', `<b>${esc(w.name)}</b> ${isBL(w) ? '<span class="pill bad">停派（黑名單）</span>' : st(w.status)}${WROLES(w).filter(r => r !== '服務人員').map(r => ` <span class="pill">${esc(r)}</span>`).join('')}<span class="s">${esc(w.phone || '')}${((S.worker_line || {})[w.id] || {}).name ? '　LINE：' + esc(S.worker_line[w.id].name) : ''}</span>`)}${td('分級', tierSel(w))}${td('區域', esc(w.regions || '—'))}${td('時間', esc(w.availability || '—'))}${can('money') ? td('時薪', WROLES(w).includes('外包廠商') ? '案件計' : esc(w.rate || '—')) : ''}${td('LINE', WB(w) ? '✓ 已綁' : '<span class="small" style="color:var(--orange)">還沒綁</span>' + (WSENT(w) ? `<span class="s">${esc(WSENT(w).at.slice(5, 10).replace('-', '/'))} ${esc(WSENT(w).by)} 傳過</span>` : '') + (mode === 'nobind' ? ' ' + btn('複製綁定訊息', 'wBindMsg', { id: w.id }, 'sec') + ' ' + btn('陪玩招募版', 'wBindMsg', { id: w.id, tpl: 'play' }, 'gray') + (WSENT(w) ? '' : ' ' + btn('已傳給他了', 'wBindSent', { id: w.id }, 'gray')) : ''))}${td('本月', n + ' 場')}</tr>`;
  })) + '</div>';
  return h;
};
function custTierSel(c) {      // 客戶 A／B／C（店長設一次就好，不用每張單審）
  const t = (S.customer_tiers || {})[c.id] || 'B';
  return `<select data-act="noop" data-change="cTier" data-id="${esc(c.id)}" aria-label="${esc(c.name)} 的客戶等級" style="width:auto;min-width:72px;padding:6px 10px">${['A', 'B', 'C'].map(k => `<option value="${k}"${k === t ? ' selected' : ''}>${k} 客</option>`).join('')}</select>`;
}
ACT.cTier = async d => { try { const r = await api('/api/customer/tier', { id: d.id, tier: d.value }); (S.customer_tiers = S.customer_tiers || {})[d.id] = d.value; toast(r.message); } catch (e) { toast(e.message, true); } };
function tierSel(w) {          // 分級下拉：點了不會打開夥伴資料（data-act=noop），改了馬上存
  const t = (S.worker_tiers || {})[w.id] || 'B';
  return `<select data-act="noop" data-change="wTier" data-id="${esc(w.id)}" aria-label="${esc(w.name)} 的派工分級" style="width:auto;min-width:72px;padding:6px 10px">${(S.tier_rules ? ['A', 'B', 'C', 'D'] : ['A', 'B', 'C']).map(k => `<option value="${k}"${k === t ? ' selected' : ''}>${k} 級</option>`).join('')}</select>`;
}
ACT.noop = () => {};
const ENTRY = [['home', '業主親自開門（到了通知業主開門）'], ['bell', '按門鈴／有人在，可直接進入'], ['self', '密碼鎖／鑰匙，自行進門'], ['contact', '管理室／管理員開門']];      // 只有業主親自開門會通知業主，其他打卡＝已進入
const ENTRY_TXT = Object.fromEntries(ENTRY);
const W_ROLES = ['服務人員', '陪玩員', '家庭管家', '整理師', '外包廠商'];      // 外包廠商＝外縣市服務廠商（案件計酬、不自動派工）                    // 夥伴職類（可複選）
const WROLES = w => (w.roles || '服務人員').split(/[、,，]/).map(s => s.trim()).filter(Boolean);
ACT.wRoleFilter = d => { SEG.wr = d.value; route(true); };
ACT.wTierFilter = d => { SEG.wt = d.value; route(true); };
ACT.wTier = async d => { try { const r = await api('/api/worker/tier', { id: d.id, tier: d.value }); (S.worker_tiers = S.worker_tiers || {})[d.id] = d.value; toast(r.message); } catch (e) { toast(e.message, true); await refresh(); } };
ACT.worker = d => {
  NEXT_SHEET = WORKER_REOPEN[d.id] = WORKER_REOPEN[d.id] || (() => ACT.worker({ id: d.id }));
  const w = IDX.workers[d.id], u = (S.users || []).find(x => x.worker_id === w.id), t = today();
  const up = S.assignments.filter(a => a.worker_id === w.id && a.status === 'accepted').map(a => IDX.orders[a.order_id]).filter(o => o && o.date >= t && o.status !== 'cancelled').sort(by);
  let h = `<dl class="kv"><dt>狀態</dt><dd>${st(w.status)}</dd><dt>派工分級</dt><dd>${tierSel(w)} <span class="small">A 先問、C 最後問；只有管理層看得到</span></dd><dt>職類</dt><dd>${WROLES(w).map(esc).join('、')}</dd><dt>電話</dt><dd>${w.phone ? `<a href="tel:${esc(w.phone)}">${esc(w.phone)}</a>` : '—'}</dd><dt>區域</dt><dd>${esc(w.regions || '—')}</dd><dt>類型</dt><dd>${esc(w.skills || '—')}</dd><dt>自備工具</dt><dd>${esc(w.tools_own || '—')}</dd><dt>接單意願</dt><dd>${esc(w.willing || '—')}${w.gender ? '　性別：' + esc(w.gender) : ''}</dd><dt>可約</dt><dd>${esc(w.availability || '—')}${w.av_note ? `<div class="small">📝 ${esc(w.av_note)}</div>` : ''}</dd>${can('money') ? `<dt>時薪</dt><dd>${esc(w.rate || '—')}</dd>` : ''}<dt>備註</dt><dd>${esc(w.notes || '—')}</dd><dt>官方 LINE</dt><dd>${((S.line_bound || {}).workers || []).includes(w.id) ? '✓ 已綁定（派工、提醒都會直接傳給他）' : '還沒綁（派工會轉到派單管理群代問；下面按「複製夥伴綁定訊息」轉傳給他）'}</dd></dl>`;
  const wl = (S.worker_line || {})[w.id];
  if (!wl && ['active', 'review'].includes(w.status)) h += `<div class="banner warn">還沒綁官方 LINE：派工卡、提醒都會轉到派單管理群代問。${btn('複製夥伴綁定訊息', 'wBindMsg', { id: w.id }, 'sec')} ${btn('複製陪玩招募訊息', 'wBindMsg', { id: w.id, tpl: 'play' }, 'gray')}${(S.worker_bind_sent || {})[w.id] ? '' : ' ' + btn('已傳給他了', 'wBindSent', { id: w.id }, 'gray')}<br><span class="small">貼給他，他按連結、送出就完成（14 天內有效）。${(S.worker_bind_sent || {})[w.id] ? `${esc(S.worker_bind_sent[w.id].at.slice(5, 10).replace('-', '/'))} ${esc(S.worker_bind_sent[w.id].by)} 已傳過，每天的群組名單不再列他。` : '傳了按「已傳給他了」，每天的群組名單就不會再列他。'}</span></div>`;
  if (wl) h += `<div class="banner">LINE 名稱：<b>${esc(wl.name || '—')}</b>（${esc(wl.brand || '')}官方帳號的好友）　<a class="btn sm" href="${esc(wl.chat)}" target="_blank" rel="noopener">💬 開 LINE 聊天</a> ${btn('複製 LINE 名稱', 'copyTxt', { t: wl.name || '' }, 'sec')}<br><span class="small">會打開 LINE 官方帳號的聊天後台；選「${esc(wl.brand || '')}」，在搜尋框貼上 LINE 名稱就找得到他。</span></div>`;      // 直達聊天室的舊網址格式不對（管理員按也錯），先改成開聊天後台＋複製名稱
  h += `<div class="btns" style="margin-top:0">${btn('合併重複', 'mergeInto', { id: w.id, t: 'workers' }, 'gray')}${btn('編輯', 'editWorker', { id: w.id }, 'sec')}${S.role === 'admin' && u ? btn('舊 App 帳號', 'userForm', { id: u.id, w: w.id }, 'gray') : ''}${btn('可排時間', 'availFor', { id: w.id }, 'gray')}${btn((S.worker_blacklist || {})[w.id] ? '移出黑名單' : '🚫 不再派給他', 'wBlack', { id: w.id }, 'gray')}${S.role === 'admin' ? btn('夥伴視角（他看到的主控板）', 'workerView', { id: w.id }, 'sec') : ''}${btn('移到待整理', 'toTriage', { id: w.id, e: 'workers' }, 'gray')}</div>`;
  h += `<div class="secttl"><h3>接下來 ${up.length} 場</h3></div>` + (up.slice(0, 10).map(o => ocard(o, { date: true })).join('') || '<div class="empty">沒有排班</div>');
  const pf = (S.worker_pf || {})[w.id];            // 應徵時放的作品集，錄取後留在夥伴資料
  if (pf && (pf.files.length || pf.link || pf.text)) h += `<div class="secttl"><h3>應徵作品集</h3></div>`
    + (pf.files.length ? `<div style="display:flex;gap:6px;flex-wrap:wrap">${pf.files.map(([id, k]) => `<a href="/api/worker-file?id=${esc(id)}" target="_blank" rel="noopener"><img src="/api/worker-file?id=${esc(id)}" alt="${k === 'feedback' ? '客戶好評截圖' : '作品集照片'}" loading="lazy" style="width:84px;height:84px;object-fit:cover;border-radius:10px"></a>`).join('')}</div>` : '')
    + (pf.link ? `<p class="small">作品集連結：<a href="${esc(pf.link)}" target="_blank" rel="noopener">${esc(pf.link)}</a></p>` : '') + (pf.text ? `<p class="small" style="white-space:pre-wrap">${esc(pf.text)}</p>` : '');
  sheet(esc(w.name), h);
};
ACT.workerView = async d => { const r = await api('/api/worker-links?id=' + encodeURIComponent(d.id)); open(r.portal, '_blank', 'noopener'); };
const AVL = a => !a.start ? '整天' : a.start === '08:00' && a.end === '13:00' ? '上午' : a.start === '13:00' && a.end === '18:00' ? '下午' : a.start + '–' + a.end;      // 可排時間：整天／上午／下午
const ckOf = aid => (S.checkins || []).find(x => x.assignment_id === aid) || {};
const seenLine = a => {      // 派工有沒有送到、夥伴有沒有在看 LINE
  const v = (S.crew_seen || {})[a.id]; if (!v) return '';
  if (!v[3]) return '<br><span class="small" style="color:var(--red)">📵 沒綁 LINE：收不到派工，請打電話</span>';
  const t = x => esc((x || '').slice(5, 16).replace('T', ' ')), ok = v[0] === 'sent', fail = (v[0] || '').startsWith('failed');
  const read = v[2] && v[1] && v[2] > v[1];
  return `<br><span class="small"${fail ? ' style="color:var(--red)"' : ''}>LINE：${!v[0] ? '這場沒傳過' : ok ? '已送達 ' + t(v[1]) : fail ? '送失敗（請打電話）' : (v[0] || '').startsWith('skipped') ? '沒發（' + esc(v[0].slice(8)) + '）' : '排隊中'}`
    + `・${v[2] ? (read ? '之後有在 LINE 動作 ' : '最後在 LINE 動作 ') + t(v[2]) : '從沒在 LINE 動作過'}${ok && !read && a.status === 'invited' ? '（可能還沒看，打電話問比較快）' : ''}</span>`;
};
const ckLine = a => { const k = ckOf(a.id), t = (S.timesheets || []).find(x => x.assignment_id === a.id); return k.arrived_at || t ? `<br><span class="small">打卡：到 ${esc((k.arrived_at || '').slice(11, 16) || (t ? t.start : '—'))}・離 ${esc((k.left_at || '').slice(11, 16) || (t ? t.end : '—'))}</span>` : '<br><span class="small">還沒打卡</span>'; };
ACT.setTimes = d => {           // 管理層手動改到場／離場打卡時間
  const a = (S.assignments || []).find(x => x.id === d.id) || {}, k = ckOf(d.id), t = (S.timesheets || []).find(x => x.assignment_id === d.id) || {}, o = IDX.orders[d.o] || {};
  sheet(`改打卡時間：${esc(wname(a.worker_id))}`, `<p class="small">${md(o.date)} ${o.start}–${o.end}。改了會同時改工時（薪資、月結都照工時算），不會通知業主；要寫原因，會留紀錄。</p>`
    + g2(fld('arrived', '到場', (k.arrived_at || '').slice(11, 16) || t.start || o.start, 'time'), fld('left', '離場（還沒離場可空白）', (k.left_at || '').slice(11, 16) || t.end || '', 'time'))
    + fld('pause', '休息（分鐘）', t.pause || 0, 'number') + fld('note', '更正原因（必填）', '', 'textarea', { rows: 2, ph: '例：夥伴忘了打卡，實際 9:30 到、13:00 離開' }),
    async f => { const r = await api('/api/order/set-times', { id: d.id, arrived: f.arrived, left: f.left, pause: Number(f.pause || 0), note: f.note }); await refresh(); return r.message; }, '儲存');
};
ACT.avTable = (d = {}) => {          // 大家的可排時間一張表
  const m = d.m || today().slice(0, 7), [y, mo] = m.split('-').map(Number), n = new Date(y, mo, 0).getDate();
  const nav = k => { const x = new Date(y, mo - 1 + k, 1); return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}`; };
  const ws = S.workers.filter(w => w.status === 'active' && !/^（示範）/.test(w.name) && !WROLES(w).includes('外包廠商')).sort((a, b) => a.name.localeCompare(b.name));
  const by = {}; for (const a of S.availability || []) if (a.date.startsWith(m)) (by[a.worker_id] = by[a.worker_id] || {})[+a.date.slice(8, 10)] = a;
  const cell = a => !a ? ['', ''] : a.kind === 'busy' ? ['✕', 'b'] : a.kind === 'unsure' ? ['?', 'u'] : !a.start ? ['○', 'f'] : a.start === '08:00' && a.end === '13:00' ? ['上', 'f'] : a.start === '13:00' && a.end === '18:00' ? ['下', 'f'] : ['◐', 'f'];
  const filled = ws.filter(w => by[w.id]).length, wd = i => new Date(y, mo - 1, i + 1).getDay();
  const head = '<tr><th class="nm">夥伴</th>' + Array.from({ length: n }, (_, i) => `<th class="${[0, 6].includes(wd(i)) ? 'we' : ''}">${i + 1}<br><small>${'日一二三四五六'[wd(i)]}</small></th>`).join('') + '</tr>';
  const body = ws.map(w => `<tr><td class="nm"><a href="#" data-act="availFor" data-id="${w.id}">${esc(w.name)}</a>${by[w.id] ? '' : ' <span class="pill bad">沒填</span>'}${w.av_note ? `<div class="small">📝 ${esc(w.av_note)}</div>` : ''}${w.availability ? `<div class="small">平常：${esc(w.availability)}</div>` : ''}</td>`
    + Array.from({ length: n }, (_, i) => { const [t, k] = cell((by[w.id] || {})[i + 1]); return `<td class="${k}">${t}</td>`; }).join('') + '</tr>').join('');
  sheet(`可排時間總表・${y} 年 ${mo} 月`, `<div class="btns" style="margin-top:0">${btn('‹ 上個月', 'avTable', { m: nav(-1) }, 'gray')}${btn('下個月 ›', 'avTable', { m: nav(1) }, 'gray')}</div>
    <p class="small">○ 整天可以　上／下＝只有上午／下午　◐ 自訂時段　✕ 不行　? 不確定　空白＝沒回報。已填 ${filled}／${ws.length} 位；沒填的系統在 LINE 提醒（每 3 天一次、每月最多 3 次）。點名字看細節、代填。</p>
    <div class="avtbl"><table>${head}${body}</table></div>`);
};
ACT.availFor = d => {
  const w = IDX.workers[d.id], m0 = today().slice(0, 7), list = (S.availability || []).filter(a => a.worker_id === w.id && a.date >= m0 + '-01').sort((a, b) => a.date.localeCompare(b.date));
  sheet(`${esc(w.name)} 的可排時間`, '<p class="small">夥伴在官方 LINE 主控板「可排時間」自己回報；這裡可以看，也可以幫他代填一天。</p>' + (w.av_note ? `<div class="banner">📝 ${esc(w.av_note)}</div>` : '')
    + (list.map(a => `<div class="item"><div class="t"><div class="n">${md(a.date)} ${a.kind === 'free' ? st('completed', '可以') : a.kind === 'unsure' ? st('review', '不確定') : st('cancelled', '不行')}</div><div class="m">${AVL(a)} ${esc(a.note || '')}</div></div></div>`).join('') || '<div class="empty">這個月起還沒有回報。</div>')
    + '<h3 style="margin-top:20px">代填一天</h3>' + g2(fld('date', '日期', today(), 'date'), fld('kind', '', 'free', 'select', { options: [['free', '可以'], ['busy', '不行']] }))
    + fld('slot', '時段（一天分上午／下午）', 'all', 'select', { options: [['all', '整天'], ['am', '上午（08:00–13:00）'], ['pm', '下午（13:00–18:00）'], ['custom', '自訂（填下面的從／到）']] })
    + g2(fld('start', '從（選「自訂」才要填）', '', 'time'), fld('end', '到', '', 'time')),
    async f => {
      const month = f.date.slice(0, 7), days = (S.availability || []).filter(a => a.worker_id === w.id && a.date.startsWith(month) && a.date !== f.date).map(a => ({ date: a.date, kind: a.kind, start: a.start, end: a.end, note: a.note }));
      if (f.kind === 'free' && f.slot === 'custom' && (!f.start || !f.end)) throw new Error('自訂時段請填從／到');
      days.push({ date: f.date, kind: f.kind, slot: f.kind === 'free' && ['am', 'pm'].includes(f.slot) ? f.slot : '', start: f.kind === 'free' && f.slot === 'custom' ? f.start : '', end: f.kind === 'free' && f.slot === 'custom' ? f.end : '' });
      await api('/api/availability', { worker_id: w.id, month, days }); return '已儲存';
    }, '儲存這一天');
};
ACT.newWorker = () => ACT.editWorker({});
ACT.editWorker = d => {
  const w = d.id ? IDX.workers[d.id] : { status: 'review' };
  sheet(w.id ? '編輯夥伴' : '新增夥伴', g2(fld('name', '姓名', w.name), fld('phone', '電話', w.phone)) + g2(fld('status', '狀態', w.status, 'select', { options: [['review', '待核對（不會被派工）'], ['active', '可派工'], ['paused', '暫停']] }), can('money') ? fld('rate', '時薪（元）', w.rate ?? '', 'number') : '')
    + `<label>職類（可複選）</label><div class="chips">${W_ROLES.map(r => `<label><input type="checkbox" name="roles" data-multi="1" value="${r}"${WROLES(w).includes(r) ? ' checked' : ''}> ${r}</label>`).join('')}</div>`
    + `<label>服務區域（可複選，整個縣市也可以）</label><div id="wregions"></div><input type="hidden" name="regions" value="${esc(w.regions || '')}">`
    + `<label>會的項目（可複選）</label><div class="chips">${['一般到府服務', '年度保養／細清', '進階專案', '搬遷服務', '定期駐點服務', '店面／辦公室', '大型案場服務', '收納整理', '陪玩陪讀', '私人管家'].map(x => `<label><input type="checkbox" name="skills" data-multi="1" value="${x}"${(w.skills || '').includes(x.slice(0, 2)) ? ' checked' : ''}> ${x}</label>`).join('')}</div>`
    + fld('tools_own', '自備工具（頓號分隔，例：吸塵器、拖把）', w.tools_own || '') + g2(fld('willing', '接單意願（夥伴自己寫的）', w.willing || ''), fld('gender', '性別', w.gender || '', 'select', { options: [['', '—'], ['女', '女'], ['男', '男'], ['不想說', '不想說']] }))
    + fld('availability', '平常可約時間（例：平日 13:00 後、週日不行）', w.availability) + fld('notes', '備註', w.notes, 'textarea')
    + (can('money') ? g2(fld('bank_name', '匯款銀行（非示範銀行扣 15 元手續費）', w.bank_name || ''), fld('bank_account', '帳號', w.bank_account || '')) + g2(fld('bank_holder', '戶名', w.bank_holder || ''), fld('payout_cycle', '薪資怎麼領', w.payout_cycle || 'month', 'select', { options: [['month', '月領'], ['week', '週領']] })) : ''),
    async f => { f.skills = (f.skills || []).join('、'); f.roles = (f.roles || []).join('、') || '服務人員'; await api('/api/save/workers', w.id ? { ...f, id: w.id, version: w.version } : f); return '已儲存'; });
  // 跟手機一樣的 104 式地區選擇器
  regionMulti($('#wregions'), (w.regions || '').split(/[、,，;；\s]+/).filter(Boolean), v => { const h = $('#sheetForm [name=regions]'); if (h) h.value = v.join('、'); });
};

/* ═════════ 更多 ═════════ */
/* 「更多」太亂 → 依功能分區，說明縮短 */
const MORE_GROUPS = [['管理群（要處理的事都在這裡）', ['asks', 'tasks']], ['客戶與報價', ['customers', 'inquiries', 'quotes', 'plans', 'testimonials']], ['行銷', ['marketing']], ['夥伴', ['workers', 'interviews', 'map']], ['帳務（記帳、收款、商城）', ['ledger', 'receipts', 'shop']],
  ['資料整理', ['importdata', 'pairing', 'triage', 'review', 'chats']], ['說明與回報', ['manual', 'flows', 'sysreport']], ['系統設定（管理員）', ['brands', 'line', 'settings']]];
const MORE_SHORT = { tasks: '已結案的待辦、客訴紀錄（要處理的都在「管理群待處理」）', ledger: '各品牌收支、待確認收據、記帳士包（LINE 打「記帳」傳收據照片）', marketing: '待審貼文（看全文、圖、核准）、發布排程、成效', asks: '要處理的事都在這：群組卡片（跟 LINE 按一樣）、要決定的題目、待辦、客訴', pairing: '偵測到還沒配對的 LINE 聊天室，自己配對、解除', inquiries: '客戶在官方 LINE 填的預約表單', quotes: '看照片、確認金額，傳報價給客戶', plans: '月方案、套票、剩餘次數', testimonials: '客戶好評＋服務前後照片',
  interviews: '履歷、面試、良民證、試派案', map: '各鄉鎮客戶與夥伴分布，看哪裡缺人', receipts: '對銀行入帳、分攤到帳單', shop: '耗材商品與訂單', triage: '保留、移動、合併、刪除（可還原）',
  review: 'Excel 匯入結果、待補資料', chats: '官方 LINE 歷史對話', manual: '每一頁怎麼用、按鈕會發哪張 LINE 卡', flows: '15 條工作流＋總覽：順序、觸發、誰傳給誰（聚光燈導覽）', sysreport: '系統問題、想要的功能（LINE 打「回報：…」也行）',
  brands: '業務線、官方 LINE、客戶選單', line: '自動發送的 LINE 卡片', settings: '帳號權限、匯款資訊、LINE Pay' };
function moreBadge(k) {
  const n = k === 'inquiries' ? (S.inquiries || []).filter(q => q.status === 'new').length : k === 'triage' ? S.triage_open : k === 'quotes' ? S.quotes_pending
    : k === 'asks' ? asksLeft() : k === 'tasks' ? [...(S.tasks || []), ...(S.service_cases || [])].filter(t => t.status === 'open').length : (S.more_n || {})[k] || 0;      // 每個入口有東西要處理都顯示數字
  return n ? `<span class="badge">${n}</span>` : '<span class="chev">›</span>';
}
PAGES.more = () => {
  const items = MORE.filter(m => (!['settings', 'line', 'brands'].includes(m[0]) || S.role === 'admin') && (m[0] !== 'ledger' || can('money'))), by = Object.fromEntries(items.map(m => [m[0], m]));
  // 回報「手機排版找不到夥伴」：手機底部只放 5 個分頁，夥伴、客戶（有結帳權限時）不在底部 → 這裡補入口
  by.workers = ['workers', '夥伴名單', '所有夥伴：資料、可排時間、LINE、派工紀錄']; if (can('money')) by.customers = ['customers', '客戶', '所有客戶、服務地點'];
  const listed = new Set(MORE_GROUPS.flatMap(g => g[1])), rest = items.filter(m => !listed.has(m[0])).map(m => m[0]);
  const row = k => { const [, t, s] = by[k]; return `<button data-go="${k}"><span class="ic">${svg(k === 'workers' || k === 'customers' ? k : k === 'inquiries' ? 'customers' : k === 'plans' || k === 'receipts' || k === 'ledger' ? 'billing' : 'more')}</span><span><b>${t}</b><div class="small">${MORE_SHORT[k] || s}</div></span>${moreBadge(k)}</button>`; };
  return phead('更多', '其他功能。', '') + `<div class="moregrp">${MORE_GROUPS.concat(rest.length ? [['其他', rest]] : []).map(([title, ks]) => { const ok = ks.filter(k => by[k]); return ok.length ? `<section><h3>${title}</h3><div class="list">${ok.map(row).join('')}</div></section>` : ''; }).join('')}</div>
  <div class="btns">${btn('登出', 'logout', {}, 'gray')}${btn('重看導覽', 'resetTours', {}, 'sec')}</div>`;
};
PAGES.me = () => PAGES.more();

PAGES.inquiries = () => {
  const list = (S.inquiries || []).slice().sort((a, b) => b.created_at.localeCompare(a.created_at));
  return phead('預約需求', '客戶點一點，需求就進來。', `把這個網址設成官方 LINE 圖文選單的「立即預約」：<b>${location.origin}/book</b>。客戶送出的估價會進「報價審核」，你確認後才會傳正式報價單給客戶。`, btn('複製表單網址', 'copyBook', {}, 'sec'))
    + '<div class="card">' + tbl(['時間', '客戶', '服務', '地區', '狀態'], list.map(q => `<tr class="click" data-act="inquiry" data-id="${q.id}">${td('時間', esc(q.created_at.slice(5, 16)))}${td('客戶', esc(q.name) + `<span class="s">${esc(q.phone)}</span>`)}${td('服務', esc(q.service_type))}${td('地區', esc(q.area))}${td('狀態', st(q.status))}</tr>`)) + '</div>';
};
ACT.copyBook = () => copy(location.origin + '/book', '預約表單網址');
ACT.inquiry = d => {
  const q = IDX.inquiries[d.id], det = JSON.parse(q.detail || '{}');
  sheet('預約需求', `<dl class="kv"><dt>稱呼</dt><dd>${esc(q.name)}</dd><dt>電話</dt><dd><a href="tel:${esc(q.phone)}">${esc(q.phone)}</a></dd><dt>LINE</dt><dd>${esc(q.line_name || '—')}</dd><dt>服務</dt><dd>${esc(q.service_type)}</dd><dt>地點</dt><dd>${esc(q.area)} ${esc(q.address)}</dd><dt>希望時間</dt><dd>${esc(q.preferred || '—')}</dd>
    ${Object.entries(det).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}<dt>補充</dt><dd>${esc(q.notes || '—')}</dd><dt>狀態</dt><dd>${st(q.status)} ${esc(q.handled_note || '')}</dd></dl>`
    + (q.order_id ? btn('打開工單', 'order', { id: q.order_id }) : `<h3>轉成工單</h3>` + pickFld('customer_id', '已是客戶？（選了就不另建）', '', custOpts(), { empty: '新客戶' }) + g2(fld('date', '日期', '', 'date'), fld('required_workers', '人數', 1, 'number')) + g2(fld('start', '開始', '', 'time'), fld('end', '結束', '', 'time'))),
    q.order_id ? null : async f => { const o = await api('/api/inquiry/convert', { id: q.id, ...f, required_workers: Number(f.required_workers || 1) }); setTimeout(() => IDX.orders[o.id] && orderSheet(o.id), 1500); return '已建立待核對工單'; }, '轉成工單',
    btn('標記已聯絡', 'inqStatus', { id: q.id, s: 'contacted' }, 'gray') + btn('未成交', 'inqStatus', { id: q.id, s: 'lost' }, 'gray') + (q.order_id ? '' : btn('刪除（測試／無效）', 'inqDel', { id: q.id }, 'gray')));
};
ACT.wBlack = async d => {       // 黑名單：不會被自動詢問、不能接單（邀請中的會收回）
  const on = !(S.worker_blacklist || {})[d.id];
  const reason = on ? prompt('列入黑名單的原因（只有管理層看得到，可空白）：', '') : '';
  if (reason === null) return;
  await api('/api/worker/blacklist', { id: d.id, on, reason }); closeSheet(); await refresh(); return on ? '已列入黑名單' : '已移出黑名單';
};
ACT.goBill = d => { BMONTH = d.m; BILL = null; SEG.bill = d.list ? 'list' : 'cust'; if (d.c) window.FOCUS = '#bc-' + d.c; if (!$('#sheet').hidden) closeSheet(); go('billing'); };      // 直接捲到那位客戶
ACT.homeJump = d => { const el = document.querySelector(d.to); if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' }); };
ACT.homeAll = d => { (SEG.homeAll = SEG.homeAll || {})[d.k] = 1; route(true); };
ACT.inqDel = async d => {      // 真的客戶沒成交請按「未成交」（留著做統計）；這顆只給測試、重複、亂填的
  if (!confirm('確定刪除這筆需求？會移到「待整理 › 回收桶」，需要時可以還原。真的客戶沒成交請改按「未成交」。')) return;
  await api('/api/inquiry/delete', { id: d.id }); closeSheet(); await refresh(); return '已移到回收桶';
};
ACT.inqStatus = async d => { await api('/api/inquiry/status', { id: d.id, status: d.s }); closeSheet(); await refresh(); return '已更新'; };

PAGES.tasks = () => {
  const open = x => x.status === 'open';
  return phead('待辦紀錄', '每個例外，都有人接住。', '要處理的（還沒結案的）也會列在「管理群待處理」；這裡看全部紀錄（含已結案）。結案要寫處理結果。', btn('新增待辦', 'newTask') + ' ' + btn('新增客訴／補掃', 'newCase', {}, 'sec'))
    + `<h2>品質與客訴</h2><div class="card">${tbl(['主旨', '客戶', '負責人', '期限', '狀態'], (S.service_cases || []).sort((a, b) => open(b) - open(a) || a.due_date.localeCompare(b.due_date)).map(t => `<tr class="click" data-act="editCase" data-id="${t.id}">${td('主旨', esc(t.title) + `<span class="s">${esc(t.detail.slice(0, 40))}</span>`)}${td('客戶', esc(cust(t.customer_id).dn || ''))}${td('負責人', esc(t.owner))}${td('期限', esc(t.due_date))}${td('狀態', st(t.status))}</tr>`))}</div>`
    + `<h2>營運待辦</h2><div class="card">${tbl(['主旨', '客戶', '負責人', '期限', '狀態'], (S.tasks || []).sort((a, b) => open(b) - open(a) || a.due_date.localeCompare(b.due_date)).map(t => `<tr class="click" data-act="editTask" data-id="${t.id}">${td('主旨', esc(t.title))}${td('客戶', esc(cust(t.customer_id).dn || ''))}${td('負責人', esc(t.owner))}${td('期限', esc(t.due_date))}${td('狀態', st(t.status))}</tr>`))}</div>`;
};
/* LINE 的東西都要能在中控台處理 → 群組題目卡在這裡回答（答了自動生效，群組會看到誰答了） */
/* 每一類要做什麼、按了會怎樣（說明清楚） */
const SQ_HELP = {
  '要審核（群發、公告、文案）': '要做：看全文和圖，沒問題按「核准」（群發會照時間發出；新業務的上線公告核准＝同步上線）；要改就寫意見，系統依意見改好再送一次。',
  '業主申請改時間': '要做：看已排夥伴新時段有沒有空。可以 → 按「改到新時間」，系統改工單並通知業主和夥伴；不行 → 寫一句話（例如哪些時段可以），系統連同「再選一個時間」傳給業主。',
  '人員到齊・待審核': '要做：看這幾位適不適合這個客人。OK → 系統通知業主（業主已確認時間的直接排定）；換掉某位 → 系統自動另外問人；拿掉某位（減少人數）→ 不補人、通知他不用去。',
  '夥伴請假': '一個請假一張卡、兩個步驟都要按：①這次算不算請假（記點用，跟補人無關）②這場怎麼補人（讓業主選／系統另外派／我自己找人）。①②按完這張就消失。',
  '現場回報': '要做：夥伴在現場遇到狀況（跟預約不一樣、損壞、找不到人…），選怎麼處理，系統會回覆夥伴。',
  '客戶留言還沒回': '要做：到這個品牌的官方 LINE 聊天室回覆客人，回完按「已回覆」。（超過 4 小時沒回才會出現）',
  '照片不齊・薪資暫不發': '要做：確認這場有正常完工就按「確認，照常發薪」；不然請夥伴補拍。',
  '收款單金額跟工單不一樣': '要做：一鍵改成工單金額，或標「本來就不同」。',
  '夥伴用品申請': '要做：同意或不同意夥伴買用品；同意的夥伴會傳發票，金額自動併入他的結算。',
  '同一天兩場：趕得上嗎': '要做：回答兩場之間趕不趕得上；系統記住，之後不會這樣排，趕不上的這場自動換人。',
  '開門方式待補': '要做：補這位業主服務當天怎麼進門（在家開門、密碼、鑰匙…），夥伴的排定卡才有進門資訊。',
  '明天的工作：要補充提醒夥伴嗎': '要做：有要特別交代夥伴的就寫，沒有就按「不用」。',
  '報價用其他方式給客戶：為什麼': '要做：寫一下為什麼這張報價沒走線上（電話談好、當面…），方便之後追蹤。',
  '素材待準備': '要做：上傳行銷要的照片（前後照、商品照）；中控台直接上傳或 LINE 按「我要傳」。',
  '客戶申請刪除個資': '要做：確認客人沒有還沒完成的預約、款項，再按「核准刪除」（不能復原）。',
  '新的 LINE 群組待確認': '要做：確認這個群組是什麼（管理群、接單大群、夥伴個人群…）。',
  '可能重複的夥伴': '要做：確認是不是同一個人，是就合併。',
  '夥伴系統介紹': '要做：確認要不要傳系統介紹給這位夥伴。'
};
PAGES.asks = () => {
  const L = S.asks || [], Q = S.staffq || [];
  const T = (S.tasks || []).filter(t => t.status === 'open'), K = (S.service_cases || []).filter(t => t.status === 'open');      // 人工待辦、客訴也放這裡（一個地方看完）
  let h = phead('管理群待處理', '要處理的事都在這裡。', '群組卡片（跟派單管理群同一件，在這裡按跟在 LINE 按一樣）、要決定的題目、人工開的待辦和客訴／補掃。結案的紀錄在「待辦紀錄」。',
    btn('新增待辦', 'newTask', {}, 'sec') + ' ' + btn('新增客訴／補掃', 'newCase', {}, 'gray'));
  const late = t => t.due_date && t.due_date < today();
  const trow = (t, act) => `<div class="card"${late(t) ? ' style="border:2px solid var(--red,#C62828)"' : ''}><h3 style="margin:0 0 6px">${esc(t.title)}</h3>
    <div class="small" style="margin-bottom:8px">${esc(t.owner || '—')}・期限 ${esc(t.due_date || '—')}${late(t) ? ' <b style="color:var(--red,#C62828)">已逾期</b>' : ''}${t.customer_id && IDX.customers?.[t.customer_id] ? '・' + esc(IDX.customers[t.customer_id].name) : ''}</div>
    ${t.detail ? `<div class="small" style="white-space:pre-wrap;margin-bottom:10px">${esc(t.detail.slice(0, 600))}</div>` : ''}<div class="btns">${btn('結案／修改', act, { id: t.id })}</div></div>`;
  const tail = (T.length ? `<h2>營運待辦（人工開的）<span class="g">${T.length}</span></h2><p class="small" style="margin:-4px 0 10px">要做：照內容處理，處理完按「結案／修改」把狀態改成已結案、寫處理結果。逾期的紅框，每天 10:30 也會列出來。</p>` + T.sort((a, b) => (a.due_date || '').localeCompare(b.due_date || '')).map(t => trow(t, 'editTask')).join('') : '')
    + (K.length ? `<h2>客訴／補掃 <span class="g">${K.length}</span></h2>` + K.sort((a, b) => (a.due_date || '').localeCompare(b.due_date || '')).map(t => trow(t, 'editCase')).join('') : '');
  if (!L.length && !Q.length) return h + (tail || '<div class="banner ok">✓ 目前沒有要處理的事。</div>');
  const groups = [...new Set(Q.map(x => x.group))];
  const left = asksLeft();
  if (left) h += `<div class="banner warn">還有 <b>${left}</b> 件沒處理：${[...groups.map(g => `${g} ${sqCount(Q.filter(x => x.group === g))}`), ...(L.length ? [`要決定的題目 ${L.length}`] : []), ...(T.length ? [`營運待辦 ${T.length}`] : []), ...(K.length ? [`客訴／補掃 ${K.length}`] : [])].map(esc).join('・')}</div>`;
  const lvPair = x => x.kind === 'lvd' ? Q.findIndex(y => y.kind === 'lvh' && y.id === x.id) : -1;      // 同一個請假 ①② 合成一張
  const acts = (x, i) => x.acts.map(([v, t, s]) => btn(t, 'sqAct', { i, v }, s === 'primary' ? '' : s)).join(' ');
  h += groups.map(g => `<h2>${esc(g)} <span class="g">${sqCount(Q.filter(x => x.group === g))}</span></h2>` + (SQ_HELP[g] ? `<p class="small" style="margin:-4px 0 10px">${esc(SQ_HELP[g])}</p>` : '')
    + Q.map((x, i) => [x, i]).filter(([x]) => x.group === g && !(x.kind === 'lvh' && Q.some(y => y.kind === 'lvd' && y.id === x.id))).map(([x, i]) => { const j = lvPair(x); return `<div class="card">
    <h3 style="margin:0 0 6px">${esc(x.title)}</h3><div class="small" style="white-space:pre-wrap;margin-bottom:10px">${esc(x.sub || '')}</div>
    <div class="btns" style="flex-wrap:wrap">${x.kind === 'rv' ? btn('看全文＋圖', 'rvOpen', { id: x.id }, 'sec') + ' ' : ''}${acts(x, i)}${j < 0 && x.oid && IDX.orders[x.oid] ? ' ' + btn('打開工單', 'order', { id: x.oid }, 'gray') : ''}</div>
    ${j >= 0 ? `<div class="small" style="white-space:pre-wrap;margin:14px 0 10px;padding-top:12px;border-top:1px solid var(--line,#E5E5EA)">${esc(Q[j].sub || '')}</div><div class="btns" style="flex-wrap:wrap">${acts(Q[j], j)}${x.oid && IDX.orders[x.oid] ? ' ' + btn('打開工單', 'order', { id: x.oid }, 'gray') : ''}</div>` : ''}</div>`; }).join('')).join('');
  if (L.length) h += `<h2>要決定的題目 <span class="g">${L.length}</span></h2><p class="small" style="margin:-4px 0 10px">要做：看題目說明，點一個選項（或「自己寫」）。答了系統就照做，群組也會看到誰答了；還沒答的每天 10:30 會再列出來。</p>`;
  return h + L.map((q, i) => `<div class="card"><p class="small">${esc((q.asked_at || '').slice(5, 16).replace('T', ' '))} 問的</p><h3 style="margin:4px 0 8px">${esc(q.topic)}</h3>
    <div style="white-space:pre-wrap;margin-bottom:12px">${esc(q.question)}</div>
    <div class="btns" style="flex-wrap:wrap">${q.options.map(o => btn(o, 'askAnswer', { i, o }, 'sec')).join(' ')} ${btn('✏️ 自己寫', 'askWrite', { i }, 'gray')}</div></div>`).join('') + tail;
};
ACT.photoRedo = async d => { if (!confirm('請這場的夥伴補拍完成照片？夥伴會收到 LINE。')) return; const r = await api('/api/order/photo-redo', { id: d.id }); return r.message; };
ACT.workerNote = d => { const o = IDX.orders[d.id];
  sheet('補充提醒夥伴', `<p class="small">會跟明天的工作卡一起給這場的夥伴；工作卡已經發了就另外傳。${o.worker_note ? `<br>已有：${esc(o.worker_note)}` : ''}</p>` + fld('text', '要提醒夥伴的事', '', 'textarea', { rows: 3, ph: '例：現場沒有工具、垃圾裝好放一樓門口' }),
    async f => { const r = await api('/api/order/worker-note', { id: d.id, text: f.text }); await refresh(); return r.message; }, '送出'); };
ACT.sqAct = async d => {                          // 管理群卡片在中控台按（跟 LINE 同一個後端）
  const x = (S.staffq || [])[+d.i]; if (!x) return;
  if (x.kind === 'ent' && d.v === 'set') return ACT.entrySet({ id: x.id });
  if (x.kind === 'mat' && d.v === 'upload') {      // 素材直接在中控台上傳
    const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*'; inp.multiple = true;
    inp.onchange = async () => { try { const files = [...inp.files].slice(0, 6); if (!files.length) return; toast(`上傳 ${files.length} 張中…`);
      const r = await api('/api/materials/upload', { key: x.id, images: await Promise.all(files.map(shrink)) }); await refresh(); toast(r.message); } catch (e) { toast(e.message); } };
    inp.click(); return;
  }
  if ((x.kind === 'rv' && d.v === 'changes') || (x.kind === 'qreason' && d.v === 'write') || (x.kind === 'prep' && d.v === 'add') || (x.kind === 'rs' && d.v === 'no'))
    return sheet(esc(x.title), `<div class="small" style="white-space:pre-wrap;margin-bottom:10px">${esc(x.sub || '')}</div>` + fld('note', x.kind === 'rv' ? '要怎麼改' : x.kind === 'prep' ? '要提醒夥伴的事' : x.kind === 'rs' ? '要跟業主說的話（會連同「再選一個時間」傳給業主）' : '原因', '', 'textarea', { rows: 3 }),
      async f => { if (!f.note) throw new Error('請寫一下'); const r = await api('/api/staffq/act', { kind: x.kind, id: x.id, v: d.v, note: f.note }); await refresh(); return r.message; }, '送出');
  const label = (x.acts.find(a => a[0] === d.v) || [])[1] || d.v;
  if (!confirm(x.broadcast && d.v === 'ok' ? `⚠️ 核准後會立刻群發，發出去不能收回。\n\n「${x.title}」\n\n上面的內容和發送對象都看過了，確定核准？` : `「${x.title}」\n→ ${label}？`)) return;
  const r = await api('/api/staffq/act', { kind: x.kind, id: x.id, v: d.v }); await refresh(); return r.message;
};
const RV_ST = { pending: '待審核', approved: '已核准', changes: '要修改', auto: '免審通過', superseded: '已改版（看新版）' };
ACT.rvOpen = async d => {                         // 審核詳情（全文、圖片、每則平台／時間）＋核准／要修改
  const v = await api('/api/review/detail?id=' + encodeURIComponent(d.id)), done = v.status !== 'pending';
  const imgs = (v.images || []).map(u => `<a href="${u}" target="_blank" rel="noopener"><img src="${u}?p=1" alt="" style="width:150px;border-radius:10px;margin:0 8px 8px 0;border:1px solid #e5e5ea"></a>`).join('');
  const posts = (v.posts || []).map(p => `<div class="item"><div class="t"><div class="n">${esc(p.platform)}　${esc(p.time || '')}${p.code ? `　<span class="small">追蹤代號 ${esc(p.code)}</span>` : ''}</div>
    <div class="m" style="white-space:pre-wrap">${esc(p.text || '（只有圖）')}</div>${p.images.length ? `<div>${p.images.map(u => `<a href="${u}" target="_blank" rel="noopener"><img src="${u}?p=1" alt="" style="width:90px;border-radius:8px;margin:6px 6px 0 0"></a>`).join('')}</div>` : ''}${p.link ? `<div class="m">連結：${esc(p.link)}</div>` : ''}${p.comment ? `<div class="m">📌 置頂留言：${esc(p.comment)}</div>` : ''}</div></div>`).join('');
  const body = `<p class="small">${esc(v.category)}・${esc((v.created_at || '').slice(5, 16).replace('T', ' '))} 送審${v.send_at ? '・預定 ' + esc(v.send_at.slice(5, 16).replace('T', ' ')) + ' 發' : ''}</p>`
    + (done ? `<div class="banner ok">已處理：${esc(RV_ST[v.status] || v.status)}${v.decided_by ? '（' + esc(v.decided_by) + '）' : ''}${v.note ? '<br>意見：' + esc(v.note) : ''}</div>` : '')
    + (imgs ? `<h3>圖片（點開看大圖）</h3><div>${imgs}</div>` : '')
    + (posts ? `<h3>每一則：平台、預定時間</h3><div class="card">${posts}</div>` : '')
    + (v.preview && v.preview.length ? `<h3>對方會收到</h3><div class="card" style="white-space:pre-wrap">${esc(v.preview.join('\n\n'))}</div>` : '')
    + `<h3>送審卡全文</h3><div class="card" style="white-space:pre-wrap;font-size:14px">${esc(v.body || '')}</div>`;
  if (done) return sheet(esc(v.title), body, null);
  sheet(esc(v.title), body, async () => {
    if (!confirm(v.broadcast ? '⚠️ 核准後會照預定時間群發，發出去不能收回。確定核准？' : '確定核准？核准後會照預定時間自動發布。')) throw new Error('沒有核准');
    const r = await api('/api/staffq/act', { kind: 'rv', id: v.id, v: 'ok' }); await refresh(); return r.message;
  }, v.broadcast ? '核准（照時間群發）' : '核准', btn('要修改（寫意見）', 'rvChanges', { id: v.id }, 'sec'));
};
ACT.rvChanges = d => sheet('要修改', fld('note', '要怎麼改（行銷系統會照這段改好、新版再送）', '', 'textarea', { rows: 4 }),
  async f => { if (!f.note) throw new Error('請寫一下要怎麼改'); const r = await api('/api/staffq/act', { kind: 'rv', id: d.id, v: 'changes', note: f.note }); await refresh(); return r.message; }, '送出意見');
PAGES.marketing = () => {                         // 更多 › 行銷
  if (!MK) { api('/api/marketing').then(v => { MK = v; route(true); }).catch(e => toast(e.message, true)); return phead('行銷', '載入中…', ''); }
  const v = MK; MK = null;
  let h = phead('行銷', '行銷系統做的貼文、排程、成效。', 'GPT 寫文案做圖、Claude 檢查，店長（或管理層）審核是唯一人工關卡；核准後照時間自動發（Threads 自動、IG／FB 會提醒人工發、LINE 群發走群發審核）。');
  const ib = v.inbox || [];      // 給行銷的素材照、參考文字、企劃
  h += `<h2 id="mkin">給行銷的素材／企劃</h2><div class="card"><p class="small" style="margin-top:0">想做的活動、參考文字、企劃內容、素材照片都放這裡；做行銷包前會先讀。客戶家照片要客戶同意才能對外用。</p>
    <label for="mkb">品牌</label><select id="mkb"><option value="all">三個品牌都可以</option><option value="demoshop">示範服務店</option><option value="homeline">生活小幫手</option><option value="bizline">企業服務線</option></select>
    <label for="mkn">說明／參考文字／企劃</label><textarea id="mkn" rows="5" style="width:100%" placeholder="例：11 月想主打年底年度保養，參考這篇貼文的語氣…"></textarea>
    <label class="photobtn" style="margin-top:8px">＋ 選照片（可以多張）<input type="file" id="mkf" accept="image/*" multiple hidden></label><p class="small" id="mkfn"></p>
    ${btn('送出給行銷', 'mkSend')}<p class="small" id="mkprog"></p></div>`
    + (ib.length ? '<div class="card">' + ib.map(x => `<div class="item"><div class="t"><div class="n">${esc(x.folder.slice(4, 6) + '/' + x.folder.slice(6, 8) + ' ' + x.folder.slice(9, 11) + ':' + x.folder.slice(11, 13))}　${esc(x.brand)}　${x.photos} 張照片${x.used ? '　<span class="pill ok">已用</span>' : ''}</div><div class="m" style="white-space:pre-wrap">${esc(x.note)}</div></div></div>`).join('') + '</div>' : '');
  const pt = v.patrol || { items: [], today: {} }, ptd = pt.today || {};      // 海巡留言全部審過才發
  h += `<h2 id="patrol">海巡留言待審 <span class="g">${pt.items.length}</span></h2><p class="small">Threads 海巡找到想找服務的貼文，系統先寫好留言；核准才會發（分散在一天裡發）。臺北的排前面。超過 24 小時沒審自動過期。今天：已發 ${ptd.posted || 0}・排隊中 ${ptd.approved || 0}・略過 ${ptd.rejected || 0}</p>`
    + (pt.items.length > 1 ? `<div style="margin:6px 0 10px">${btn('全部照目前文字核准（' + pt.items.length + ' 則）', 'ptAll', { confirm: `核准全部 ${pt.items.length} 則留言？核准後會分散發出。` })}</div>` : '')
    + '<div class="card">' + (pt.items.map(r => `<div class="item" style="display:block"><div class="m">${r.yilan ? '<b>📍臺北</b>・' : ''}@${esc(r.target_author || '')}・${esc((r.created_at || '').slice(5, 16).replace('T', ' '))}・❤️${r.post_like_count || 0} 💬${r.post_reply_count || 0}　<a href="${esc(r.target_url || '#')}" target="_blank" rel="noopener">看原貼文</a></div>`
      + `<div class="small" style="white-space:pre-wrap;margin:6px 0;max-height:7em;overflow:auto">${esc(r.target_content)}</div>`
      + `<textarea id="pt_${r.id}" rows="3" style="width:100%" aria-label="留言內容">${esc(r.reply_text || '')}</textarea>`
      + `<div style="margin-top:6px">${btn('核准', 'ptOk', { id: r.id })}${btn('略過', 'ptSkip', { id: r.id }, 'gray')}</div></div>`).join('') || '<div class="empty">沒有待審的留言。</div>') + '</div>';
  const td_ = v.tdrafts || [];      // 5100 後台不用了 → 舊產文留下的草稿在這審
  if (td_.length) h += `<h2 id="tdrafts">Threads 草稿待審 <span class="g">${td_.length}</span></h2><p class="small">舊的自動產文留下的草稿（之後不會再產）。核准會排進最近一個空的發文時段；不要的按略過。</p><div class="card">`
    + td_.map(r => `<div class="item" style="display:block"><div class="m">${esc(r.topic || '')}・${esc((r.created_at || '').slice(5, 16).replace('T', ' '))}</div><textarea id="td_${r.id}" rows="8" style="width:100%" aria-label="貼文內容">${esc(r.content || '')}</textarea><div style="margin-top:6px">${btn('核准', 'tdOk', { id: r.id })}${btn('略過', 'tdSkip', { id: r.id }, 'gray')}</div></div>`).join('') + '</div>';
  const tp = v.tposts || [], tst = { approved: '⏳ 排隊中', posted: '✅ 已發', failed: '❌ 失敗' };      // Threads 發文
  h += `<h2 id="tposts">Threads 發文（前 2 天～後 7 天）<span class="g">${tp.length}</span></h2><p class="small">示範服務店每天 5 篇（08:30、11:45、14:30、18:30、21:00），每週日 14:00 審下週那包；核准後海巡系統照時間發，連結自動放置頂留言。</p><div class="card">`
    + (tp.map(r => `<details class="fold"><summary>${esc(((r.posted_at || r.scheduled_at || '')).slice(5, 16).replace('T', ' '))}　${tst[r.status] || esc(r.status)}${r.images ? '　🖼' + r.images : ''}　<small>${esc((r.content || '').split(String.fromCharCode(10))[0].slice(0, 40))}</small></summary><div class="in"><div class="small" style="white-space:pre-wrap">${esc(r.content)}</div>${r.pin_comment ? `<div class="small">📌 置頂留言：${esc(r.pin_comment)}</div>` : ''}${r.posted_url ? `<div class="small"><a href="${esc(r.posted_url)}" target="_blank" rel="noopener">看貼文</a></div>` : ''}${r.error ? `<div class="small">⚠️ ${esc(r.error)}</div>` : ''}</div></details>`).join('') || '<div class="empty">這段時間沒有排發文。</div>') + '</div>';
  h += `<h2>待審核 <span class="g">${v.pending.length}</span></h2><div class="card">` + (v.pending.map(r => `<div class="item"><div class="t"><div class="n">${esc(r.title)}</div><div class="m">${esc(r.category)}・${esc((r.created_at || '').slice(5, 16).replace('T', ' '))}</div></div><div class="a">${can('money') ? btn('看全文＋圖、審核', 'rvOpen', { id: r.id }) : ''}</div></div>`).join('') || '<div class="empty">沒有待審的。</div>') + '</div>';
  h += `<h2>行銷包</h2><div class="card">` + (v.packages.map(p => `<div class="item"><div class="t"><div class="n">${esc(p.id)}｜${esc(p.title)}</div><div class="m">${esc(p.status)}・第 ${esc(p.revision ?? 1)} 版・${p.posts} 則・最早 ${esc(p.first || '—')}</div></div></div>`).join('') || '<div class="empty">還沒有。</div>') + '</div>';
  const cols = v.schedule.length ? Object.keys(v.schedule[0]) : [];
  h += `<h2>發布排程 <span class="g">${v.schedule.length}</span></h2>` + (cols.length ? `<div class="card" style="overflow-x:auto">${tbl(cols, v.schedule.map(r => `<tr>${cols.map(k => td(k, esc(r[k] || ''))).join('')}</tr>`))}</div>` : '<div class="card"><div class="empty">還沒有核准的貼文排程（核准後會出現在這裡：預定時間、平台、狀態、連結）。</div></div>');
  h += `<h2>要人工發的（IG／FB）<span class="g">${v.manual.length}</span></h2><div class="card">` + (v.manual.map(m => `<div class="item"><div class="t"><div class="n">${esc(m)}</div></div></div>`).join('') || '<div class="empty">沒有。</div>') + '</div>';
  h += `<h2>成效（點擊 → 進 LINE → 估價 → 成立）</h2><div class="card" style="overflow-x:auto">${tbl(['代號', '點擊', '進 LINE', '估價', '成立', '成立金額'], v.funnel.map(f => `<tr>${td('代號', esc(f.code) + (f.test ? ' <span class="small">（測試）</span>' : ''))}${td('點擊', f.clicks)}${td('進 LINE', f.line)}${td('估價', f.quotes)}${td('成立', f.signed)}${td('成立金額', can('money') ? 'NT$ ' + Number(f.amount).toLocaleString() : '—')}</tr>`))}</div><p class="small">營運測試點的點擊（行銷資料夾 05_成效 › 測試點擊.md）每週算成效時會扣掉。</p>`;
  return h;
};
let MK = null;
ACT.tdOk = async d => { const t = ($('#td_' + d.id) || {}).value || ''; if (!t.trim()) throw new Error('內容不能是空的'); const r = await api('/api/tdraft/act', { id: d.id, v: 'ok', text: t }); route(true); return r.message; };
ACT.tdSkip = async d => { const r = await api('/api/tdraft/act', { id: d.id, v: 'skip' }); route(true); return r.message; };
ACT.ptOk = async d => { const t = ($('#pt_' + d.id) || {}).value || ''; if (!t.trim()) throw new Error('留言不能是空的'); const r = await api('/api/patrol/act', { id: d.id, v: 'ok', text: t }); route(true); return r.message; };
ACT.ptSkip = async d => { const r = await api('/api/patrol/act', { id: d.id, v: 'skip' }); route(true); return r.message; };
ACT.ptAll = async () => { let n = 0; for (const e of document.querySelectorAll('textarea[id^="pt_"]')) { if (!e.value.trim()) continue; await api('/api/patrol/act', { id: e.id.slice(3), v: 'ok', text: e.value }); n++; } route(true); return `✅ 已核准 ${n} 則，會分散發出`; };
ACT.askAnswer = async d => {
  const q = (S.asks || [])[+d.i]; if (!q) return;
  if (!confirm(`【${q.topic}】回答「${d.o}」？答了系統會自動照做。`)) return;
  const r = await api('/api/ask/answer', { topic: q.topic, answer: d.o }); await refresh(); return r.message;
};
ACT.askWrite = d => {
  const q = (S.asks || [])[+d.i]; if (!q) return;
  sheet(esc(q.topic), `<div class="small" style="white-space:pre-wrap;margin-bottom:10px">${esc(q.question)}</div>` + fld('answer', '你的回答', '', 'textarea', { rows: 3 }),
    async f => { if (!f.answer) throw new Error('請寫下回答'); const r = await api('/api/ask/answer', { topic: q.topic, answer: f.answer }); await refresh(); return r.message; }, '送出回答');
};
function taskForm(table, t) {
  sheet(table === 'tasks' ? '營運待辦' : '客訴／補掃', fld('title', '主旨', t.title) + pickFld('customer_id', '客戶', t.customer_id, custOpts(t.customer_id), { empty: '不指定' }) + (table === 'service_cases' ? fld('order_id', '相關工單', t.order_id, 'select', { options: optsOf(S.orders.filter(o => o.status === 'completed' || o.id === t.order_id).sort(by).reverse().slice(0, 80), o => `${md(o.date)} ${site(o.site_id).name || o.title}`, '不指定') }) : '')
    + g2(fld('owner', '負責人', t.owner || ME.name), fld('due_date', '期限', t.due_date || addDays(today(), 2), 'date')) + fld('detail', '內容', t.detail, 'textarea')
    + g2(fld('status', '狀態', t.status || 'open', 'select', { options: [['open', '處理中'], ['resolved', '已結案']] }), '') + fld('resolution', '處理結果（補掃、退款、減免、賠償…結案必填）', t.resolution, 'textarea', { rows: 2 }),
    async f => { const p = { ...f, customer_id: f.customer_id || null }; if (table === 'service_cases') p.order_id = f.order_id || null; await api('/api/save/' + table, t.id ? { ...p, id: t.id, version: t.version } : p); return '已儲存'; });
}
ACT.newTask = () => taskForm('tasks', {}); ACT.editTask = d => taskForm('tasks', S.tasks.find(x => x.id === d.id));
ACT.newCase = d => { const o = d.id ? IDX.orders[d.id] : null; taskForm('service_cases', o ? { order_id: o.id, customer_id: o.customer_id, title: '客訴／補掃：' + md(o.date) } : {}); };
ACT.editCase = d => taskForm('service_cases', S.service_cases.find(x => x.id === d.id));

PAGES.chats = () => {
  const th = (S.chat_threads || []).slice().sort((a, b) => b.last_at.localeCompare(a.last_at));
  return phead('對話檔案', '對話留存，決定可追溯。', `示範服務店官方 LINE 匯出 ${th.length} 份對話（只讀）。搜尋關鍵字、把對話連到客戶。`)
    + `<div class="g2"><input type="search" id="chatq" placeholder="搜尋，例如：改期、匯款、地址" aria-label="搜尋對話"><button class="btn" data-act="chatSearch">搜尋</button></div><div id="chatres" style="margin-top:14px"></div>`
    + `<h2>對話列表</h2><div class="card">${tbl(['對話', '期間', '則數', '分類', '客戶'], th.map(t => `<tr class="click" data-act="openThread" data-id="${t.id}">${td('對話', esc(t.source.replace(/\.csv$/, '')))}${td('期間', esc(t.last_at.slice(0, 10)))}${td('則數', t.count)}${td('分類', esc({ review: '待分類', customer: '客戶', recruit: '求職', vendor: '廠商', test: '測試' }[t.classification]))}${td('客戶', esc(cust(t.customer_id).dn || ''))}</tr>`))}</div>`;
};
ACT.chatSearch = async () => { const r = await api('/api/chats?q=' + encodeURIComponent($('#chatq').value)); $('#chatres').innerHTML = `<div class="card"><p class="small">找到 ${r.total} 則（最多顯示 100）</p>${r.messages.map(m => `<div class="item"><div class="t"><div class="m">${esc(m.local_time.slice(0, 16))} · ${esc(m.source.replace(/\.csv$/, ''))} · ${esc(m.sender)}</div><div>${esc(m.content.slice(0, 300))}</div></div></div>`).join('')}</div>`; };
ACT.openThread = async d => {
  const t = S.chat_threads.find(x => x.id === d.id), r = await api('/api/chats?thread=' + encodeURIComponent(d.id));
  sheet(esc(t.source.replace(/\.csv$/, '')), pickFld('customer_id', '對應客戶', t.customer_id, custOpts(t.customer_id), { empty: '未對應' }) + fld('classification', '分類', t.classification, 'select', { options: [['review', '待分類'], ['customer', '客戶'], ['recruit', '求職'], ['vendor', '廠商'], ['test', '測試']] })
    + `<p class="small">最新 ${r.messages.length} 則（共 ${r.total}）</p>` + r.messages.map(m => `<div class="item"><div class="t"><div class="m">${esc(m.local_time.slice(0, 16))} · ${esc(m.sender)}</div><div style="white-space:pre-wrap">${esc(m.content)}</div></div></div>`).join(''),
    async f => { await api('/api/workflow/chat-link', { id: t.id, version: t.version, customer_id: f.customer_id || null, classification: f.classification, notes: t.notes }); return '已更新'; });
};

PAGES.review = () => {
  const iss = (S.issues || []).filter(i => i.status === 'open'), runs = (S.issues || []).filter(i => i.kind === '10/8 資料重整');
  const rv = S.orders.filter(o => ['review', 'draft'].includes(o.status) && o.date >= today());
  let h = phead('資料核對', '資料有來源，決定有依據。', 'Excel 原檔不改、每一列都保留來源；這裡列出還要人確認的地方。', S.role === 'admin' ? btn('立刻備份', 'backup', {}, 'sec') : '');
  for (const r of runs) { const j = JSON.parse(r.detail); h += `<div class="banner blue">10/8 重整：${j.confirmed} 筆排定、${j.review} 筆待核對、${j.cancelled} 筆取消、${j.archived} 筆 10 月前封存為歷史。</div>`; }
  h += `<h2>待核對工單 <span class="g">${rv.length}</span></h2><div class="card">${tbl(['日期', '案場', '待處理'], rv.sort(by).map(o => `<tr class="click" data-act="order" data-id="${o.id}">${td('日期', md(o.date) + ' ' + o.start)}${td('案場', redoTag(o) + esc(site(o.site_id).name || o.title))}${td('待處理', `<span class="small">${esc(o.review_note.split('；').filter(s => /待|撞期|原表/.test(s)).join('；'))}</span>`)}</tr>`))}</div>`;
  h += `<h2>匯入時發現的問題 <span class="g">${iss.length}</span></h2><div class="card">${tbl(['類型', '說明', ''], iss.filter(i => i.kind !== '10/8 資料重整').map(i => `<tr>${td('類型', esc(i.kind))}${td('說明', esc(i.detail) + `<span class="s">${esc(i.source)}</span>`)}${td('', btn('已處理', 'resolveIssue', { id: i.id }, 'gray'))}</tr>`))}</div>`;
  h += `<h2>可能重複</h2><div class="card" id="dups"><div class="empty">載入中…</div></div>`;
  h += `<h2>Excel 原始列</h2><div class="card"><div class="g2"><select id="srcSheet" aria-label="頁籤"></select><button class="btn sec" data-act="loadSource">查看</button></div><div id="srcRows"></div></div>`;
  return h;
};
PAGES.review.after = async () => {
  const s = await api('/api/sheets'); const el = $('#srcSheet'); if (el) el.innerHTML = s.map(x => `<option>${esc(x)}</option>`).join('');
  const d = await api('/api/duplicates'), box = $('#dups');
  if (box) box.innerHTML = d.length ? '<p class="small">名字相近，可能是同一位。確認是同一人才合併：左邊保留，右邊的資料、工單、別名全部併過來。</p>' + d.map(x => `<div class="item"><div class="t"><div class="n">${esc(x.a.name)} ⟵ ${esc(x.b.name)}</div><div class="m">${x.table === 'customers' ? '客戶' : '夥伴'}・${esc(x.why)}</div></div><div class="a">${btn('合併到左邊', 'doMerge', { t: x.table, keep: x.a.id, drop: x.b.id, confirm: `把「${x.b.name}」合併到「${x.a.name}」？` }, 'sec')}${btn('合併到右邊', 'doMerge', { t: x.table, keep: x.b.id, drop: x.a.id, confirm: `把「${x.a.name}」合併到「${x.b.name}」？` }, 'gray')}</div></div>`).join('') : '<div class="empty">沒有疑似重複。</div>';
};
ACT.doMerge = async d => { await api('/api/merge', { table: d.t, keep: d.keep, drop: d.drop }); await refresh(); return '已合併'; };
ACT.mergeInto = d => {
  const list = d.t === 'customers' ? S.customers.filter(c => c.id !== d.id) : S.workers.filter(w => w.id !== d.id);
  sheet('合併重複', `<p class="small">選一筆跟「${esc((d.t === 'customers' ? IDX.customers : IDX.workers)[d.id].name)}」是同一位的資料；它的地點、工單、別名會併到這裡，然後刪除。</p>` + fld('drop', '要併進來的那筆', '', 'select', { options: optsOf(list.sort((a, b) => a.name.localeCompare(b.name))) }),
    async f => { if (!f.drop) throw new Error('請選一筆'); if (!confirm('確定合併？被併進來的那筆會刪除。')) return 'keep'; await api('/api/merge', { table: d.t, keep: d.id, drop: f.drop }); return '已合併'; }, '合併');
};
ACT.loadSource = async () => { const r = await api('/api/source?sheet=' + encodeURIComponent($('#srcSheet').value)); $('#srcRows').innerHTML = r.map(x => `<details class="fold"><summary>第 ${x.row_no} 列 <small>${esc(Object.values(JSON.parse(x.payload)).filter(Boolean).slice(0, 4).join(' · ').slice(0, 60))}</small></summary><div class="in"><pre class="copybox">${esc(JSON.stringify(JSON.parse(x.payload), null, 1))}</pre></div></details>`).join(''); };
ACT.resolveIssue = d => sheet('記錄處理結果', fld('resolution', '怎麼處理的', '', 'textarea'), async f => { await api('/api/issue/resolve', { id: d.id, ...f }); return '已結案'; });
ACT.backup = async () => { const r = await api('/api/backup', {}); return '已備份：' + r.file; };

/* ═════════ 設定與帳號（管理員）═════════ */
PAGES.settings = () => {
  if (S.role !== 'admin') return '<div class="banner warn">只有管理員可以看這頁。</div>';
  const set = S.settings || {}, li = S.line_accounts || [];
  let h = phead('設定', '帳號、匯款、日曆、LINE。', '');
  h += `<div class="secttl"><h2 style="margin:0">帳號與權限</h2>${btn('新增帳號', 'userForm', {})}</div><div class="card">${tbl(['姓名', '帳號', '角色', '狀態', '最後登入'], (S.users || []).map(u => `<tr class="click" data-act="userForm" data-id="${u.id}">${td('姓名', esc(u.name))}${td('帳號', esc(u.username))}${td('角色', esc({ admin: '管理員', staff: '客服排班', finance: '帳務', cleaner: '服務人員' }[u.role]))}${td('狀態', u.active ? (u.must_change ? st('pending', '待首次登入') : st('active', '啟用')) : st('paused', '停用'))}${td('最後登入', esc((u.last_login || '').slice(0, 16)))}</tr>`))}
    <p class="small">管理員：全部。客服排班：客戶、排班、派工（看不到收款與夥伴時薪）。帳務：加上結帳收款。服務人員：只看得到自己的工作。停用或改權限立即登出。</p></div>`;
  h += `<h2>最近的異動通知</h2><div class="card">${(S.alert_log || []).map(a => `<div class="item"><div class="t"><div class="n">${esc(a.title)}</div><div class="m">${esc(a.body)} · ${esc((a.at || '').slice(5, 16))}</div></div></div>`).join('') || '<div class="empty">還沒有通知。</div>'}<p class="small">通知推播給管理員、客服、帳務的手機（要先在手機上打開通知）。同一件事只通知一次。</p></div>`;
  h += `<h2>請款訊息的匯款資訊</h2><div class="card"><textarea id="bank" rows="3" placeholder="例：示範銀行（008）帳號 xxx 戶名 示範服務股份有限公司">${esc(set.bank_info || '')}</textarea><div class="btns">${btn('儲存', 'saveBank')}</div></div>`;
  if (S.role === 'admin' && S.linepay) {          // LINE Pay：金鑰自己貼（不會顯示出來）；測試模式不會真的扣款
    const lp = S.linepay;
    h += `<h2>LINE Pay 線上付款</h2><div class="card"><p class="small">狀態：${lp.ready ? '金鑰已設定' : '還沒設定金鑰'}・${lp.mode === 'production' ? '正式' : '測試（sandbox，不會真的扣款）'}・${lp.enabled ? '<b>客戶看得到 LINE Pay 按鈕</b>' : '客戶看不到（只有匯款）'}</p>
      <p class="small">各品牌目前用的金鑰：示範服務店 ${esc(lp.keys.demoshop || '未設定')}・企業線 ${esc(lp.keys.bizline || '未設定')}・生活線 ${esc(lp.keys.homeline || '未設定')}（每個品牌申請自己的 LINE Pay 商店，付款頁才會顯示自己的品牌名）</p>
      <label class="small">這組金鑰給哪個品牌</label><select id="lpBrand"><option value="">共用（三個品牌都用）</option><option value="demoshop">示範服務店</option><option value="bizline">企業線</option><option value="homeline">生活線</option></select>
      <label class="small">Channel ID（LINE Pay 商家後台 › 管理付款連結 › 管理連結金鑰）</label><input id="lpId" name="lp-channel-id" autocomplete="off" inputmode="numeric" placeholder="10 位數字（商家後台「通路 ID」）；要設定或更換才填">
      <label class="small">Channel Secret Key</label><input id="lpSec" name="lp-channel-secret" type="password" autocomplete="new-password" placeholder="商家後台「通路密鑰」；要設定或更換才填">
      <label class="small">模式</label><select id="lpMode"><option value="sandbox"${lp.mode !== 'production' ? ' selected' : ''}>測試（sandbox）</option><option value="production"${lp.mode === 'production' ? ' selected' : ''}>正式</option></select>
      <label class="chk" style="margin-top:10px"><input type="checkbox" id="lpOn"${lp.enabled ? ' checked' : ''}> 給客戶 LINE Pay 付款按鈕</label>
      <div class="btns">${btn('儲存', 'saveLinePay')}${lp.ready ? btn('測試連線', 'testLinePay', {}, 'sec') : ''}</div>
      <p class="small">貼好金鑰、按儲存後按「測試連線」：會建立一筆 1 元的付款請求確認金鑰和伺服器對外 IP都通，不會扣款。</p>
      <p class="small">正式模式：付款完成會自動記成已收款（不用手動核對），開工前款項收齊就自動排定。退款請到 LINE Pay 商家後台，或在這裡的交易紀錄按退款。</p>
      <p class="small">測試模式（sandbox）：客戶看不到按鈕；要試付款流程，打開報價單網址在後面加 <code>?lptest=1</code>。測試付款不會記成已收款，只會在派單管理群通知「測試付款成功」。</p>
      ${(S.linepay_tx || []).length ? tbl(['時間', '款項', '>金額', '狀態', ''], S.linepay_tx.map(t => `<tr>${td('時間', esc((t.confirmed_at || t.created_at).slice(5, 16).replace('T', ' ')))}${td('款項', esc(t.pid))}${td('金額', '$' + t.amount, 'r')}${td('狀態', esc({ requested: '未完成', confirmed: '已付款', failed: '失敗', refunded: '已退款', partial_refund: '部分退款' }[t.status] || t.status) + (t.mode === 'sandbox' ? '（測試）' : ''))}${td('', t.status === 'confirmed' ? btn('退款', 'lpRefund', { k: t.order_key, a: t.amount }, 'gray') : '')}</tr>`)) : ''}</div>`;
  }
  const gi = S.integrations || {};          // 雲端搬本機：Google 日曆改由中樞直接同步（取代 Apps Script，預設關）
  h += `<h2>Google 日曆</h2><div class="card"><p class="small">通知一律用 LINE 官方。需要在 Google 日曆看排班時才開：中樞每 10 分鐘把 7 天前以後「已排定／已完工」的工單寫進專用日曆「示範服務店・服務排班」（只有工單編號與時間，不含姓名、地址、金額）。</p>
    <p class="small">狀態：${gi.google_configured ? '已載入用戶端' : '還沒載入 OAuth 用戶端'}・${gi.google_connected ? '已授權' : '還沒授權'}・${gi.google_calendar_sync ? '<b>自動同步中</b>' : '同步關閉'}${gi.google_calendar_last_sync ? '・最後同步 ' + esc(gi.google_calendar_last_sync) : ''}</p>
    ${gi.google_configured ? '' : `<label class="small">Google OAuth 用戶端 JSON（Google Cloud › 憑證 › 桌面應用程式 › 下載 JSON，整份貼上）</label><textarea id="gcJson" rows="3" autocomplete="off"></textarea>`}
    <div class="btns">${gi.google_configured ? '' : btn('載入用戶端', 'gcConfig')}${gi.google_configured && !gi.google_connected ? btn('連接 Google（在這台電腦按）', 'gcConnect') : ''}${gi.google_connected ? btn(gi.google_calendar_sync ? '關閉同步' : '開啟同步', 'gcToggle', { on: gi.google_calendar_sync ? '' : '1' }, gi.google_calendar_sync ? 'gray' : '') + (gi.google_calendar_sync ? btn('立即同步', 'gcSync', {}, 'sec') : '') : ''}</div></div>`;
  h += `<h2>官方 LINE</h2><div class="card">${li.map(a => `<div class="item"><div class="t"><div class="n">${esc(a.name)} ${esc(a.basic_id)}</div><div class="m">${a.verified_at ? '只讀連線，' + esc(a.verified_at.slice(0, 16)) + ' 驗證' : '尚未連線'}</div></div><div class="a">${a.basic_id ? btn('重新驗證', 'lineVerify', { id: a.id }, 'gray') : ''}</div></div>`).join('')}
    <p class="small">派工卡、報價單、前一天提醒、到場通知、完成照片由系統自動用官方 LINE 發送；測試／正式在「更多 › LINE 自動化」切換。客戶圖文選單已連到 <b>${location.origin}/book</b>。</p></div>`;
  return h;
};
ACT.testLinePay = async () => { const r = await api('/api/linepay/test', { brand: ($('#lpBrand') || {}).value || '' }); return r.message; };
ACT.saveLinePay = async () => {
  if ($('#lpMode').value === 'production' && $('#lpOn').checked && !confirm('切到「正式」並開放給客戶：客戶付款會真的扣款。確定？')) return;
  await api('/api/linepay/settings', { brand: $('#lpBrand').value, channel_id: $('#lpId').value, channel_secret: $('#lpSec').value, mode: $('#lpMode').value, enabled: $('#lpOn').checked }); await refresh(); return '已儲存';
};
ACT.lpRefund = async d => {
  const a = prompt(`退款金額（元，全額 ${d.a}）：`, d.a); if (a === null) return;
  if (!confirm(`確定退款 $${a}？會真的退回客戶的 LINE Pay。`)) return;
  await api('/api/linepay/refund', { key: d.k, amount: Number(a) === Number(d.a) ? null : Number(a) }); await refresh(); return '已退款';
};
ACT.saveBank = async () => { await api('/api/settings', { bank_info: $('#bank').value }); await refresh(); return '已儲存'; };
ACT.lineVerify = async d => { const r = await api('/api/line/verify', { account_id: d.id }); await refresh(); return r.name + '：驗證成功（只讀）'; };
ACT.calKey = async d => {
  if (d.rotate && !confirm('重新產生後，舊的 Google 同步會失效，要到 Apps Script 更新金鑰。確定？')) return;
  const r = await api('/api/calendar/key', { rotate: !!d.rotate });
  $('#calout').innerHTML = `<p class="small">把下面這段貼到 Apps Script 的 <b>FEED_URL</b>（只顯示這一次，請勿外流）：</p><div class="copybox">${esc(location.origin + '/api/calendar/feed?key=' + r.key)}</div>${btn('複製', 'copyFeed', { u: location.origin + '/api/calendar/feed?key=' + r.key }, 'sec')}`;
};
ACT.copyFeed = d => copy(d.u, '同步網址');
ACT.gcConfig = async () => { let j; try { j = JSON.parse($('#gcJson').value); } catch (e) { return '請整份貼上 JSON'; } await api('/api/google/config', j); await refresh(); return '已載入（加密保存）'; };
ACT.gcConnect = async () => {
  if (!/^(127\.0\.0\.1|localhost)/.test(location.host)) return '請在中樞這台電腦用 http://127.0.0.1:4781 開中控台再按（Google 授權只回到本機）';
  const r = await api('/api/google/connect', {}); location.href = r.url;
};
ACT.gcToggle = async d => { await api('/api/google/calendar', { on: !!d.on }); await refresh(); return d.on ? '已開啟，每 10 分鐘同步' : '已關閉'; };
ACT.gcSync = async () => { const r = await api('/api/google/sync', {}); await refresh(); return `已同步 ${r.synced} 筆` + (r.errors.length ? `，${r.errors.length} 筆失敗` : ''); };
ACT.userForm = d => {
  const u = d.id ? (S.users || []).find(x => x.id === d.id) : { role: d.w ? 'cleaner' : 'staff', worker_id: d.w || '', active: 1 };
  sheet(u.id ? '編輯帳號' : '新增帳號', g2(fld('name', '姓名', u.name || (d.w ? IDX.workers[d.w].name : '')), fld('username', '登入帳號（英文或數字）', u.username))
    + g2(fld('role', '角色', u.role, 'select', { options: [['staff', '客服排班'], ['finance', '帳務'], ['admin', '管理員'], ['cleaner', '服務人員']] }), fld('worker_id', '對應夥伴（服務人員必選）', u.worker_id, 'select', { options: optsOf(S.workers.filter(w => w.status !== 'paused' || w.id === u.worker_id), w => w.name, '不適用') }))
    + fld('password', u.id ? '重設密碼（空白＝不改）' : '初始密碼', '', 'text', { hint: '對方第一次登入會被要求換成自己的密碼。管理員／帳務至少 10 碼，其他至少 6 碼。' })
    + (u.id ? fld('active', '啟用', u.active, 'check') : ''),
    async f => { await api('/api/users', { ...f, id: u.id, active: u.id ? !!f.active : true, worker_id: f.worker_id || null }); return '已儲存' + (f.password ? '，請把帳號密碼私下交給本人' : ''); });
};

ACT.goMap = () => go('map');

document.addEventListener('change', e => { if (e.target.id === 'mkf') { const n = e.target.files.length; const el = $('#mkfn'); if (el) el.textContent = n ? `已選 ${n} 張` : ''; } });
ACT.mkSend = async () => {      // 先送說明，照片一張一張傳（每張縮小）
  const note = ($('#mkn') || {}).value || '', brand = ($('#mkb') || {}).value || 'all', files = [...((($('#mkf') || {}).files) || [])];
  if (!note.trim() && !files.length) throw new Error('寫一點說明或選照片再送出');
  const prog = $('#mkprog'); const r = await api('/api/mk/upload', { brand, note });
  for (let i = 0; i < files.length; i++) { if (prog) prog.textContent = `上傳照片 ${i + 1}/${files.length}…`; await api('/api/mk/upload', { brand, folder: r.folder, files: ['data:image/jpeg;base64,' + await shrink(files[i])] }); }
  MK = null; route(true); return `已送出給行銷（${files.length} 張照片）`;
};
