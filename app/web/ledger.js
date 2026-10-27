'use strict';
/* 記帳：示範公司（示範服務店／生活線／企業線）每月收支、待確認憑證、記帳士包、讀取金鑰、給記帳士的問題、承攬檢查表 */
let LG = null, LGMONTH = null, LGOPEN = '', LGPOLL = null;
async function loadLedger() { LG = await api('/api/ledger?month=' + LGMONTH); }
async function lgReload() { await loadLedger(); route(true); }
const lgCat = k => Object.fromEntries([...LG.cats.expense, ...LG.cats.income].map(([a, b]) => [a, b]))[k] || ({ service: '服務收入（完工）', billed_other: '帳單其他費用', shop: '商城收入', worker: '夥伴承攬報酬', reimburse: '夥伴代墊用品', vendor: '配合廠商外包' })[k] || k;
const lgBrand = b => (LG.brands.find(x => x[0] === (b || '')) || ['', '共同'])[1];
const LGSTATE = { reading: '讀取中…', draft: '待確認', ok: '已記帳', void: '已作廢' };

PAGES.ledger = () => {
  const p = (location.hash.slice(1) || '').split('/')[1] || '';
  LGMONTH = LGMONTH || today().slice(0, 7);
  if (!LG || LG.month !== LGMONTH) { loadLedger().then(() => route(true)).catch(e => toast(e.message, true)); return phead('記帳', '載入中…', ''); }
  if (p && p !== LGOPEN) { LGOPEN = p; setTimeout(() => lgEdit(p), 60); }          // LINE 確認卡「改金額、日期（中控台）」直接打開這一筆
  const v = LG, [y, m] = LGMONTH.split('-').map(Number), A = v.all;
  if (v.pending.some(e => e.status === 'reading') && !LGPOLL) LGPOLL = setTimeout(() => { LGPOLL = null; if (VIEW === 'ledger') lgReload().catch(() => { }); }, 6000);
  let h = phead('記帳', `${esc(v.company)}　${y} 年 ${m} 月`, '系統裡的錢自動算（完工服務、商城、夥伴承攬報酬、代墊用品、配合廠商）；其他收支在 LINE 一對一打「記帳」傳收據照片，或在這裡記。每月 5 日自動做好記帳士包。');
  h += `<div class="calbar"><span></span><div class="paddles"><button class="paddle" data-act="lgMove" data-n="-1" aria-label="上個月">‹</button><button class="today" data-act="lgMove" data-n="0">本月</button><button class="paddle" data-act="lgMove" data-n="1" aria-label="下個月">›</button></div></div>`;
  h += `<div class="grid"><div class="tile stat good"><div class="k">收入</div><div class="v">${money(A.income)}</div><div class="s">${v.estimates.n ? `含預估 ${money(v.estimates.amount)}` : '都已核定'}</div></div>
    <div class="tile stat"><div class="k">支出</div><div class="v">${money(A.expense)}</div><div class="s">夥伴＋憑證＋外包</div></div>
    <div class="tile stat ${A.net < 0 ? 'warn' : ''}"><div class="k">淨額</div><div class="v">${money(A.net)}</div><div class="s">收入－支出</div></div>
    <div class="tile stat ${v.pending.length ? 'warn' : ''}"><div class="k">待確認</div><div class="v">${v.pending.length}</div><div class="s">LINE／上傳的憑證</div></div></div>`;
  h += `<div class="btns">${btn('📷 上傳收據照片／PDF（自動讀）', 'lgUpload')}${btn('＋ 記一筆支出', 'lgNew', { k: 'expense' }, 'sec')}${btn('＋ 記一筆收入', 'lgNew', { k: 'income' }, 'sec')}${btn('⬇ 下載記帳士包', 'lgPkg', {}, 'sec')}</div>`;
  if (v.output_tax_all) h += `<div class="banner">銷項稅額（代收，不算收入）：${money(v.output_tax_all)}　含稅報價的服務收入、帳單上的營業稅都拆出來另列（店長 照會計原則）。</div>`;
  if (v.estimates.n) h += `<div class="banner warn">有 ${v.estimates.n} 場完工還沒核定金額，收入先用系統預估（${money(v.estimates.amount)}）。到「結帳」核定後這裡會自動更新。</div>`;
  if (!v.key.set) h += `<div class="banner">讀取憑證的金鑰還沒設定：照片照樣會存，但金額、店家要自己填。${S.role === 'admin' ? '<a data-act="lgKey">現在設定 ›</a>' : '請管理員到這一頁最下面「設定」貼上。'}</div>`;

  h += `<h2>待確認 <span class="g">${v.pending.length}</span></h2><div class="card">` + (v.pending.map(e => `<div class="item"><div class="t">
      <div class="n">${e.photo ? `<a href="/api/ledger/photo/${e.id}" target="_blank" rel="noopener">📷</a> ` : ''}${esc(e.counterparty || (e.status === 'reading' ? '讀取中…' : '（還沒有店家）'))}　<b>${e.amount ? money(e.amount) : '—'}</b></div>
      <div class="m">${esc(e.date || '')}・${esc(lgCat(e.category))}・${esc(lgBrand(e.brand))}・${LGSTATE[e.status] || e.status}${e.memo ? '・' + esc(e.memo.slice(0, 60)) : ''}</div></div>
      <div class="a">${e.status === 'draft' && e.amount ? btn('✅ 確認', 'lgOk', { id: e.id }) : ''}${e.status !== 'reading' ? btn('修改', 'lgEditBtn', { id: e.id }, 'sec') : ''}${e.photo && e.status === 'draft' && v.key.set && ['failed', 'nokey', 'odd'].includes(e.ocr_state) ? btn('重新讀取', 'lgReread', { id: e.id }, 'sec') : ''}${btn('作廢', 'lgVoid', { id: e.id }, 'gray')}</div></div>`).join('') || '<div class="empty">沒有待確認的。LINE 一對一打「記帳」再傳收據照片，讀完會出現在這裡（LINE 也會回確認卡）。</div>') + '</div>';

  const bs = v.brands, row = r => `<tr>${td('類別', esc(r.name) + (r.auto ? ' <span class="small">自動</span>' : ''))}${bs.map(([b, n]) => td(n, r.by[b] ? money(r.by[b]) : '<span class="small">—</span>', 'r')).join('')}${td('合計', `<b>${money(r.total)}</b>`, 'r')}</tr>`;
  const sum = (k, t) => `<tr>${td('類別', `<b>${t}</b>`)}${bs.map(([b, n]) => td(n, `<b>${money(v.totals[b][k])}</b>`, 'r')).join('')}${td('合計', `<b>${money(A[k])}</b>`, 'r')}</tr>`;
  h += `<h2>各品牌收支</h2><div class="card" style="overflow-x:auto">${tbl(['類別', ...bs.map(([, n]) => '>' + n), '>合計'],
    [...v.rows.filter(r => r.kind === 'income').map(row), sum('income', '收入合計'), ...v.rows.filter(r => r.kind === 'expense').map(row), sum('expense', '支出合計'), sum('net', '淨額')])}
    <p class="small">「共同」＝三個品牌一起用的支出（例如 LINE 官方帳號、油資）。收入以服務完成日算（管理帳）；開發票、收款的時點請記帳士確認。</p></div>`;

  h += `<h2>應收・應付</h2><div class="card"><div class="item"><div class="t"><div class="n">完工還沒開帳</div><div class="m">到「結帳」開帳</div></div><div class="a"><b>${money(v.ar.to_bill)}</b></div></div>
    <div class="item"><div class="t"><div class="n">已開帳還沒收</div><div class="m">${v.ar.overdue ? '其中逾期 ' + money(v.ar.overdue) : '沒有逾期'}（含之前月份）</div></div><div class="a"><b>${money(v.ar.balance)}</b></div></div>
    <div class="item"><div class="t"><div class="n">夥伴還沒結算</div><div class="m">這個月完工、還沒建結算單</div></div><div class="a"><b>${money(v.ap.workers)}</b></div></div>
    ${v.ap.vendors ? `<div class="item"><div class="t"><div class="n">配合廠商未付</div></div><div class="a"><b>${money(v.ap.vendors)}</b></div></div>` : ''}
    <div class="btns">${btn('到結帳 ›', 'lgGo', { v: 'billing' }, 'sec')}</div></div>`;

  h += `<h2>這個月記的帳 <span class="g">${v.entries.filter(e => e.status === 'ok').length}</span></h2><div class="card" style="overflow-x:auto">${tbl(['日期', '品牌', '類別', '對象', '>金額', '憑證', ''],
    v.entries.map(e => `<tr style="${e.status === 'void' ? 'opacity:.45' : ''}">${td('日期', md(e.date))}${td('品牌', esc(lgBrand(e.brand)))}${td('類別', (e.kind === 'income' ? '收入・' : '') + esc(lgCat(e.category)) + (e.status === 'void' ? ' <span class="small">已作廢</span>' : ''))}${td('對象', esc(e.counterparty || e.memo.slice(0, 20) || '—'))}${td('金額', money(e.amount), 'r')}${td('憑證', e.photo ? `<a href="/api/ledger/photo/${e.id}" target="_blank" rel="noopener">📷 看</a>` : (e.doc_no ? esc(e.doc_no) : '—'))}${td('', btn('修改', 'lgEditBtn', { id: e.id }, 'sec') + (e.status === 'ok' ? btn('作廢', 'lgVoid', { id: e.id }, 'gray') : ''))}</tr>`))}</div>`;

  h += `<h2>記帳士包</h2><div class="card"><div class="item"><div class="t"><div class="n">示範公司統編</div><div class="m">${v.tax_id ? esc(v.tax_id) + '・收據上買方統編不一樣會提醒' : '還沒填：填了以後，別家公司（合作店家）的收據會被標出來'}</div></div><div class="a">${btn(v.tax_id ? '修改' : '填統編', 'lgTaxId', {}, 'sec')}</div></div><p class="small">${v.package.exists ? `上次產生：${esc(v.package.at.replace('T', ' '))}。` : '這個月還沒產生。'}每月 5 日 10:00 自動做上個月的，管理群會收到一張卡。內容：收支總表、收入明細、支出明細（附憑證照片）、夥伴承攬明細（Excel 可直接開）。待確認的憑證不會算進去。</p>
    <div class="btns">${btn(`⬇ 下載 ${m} 月記帳士包`, 'lgPkg')}</div></div>`;

  h += `<h2>給記帳士的問題・承攬檢查表</h2><div class="card">${LG_ASK}${LG_RISK}</div>`;
  if (S.role === 'admin') h += `<h2>設定（管理員）</h2><div class="card"><div class="item"><div class="t"><div class="n">讀取憑證的 Claude 金鑰</div>
      <div class="m">${v.key.set ? `已設定（結尾 …${esc(v.key.tail || '????')}）${v.key.checked ? '・' + esc(v.key.checked.replace('T', ' ')) + ' 測試可用' : ''}` : '還沒設定'}・模型 ${esc(v.key.model)}（便宜、夠讀收據）</div></div>
      <div class="a">${btn(v.key.set ? '換金鑰' : '貼上金鑰', 'lgKey')}${v.key.set ? btn('測試', 'lgKeyTest', {}, 'sec') + btn('清除', 'lgKeyClear', {}, 'gray') : ''}</div></div>
      <p class="small">金鑰到 console.anthropic.com → API Keys 建立，複製後貼在這裡（Windows 加密存，畫面上只顯示結尾 4 碼）。只會把「憑證照片」送去讀，不送客戶資料。一張收據約 NT$0.1 以內。</p></div>`;
  return h;
};

