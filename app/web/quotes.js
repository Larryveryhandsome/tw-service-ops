'use strict';
/* 報價審核（店長）：客戶送出 → 系統初估＋照片 → 在這裡看照片、對照過往報價、修改品項與範圍 → 確認並傳給客戶。 */
let QO = null, QD = null, QUPD = null;
document.getElementById('sheetBody').addEventListener('input', () => { if ($('#q-taxed') && QUPD) { QUPD(); qDraftSave(); } });
/* 報價草稿：每次修改都先存在這台電腦（不小心關掉、跳出去，再打開可以還原）；儲存／傳出後清掉 */
const QDK = id => 'qdraft:' + id;
function qDraftSave() { try { if (QD && QD.status === 'draft' && $('#q-date')) localStorage.setItem(QDK(QD.id), JSON.stringify({ at: Date.now(), p: qPayload() })); } catch (e) { } }
function qDraftGet(id) { try { return JSON.parse(localStorage.getItem(QDK(id)) || 'null'); } catch (e) { return null; } }
function qDraftDrop(id) { try { localStorage.removeItem(QDK(id)); } catch (e) { } }
ACT.qRestore = () => { const d = qDraftGet(QD.id); if (!d) return; QD = { ...QD, items: d.p.items, note: d.p.note, taxed: d.p.taxed, date: d.p.date, start: d.p.start, people: d.p.people, hours: d.p.hours, scope: d.p.scope }; quoteSheet(true); SHEET_DIRTY = true; return '已還原上次沒存到的修改（記得按儲存）'; };
ACT.qLinkSent = async () => { await api('/api/quote/link-sent', { id: QD.id }); QD = await api('/api/quote?id=' + encodeURIComponent(QD.id)); QO = null; quoteSheet(); route(true); return '已記錄：連結已傳給客戶'; };
ACT.qCopyLink = () => copy(QD.public_url, '報價單連結');
const QSEG = { drafting: '草稿', review: '待確認', photos: '等照片', site_visit: '現場估價', sent: '已傳客戶', signed: '已簽收', all: '全部' };
const ntd = n => n == null ? '—' : 'NT$ ' + Number(n || 0).toLocaleString('zh-TW');
let QO_AT = 0;
PAGES.quotes = () => {
  if (QO && (Date.now() - QO_AT > 20000 || QO.rule_brand !== (SEG.qb || 'demoshop'))) QO = null;     // 超過 20 秒或換品牌就重抓
  if (!QO) { QO_AT = Date.now(); api('/api/quotes?b=' + encodeURIComponent(SEG.qb || 'demoshop')).then(v => { QO = v; route(true); }).catch(e => toast(e.message, true)); return phead('報價', '載入中…', ''); }
  const kw = (SEG.qq || '').trim().toLowerCase();       // 可以用預約編號、報價單號、姓名、電話、LINE 名稱找
  const seg = kw ? 'all' : (SEG.qs || ['review', 'site_visit', 'photos'].find(k => QO.quotes.some(q => q.stage_key === k)) || 'review'), list = QO.quotes.filter(q => (seg === 'all' || q.stage_key === seg) && (!kw || [q.code, q.id, q.name, q.phone, q.line_name].some(v => String(v || '').toLowerCase().includes(kw))));      // 預設打開有東西的分頁
  let h = phead('報價', '每一張報價，都先經過你。', '客戶送出後系統會先初估、附上照片；你確認或修改後按「確認並傳給客戶」，客戶才看得到正式報價單並簽收。現場估價完或大型案場，可以自己「新增報價單」。', btn('新增報價單', 'quoteNew'));
  h += segctl('qs', Object.entries(QSEG).map(([k, t]) => [k, t, k === 'all' ? '' : QO.quotes.filter(q => q.stage_key === k).length || '']), seg);
  h += `<input type="search" id="qq" placeholder="搜尋：預約編號、報價單號、姓名、電話、LINE 名稱" value="${esc(SEG.qq || '')}" style="margin:8px 0;max-width:420px">`;
  h += '<div class="card">' + tbl(['送出時間', '客戶', '類型', '金額', '照片', '狀態'], list.map(q => `<tr class="click" data-act="quoteOpen" data-id="${q.id}">
    ${td('送出', esc(q.created_at.slice(5, 16).replace('T', ' ')))}${td('客戶', `<b>${esc(q.name)}</b>${q.line_name && q.line_name !== q.name ? `<span class="small">（LINE：${esc(q.line_name)}）</span>` : ''}<span class="s">${esc(q.phone || '')}${q.code ? '・預約編號 ' + esc(q.code) : ''}・${esc(q.id)}</span>`)}
    ${td('類型', esc(q.service_type || '') + `<span class="s">${esc(q.brand_name || '')}・${esc(q.area || '')}${q.size ? '・' + esc(q.size) + ' 單位' : ''}</span>`)}
    ${td('金額', q.mode === 'site_visit' ? `現場估價<span class="s">估價費 ${q.visit_fee ? ntd(q.visit_fee) : '免費'}</span>` : ntd(q.total))}
    ${td('照片', q.photos ? q.photos + ' 張' : '<span class="small">—</span>')}${td('狀態', st(q.stage_key === 'review' || q.stage_key === 'site_visit' ? 'pending' : q.stage_key === 'signed' ? 'completed' : q.stage_key === 'cancelled' ? 'archived' : 'review', q.stage_name))}</tr>`)) + '</div>';
  if (S.role === 'admin') h += `<h2>報價規則與資料</h2><p class="small">每個品牌各自一份（定價、範圍、報價單抬頭、匯款都不會混用）。現在編輯：</p>`
    + segctl('qb', (S.brands || []).map(b => [b.id, b.name]), SEG.qb || 'demoshop') + `<div class="list">${[['rules', '估價與現場估價規則', '門檻、估價費、每小時單價、最低消費、派工方式'], ['addons', '加購項目與價格', '急件、假日、材料費⋯'],
    ['scopes', '標準服務範圍', '報價單的「規格與範圍說明」、夥伴看到的範圍'], ['company', '公司與匯款資料', '報價單抬頭、地址、匯款帳號'], ['bindmsg', '給舊客戶的打招呼訊息', '新系統上線、配合廠商服務、LINE 綁定（示範服務店／企業線／生活線各一份）']].map(([k, t, s]) => `<button data-act="qset" data-k="${k}"><span><b>${t}</b><div class="small">${s}</div></span><span class="chev">›</span></button>`).join('')}</div>`;
  return h;
};
const PLABEL = { low_value: '線上報價上限（單次服務超過就請客戶現場估價；加購不算）', low_value_bnb: '店家的線上報價上限', site_visit_fee: '現場估價費', community_visit_fee: '大型案場服務現場估價費', max_auto_size: '超過幾單位一律現場估價',
  rate_single: '居家單次（每人每小時）', rate_plan: '定期 4 次以上（每人每小時）', rate_deep: '年度保養／搬家（每人每小時）', rate_reno: '進階專案（每人每小時）', rate_shop: '店面／辦公室（每人每小時）',
  bnb_per_room: '店家每間房', min_single: '居家最低消費', min_deep: '年度保養最低消費', min_reno: '進階專案最低消費', min_bnb: '店家最低消費', pet_pct: '寵物加成（%）', ladder: '高處（梯子）加價',
  no_lift_per_floor: '無電梯每層加價', townhouse_pct: '透天樓層加成（%）', k_deep: '年度保養每單位人時', k_reno: '進階專案每單位人時',
  trash_bag: '垃圾清收：第一袋', trash_bag_more: '垃圾清收：第二袋起每袋（0＝同第一袋）', trash_max_bags: '垃圾清收：一次最多幾袋線上初估（超過專人報價；0＝不限）',
  trash_addon: '服務加購垃圾清收：每袋', trash_pay: '垃圾清收夥伴酬勞（固定，不看時數；每袋，店長選「每次」就是每次）', trash_addon_pay: '服務加購垃圾清收：夥伴每袋另加（0＝算在時薪）' };
