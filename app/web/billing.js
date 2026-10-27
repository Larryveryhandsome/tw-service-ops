'use strict';
/* 結帳：月結一目了然（客戶應收／已收／未收／逾期）、一鍵開帳、請款與催款文字、夥伴薪資；方案次數、收款對帳池 */
let BILL = null, BMONTH = null;
async function loadBill() { BILL = await api('/api/billing?month=' + BMONTH); }
const STATE_TXT = { overdue: '逾期未收', to_bill: '可以開帳', billed: '待收款', in_service: '服務中', paid: '已收清' };

PAGES.billing = () => {
  BMONTH = BMONTH || today().slice(0, 7);
  if (!BILL || BILL.month !== BMONTH) { loadBill().then(() => route(true)).catch(e => toast(e.message, true)); return phead('結帳', '載入中…', ''); }
  const T = BILL.totals, [y, m] = BMONTH.split('-').map(Number), mode = SEG.bill || 'cust';
  let h = phead('結帳', `${y} 年 ${m} 月，一目了然。`, '排班是預估、實際以完工登記為準。開帳前每一筆金額都可以改；系統只登記，不會發訊息也不會轉帳。');
  h += `<div class="calbar"><span></span><div class="paddles"><button class="paddle" data-act="bmove" data-n="-1" aria-label="上個月">‹</button><button class="today" data-act="bmove" data-n="0">本月</button><button class="paddle" data-act="bmove" data-n="1" aria-label="下個月">›</button></div></div>`;
  h += `<div class="grid" data-tour="bill-tiles"><div class="tile stat"><div class="k">本月應收（預估）</div><div class="v">${money(T.expected)}</div><div class="s">${T.customers} 位客戶</div></div>
    <div class="tile stat good"><div class="k">已收</div><div class="v">${money(T.paid)}</div><div class="s">已對帳登記</div></div>
    <div class="tile stat ${T.balance ? 'warn' : ''}"><div class="k">未收</div><div class="v">${money(T.balance)}</div><div class="s">含之前月份</div></div>
    <div class="tile stat ${T.to_bill ? 'warn' : ''}"><div class="k">完工未開帳</div><div class="v">${money(T.to_bill)}</div><div class="s">${T.overdue ? '逾期 ' + money(T.overdue) : '沒有逾期'}</div></div></div>`;
  h += segctl('bill', [['cust', '客戶帳', BILL.customers.filter(b => ['overdue', 'to_bill'].includes(b.state)).length], ['pay', '夥伴薪資'], ['vendor', '配合廠商', (S.vendor_costs || []).filter(x => x.status === 'pending').length || ''], ['inv', '帳單列表']], mode);
  if (!BILL.bank_info && BILL.customers.some(b => !b.bank)) h += `<div class="banner warn">有品牌還沒設定匯款資訊，請款訊息會缺帳號。${S.role === 'admin' ? '<a data-go="settings">去設定 ›</a>' : '請管理員到設定填寫。'}</div>`;
  if (mode === 'cust') {
    h += '<p class="small">每一筆都可以改：按「改收費」→「這次收費」填實際要收的金額（例：人員被客戶趕走、交通補貼不收就扣掉），或「調整」加減。開帳時也可以再改。</p>';      //
    h += BILL.customers.map(b => {
      const plan = b.plans.map(p => `${esc(p.name)} 剩 ${p.remaining} 次`).join('、');
      return `<div class="card" data-tour="bill-card" id="bc-${esc(b.customer_id)}"><div class="secttl" style="margin-top:0"><h3><a data-act="customer" data-id="${esc(b.customer_id)}">${esc(b.name)}</a> ${st(b.state, STATE_TXT[b.state])}</h3><div class="mono"><b>${money(b.expected)}</b></div></div>
        <div class="small">${esc(b.billing_rule || '')}${plan ? '　· 方案：' + plan : ''}${b.older_balance ? `　· <b style="color:var(--red)">之前月份未收 ${money(b.older_balance)}</b>` : ''}</div>
        ${tbl(['日期', '夥伴', '>預排', '>實際', '狀態', '>金額', ''], b.orders.map(o => `<tr class="click" data-act="order" data-id="${o.id}">${td('日期', md(o.date) + ' ' + o.start)}${td('夥伴', esc(o.workers.join('、') || '—'))}${td('預排', hrs(o.planned_minutes), 'r')}${td('實際', o.actual_minutes == null ? '<span class="small">未登記</span>' : hrs(o.actual_minutes), 'r')}${td('狀態', o.invoice_id ? st('issued', '已開帳') : st(o.status))}${td('金額', o.amount == null ? '<span style="color:var(--red)">缺價格</span>' : money(o.amount) + `<span class="s">${esc(o.basis)}</span>`, 'r')}${td('', btn('改收費', 'editOrder', { id: o.id }, 'gray'))}</tr>`))}
        <div class="totals"><span>已開帳 ${money(b.billed)} · 已收 ${money(b.paid)}</span><span>未收 ${money(b.balance)}</span></div>
        <div class="btns">${b.unbilled_completed.length ? btn(`開帳（${b.unbilled_completed.length} 筆完工）`, 'billCustomer', { id: b.customer_id }) : ''}${b.invoices.filter(i => i.status === 'issued' && i.balance > 0).map(i => btn('登記收款 ' + money(i.balance), 'payInvoice', { id: i.id }, 'sec')).join('')}${b.invoices.length ? btn('複製請款訊息', 'billText', { id: b.customer_id }, 'gray') : ''}${b.balance ? btn('複製催款訊息', 'dunText', { id: b.customer_id }, 'gray') : ''}${b.pending_service ? `<span class="small" style="align-self:center">還有 ${b.pending_service} 場未完工</span>` : ''}</div></div>`;
    }).join('') || '<div class="empty">這個月沒有服務。</div>';
  } else if (mode === 'vendor') {          // 配合廠商：未付的外包成本（不分月份）＋廠商名單
    const pend = (S.vendor_costs || []).filter(x => x.status === 'pending'), byV = {};
    pend.forEach(x => (byV[x.vendor_id] = byV[x.vendor_id] || []).push(x));
    h += `<div class="btns">${btn('新增配合廠商', 'vendorForm', {}, 'sec')}</div>`;
    h += Object.entries(byV).map(([vid, xs]) => { const v = (S.vendors || []).find(y => y.id === vid) || {}, tot = xs.reduce((n, x) => n + x.amount, 0);
      return `<div class="card"><div class="secttl" style="margin-top:0"><h3>${esc(v.name || '（已刪除廠商）')} <span class="small">${esc(v.category || '')}</span></h3><div class="mono"><b>${money(tot)}</b></div></div>
        <div class="small">${esc(v.phone || '')}${v.bank ? '　· ' + esc(v.bank) : '　· <b style="color:var(--red)">還沒填匯款帳號</b>'}</div>
        ${tbl(['日期', '工單', '項目', '>金額'], xs.map(x => `<tr class="click" data-act="order" data-id="${x.order_id}">${td('日期', esc(x.date || ''))}${td('工單', esc(x.title || ''))}${td('項目', esc(x.item))}${td('金額', money(x.amount), 'r')}</tr>`))}
        <div class="btns">${btn(`登記已付（${xs.length} 筆 ${money(tot)}）`, 'vendorPaid', { ids: xs.map(x => x.id).join(',') })}${btn('編輯廠商', 'vendorForm', { id: vid }, 'gray')}</div></div>`; }).join('') || '<div class="empty">沒有未付的外包款。外包成本在工單詳情「配合廠商」記錄。</div>';
    const vs = S.vendors || [];
    if (vs.length) h += `<div class="secttl"><h3>廠商名單 ${vs.length}</h3></div><div class="card">` + vs.map(v => `<div class="item"><div class="t"><div class="n">${esc(v.name)} <span class="small">${esc(v.category || '')}${v.status === 'paused' ? '・暫停' : ''}</span></div><div class="m">${esc(v.phone || '—')}</div></div><div class="a">${btn('編輯', 'vendorForm', { id: v.id }, 'gray')}</div></div>`).join('') + '</div>';
  } else if (mode === 'pay') {
    h += '<div class="card">' + tbl(['夥伴', '>場次', '>完工', '>時數', '>應付', '狀態', ''], BILL.payroll.map(w => `<tr>${td('夥伴', esc(w.name))}${td('場次', w.jobs, 'r')}${td('完工', w.done, 'r')}${td('時數', hrs(w.minutes), 'r')}${td('應付', money(w.pay) + (w.missing ? `<span class="s" style="color:var(--red)">${w.missing} 筆缺時薪</span>` : ''), 'r')}${td('狀態', w.settled >= w.done && w.done ? st('paid', '已結算') : st('pending', '未結算'))}${td('', btn('薪資明細', 'payText', { id: w.worker_id }, 'gray') + (w.done > w.settled ? btn('建立結算（月）', 'settle', { id: w.worker_id }, 'sec') + btn('之前已匯（補登、不通知）', 'payOld', { id: w.worker_id }, 'gray') : '') + ((IDX.workers[w.worker_id] || {}).payout_cycle === 'week' ? btn('結算上週（週領）', 'settleWeek', { id: w.worker_id }, 'sec') : ''))}</tr>`)) + '</div>';
    const sets = (S.settlements || []).filter(s => s.period === BMONTH || (String(s.period).startsWith('W') && s.period.slice(1, 8) === BMONTH));
    const bank = id => { const w = IDX.workers[id] || {}; return w.bank_account ? `${esc(w.bank_name || '')} ${esc(w.bank_account)}${w.bank_holder ? '（' + esc(w.bank_holder) + '）' : ''}` : '<b style="color:var(--red)">還沒填匯款帳號</b>'; };
    if (sets.length) h += `<h2>結算單</h2><div class="card">${tbl(['夥伴', '期間', '>應付', '>手續費', '>實匯', '帳號', '狀態', ''], sets.map(s => `<tr>${td('夥伴', esc(wname(s.worker_id)))}${td('期間', String(s.period).startsWith('W') ? '週 ' + esc(s.period.slice(6)) + ' 起' : '月')}${td('應付', money(s.total), 'r')}${td('手續費', s.fee ? '−' + money(s.fee) : '—', 'r')}${td('實匯', money(s.total - (s.fee || 0)), 'r')}${td('帳號', bank(s.worker_id))}${td('狀態', st(s.status === 'draft' ? 'pending' : 'paid', s.status === 'draft' ? '待匯款' : '已匯款 ' + (s.paid_at || '').slice(0, 10)))}${td('', s.status === 'draft' ? btn('登記已匯款', 'settlePaid', { id: s.id }, 'sec') : esc(s.reference))}</tr>`))}</div>`;
    h += '<p class="small">取消但有補貼（例如到場被鎖在外面的車馬費）也會算進應付。登記已匯款只是記錄，系統不會轉帳。</p>';
  } else {
    const inv = (S.invoices || []).filter(i => i.period === BMONTH);
    h += '<div class="card">' + tbl(['客戶', '狀態', '到期', '>應收', '>已收', '>未收', ''], inv.map(i => `<tr>${td('客戶', esc(cust(i.customer_id).dn))}${td('狀態', st(i.status === 'issued' && i.balance === 0 ? 'paid' : i.status, i.status === 'issued' && i.balance === 0 ? '已收清' : ''))}${td('到期', esc(i.due_date))}${td('應收', money(i.total), 'r')}${td('已收', money(i.paid), 'r')}${td('未收', money(i.balance), 'r')}${td('', btn('明細', 'invoiceDetail', { id: i.id }, 'gray') + (i.status === 'draft' ? btn('核定', 'issueInvoice', { id: i.id }, 'sec') : ''))}</tr>`)) + '</div>';
  }
  return h;
};
ACT.bmove = d => { if (d.n === '0') BMONTH = today().slice(0, 7); else { const [y, m] = BMONTH.split('-').map(Number); const n = new Date(y, m - 1 + Number(d.n), 1); BMONTH = n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0'); } BILL = null; route(true); };
const billOf = id => BILL.customers.find(b => b.customer_id === id);
async function rebill() { BILL = null; await refresh(); }

ACT.billCustomer = d => {
  const b = billOf(d.id), list = b.orders.filter(o => o.status === 'completed' && !o.invoice_id && !o.prepaid);      // 稽核：線上簽約已預付的不列（以前整筆開帳報錯）
  const plan = b.plans[0];
  sheet(`開帳：${esc(b.name)} ${BMONTH}`, `<p class="small">每筆金額依「已核定 → 案場每次價 → 時薪×實際時數」預先帶入，可以直接改。方案月費的客戶可以把各次改 0、下方填月費。</p>`
    + list.map(o => `<div class="item"><div class="t"><div class="n">${md(o.date)} ${o.start}–${o.end}</div><div class="m">${esc(o.workers.join('、'))} · 實際 ${hrs(o.actual_minutes)} · ${esc(o.basis)}</div></div><div class="a" style="width:140px"><input name="amt_${o.id}" type="number" inputmode="decimal" value="${o.amount == null ? '' : o.amount / 100}" aria-label="金額" placeholder="金額"></div></div>`).join('')
    + g2(fld('plan_desc', '方案／其他項目說明', plan ? `${plan.name} 月費` : ''), fld('plan_amt', '金額（元）', '', 'number'))
    + fld('tax', '加 5% 營業稅（需開發票的客戶）' + (list.some(o => o.basis === '已核定') ? '｜注意：「已核定」的金額來自報價單，有勾發票的已經含稅' : ''),
      /發票|稅/.test(b.billing_rule || '') && !/不開|免開|不用|不需|含稅/.test(b.billing_rule || '') && !list.some(o => o.basis === '已核定') ? 1 : 0, 'check')
    + g2(fld('due_date', '付款期限', BMONTH + '-' + (/月底/.test(b.billing_rule || '') ? '28' : '10') > today() ? BMONTH + (/月底/.test(b.billing_rule || '') ? '-28' : '-10') : addDays(today(), 7), 'date'), fld('notes', '備註', ''))
    + `<div class="totals"><span>合計</span><span id="billSum">—</span></div>`,
    async f => {
      const lines = list.map(o => ({ order_id: o.id, amount: toCents(f['amt_' + o.id]) })).filter(l => l.amount !== null);
      if (lines.length !== list.length) throw new Error('每一筆都要填金額（不收費填 0）');
      const manual = [];
      if (f.plan_desc && f.plan_amt) manual.push({ description: f.plan_desc, amount: toCents(f.plan_amt) });
      const sub = lines.reduce((n, l) => n + l.amount, 0) + manual.reduce((n, l) => n + l.amount, 0);
      if (f.tax) manual.push({ description: '營業稅 5%', amount: Math.round(sub * 0.05 / 100) * 100 });
      await api('/api/billing/bill', { customer_id: b.customer_id, month: BMONTH, lines, manual_lines: manual, due_date: f.due_date, notes: f.notes, issue: true });
      BILL = null; return '已開帳，可以複製請款訊息';
    }, '開帳');
  const sum = () => { const f = form(); let n = list.reduce((a, o) => a + (Number(f['amt_' + o.id]) || 0), 0) + (Number(f.plan_amt) || 0); if (f.tax) n += Math.round(n * .05); $('#billSum').textContent = '$' + n.toLocaleString('zh-TW'); };
  $('#sheetForm').addEventListener('input', sum); $('#sheetForm').addEventListener('change', sum); sum();
};
function billText(b, dun) {
  const inv = b.invoices.filter(i => i.status === 'issued'), lines = (S.invoice_lines || []).filter(l => inv.some(i => i.id === l.invoice_id));
  const [, m] = BMONTH.split('-').map(Number), total = inv.reduce((n, i) => n + i.total, 0), paid = inv.reduce((n, i) => n + i.paid, 0), bal = total - paid + (dun ? b.older_balance : 0);
  const body = lines.map(l => { const o = IDX.orders[l.order_id]; return o ? `${md(o.date)} ${o.start}–${o.end}　${money(l.amount)}` : `${l.description}　${money(l.amount)}`; }).join('\n');
  const due = inv.map(i => i.due_date).sort()[0] || '';
  const bank = b.bank || BILL.bank_info || '';
  if (dun) return `${b.name} 您好，這裡是${b.brand_name || '示範服務店'} 😊\n提醒您 ${m} 月服務款項 ${money(bal)} 尚未收到${due ? `（原付款期限 ${md(due)}）` : ''}。\n若已匯款請告訴我們帳號末五碼，我們馬上核對；如有任何疑問也歡迎直接回覆，謝謝您！${bank ? '\n\n' + bank : ''}`;
  return `${b.name} 您好，這裡是${b.brand_name || '示範服務店'} 😊\n${m} 月服務明細如下：\n${body}\n合計 ${money(total)}${paid ? `，已收 ${money(paid)}，本次應付 ${money(total - paid)}` : ''}${due ? `\n付款期限：${md(due)}` : ''}${bank ? '\n\n' + bank : ''}\n\n匯款後請告訴我們帳號末五碼，謝謝您 🙏`;
}
ACT.billText = d => copy(billText(billOf(d.id), false), '請款訊息');
ACT.dunText = d => copy(billText(billOf(d.id), true), '催款訊息');
ACT.payInvoice = d => {
  const inv = S.invoices.find(i => i.id === d.id), key = crypto.randomUUID();
  sheet('登記收款', `<p>${esc(cust(inv.customer_id).dn)} ${esc(inv.period)}　未收 <b>${money(inv.balance)}</b></p>` + g2(fld('amount', '收到金額（元）', inv.balance / 100, 'number'), fld('date', '收款日期', today(), 'date'))
    + g2(fld('method', '方式', '匯款', 'select', { options: [['匯款', '匯款'], ['現金', '現金'], ['LINE Pay', 'LINE Pay'], ['其他', '其他']] }), fld('reference', '核對依據（帳號末五碼、收款人）', ''))
    + '<p class="small">請確認銀行真的入帳再登記，聊天裡說「已匯款」不算。</p>',
    async f => { if (!f.reference) throw new Error('請填帳號末五碼或收款人'); await api(`/api/invoice/${inv.id}/payment`, { amount: toCents(f.amount), date: f.date, method: f.method, reference: f.reference, request_key: key }); BILL = null; return '已登記收款'; }, '登記');
};
ACT.invoiceDetail = d => {
  const i = S.invoices.find(x => x.id === d.id);
  sheet('帳單明細', tbl(['項目', '>金額'], S.invoice_lines.filter(l => l.invoice_id === i.id).map(l => `<tr>${td('項目', esc(l.description))}${td('金額', money(l.amount), 'r')}</tr>`))
    + `<div class="totals"><span>應收</span><span>${money(i.total)}</span></div><h3>收款</h3>` + tbl(['日期', '方式', '>金額', '依據'], S.payments.filter(p => p.invoice_id === i.id).map(p => `<tr>${td('日期', esc(p.date))}${td('方式', esc(p.method))}${td('金額', money(p.amount), 'r')}${td('依據', esc(p.reference))}</tr>`))
    + `<div class="btns">${i.status === 'issued' ? btn('減免／折讓', 'credit', { id: i.id }, 'gray') : ''}</div>`);
};
ACT.issueInvoice = async d => { await api(`/api/invoice/${d.id}/issue`, {}); BILL = null; await refresh(); return '已核定'; };
ACT.credit = d => { const key = crypto.randomUUID(); sheet('減免／折讓', fld('amount', '金額（元）', '', 'number') + fld('reason', '原因（例：9 折、補償）', '', 'textarea') + fld('date', '日期', today(), 'date'), async f => { await api('/api/workflow/credit', { invoice_id: d.id, amount: toCents(f.amount), reason: f.reason, date: f.date, request_key: key }); BILL = null; return '已記錄減免'; }); };
ACT.payText = d => {       // 明細跟月結、結算單、夥伴「我的收入」同一套（伺服器 board.pay_lines 算好）
  const w = BILL.payroll.find(x => x.worker_id === d.id), [, m] = BMONTH.split('-').map(Number);
  const txt = `${w.name} 您好，${m} 月工作明細：\n` + (w.lines || []).map(x => `${md(x.date)} ${x.site}${x.minutes != null ? ' ' + hrs(x.minutes) : ''}${x.pay != null ? ' ' + money(x.pay) : '（缺時薪）'}${x.basis !== '打卡' ? `（${x.basis}）` : ''}`).join('\n') + `\n合計 ${money(w.pay)}，再麻煩核對，謝謝你的辛苦 🙏`;
  return copy(txt, '薪資明細');
};
ACT.settleWeek = async d => {      // 週領 → 結算上週（週一到週日）
  const t = new Date(); const day = (t.getDay() + 6) % 7; const mon = new Date(t.getFullYear(), t.getMonth(), t.getDate() - day - 7);
  const iso = mon.getFullYear() + '-' + String(mon.getMonth() + 1).padStart(2, '0') + '-' + String(mon.getDate()).padStart(2, '0');
  await api('/api/settlements', { worker_id: d.id, period: 'W' + iso }); BILL = null; await refresh(); return `已建立上週（${iso} 起）結算單`;
};
ACT.settle = async d => { await api('/api/settlements', { worker_id: d.id, period: BMONTH }); BILL = null; await refresh(); return '已建立結算單'; };
ACT.settlePaid = d => sheet('登記已匯款', fld('reference', '匯款日期／帳號末五碼／備註', '', 'textarea')
  + '<label class="chk" style="margin-top:10px"><input type="checkbox" name="silent" value="1"> 不通知夥伴（之前已經匯過，只是補登）</label>',
  async f => { const r = await api(`/api/settlement/${d.id}/paid`, { reference: f.reference, silent: !!f.silent }); BILL = null; return r.notified ? '已登記，已 LINE 通知夥伴' : '已登記（沒有通知夥伴）'; });
ACT.payOld = d => sheet(`之前已匯：${esc(wname(d.id))}`, `<p class="small">${BMONTH} 這位夥伴已完工、還沒結算的場次，一次記成「已匯款」。<b>不會傳 LINE 給夥伴</b>（很多人其實已經匯過了）。</p>`
  + fld('reference', '什麼時候、怎麼匯的（例：10/20 匯款，週領）', '之前已匯（補登）', 'textarea'),
  async f => { const s = await api('/api/settlements', { worker_id: d.id, period: BMONTH }); if (!s || !s.id) throw new Error('沒有可以結算的場次');
    await api(`/api/settlement/${s.id}/paid`, { reference: f.reference || '之前已匯（補登）', silent: true }); BILL = null; await refresh(); return '已補登為已匯款（沒有通知夥伴）'; }, '補登為已匯款');      // 

/* ═════════ 方案與次數 ═════════ */
PAGES.plans = () => phead('方案與次數', '每次服務，都有依據。', '月方案、套票的可用次數、已排、剩餘。補掃不扣次。', can('money') ? btn('新增方案', 'newPlan') : '')
  + '<div class="card">' + tbl(['客戶／方案', '期間', '>金額', '>剩餘', '>已排', '條件', ''], (S.plans || []).map(p => `<tr>${td('方案', `<b>${esc(cust(p.customer_id).dn)}</b><span class="s">${esc(p.name)}</span>`)}${td('期間', esc(p.valid_from + '～' + p.valid_to))}${td('金額', money(p.price), 'r')}${td('剩餘', p.remaining, 'r')}${td('已排', p.reserved, 'r')}${td('條件', `<span class="small">${esc(p.terms)}</span>`)}${td('', can('money') ? btn('展延／調整', 'planAdjust', { id: p.id }, 'gray') : '')}</tr>`)) + '</div>';
ACT.newPlan = () => sheet('新增方案', pickFld('customer_id', '客戶', '', custOpts()) + fld('name', '方案名稱（例：每月 4 次 4 小時）', '') + g2(fld('valid_from', '開始', today().slice(0, 7) + '-01', 'date'), fld('valid_to', '到期', '', 'date')) + g2(fld('price', '方案金額（元）', '', 'number'), fld('units', '可用次數', 4, 'number')) + fld('terms', '約定（未用可否遞延、指定人員…）', '', 'textarea'),
  async f => { await api('/api/workflow/plan', { ...f, price: toCents(f.price), units: Number(f.units) }); return '已建立'; });
ACT.planAdjust = d => { const p = S.plans.find(x => x.id === d.id); sheet('展延／調整次數', fld('valid_to', '新到期日', p.valid_to, 'date') + fld('units', '增減次數（不變填 0）', 0, 'number') + fld('reason', '依據', '', 'textarea'), async f => { await api('/api/workflow/plan-adjust', { id: p.id, ...f, units: Number(f.units) }); return '已調整'; }); };

/* ═════════ 收款對帳池（預收、一筆匯款付多張帳單）═════════ */
PAGES.receipts = () => phead('收款對帳', '款項先核對，再入帳。', '客戶一次匯多個月、預收、保證金：先登記在這裡、確認銀行入帳，再分攤到帳單。', can('money') ? btn('登記收款', 'newReceipt') : '')
  + ((S.payment_requests || []).filter(x => x.status === 'reported').map(x => `<div class="item"><div class="t"><div class="n">💰 ${esc(x.customer || '—')}　${esc(x.label)}　NT$ ${(x.amount / 100).toLocaleString()}</div><div class="m">客戶回報已匯款${x.ref ? '・末五碼 ' + esc(x.ref) : ''}：先到網銀核對入帳</div></div><div class="a">${btn('確認收款', 'payOk', { id: x.id })}${x.token ? btn('報價單', 'openQ', { t: x.token }, 'gray') : ''}${x.order_id ? btn('工單', 'order', { id: x.order_id }, 'gray') : ''}</div></div>`).join('').replace(/^(.+)$/s, '<div class="secttl"><h3>客戶回報已匯款・待確認</h3></div><div class="card">$1</div>'))      // 紅點數字＝這幾筆，點進來就看得到、直接按
  + '<div class="card">' + tbl(['客戶', '日期', '狀態', '>金額', '>已分攤', '>可用', ''], (S.receipts || []).map(r => `<tr>${td('客戶', esc(cust(r.customer_id).dn) + `<span class="s">${esc(r.reference)}</span>`)}${td('日期', esc(r.date))}${td('狀態', st(r.status))}${td('金額', money(r.amount), 'r')}${td('已分攤', money(r.allocated), 'r')}${td('可用', money(r.available), 'r')}${td('', r.status === 'pending' ? btn('確認入帳', 'rVerify', { id: r.id }, 'sec') : r.available > 0 ? btn('分攤到帳單', 'rAlloc', { id: r.id }, 'sec') : '')}</tr>`)) + '</div>';
ACT.newReceipt = () => { const key = crypto.randomUUID(); sheet('登記收款', pickFld('customer_id', '客戶', '', custOpts()) + g2(fld('amount', '金額（元）', '', 'number'), fld('date', '日期', today(), 'date')) + g2(fld('kind', '類型', 'payment', 'select', { options: [['payment', '服務款'], ['prepayment', '預收／保證金']] }), fld('method', '方式', '匯款')) + fld('reference', '核對依據（末五碼）', ''), async f => { await api('/api/workflow/receipt', { ...f, amount: toCents(f.amount), request_key: key }); return '已登記，待確認入帳'; }); };
ACT.rVerify = d => sheet('確認銀行已入帳', fld('reference', '核對人與日期、銀行明細', '', 'textarea'), async f => { await api('/api/workflow/receipt-verify', { id: d.id, ...f }); return '已確認'; });
ACT.rAlloc = d => { const r = S.receipts.find(x => x.id === d.id), key = crypto.randomUUID(); sheet('分攤到帳單', fld('invoice_id', '帳單', '', 'select', { options: optsOf(S.invoices.filter(i => i.customer_id === r.customer_id && i.status === 'issued' && i.balance > 0), i => `${i.period} 未收 ${money(i.balance)}`) }) + g2(fld('amount', '金額（元）', r.available / 100, 'number'), fld('date', '日期', today(), 'date')), async f => { await api('/api/workflow/allocate', { receipt_id: r.id, ...f, amount: toCents(f.amount), request_key: key }); BILL = null; return '已分攤'; }); };

/* ═════════ 收款紀錄（管理群點得到歷史付款）═════════ */
let PH = null, PHM = null;
PAGES.payhist = () => {
  if (!can('money')) return phead('收款紀錄', '只有看得到金額的人可以看。', '');
  PHM = PHM ?? today().slice(0, 7);
  if (!PH || PH.month !== PHM) { api('/api/payhist?month=' + PHM).then(v => { PH = v; route(true); }).catch(e => toast(e.message, true)); return phead('收款紀錄', '載入中…', ''); }
  const s = PH.summary, f = SEG.phm || 'all', [y, m] = (PHM || today().slice(0, 7)).split('-').map(Number);
  const list = PH.rows.filter(x => f === 'all' || (f === 'wait' ? x.status.startsWith('客戶回報') : x.method.startsWith(f)));
  let h = phead('收款紀錄', '每一筆進來的錢。', 'LINE Pay、匯款確認、商城、帳單收款、預收款都在這裡。點一筆直接打開那張工單或報價單。派單管理群打「收款紀錄」也看得到本月合計。',
    `${btn('‹', 'phMonth', { d: -1 }, 'gray')}<b style="margin:0 8px">${PHM ? `${y} 年 ${m} 月` : '全部'}</b>${btn('›', 'phMonth', { d: 1 }, 'gray')}${btn(PHM ? '看全部月份' : '回本月', 'phAll', {}, 'gray')}`);
  h += `<div class="grid"><div class="tile stat good"><div class="k">已收</div><div class="v">${money(s.total * 100)}</div><div class="s">${s.count} 筆</div></div>`
    + Object.entries(s.by_method).map(([k, v]) => `<div class="tile stat"><div class="k">${esc(k)}</div><div class="v">${money(v * 100)}</div></div>`).join('')
    + (s.waiting_n ? `<div class="tile stat warn"><div class="k">待核對</div><div class="v">${money(s.waiting * 100)}</div><div class="s">${s.waiting_n} 筆客戶回報已匯款</div></div>` : '') + '</div>';
  h += segctl('phm', [['all', '全部', PH.rows.length], ['LINE Pay', 'LINE Pay'], ['匯款', '匯款'], ['商城', '商城'], ['wait', '待核對', s.waiting_n]], f);
  h += '<div class="card">' + tbl(['時間', '客戶', '款項', '方式', '狀態', '>金額', ''], list.map(x => `<tr class="click" ${x.order_id ? `data-act="order" data-id="${esc(x.order_id)}"` : x.quote_token ? `data-act="openQ" data-t="${esc(x.quote_token)}"` : ''}>`
    + td('時間', esc(x.at.slice(5, 16).replace('T', ' '))) + td('客戶', esc(x.name) + (x.brand ? `<span class="s">${esc(x.brand)}</span>` : '')) + td('款項', esc(x.label) + (x.ref ? `<span class="s">末五碼 ${esc(x.ref)}</span>` : ''))
    + td('方式', esc(x.method)) + td('狀態', x.status.startsWith('客戶回報') ? `<span class="st pending">待核對</span>` : `<span class="st paid">${esc(x.status)}</span>`) + td('金額', money(x.amount * 100), 'r')
    + td('', (x.status.startsWith('客戶回報') && x.kind === 'request' ? btn('確認收款', 'payOk', { id: x.id }) : '') + (x.kind === 'request' ? btn('改金額', 'payEdit', { id: x.id }, 'gray') : '')) + '</tr>')) + '</div>';
  return h;
};
ACT.phMonth = d => { const [y, m] = (PHM || today().slice(0, 7)).split('-').map(Number), n = new Date(y, m - 1 + Number(d.d), 1); PHM = `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}`; PH = null; route(true); };
ACT.phAll = () => { PHM = PHM ? '' : today().slice(0, 7); PH = null; route(true); };
{ const po = ACT.payOk; ACT.payOk = async d => { const r = await po(d); PH = null; if (VIEW === 'payhist') route(true); return r; }; }      // 收款紀錄頁按確認收款 → 重新整理

ACT.payEdit = d => {      // 收款單金額要能改（例：不收交通補貼）
  const x = (S.payment_requests || []).find(r => r.id === d.id) || (PH && PH.rows.find(r => r.id === d.id)) || {};
  sheet('改收款金額', `<p class="small">${esc(x.customer || x.name || '')} ${esc(x.label || '')}：改了以這裡為準，客戶的報價單付款區會跟著變。</p>`
    + fld('amount', '金額（元）', x.amount != null ? (x.kind === 'request' ? x.amount : x.amount / 100) : '', 'number') + fld('note', '為什麼改（留紀錄）', '', 'textarea'),
    async f => { if (!(Number(f.amount) >= 0)) throw new Error('請填金額'); await api('/api/payment/edit', { id: d.id, amount: Math.round(Number(f.amount) * 100), note: f.note || '' }); PH = null; return '已改金額'; });
};
