// 試算表／檔案匯入（客戶、夥伴、工單）：上傳 → 預覽 → 確認匯入
let IMP = { kind: 'customers', rows: null, file: null };
const IMP_KINDS = { customers: '客戶', workers: '夥伴', orders: '工單', prices: '報價品項價格' };
const IMP_HINT = {
  customers: '表頭要有「姓名（或名稱）」，可以有：電話、LINE名稱、品牌（示範服務店／生活線／企業線）、地址、地區、備註。電話對到既有客戶只補空白欄位。',
  workers: '表頭要有「姓名」，可以有：電話、服務區域、技能、可排時間、時薪、狀態（寫「可派工」才會直接可派，其他先待核對）、備註。',
  prices: '表頭要有「項目」「價格」，可以有「品牌」（示範服務店／生活線／企業線，沒寫就是示範服務店）。項目例：居家單次時薪、定期方案時薪、年度保養時薪、店家每間房、現場估價費、垃圾每袋、生活線時薪；加購項目寫「加購：冰箱服務」。預覽會顯示原本→新價格，只有看得到金額的人能確認匯入。',
  orders: '表頭要有「日期」「開始」「客戶（或電話）」，可以有：結束、地址、人數、類型、範圍、備註。匯入的工單先擋派工，確認後在工單按「恢復自動派」。'
};
PAGES.importdata = () => {
  if (SEG.impk && SEG.impk !== IMP.kind) { IMP.kind = SEG.impk; IMP.rows = null; IMP.file = null; }
  let h = phead('資料匯入', '試算表一次帶進來。', '上傳 Excel（.xlsx）或 CSV：第一列是表頭、第二列開始是資料。先預覽，確認沒問題再按「確認匯入」。');
  h += `<div class="card">${segctl('impk', Object.entries(IMP_KINDS).map(([k, t]) => [k, t, '']), IMP.kind)}<p class="small">${esc(IMP_HINT[IMP.kind])}</p>
    <input type="file" id="impFile" accept=".xlsx,.csv"> ${btn('預覽', 'impPreview', {}, 'sec')}</div>`;
  if (IMP.rows) {
    const n = a => IMP.rows.filter(r => r.action === a).length;
    h += `<div class="card"><p>共 ${IMP.rows.length} 列：<b>新增 ${n('add')}</b>、更新 ${n('update')}、跳過 ${n('skip')}</p>`
      + tbl(['列', '動作', '內容', '說明'], IMP.rows.slice(0, 200).map(r => `<tr>${td('列', r.i)}${td('動作', st(r.action === 'skip' ? 'archived' : r.action === 'add' ? 'completed' : 'pending', { add: '新增', update: '更新', skip: '跳過' }[r.action]))}${td('內容', esc(Object.values(r.data).join('｜').slice(0, 80)))}${td('說明', esc(r.why || ''))}</tr>`))
      + `<div class="btns">${btn('確認匯入', 'impCommit', {})}${btn('重新選檔', 'impReset', {}, 'gray')}</div></div>`;
  }
  return h;
};
async function impRead() {
  const f = ($('#impFile') || {}).files && $('#impFile').files[0]; if (!f && !IMP.file) throw new Error('請先選檔案');
  if (f) { const b = await f.arrayBuffer(); let s = ''; new Uint8Array(b).forEach(x => s += String.fromCharCode(x)); IMP.file = { filename: f.name, content: btoa(s) }; }
  return IMP.file;
}
ACT.impPreview = async () => { const file = await impRead(); IMP.rows = (await api('/api/import/preview', { kind: IMP.kind, ...file })).rows; route(true); return `預覽好了：${IMP.rows.length} 列`; };
ACT.impCommit = async () => {
  if (!confirm(`確定匯入${IMP_KINDS[IMP.kind]}？`)) return;
  const r = await api('/api/import/commit', { kind: IMP.kind, ...IMP.file }); IMP.rows = null; IMP.file = null; await refresh();
  return `匯入完成：新增 ${r.added}、更新 ${r.updated}、跳過 ${r.skipped}` + (r.errors.length ? `；有 ${r.errors.length} 列失敗：${r.errors.slice(0, 3).join('；')}` : '');
};
ACT.impReset = () => { IMP.rows = null; IMP.file = null; route(true); };
