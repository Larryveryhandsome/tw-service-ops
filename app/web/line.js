'use strict';
/* LINE 自動化（管理員）：模式開關、本月則數、派工群組、LINE 帳號對應客戶／夥伴、示範、發送紀錄 */
let LO = null;
const LKIND = { dispatch: '派工卡', assigned: '承接卡（客戶）', remind_c: '前一天提醒（客戶）', remind_w: '前一天提醒（夥伴）', change: '時間異動', cancel: '取消通知', grabbed: '接單成功（回覆）', taken: '已額滿（回覆）', cok: '客戶確認（回覆）', cchg: '客戶想改期（回覆）', reply: '回覆', demo: '示範' };
PAGES.line = () => {
  if (S.role !== 'admin') return '<div class="banner warn">只有管理員可以看這頁。</div>';
  if (!LO) { api('/api/line/overview').then(v => { LO = v; route(true); }).catch(e => toast(e.message, true)); return phead('LINE 自動化', '載入中…', ''); }
  const q = LO.quota || {}, nameOf = uid => (LO.users.find(u => u.user_id === uid) || {}).display_name || uid.slice(0, 8);
  let h = phead('LINE 自動化', '通知自動發，不用再一則一則打。', '派工卡、承接卡、前一天提醒、改期取消，全部由系統用示範服務店官方 LINE 自動發送。');
  h += `<div class="grid"><div class="tile stat ${LO.mode === 'on' ? 'good' : 'warn'}"><div class="k">目前模式</div><div class="v">${{ off: '關閉', test: '測試', on: '正式' }[LO.mode]}</div><div class="s">${LO.mode === 'test' ? '只發給測試名單' : LO.mode === 'on' ? '客戶與夥伴都會收到' : '完全不發'}</div></div>
    <div class="tile stat ${q.limit && q.used / q.limit > .8 ? 'warn' : ''}"><div class="k">本月推播則數</div><div class="v">${q.used ?? '—'} / ${q.limit ?? '—'}</div><div class="s">回覆不算則數</div></div></div>`;
  if (q.limit && q.limit <= 200) h += `<div class="banner warn">目前是免費「輕用量」方案（每月 ${q.limit} 則）。一張訂單大約用 3–4 則推播，正式上線建議升級「中用量」（原則上以 LINE 官方公告價格為準）。</div>`;
  h += `<h2>設定</h2><div class="card"><p class="small">每個品牌（官方 LINE）各自切換：<b>測試</b>＝只發給下面的測試名單和派單管理群；<b>正式</b>＝客戶和夥伴都會收到。</p>`
    + (LO.brands || []).map(b => `<div class="item"><div class="t"><div class="n">${esc(b.name)}</div><div class="m">${{ off: '關閉', test: '測試中', on: '正式發送中' }[(LO.modes || {})[b.id]] || ''}</div></div><div class="a"><select class="lbmode" data-b="${b.id}">${[['off', '關閉'], ['test', '測試（只發名單）'], ['on', '正式發送']].map(([k, t]) => `<option value="${k}"${(LO.modes || {})[b.id] === k ? ' selected' : ''}>${t}</option>`).join('')}</select></div></div>`).join('') + `
    <label>測試名單（測試模式只會發給這些人）</label><div class="chips">${LO.users.filter(u => u.user_id.startsWith('U')).slice(0, 60).map(u => `<label><input type="checkbox" class="ltest" value="${u.user_id}"${LO.test_users.includes(u.user_id) ? ' checked' : ''}> ${esc(u.display_name || u.user_id.slice(0, 8))}</label>`).join('')}</div>
    <label>內部人員（這些 LINE 帳號在夥伴群組裡說話不會被當成夥伴；在夥伴群組按「我要接單」＝代接）</label><div class="chips">${LO.users.filter(u => u.user_id.startsWith('U')).slice(0, 60).map(u => `<label><input type="checkbox" class="lstaff" value="${u.user_id}"${LO.staff.includes(u.user_id) ? ' checked' : ''}> ${esc(u.display_name || u.user_id.slice(0, 8))}</label>`).join('')}</div>
    <div class="btns">${btn('儲存設定', 'lineSave')}</div><p class="small">${esc(LO.webhook || '')}</p></div>`;
  const ROLE = { admin: '派單管理（通知都發這裡）', pool: '接單管理（全體夥伴）', worker: '夥伴個人群組', job: '單一案場群組' };
  h += `<h2>LINE 群組 <span class="g">${LO.groups.filter(g => g.active).length}</span></h2><div class="card"><p class="small">官方帳號被拉進群組會自動判斷：名字含「派單管理」→ 管理群；「接單管理」→ 全體夥伴群；「示範服務店-姓名」→ 那位夥伴的個人群組（派工卡一人一單發到這裡）。判斷錯了直接在這裡改。要找這些群組：用<b>自己的 LINE</b> 搜尋群組名（官方帳號後台的聊天列表不會顯示群組，這是 LINE 的限制）。</p>`
    + tbl(['群組', '用途', '對應夥伴'], LO.groups.filter(g => g.active).map(g => `<tr>${td('群組', `<b>${esc(g.name || g.group_id.slice(0, 10))}</b>${g.group_id === LO.admin_group ? '<span class="s">✔ 通知發到這裡</span>' : ''}`)}
      ${td('用途', `<select data-change="lineRole" data-gid="${g.group_id}">${Object.entries(ROLE).map(([k, t]) => `<option value="${k}"${k === (g.role || 'job') ? ' selected' : ''}>${t}</option>`).join('')}</select>`)}
      ${td('對應夥伴', g.role === 'worker' ? `<select data-change="lineRole" data-gid="${g.group_id}" data-wsel="1">${optsOf(S.workers, w => w.name, '—').map(([k, t]) => `<option value="${k}"${k === (g.worker_id || '') ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>` : '<span class="small">—</span>')}</tr>`))
    + `<div class="btns">${btn('重新自動判斷全部群組', 'lineClassify', {}, 'sec')}</div></div>`;
  h += `<h2>示範：自己當客戶、當夥伴</h2><div class="card"><p>按「傳派工卡給我」→ 你的 LINE 會收到派工卡 → 按「我要接單」→ 馬上收到「接單成功」，同時以客戶身分收到「專業服務人員已承接」卡 → 按「確認這個時間」→ 自動排定。前一天提醒是每天自動發的，不用按。</p>
    <label for="ldemo">傳給誰</label><select id="ldemo">${LO.test_users.map(u => `<option value="${u}">${esc(nameOf(u))}</option>`).join('')}</select>
    <div class="btns">${btn('① 傳派工卡給我', 'lineDemo', { step: 'start' })}${btn('清除示範資料', 'lineDemo', { step: 'cleanup' }, 'gray')}</div></div>`;
  const unl = LO.users.filter(u => u.user_id.startsWith('U'));
  h += `<h2>LINE 帳號對應 <span class="g">${unl.length}</span></h2><div class="card"><p class="small">每個 LINE 好友都有專屬編號。客戶送出預約後按「用 LINE 傳送預約編號」會自動對應；夥伴用綁定訊息綁定（派單管理群每天 10:00 的名單可以拿）；其他的在這裡手動選。</p>`
    + tbl(['LINE 名稱', '最後訊息', '客戶', '夥伴'], unl.map(u => `<tr>${td('LINE', `<b>${esc(u.display_name || '（未取得名稱）')}</b><span class="s">${esc((u.last_seen || '').slice(5, 16))}${u.is_friend ? '' : ' · 已封鎖'}</span>`)}${td('最後訊息', `<span class="small">${esc((u.last_text || '').slice(0, 30))}</span>`)}
      ${td('客戶', `<select data-change="lineLink" data-uid="${u.user_id}" data-acc="${u.account}" data-kind="customer_id">${optsOf(S.customers.filter(c => c.status !== 'ended' || c.id === u.customer_id).sort((a, b) => a.name.localeCompare(b.name)), c => c.name, '—').map(([k, t]) => `<option value="${k}"${k === (u.customer_id || '') ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>`)}
      ${td('夥伴', `<select data-change="lineLink" data-uid="${u.user_id}" data-acc="${u.account}" data-kind="worker_id">${optsOf(S.workers.filter(w => w.status !== 'paused' || w.id === u.worker_id), w => w.name, '—').map(([k, t]) => `<option value="${k}"${k === (u.worker_id || '') ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>`)}</tr>`)) + '</div>';
  h += `<h2>最近發送</h2><div class="card">${tbl(['時間', '種類', '對象', '狀態'], LO.outbox.map(m => `<tr>${td('時間', esc((m.created_at || '').slice(5, 16)))}${td('種類', esc(LKIND[m.kind] || m.kind) + `<span class="s">${esc(m.alt.slice(0, 40))}</span>`)}${td('對象', esc(m.to_id === 'reply' ? '（回覆）' : nameOf(m.to_id)))}${td('狀態', m.status === 'sent' ? st('completed', '已送出') : m.status === 'queued' ? st('pending', '排隊中') : m.status.startsWith('skipped') ? st('archived', m.status === 'skipped:test' ? '測試模式略過' : '關閉略過') : st('overdue', '失敗') + `<span class="s">${esc(m.error.slice(0, 60))}</span>`)}</tr>`))}</div>`;
  return h;
};
ACT.lmode = d => { LO.mode = d.val; route(true); };
SEG.lmode = null;
ACT.seg = (orig => d => { if (d.seg === 'lmode') { LO.mode = d.val; return route(true); } return orig(d); })(ACT.seg);
ACT.lineSave = async () => {
  const modes = Object.fromEntries($$('.lbmode').map(x => [x.dataset.b, x.value]));
  const goOn = Object.entries(modes).filter(([b, m]) => m === 'on' && (LO.modes || {})[b] !== 'on').map(([b]) => (LO.brands.find(x => x.id === b) || {}).name);
  if (goOn.length && !confirm(`「${goOn.join('、')}」切成正式發送後，客戶和夥伴都會收到 LINE 自動通知，也會用掉每月推播則數。確定？`)) return;
  await api('/api/line/settings', { mode: LO.mode, modes, test_users: $$('.ltest:checked').map(x => x.value), group: LO.group || '' });
  await api('/api/line/staff', { users: $$('.lstaff:checked').map(x => x.value) });
  LO = null; route(true); return '已儲存';
};
ACT.lineLink = async (d, el) => { await api('/api/line/link', { user_id: d.uid, account: d.acc, [d.kind]: el.value }); LO = null; toast('已對應'); };
ACT.lineDemo = async d => {
  if (d.step === 'cleanup' && !confirm('清除示範產生的客戶、夥伴、工單？')) return;
  const r = await api('/api/line/demo', { step: d.step, user_id: $('#ldemo')?.value });
  LO = null; await refresh();
  return d.step === 'cleanup' ? `已清除 ${r.cleaned} 筆示範資料` : '已送出，請看 LINE（約 10 秒內）';
};
ACT.lineRole = async (d, el) => {
  const g = LO.groups.find(x => x.group_id === d.gid);
  const body = d.wsel ? { group_id: d.gid, role: 'worker', worker_id: el.value } : { group_id: d.gid, role: el.value, worker_id: el.value === 'worker' ? g.worker_id : '' };
  await api('/api/line/group-role', body); LO = null; route(true); toast('已更新');
};
ACT.lineClassify = async () => { await api('/api/line/classify', {}); LO = null; route(true); return '已重新判斷'; };