ACT.qset = d => {
  const p = QO.pricing;
  if (d.k === 'rules') return sheet('估價與現場估價規則', `<p class="small">改完按儲存，新的估價馬上用新規則；已經送出的報價不受影響。</p>`
    + `<label>簽收後派工方式</label><select name="dispatch_mode"><option value="manual"${QO.dispatch_mode === 'manual' ? ' selected' : ''}>店長挑人（建議）</option><option value="auto"${QO.dispatch_mode === 'auto' ? ' selected' : ''}>系統自動派</option></select>`
    + Object.entries(PLABEL).map(([k, t]) => `<label>${t}</label><input name="${k}" type="number" step="any" value="${p[k]}">`).join(''),
    async f => { const { dispatch_mode, ...nums } = f; const out = { ...p }; for (const k in nums) out[k] = Number(nums[k]); await api('/api/pricing', { ...out, brand: QO.rule_brand }); await api('/api/dispatch-mode', { mode: dispatch_mode }); QO = null; route(true); return '已儲存'; });
  if (d.k === 'addons') return sheet('加購項目與價格', `<p class="small">一行一項，格式：名稱=價格。刪掉那一行就不再提供。</p><textarea name="addons" rows="12">${esc(Object.entries(p.addons).map(([k, v]) => `${k}=${v}`).join('\n'))}</textarea>`,
    async f => { const a = {}; f.addons.split('\n').forEach(l => { const [k, v] = l.split('='); if (k && v && !isNaN(+v)) a[k.trim()] = +v; }); await api('/api/pricing', { ...p, addons: a, brand: QO.rule_brand }); QO = null; route(true); return '已儲存'; });
  if (d.k === 'scopes') return sheet('標準服務範圍', `<p class="small">一行一項。報價單會自動帶入，夥伴也會看到同一份範圍。</p>` + Object.entries(QO.scopes).map(([k, v]) => `<label>${esc(k)}</label><textarea name="sc_${esc(k)}" rows="${v.length + 1}">${esc(v.join('\n'))}</textarea>`).join(''),
    async f => { const sc = {}; for (const k in f) if (k.startsWith('sc_')) sc[k.slice(3)] = f[k].split('\n').map(s => s.trim()).filter(Boolean); await api('/api/scopes', { scopes: sc, brand: QO.rule_brand }); QO = null; route(true); return '已儲存'; });
  if (d.k === 'bindmsg') return sheet('給舊客戶的打招呼訊息', `<p class="small">在「客戶 › 還沒綁 LINE 的客戶」按「複製綁定訊息」時用的內容。<b>{name}</b> 會換成客戶稱呼、<b>{url}</b> 會換成他的綁定連結（一定要保留）。整段清空再儲存＝恢復預設。</p>`
    + (QO.brands || []).map(b => [b.id, b.name]).map(([k, t]) => `<label>${t}</label><textarea name="${k}" rows="14">${esc(QO.bind_message[k])}</textarea>`).join(''),
    async f => { await api('/api/bind-message', f); QO = null; route(true); return '已儲存'; });
  const co = QO.company, CL = { name: '公司名稱（報價單）', slogan: '標語', tagline: '英文標語', address: '地址', bank: '銀行', holder: '戶名', account: '帳號', review_url: 'Google 評論連結（客戶給 4–5 星時會請他去寫評論）' };
  sheet('公司與匯款資料', Object.entries(CL).map(([k, t]) => fld(k, t, co[k])).join('')
    + `<label>公司報價章（PNG，確認後的報價單會蓋上）${QO.stamp ? '　✓ 已上傳' : ''}</label><input type="file" id="stampf" accept="image/png">`,
    async f => { await api('/api/company', { ...f, brand: QO.rule_brand }); const file = $('#stampf').files[0];
      if (file) { const img = await new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = no; r.readAsDataURL(file); }); await api('/api/stamp', { image: img, brand: QO.rule_brand }); }
      QO = null; route(true); return '已儲存'; });
};
ACT.quoteOpen = async d => { QD = await api('/api/quote?id=' + encodeURIComponent(d.id)); quoteSheet(); };
const Q_PAY_TEXT = { 訂金尾款: '服務日 7 天前支付 30% 訂金，尾款於完工後 3 天內付清。', 全額預付: '服務日 7 天前支付全額。', 完工後付款: '完工後 3 天內付清，不需先付訂金。',
  每月月結: '每月結算一次，次月 5 號前付清上個月的服務費。', 當日結算: '服務完成當天結算付款。', 月訂閱: '每月 5 號前支付當月費用；第一個月於首次服務日 7 天前支付。' };      // 跟 contract.PAY_TEXT 同一套說法