const LG_ASK = `<details><summary><b>第一次跟記帳士見面，帶這 10 題</b></summary><ol class="small" style="line-height:1.8">
<li>夥伴是「承攬、每場計酬」：報酬要用哪一種所得申報（執行業務、其他所得、薪資）？每次給付超過多少要扣繳？</li>
<li>二代健保補充保費：單次給付超過門檻要不要扣、費率多少（請記帳士確認現行金額）？</li>
<li>扣繳憑單需要夥伴的身分證字號、戶籍地址：系統目前沒有收，要不要開始收（原則上要先告知用途、取得同意）？</li>
<li>營業稅：示範公司要開統一發票（5%）還是小規模（1%）？客戶要發票時怎麼開？（管理群「收據發票」那題一起定）</li>
<li>三個品牌同一家公司：帳要不要分部門？目前系統每一筆都有標品牌。</li>
<li>收入認列：用「服務完成日」還是「收款日」？訂金要不要列預收款？</li>
<li>商城賣耗材：進貨要不要做存貨、怎麼算成本？</li>
<li>夥伴代墊用品：用夥伴拿回來的發票報帳可以嗎？要不要打示範公司統編？</li>
<li>每個月要交什麼、幾號前交：這份記帳士包（CSV＋憑證照片）夠不夠、格式要不要改？</li>
<li>沒有發票的小額支出（停車、市場）怎麼處理（支出證明單）？</li></ol></details>`;
const LG_RISK = `<details><summary><b>承攬 vs 僱傭 檢查表（勞基法風險，原則上以實際情況認定）</b></summary><div class="small" style="line-height:1.8">
<p>夥伴目前是承攬（每場計酬）。如果實際上「受公司指揮監督」，原則上可能被認定是勞工，要補勞健保、勞退 6%、加班費、特休。下面每一項打勾越多越安全：</p>
<b>人格從屬性</b><ul><li>☐ 夥伴可以自由拒絕派單，不會被處罰（目前「記點／停派」規則，原則上建議寫成「不再優先派單」，不罰款）</li>
<li>☐ 不規定上下班時間、不做考勤（目前的「到場打卡」，原則上定位為服務紀錄、通知客戶，不扣薪）</li>
<li>☐ 不適用公司工作規則、獎懲（請假流程、記點，原則上寫在「合作條款」，不是員工守則）</li>
<li>☐ 可以自己找人代班、也可以同時接別家的案子</li></ul>
<b>經濟從屬性</b><ul><li>☑ 按場計酬，沒有底薪、固定月薪</li><li>☐ 工具用品由夥伴自備，或明訂為「材料費」另計（目前代墊用品由公司付，偏僱傭）</li><li>☐ 做不好要重做、不另計酬（自負成果責任）</li></ul>
<b>組織從屬性</b><ul><li>☐ 不納入公司排班表、不強制參加會議（訓練關卡原則上定位為「服務標準說明」）</li><li>☐ 前後照片定位為「驗收成果」，不是監督工作過程</li></ul>
<b>文件</b><ul><li>☐ 每位夥伴簽「承攬契約」：可拒絕派單、自行決定作業方式、按件計酬</li><li>☐ 承攬不保勞保；建議公司保意外險／公共意外責任險</li><li>☐ 請記帳士或勞動法律師看一次契約與目前的派工規則</li></ul></div></details>`;

ACT.lgMove = d => { const n = +d.n; if (!n) LGMONTH = today().slice(0, 7); else { const [y, m] = LGMONTH.split('-').map(Number), x = new Date(y, m - 1 + n, 1); LGMONTH = `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}`; } LG = null; route(true); };
ACT.lgGo = d => go(d.v);
ACT.lgNew = d => lgForm({ kind: d.k, date: today(), category: d.k === 'income' ? 'other_income' : 'other', brand: '' });
ACT.lgEditBtn = d => lgEdit(d.id);
async function lgEdit(id) {
  let e = [...LG.pending, ...LG.entries].find(x => x.id === id);
  if (!e) { try { e = await api('/api/ledger/entry?id=' + encodeURIComponent(id)); } catch (err) { return toast(err.message, true); } }
  if (e.status === 'reading') return toast('這張還在讀取，等幾秒再點（頁面會自動更新）');
  lgForm(e);
}
function lgForm(e) {
  const cats = LG.cats[e.kind === 'income' ? 'income' : 'expense'].map(([k, n, s]) => [k, s ? `${n}（${s}）` : n]);
  const pic = e.photo ? `<a href="/api/ledger/photo/${e.id}" target="_blank" rel="noopener"><img src="/api/ledger/photo/${e.id}" alt="憑證照片" style="max-width:100%;max-height:260px;border-radius:10px;margin-bottom:10px"></a>` : '';
  sheet(e.id ? (e.kind === 'income' ? '修改收入' : '修改支出') : (e.kind === 'income' ? '記一筆收入' : '記一筆支出'), pic
    + g2(fld('date', '日期', e.date || today(), 'date'), fld('amount', '金額（元，含稅）', e.amount ? e.amount / 100 : '', 'number'))
    + g2(fld('category', '類別', e.category, 'select', { options: cats }), fld('brand', '品牌', e.brand || '', 'select', { options: LG.brands.map(([b, n]) => [b, b ? n : '共同（三個品牌一起）']) }))
    + g2(fld('counterparty', e.kind === 'income' ? '付款人／來源' : '店家／對象', e.counterparty || ''), fld('method', '付款方式', e.method || '', 'select', { options: [['', '—'], ...LG.methods.map(x => [x, x])] }))
    + (e.kind === 'income' ? '' : g2(fld('doc_type', '憑證類型', e.doc_type || '', 'select', { options: [['', '—'], ...LG.doc_types.map(x => [x, x])] }), fld('doc_no', '發票號碼', e.doc_no || '', 'text', { ph: 'AB12345678' })))
    + (e.kind === 'income' ? '' : g2(fld('tax_id', '賣方統編', e.tax_id || '', 'text', { ph: '8 位數字' }), fld('tax', '稅額（元，沒有就空白）', e.tax ? e.tax / 100 : '', 'number')))
    + fld('memo', '說明', e.memo || '', 'textarea', { rows: 2 }),
    async f => {
      const r = await api('/api/ledger/save', { ...f, id: e.id || '', version: e.version || '', kind: e.kind || 'expense', amount: toCents(f.amount), tax: toCents(f.tax) || 0 });
      await loadLedger(); return r.message;
    }, e.id && e.status !== 'ok' ? '確認並記帳' : '儲存');
}
ACT.lgOk = async d => { const r = await api('/api/ledger/confirm', { id: d.id }); await lgReload(); return r.message; };
ACT.lgVoid = async d => { if (!confirm('作廢這筆？（不會算進帳，紀錄還看得到）')) return; const r = await api('/api/ledger/void', { id: d.id }); await lgReload(); return r.message; };
ACT.lgReread = async d => { const r = await api('/api/ledger/reread', { id: d.id }); await lgReload(); return r.message; };
ACT.lgUpload = () => {
  const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*,application/pdf'; inp.multiple = true;      // PDF 掃描檔也可以（一頁好幾張收據會自動拆開）
  inp.onchange = async () => { try { const files = [...inp.files].slice(0, 10); if (!files.length) return; toast(`上傳 ${files.length} 個檔案中…`);
    const pdf = files.filter(f => f.type === 'application/pdf' || /\.pdf$/i.test(f.name)), img = files.filter(f => !pdf.includes(f));
    if (pdf.some(f => f.size > 5 * 1024 * 1024)) throw new Error('PDF 超過 5MB，請分開掃描或縮小');
    const b64 = f => new Promise((ok, bad) => { const rd = new FileReader(); rd.onload = () => ok(String(rd.result).split(',')[1]); rd.onerror = () => bad(new Error('檔案讀不到')); rd.readAsDataURL(f); });
    const r = await api('/api/ledger/upload', { images: await Promise.all(img.map(shrink)), pdfs: await Promise.all(pdf.map(b64)) }); await lgReload(); toast(r.message); } catch (e) { toast(e.message, true); } };
  inp.click();
};
ACT.lgTaxId = () => sheet('示範公司的統編', '<p class="small">填了之後，收據上的「買方統編」如果不是這個，確認卡會提醒「可能是別家公司的」（合作店家的收據不要記進來）。</p>' + fld('tax_id', '統一編號（8 位數字）', LG.tax_id || '', 'text', { ph: '12345678' }),
  async f => { const r = await api('/api/ledger/taxid', { tax_id: f.tax_id }); await loadLedger(); return r.message; }, '儲存');