const Q_STYPES = [['一般到府服務', '一般到府服務'], ['店面／辦公室服務', '店面服務'], ['大型案場服務', '大型案場服務'], ['店家服務', '店家服務'], ['年度保養／細清', '年度保養'], ['進階專案', '進階專案'], ['我不確定', '我不確定']];   // 客服 服務項目下拉（跟 booking.STYPES 一樣）
function itemRow(it, i) {
  return `<div class="qitem" data-i="${i}"><div class="g2"><div><label>項目</label><input class="qi-title" value="${esc(it.title)}"></div><div${can('money') ? '' : ' hidden'}><label>費用（元）</label><input class="qi-amt" type="number" inputmode="numeric" value="${it.amount ?? ''}"></div>${can('money') ? '' : '<div><label>費用</label><p class="small">由店長填寫</p></div>'}</div>
    <label>費用單位說明（選填，例：／次（每次 4 小時））</label><input class="qi-unit" value="${esc(it.unit || '')}">
    <label class="chk" style="margin-top:6px"><input type="checkbox" class="qi-per"${it.per_unit ? ' checked' : ''}> 單價項目（例：每間 800，依實際數量計，不計入小計）</label>
    <label class="chk"><input type="checkbox" class="qi-sep"${it.separate ? ' checked' : ''}> 依當天狀況另計（放在最後一頁、不列小計，例：整理師時薪、加購）</label>
    <label>副標（規模、位置，例：A廠 2F 廁所）</label><input class="qi-sub" value="${esc(it.sub || '')}">
    <label>規格與範圍說明（一行一項）</label><textarea class="qi-scope" rows="${Math.max(2, (it.scope || []).length)}">${esc((it.scope || []).join('\n'))}</textarea>
    <div class="btns" style="margin-top:6px">${btn('套用標準範圍', 'qiStd', { i }, 'gray')}${btn('刪除這項', 'qiDel', { i }, 'gray')}</div></div>`;
}
function readItems() {
  return $$('.qitem').map(el => ({ title: el.querySelector('.qi-title').value.trim(), sub: el.querySelector('.qi-sub').value.trim(), unit: el.querySelector('.qi-unit').value.trim(), per_unit: el.querySelector('.qi-per').checked, separate: el.querySelector('.qi-sep').checked,
    amount: Number(el.querySelector('.qi-amt').value || 0), scope: el.querySelector('.qi-scope').value.split('\n').map(s => s.trim()).filter(Boolean) }));
}
function quoteSheet(restored) {
  const q = QD, f = q.form, e = q.estimate, editable = q.status === 'draft', dr = !restored && editable && qDraftGet(q.id);
  const ans = [['空間', f.space && { home: '住家', bnb: '店家', shop: '店面／辦公室', community: '社區・大樓公共區域', factory: '廠房・其他' }[f.space]],
    ['狀況', f.condition && { regular: '平常有住，定期維持', deep: '很久沒徹底整理', reno: '剛施工／整修完', move: '空屋（搬入前／搬出後）' }[f.condition]],
    ['系統判斷', f.service_type], ['頻率', f.freq], ['規模', f.size], ['房／衛／樓', [f.rooms, f.baths, f.floors].map(x => x || '—').join(' / ')],
    ['房屋', f.kind], ['其他', [f.pets && '有寵物', f.ladder && '需梯子', f.lift].filter(Boolean).join('、')], ['加購', (f.addons || []).join('、')],
    ['地址', `${f.area || ''} ${f.address || ''}`], ['希望', `${q.date || ''} ${f.slot || ''}`], ['聯絡', `${f.name}　${f.phone}${f.line_name ? '　LINE：' + f.line_name : ''}`],
    ['公司／統編', [f.company, f.tax_id].filter(Boolean).join('　')], ['客戶交代', f.notes]].filter(r => r[1]);
  let h = `<div class="banner ${q.stage === 'review' ? 'warn' : ''}">${esc(q.id)}・${esc(q.stage_name)}${q.reviewed_by ? '・最後修改 ' + esc(q.reviewed_by) : ''}${q.line_linked ? '・客戶收得到 LINE（確認後自動傳報價卡）' : q.line_mode && q.line_mode !== 'on' ? `・${esc(q.brand_name || '')}官方 LINE 還在${q.line_mode === 'test' ? '測試' : '關閉'}模式，客戶收不到（確認後要把連結傳給他）` : '・客戶還沒綁 LINE（確認後要把連結傳給他）'}</div>`;
  if (dr) h += `<div class="banner warn">這張有上次沒儲存的修改（${new Date(dr.at).toLocaleString('zh-TW', { hour12: false }).slice(5, 16)}）。${btn('還原', 'qRestore', {}, 'sec')}</div>`;
  if (!q.line_linked && q.stage === 'sent' && q.status === 'draft') h += `<div class="banner ${q.link_sent ? '' : 'red'}">${q.link_sent ? '報價單連結已傳給客戶：' + esc(q.link_sent) : '客戶沒綁 LINE，系統傳不過去：請把報價單連結傳給他，傳完按「已傳給客戶」。'}<div class="btns" style="margin:8px 0 0">${btn('複製報價單連結', 'qCopyLink', {}, 'sec')}${q.customer_id ? btn('複製綁定訊息', 'bindMsg', { id: q.customer_id }, 'sec') : ''}${q.link_sent ? '' : btn('已傳給客戶', 'qLinkSent', {})}</div></div>`;
  if (!q.customer_id && editable) h += pickFld('q_cust', '對到既有客戶（還沒對到；是老客戶就選他，成立時不會另建一位）', '', custOpts().filter(([id]) => BRAND_ID(IDX.customers[id] || {}) === (q.account || 'demoshop')), { empty: '新客戶（成立時建一位新的）' });      // Lin
  if (q.scale_warn) h += `<div class="banner warn">⚠️ ${esc(q.scale_warn)}</div>`;          // 
  if (!q.customer_id && (q.maybe_customers || []).length) h += `<div class="banner red">⚠️ 可能是既有客戶（還沒對到；直接成立會另建一位、收不到 LINE）：${q.maybe_customers.map(x => `<br><b>${esc(x.name)}</b>（${esc(x.why)}）${editable ? ' ' + btn('就是他', 'qLinkCust', { cid: x.id, n: x.name }, 'sec') : ''}`).join('')}${editable ? `<br><label class="small"><input type="checkbox" id="q-newok"${q.form.new_ok ? ' checked' : ''}> 確定是新客戶（不是上面這幾位）</label>` : ''}</div>`;      // Lin
  if ((q.form || {}).feedback) h += `<div class="banner warn">💬 客戶說：<b>${esc(q.form.feedback.reason)}</b>（${esc((q.form.feedback.at || '').slice(5, 16).replace('T', ' '))}）：調整範圍／金額後再傳一次</div>`;      // 稽核 F6
  if (q.customer_id) h += `<div class="banner ${q.customer_match ? 'warn' : ''}">已對到客戶：<b>${esc(q.customer_name || '')}</b>${q.customer_match ? '（依電話相同自動對上，請確認是同一位；簽約後工單會建在這位客戶底下）' : ''}${q.line_linked ? '・已綁 LINE，確認後報價卡會直接傳給他' : ''}</div>`;
  h += `<h3>客戶填寫</h3><dl class="kv">${ans.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`;
  h += `<h3>現況照片 <span class="g">${q.photos.length}</span></h3>` + (q.photos.length ? `<div class="thumbs">${q.photos.map((id, k) => `<button type="button" class="thumb" data-act="galIds" data-k="q" data-cap="現況照片" data-i="${k}" data-ids="${esc(q.photos.join(','))}" aria-label="現況照片 ${k + 1}"><img src="/api/quote-photo?id=${id}" alt="" loading="lazy"></button>`).join('')}</div><p class="small" style="margin:4px 0 0">點照片看大圖，可以左右滑。</p>`
    : `<div class="empty">${q.photo_via === 'line' ? '客戶選擇用 LINE 傳照片，還沒收到。' : '沒有照片'}</div>`);
  if (e.mode === 'auto') h += `<h3>系統初估（只供參考）</h3><div class="estbox" style="margin-top:0">${e.total ? e.items.map(i => `<div class="estrow"><span>${esc(i.label)}</span><span>${ntd(i.amount)}</span></div>`).join('')
    + `<div class="estrow total"><span>${e.visits > 1 ? `每月 ${e.visits} 次合計` : '合計'}</span><span>${ntd(e.total)}</span></div>` : '<p class="small" style="margin:0">這類系統沒辦法初估金額，請看照片人工報價。</p>'}
    <p class="small" style="margin:0">系統初估人力 ${e.people} 人 × 約 ${e.hours} 小時；<b>實際安排以下面「服務安排」為準（目前 ${q.people || 1} 人 × ${q.hours || '—'} 小時）</b></p></div>`;
  h += `<h3>過往相近報價（一致化參考）</h3>` + (q.similar.length ? tbl(['報價單', '規模', '小計', '狀態'], q.similar.map(s => `<tr>${td('報價單', esc(s.id))}${td('規模', esc(s.size || '—'))}${td('小計', ntd(s.subtotal))}${td('狀態', esc(s.status))}</tr>`)) : '<p class="small">還沒有同類型、規模相近的報價。</p>');
  h += `<h3>正式報價單</h3>` + fld('q_stype', '服務項目（決定標準範圍、估價方式）', f.service_type || '', 'select', { options: [['', '— 請選服務項目 —'], ...(q.stypes || Q_STYPES)] })
    + (['', '我不確定', '服務'].includes(f.service_type || '') ? '<p class="small" style="color:#8A5A00;margin:4px 0 0">服務項目還沒確定：請店長（或看過現場的人）從下拉選一個，報價單才不會錯。</p>' : '')
    + `<div id="qitems">${q.items.map(itemRow).join('')}</div>`
    + (editable ? `<div class="btns">${btn('＋ 加一個項目', 'qiAdd', {}, 'sec')}</div>` : '')
    + `<label>備註（會印在報價單上，例：1F 廁所殘污較重，故與其他樓層分開報價）</label><textarea id="q-note" rows="2">${esc(q.note || '')}</textarea>
    <label class="chk" style="margin-top:12px"><input type="checkbox" id="q-taxed"${q.taxed ? ' checked' : ''}> 加 5% 稅額（開發票）</label>
    <label class="chk"><input type="checkbox" id="q-total-only"${f.total_only ? ' checked' : ''}> 只給業主看總價（品項照樣分類列出，但不顯示每一項的金額）</label>
    ${q.clean_level ? `<label for="q-level" style="margin-top:12px">這份報價是服務 Level 幾（業主簽收前會看到三級說明並選；選得比這個高就不能簽，會請你重新報價）</label><select id="q-level">${[[1, 'Level 1｜基礎服務（日常維護型）'], [2, 'Level 2｜深度服務（年度保養型）'], [3, 'Level 3｜精緻加強服務（細節精查型）']].map(([n, t]) => `<option value="${n}"${q.clean_level === n ? ' selected' : ''}>${t}</option>`).join('')}</select>${f.requote_want ? `<p class="small" style="color:var(--orange)">業主要求重新報價：${esc(f.requote_want)}</p>` : ''}${f.dirt ? `<p class="small">業主選的：${esc(({ light: '輕度', mid: '中度', heavy: '重度' }[f.dirt] || ''))}髒污、Level ${esc(f.level_cust || '')}、${esc(f.want_freq || '')}${f.want_hours ? '、每次約 ' + esc(f.want_hours) + ' 小時' : ''}</p>` : ''}` : ''}
    ${[['trash', '垃圾代丟等級', ['少量代丟（1～5 袋）', '中量代丟（6～15 袋）', '大量代丟（15 袋以上、要車）']], ['tidy', '整理等級', ['輕度整理｜基礎物品歸位', '中度整理｜分類與收納', '重度整理｜深度整理與空間規劃']]].map(([k, t, ls]) => `<label for="q-${k}" style="margin-top:12px">${t}（業主簽收前會看到三級說明並選；不適用選「不適用」）</label><select id="q-${k}"><option value="0">不適用</option>${ls.map((x, i) => `<option value="${i + 1}"${((q.scale_levels || {})[k] || 0) === i + 1 ? ' selected' : ''}>${'①②③'[i]} ${x}</option>`).join('')}</select>`).join('')}
    <label for="q-pay" style="margin-top:12px">付款方式（已經跟客戶談好的就選；客戶看到的合約只會列這一種）</label><select id="q-pay">${(q.pay_options || []).map(([k, t]) => `<option value="${esc(k)}"${(q.pay_method || '') === k ? ' selected' : ''}>${esc(t)}</option>`).join('')}${q.pay_method && !(q.pay_options || []).some(o => o[0] === q.pay_method) ? `<option value="${esc(q.pay_method)}" selected>${esc(q.pay_method)}（客戶選的）</option>` : ''}</select>
    <div class="small" id="q-pay-terms" style="margin:4px 0 8px;color:var(--sub)">客戶看到的付款條款：${(q.pay_terms || []).map(esc).join(' ')}</div>
    <label class="chk"><input type="checkbox" id="q-show-crew"${f.hide_crew ? '' : ' checked'}> 報價單上寫明「本報價依 ${q.people || 1} 位服務人員 × 約 ${q.hours || '—'} 小時估算」（建議勾，客戶比較不會只看價格）</label>
    <h3>服務安排（內部排班用；客戶的報價單不顯示日期時間）</h3>
    <div class="g3"><div><label>日期</label><input id="q-date" type="date" value="${esc(q.date || '')}"></div><div><label>開始</label><input id="q-start" type="time" value="${esc(q.start || '09:00')}"></div>
      <div><label>人數 × 小時</label><div style="display:flex;gap:6px"><input id="q-people" type="number" min="1" value="${q.people || 1}"><input id="q-hours" type="number" step="0.5" min="0.5" value="${q.hours || 3}"></div></div></div>
    ${can('money') ? `<label>報價期限（管理層決定；空白＝傳出日＋${q.valid_days || 5} 天）</label><div style="display:flex;gap:8px;align-items:center"><input id="q-valid" type="date" value="${esc(q.valid_until || '')}" style="max-width:200px">
      <span class="small">目前：${esc(ymdS(q.valid_effective))}${q.valid_until ? '（管理層指定）' : q.sent_at ? `（傳出日＋${q.valid_days || 5} 天）` : `（還沒傳；會是傳出日＋${q.valid_days || 5} 天）`}</span></div>
      <p class="small" style="margin:4px 0 0">改了會同時改 PDF、線上報價、LINE 報價卡；過了期限客戶就不能線上簽。不會收回審核。</p>` : ''}
    <label>給夥伴看的服務範圍（不含價格、客戶資料；一行一項）</label><textarea id="q-scope" rows="4">${esc(q.scope || '')}</textarea>
    <div class="estbox" id="q-total"></div>`;
  const extra = btn('預覽報價單', 'qPreview', {}, 'gray') + btn('下載 A4 PDF', 'qPdf', {}, 'gray') + (['sent', 'signed'].includes(q.stage) || q.status === 'signed' ? btn('複製 PDF 連結（給客戶 Email）', 'qPdfLink', {}, 'gray') : '') + (editable && can('money') ? btn('客戶已同意（電話／當面），直接成立', 'qConfirm', {}, 'sec') : '') + (editable ? btn('先儲存', 'qSave', {}, 'sec') + btn('交給店長確認', 'qAsk', {}, 'sec') + btn('請客戶補照片／資料', 'qMore', {}, 'sec') + (q.mode === 'site_visit' ? btn('改成線上報價（不跑現場）', 'qStage', { s: 'review' }, 'gray') : btn('改成現場估價', 'qStage', { s: 'site_visit' }, 'gray')) + btn('取消這張', 'qStage', { s: 'cancelled' }, 'gray') : '');
  sheet('報價審核', h, editable && can('money') ? async () => { await qSend(); return 'keep'; } : null, q.stage === 'sent' ? '更新並重新傳給客戶' : '確認並傳給客戶', extra);
  if (!editable) $$('#sheetBody input,#sheetBody textarea,#sheetBody select').forEach(x => x.disabled = true);
  const qp = $('#q-pay');            // 換付款方式 → 馬上看到客戶會看到的付款條款
  if (qp) qp.addEventListener('change', () => { const t = Q_PAY_TEXT[qp.value]; $('#q-pay-terms').textContent = t ? '客戶看到的付款條款：' + t : '依服務類型（年度保養／進階專案＝訂金 30%＋尾款；定期＝月訂閱；其他＝全額預付），存檔後這裡會顯示'; });
  const st = $('#f-q_stype');
  if (st && editable) st.addEventListener('change', () => {            // 換服務項目：第一個品項還是舊類型／「服務」就跟著換，標準範圍也跟著
    const it = readItems(), old = QD.form.service_type, v = st.value; if (!v) return;
    if (it[0] && (!it[0].title || it[0].title === '服務' || it[0].title === old)) it[0].title = v === '我不確定' ? '服務' : v;
    QD.form.service_type = v; $('#qitems').innerHTML = it.map(itemRow).join(''); $('#sheetBody').dispatchEvent(new Event('input'));
  });
  const upd = () => { const it = readItems(), sub = it.reduce((n, x) => n + (x.per_unit || x.separate ? 0 : x.amount), 0), tax = $('#q-taxed').checked ? Math.round(sub * 0.05) : 0;
    $('#q-total').innerHTML = `<div class="estrow"><span>小計</span><span>${ntd(sub)}</span></div>${tax ? `<div class="estrow"><span>稅額 5%</span><span>${ntd(tax)}</span></div>` : ''}<div class="estrow total"><span>總計</span><span>${ntd(sub + tax)}</span></div>`; };
  QUPD = upd; if (can('money')) upd(); else $('#q-total').hidden = true;
}
function qPayload() {
  return { id: QD.id, items: readItems(), note: $('#q-note').value, taxed: $('#q-taxed').checked, date: $('#q-date').value, start: $('#q-start').value,
    people: +$('#q-people').value, hours: +$('#q-hours').value, scope: $('#q-scope').value, ...($('#q-valid') ? { valid_until: $('#q-valid').value } : {}),
    ...($('#f-q_stype') && $('#f-q_stype').value ? { service_type: $('#f-q_stype').value } : {}),
    ...($('#q-total-only') ? { total_only: $('#q-total-only').checked, show_crew: $('#q-show-crew').checked } : {}),
    ...($('#q-pay') ? { pay_method: $('#q-pay').value } : {}), ...($('#q-level') ? { level: +$('#q-level').value } : {}), ...Object.fromEntries(['trash', 'tidy'].filter(k => $('#q-' + k) && +$('#q-' + k).value !== ((QD.scale_levels || {})[k] || 0)).map(k => [k + '_level', +$('#q-' + k).value])), ...($('#f-q_cust') && $('#f-q_cust').value ? { customer_id: $('#f-q_cust').value } : {}), ...($('#q-newok') ? { new_ok: $('#q-newok').checked } : {}) };
}
const ymdS = s => s ? s.replace(/-/g, '/') : '—';
ACT.qiAdd = () => { const it = readItems(); it.push({ title: '', sub: '', amount: 0, scope: [] }); $('#qitems').innerHTML = it.map(itemRow).join(''); };
ACT.qiDel = d => { const it = readItems(); it.splice(+d.i, 1); $('#qitems').innerHTML = it.map(itemRow).join(''); $('#sheetBody').dispatchEvent(new Event('input')); };
ACT.qiStd = d => { const it = readItems(), std = QD.scopes[it[+d.i].title] || QD.scopes[QD.form.service_type] || []; it[+d.i].scope = std.slice(); $('#qitems').innerHTML = it.map(itemRow).join(''); };
ACT.qPreview = async () => { if (QD.status === 'draft') await api('/api/quote/save', qPayload()); open(`/q/${QD.token}?preview=1&sig=${QD.preview_sig || ''}`, '_blank', 'noopener'); };
function qnAuto() {        // 選了既有客戶 → 名稱、電話、地址、品牌直接帶進欄位（只填空白或剛剛自動帶的，不蓋掉自己打的）
  const sel = $('#f-customer_id'); if (!sel) return;
  const fill = () => { const k = IDX.customers[sel.value], s = k ? (IDX.sitesOf[k.id] || []).find(x => x.address) || {} : {};
    for (const [n, v] of [['name', k && k.name], ['phone', k && k.phone], ['address', s.address]]) { const el = $('#f-' + n); if (!el) continue;
      if (!el.value.trim() || el.value === el.dataset.auto) { el.value = v || ''; el.dataset.auto = v || ''; } }
    const b = $('#f-brand'); if (k && b) b.value = BRAND_ID(k); };
  sel.addEventListener('change', fill); fill();
}
ACT.quoteNew = (d = {}) => { const k = d.cid ? IDX.customers[d.cid] : null; setTimeout(qnAuto, 0); return sheet('新增報價單', `<p class="small">先選既有客戶（或填新客戶資料），下一步再填品項、範圍、金額。存成「待主管確認」，確認後才會傳給客戶。</p>`
  + pickFld('customer_id', '既有客戶（選了報價就對到這位；下面空白的會自動帶入）', k ? k.id : '', custOpts(k ? k.id : ''), { empty: '新客戶（下面自己填）' })
  + fld('brand', '品牌', k ? BRAND_ID(k) : (SEG.qb || 'demoshop'), 'select', { options: (S.brands || []).map(b => [b.id, b.name]) })
  + g2(fld('company', '公司（開發票才填）', ''), fld('name', '聯絡人／客戶名稱', '')) + g2(fld('phone', '連絡電話', ''), fld('tax_id', '統一編號', ''))
  + fld('address', '地址', '') + fld('service_type', '服務項目', '', 'select', { options: [['', '— 請選服務項目 —'], ...Q_STYPES] })
  + fld('pay_method', '付款方式（已經跟客戶談好的就先選；不確定就用標準）', '', 'select', { options: [['', '依服務類型（標準）'], ['訂金尾款', '訂金 30%＋尾款'], ['全額預付', '全額預付'], ['完工後付款', '完工後付款（不收訂金）'], ['每月月結', '每月月結'], ['當日結算', '當日結算'], ['月訂閱', '月訂閱']] }),
  async f => { if (!f.service_type) throw new Error('請選服務項目');
    if (f.customer_id && IDX.customers[f.customer_id] && BRAND_ID(IDX.customers[f.customer_id]) !== f.brand) throw new Error('這位客戶的品牌跟上面選的不一樣');
    const r = await api('/api/quote/create', { brand: f.brand, service_type: f.service_type, pay_method: f.pay_method, customer_id: f.customer_id || '', customer: { company: f.company, name: f.name || f.company, phone: f.phone, tax_id: f.tax_id, address: f.address },
    items: [{ title: f.service_type === '我不確定' ? '服務' : f.service_type, sub: '', amount: 0, scope: [] }] }); QO = null; QD = r; quoteSheet(); return 'keep'; }, '下一步：填品項'); };