ACT.lgPkg = () => { location.href = '/api/ledger/package?month=' + LGMONTH; return '記帳士包下載中（zip，Excel 可以直接開裡面的 CSV）'; };
ACT.lgKey = () => sheet('讀取憑證的 Claude 金鑰', `<p class="small">到 console.anthropic.com → API Keys 建立一把金鑰，複製後貼在下面。存進去會用 Windows 加密，畫面上只看得到結尾 4 碼；只用來讀「憑證照片」。</p>`
  + `<label for="f-key">API 金鑰（sk-ant- 開頭）</label><input id="f-key" name="key" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore placeholder="sk-ant-…" style="-webkit-text-security:disc">`,      // 不用 type=password，瀏覽器才不會當成登入密碼問「要更新密碼嗎」
  async f => { if (!f.key) throw new Error('請貼上金鑰'); const r = await api('/api/ledger/key', { key: f.key }); await loadLedger(); return r.message; }, '儲存金鑰');
ACT.lgKeyTest = async () => { const r = await api('/api/ledger/key/test', {}); await lgReload(); if (!r.ok) throw new Error(r.message); return r.message; };
ACT.lgKeyClear = async () => { if (!confirm('清除金鑰？之後的憑證照片只存檔，金額要自己填。')) return; const r = await api('/api/ledger/key/clear', {}); await lgReload(); return r.message; };