ACT.qConfirm = () => { const pl = qPayload(); return sheet('客戶已同意，直接成立', `<p>已經用電話或當面跟客戶談好價格和付款方式了，就不用再請客戶線上簽收：系統直接建工單、收款單，開始派工。</p>
    <p class="small">會照這張報價單的品項、日期、付款方式成立（建議先按「先儲存」）。客戶有綁 LINE 的會收到付款通知（依付款方式）。</p>`
  + fld('how', '客戶怎麼同意的', '電話', 'select', { options: [['電話', '電話'], ['當面', '當面'], ['LINE 訊息', 'LINE 訊息'], ['Email', 'Email']] })
  + (!QD.customer_id && (QD.maybe_customers || []).length ? `<div class="banner red">⚠️ 可能是既有客戶：${QD.maybe_customers.map(x => esc(x.name)).join('、')}。是的話先關掉這個視窗，在報價上按「就是他」。</div>` + fld('new_ok', '確定是新客戶（不是上面這幾位）', false, 'check') : ''),
  async f => { await api('/api/quote/save', pl); const r = await api('/api/quote/confirm', { id: pl.id, how: f.how, new_ok: !!f.new_ok }); QO = null; await refresh(); closeSheet();
    if (r.order) setTimeout(() => IDX.orders[r.order] && orderSheet(r.order), 600); return r.already ? '這張已經成立了' : '✅ 已成立：工單和收款單都建好了，系統開始派工'; }, '確認成立'); };      // 已談好的不用再線上簽；pl＝按下去當下的報價內容
ACT.qLinkCust = async d => { QD = await api('/api/quote/save', { ...qPayload(), customer_id: d.cid }); QO = null; quoteSheet(); return `已對到 ${d.n}`; };      // Lin
ACT.qPdfLink = () => copy(QD.public_url + '.pdf', '報價單 PDF 連結');          // 客戶要 PDF／Email → 貼這個連結就好
ACT.qPdf = async () => { if (QD.status === 'draft') await api('/api/quote/save', qPayload()); open(`/api/quote-pdf/${QD.id}`, '_blank', 'noopener'); };
let QPAY = null;
ACT.qAsk = () => { QPAY = qPayload(); return qAskSheet(); };
const qAskSheet = () => sheet('交給店長確認', `<p class="small">會先儲存目前內容，再通知店長（派單管理群＋手機通知），她按了會直接打開這張報價。金額不確定可以先空著。</p>`
  + fld('msg', '想交代店長的話（選填）', '', 'textarea', { ph: '例：現場看過，1F 廁所比較髒；金額請你判斷' }),
  async f => { await api('/api/quote/ask', { ...QPAY, msg: f.msg }); return '已通知店長'; }, '送出');
ACT.qSave = async () => { const was = QD.stage; QD = await api('/api/quote/save', qPayload()); qDraftDrop(QD.id); QO = null; quoteSheet(); SHEET_DIRTY = false;
  return was === 'sent' && QD.stage !== 'sent' ? '已儲存。改到金額或日期，這張已收回成「待確認」：按「確認並傳給客戶」，客戶才看得到' : was === 'sent' ? '已儲存（客戶打開連結就會看到更新）' : '已儲存（客戶還看不到）'; };
async function qSend() {
  if (!confirm('確認這張報價沒問題，要傳給客戶了嗎？')) return;
  const r = await api('/api/quote/send', qPayload()); QO = null; qDraftDrop(QD.id); SHEET_DIRTY = false;
  if (r.line_sent) { closeSheet(); route(true); toast(`已傳給客戶的 LINE（${ntd(r.total)}）`); }
  else { await copy(r.url, '報價單連結'); QD = await api('/api/quote?id=' + encodeURIComponent(QD.id)); quoteSheet(); route(true); toast('客戶沒綁 LINE：報價單連結已複製，請傳給他，傳完按「已傳給客戶」'); }
}
ACT.qMore = () => sheet('請客戶補照片／資料', `<p class="small">會用 LINE 傳給客戶（客戶沒綁 LINE 就複製連結給他）。報價會退回「等照片」，補齊後再通知你。</p>` + fld('reason', '要補什麼', '', 'textarea', { ph: '例：浴室和陽台沒有拍到，請各補一張；廚房請拍抽油煙機近照' }),
  async f => { const r = await api('/api/quote/more', { id: QD.id, reason: f.reason }); QO = null; route(true); if (!r.line_sent) await copy(r.url, '補資料連結'); return r.line_sent ? '已用 LINE 通知客戶' : '客戶沒綁 LINE，連結已複製'; });
ACT.qStage = async d => {
  if (!confirm(d.s === 'cancelled' ? '取消這張報價？' : d.s === 'review' ? '改成線上報價（不跑現場、不收估價費）？' : '改成現場估價？（會請你們跟客戶約時間）')) return;
  const r = await api('/api/quote/stage', { id: QD.id, stage: d.s }); QO = null; closeSheet(); route(true);
  if (d.s === 'cancelled') return r.notified ? '已取消，已用 LINE 通知客戶「已經幫您取消」' : r.knew ? '已取消；客戶沒綁 LINE，請打電話告知（已提醒派單管理群）' : '已取消';
  return '已更新';
};

document.addEventListener('input', e => {            // 報價搜尋框：打字就篩（不重抓資料）
  if (e.target.id !== 'qq' || e.isComposing) return;      // 注音選字中不重畫（中文姓名才搜得到）
  SEG.qq = e.target.value; const pos = e.target.selectionStart; route(true);
  const el = document.getElementById('qq'); if (el) { el.focus(); try { el.setSelectionRange(pos, pos); } catch (x) { } }
});
document.addEventListener('compositionend', e => { if (e.target.id === 'qq') e.target.dispatchEvent(new Event('input', { bubbles: true })); });
