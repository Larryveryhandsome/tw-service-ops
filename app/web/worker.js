'use strict';
/* 服務人員手機端：接單、我的班（打卡、拍照、完工回報）、可排時間、我。只看得到自己的工作，看不到客戶電話與價格。 */
const W_LBL = { invited: '邀請你', applied: '已報名，等確認', accepted: '已接單', declined: '已婉拒' };
const myJob = id => (S.mine || []).find(x => x.assignment_id === id);

PAGES.jobs = () => {
  if (S.role !== 'cleaner') return PAGES.home();
  const inv = (S.mine || []).filter(x => x.assignment_status === 'invited' && x.date >= S.today), open = S.jobs.filter(j => !j.mine || j.mine === 'applied');
  let h = phead('接單', `${esc(S.me.name || ME.name)}，有新工作嗎？`, '「邀請你」是示範服務店依你的區域和時間挑的；「開放報名」誰都可以報，示範服務店會確認後通知你。');
  if (S.me.status !== 'active') h += '<div class="banner warn">你的資料還在核對，核對好才能接單。有問題請在 LINE 跟示範服務店說。</div>';
  if (!S.push_devices) h += `<div class="banner blue" data-act="enablePush">🔔 打開通知，有新工作會馬上告訴你 ›</div>`;
  h += `<h2>邀請你 <span class="g">${inv.length}</span></h2>` + (inv.map(x => `<div class="card" data-tour="invite"><div class="secttl" style="margin-top:0"><h3>${md(x.date)} ${x.start}–${x.end}</h3>${st('invited', '邀請')}</div>
    <div class="small">${esc(x.street || x.area)} · ${esc(x.service_type || '服務')} · ${hrs(mins(x.start, x.end))}</div><div class="bigbtns">${btn('接受', 'wRespond', { id: x.assignment_id, ok: 1 })}${btn('不方便', 'wRespond', { id: x.assignment_id, ok: '' }, 'gray')}</div></div>`).join('') || '<div class="empty">目前沒有邀請。</div>');
  h += `<h2>開放報名 <span class="g">${open.length}</span></h2>` + (open.map(j => `<div class="card"><div class="secttl" style="margin-top:0"><h3>${md(j.date)} ${j.start}–${j.end}</h3>${j.mine ? st('applied', '已報名') : st('new', `缺 ${j.required_workers - j.taken} 位`)}</div>
    <div class="small">${esc(j.street || j.area || '臺北')} · ${esc(j.service_type || '服務')} · 約 ${hrs(j.minutes)}</div>${j.scope ? `<div class="small" style="margin-top:6px">範圍：${esc(j.scope.split(/\n/).join('、'))}</div>` : ''}${j.note ? `<div class="small" style="margin-top:6px;white-space:pre-wrap">${esc(j.note)}</div>` : ''}
    <div class="btns">${j.mine ? btn('撤回報名', 'wWithdraw', { id: j.id }, 'gray') : btn('我要報名', 'wApply', { id: j.id })}</div></div>`).join('') || '<div class="empty">目前沒有開放的工作。</div>');
  return h;
};
ACT.wRespond = async d => { await api('/api/me/respond', { assignment_id: d.id, accept: !!d.ok }); await refresh(); return d.ok ? '已接受，示範服務店確認後會通知你地址' : '已回覆不方便'; };
ACT.wApply = async d => { await api('/api/me/apply', { order_id: d.id }); await refresh(); return '已報名，等示範服務店確認'; };
ACT.wWithdraw = async d => { await api('/api/me/withdraw', { order_id: d.id }); await refresh(); return '已撤回'; };

PAGES.mine = () => {
  const t = S.today, mine = (S.mine || []).filter(x => x.assignment_status === 'accepted' && x.order_status !== 'cancelled');
  const todays = mine.filter(x => x.date === t), next = mine.filter(x => x.date > t), past = mine.filter(x => x.date < t).reverse();
  let h = phead('我的班', '今天也辛苦了。', '到場按「到場打卡」、先拍服務前照片；做完拍完成照片、寫回報，再按「離場打卡」。');
  h += `<h2>今天</h2>` + (todays.map(jobCard).join('') || '<div class="empty">今天沒有排班。</div>');
  h += `<h2>接下來 <span class="g">${next.length}</span></h2>` + (next.map(jobCard).join('') || '<div class="empty">還沒有排班。</div>');
  h += `<h2>做過的</h2>` + (past.slice(0, 15).map(x => `<div class="item"><div class="t"><div class="n">${md(x.date)} ${esc(x.site_name || x.area)}</div><div class="m">${x.timesheet ? `${x.timesheet.start}–${x.timesheet.end}　${hrs(x.timesheet.minutes)}` : '未打卡'}${x.pay != null ? '　' + money(x.pay) : ''}</div></div></div>`).join('') || '<div class="empty">還沒有紀錄。</div>');
  return h;
};
function jobCard(x) {
  const ok = x.order_status === 'confirmed', ck = x.checkin || {}, isToday = x.date === S.today;
  let h = `<div class="card" data-tour="job"><div class="secttl" style="margin-top:0"><h3>${md(x.date)} ${x.start}–${x.end}</h3>${st(ok ? 'confirmed' : x.order_status === 'completed' ? 'completed' : 'review', ok ? '已排定' : x.order_status === 'completed' ? '已完工' : '等客戶確認')}</div>`;
  if (!x.address) h += `<div class="small">${esc(x.street || x.area)} · ${esc(x.service_type)}　完整地址會在排定後顯示。</div>${x.scope ? `<div class="small" style="margin-top:6px">範圍：${esc(x.scope.split(/\n/).join('、'))}</div>` : ''}</div>`;
  else {
    h += `<dl class="kv"><dt>地點</dt><dd><b>${esc(x.site_name)}</b><br>${esc(x.address)}${(x.navigation || '').startsWith('http') ? `<br><a href="${esc(x.navigation)}" target="_blank" rel="noopener">開 Google 地圖導航 ›</a>` : x.navigation ? '<br>' + esc(x.navigation) : `<br><a href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(x.address)}" target="_blank" rel="noopener">開地圖 ›</a>`}</dd>
      ${x.entry_notes ? `<dt>進場</dt><dd>${esc(x.entry_notes)}</dd>` : ''}${x.scope ? `<dt>服務範圍</dt><dd style="white-space:pre-wrap">${esc(x.scope)}</dd>` : x.checklist ? `<dt>服務重點</dt><dd>${esc(x.checklist)}</dd>` : ''}${x.site_notes ? `<dt>注意</dt><dd>${esc(x.site_notes)}</dd>` : ''}${x.order_notes ? `<dt>這次</dt><dd>${esc(x.order_notes)}</dd>` : ''}</dl>`;
    if (ok && isToday) {
      h += `<div class="bigbtns">${ck.arrived_at ? `<div class="banner ok" style="margin:0">✓ ${ck.arrived_at.slice(11, 16)} 到場</div>` : btn('到場打卡', 'wCheck', { id: x.assignment_id, kind: 'arrive' })}
        ${ck.left_at ? `<div class="banner ok" style="margin:0">✓ ${ck.left_at.slice(11, 16)} 離場</div>` : ck.arrived_at ? btn('離場打卡', 'wCheck', { id: x.assignment_id, kind: 'leave' }, 'green') : ''}</div>`;
    }
    if (ok) h += `<div class="btns">${btn(`拍照上傳（${x.photos}）`, 'wPhoto', { id: x.assignment_id }, 'sec')}${btn('完工回報', 'wReport', { id: x.assignment_id }, 'sec')}</div>`;
    if (x.field_report) h += `<div class="small" style="margin-top:8px;white-space:pre-wrap">回報：${esc(x.field_report)}</div>`;
    h += '</div>';
  }
  return h;
}
ACT.wCheck = async d => {
  let pos = {};
  try { pos = await new Promise((ok, no) => navigator.geolocation.getCurrentPosition(p => ok({ lat: p.coords.latitude, lng: p.coords.longitude }), no, { timeout: 8000, enableHighAccuracy: true })); } catch (e) { }
  if (d.kind === 'leave') return sheet('離場打卡', fld('pause', '中間休息幾分鐘（沒有填 0）', 0, 'number') + fld('notes', '今天的狀況（選填）', '', 'textarea'), async f => { await api('/api/me/checkin', { assignment_id: d.id, kind: 'leave', pause: Number(f.pause || 0), notes: f.notes, ...pos }); return '已離場打卡，辛苦了！'; }, '打卡');
  await api('/api/me/checkin', { assignment_id: d.id, kind: 'arrive', ...pos }); await refresh(); return '已到場打卡，記得拍服務前照片';
};
ACT.wPhoto = d => {
  const x = myJob(d.id);
  sheet('拍照上傳', fld('label', '這是', ck(x) ? 'after' : 'before', 'select', { options: [['before', '服務前'], ['after', '完工'], ['incident', '異常／損壞']] }) + '<label for="f-file">選照片（可以一次多張）</label><input id="f-file" type="file" accept="image/jpeg,image/png" multiple capture="environment">',
    async f => { const files = [...$('#f-file').files]; if (!files.length) throw new Error('請選照片'); for (const file of files) await api('/api/me/photo', { assignment_id: d.id, label: f.label, service_date: x.date, name: file.name, content: await shrink(file) }); return `已上傳 ${files.length} 張`; }, '上傳');
};
const ck = x => x.checkin && x.checkin.arrived_at;
ACT.wReport = d => sheet('完工回報', fld('report', '做了什麼、有沒有特別狀況（損壞、缺用品、客戶交代）', '', 'textarea', { rows: 5 }), async f => { await api('/api/me/report', { assignment_id: d.id, ...f }); return '已回報給示範服務店'; }, '送出');

/* 可排時間：點日期切換 有空 → 不行 → 未填 */
let AVM = null, AV = {};
PAGES.avail = () => {
  if (S.role !== 'cleaner') return PAGES.more();
  AVM = AVM || (() => { const d = dOf(S.today); d.setDate(1); d.setMonth(d.getMonth() + (dOf(S.today).getDate() > 20 ? 1 : 0)); return iso(d).slice(0, 7); })();
  if (AV._m !== AVM) { AV = { _m: AVM }; for (const a of (S.availability || []).filter(a => a.date.startsWith(AVM))) { AV[a.date] = a.kind; if (a.start) AV._s = a.start, AV._e = a.end; } }
  const [y, m] = AVM.split('-').map(Number), first = new Date(y, m - 1, 1), days = new Date(y, m, 0).getDate();
  let h = phead('可排時間', `${m} 月哪幾天可以排？`, '點一下＝可以（綠），再點＝不行（紅），再點＝清掉。按儲存後示範服務店派工會優先找有空的你。');
  h += `<div class="calbar"><h2 class="ttl" style="margin:0">${y} 年 ${m} 月</h2><div class="paddles"><button class="paddle" data-act="avMove" data-n="-1" aria-label="上個月">‹</button><button class="paddle" data-act="avMove" data-n="1" aria-label="下個月">›</button></div></div>`;
  h += g2(fld('avs', '可以的時段（空白＝整天）', AV._s || '', 'time'), fld('ave', '到', AV._e || '', 'time'));
  h += '<div class="calwrap avcal" style="margin-top:14px" data-tour="avcal"><div class="cal">' + [...WD].map(d => `<div class="h">${d}</div>`).join('');
  for (let i = 0; i < first.getDay(); i++) h += '<div class="d blank"></div>';
  for (let d = 1; d <= days; d++) { const k = `${AVM}-${String(d).padStart(2, '0')}`, v = AV[k]; h += `<div class="d ${v || ''} ${k === S.today ? 'today' : ''}" data-act="avToggle" data-date="${k}"><div class="dn"><span>${d}</span></div>${v ? `<span class="lab">${v === 'free' ? '可以' : '不行'}</span>` : ''}</div>`; }
  h += `</div></div><div class="btns">${btn('儲存', 'avSave')}${btn('整月平日都可以', 'avWeekdays', {}, 'gray')}</div>`;
  return h;
};
ACT.avMove = d => { const [y, m] = AVM.split('-').map(Number); const n = new Date(y, m - 1 + Number(d.n), 1); AVM = iso(n).slice(0, 7); route(true); };
ACT.avToggle = d => { AV._s = $('#f-avs').value; AV._e = $('#f-ave').value; AV[d.date] = { undefined: 'free', free: 'busy', busy: undefined }[AV[d.date]]; route(true); };
ACT.avWeekdays = () => { AV._s = $('#f-avs').value; AV._e = $('#f-ave').value; const [y, m] = AVM.split('-').map(Number); for (let d = 1; d <= new Date(y, m, 0).getDate(); d++) { const dt = new Date(y, m - 1, d); if (dt.getDay() % 6) AV[iso(dt)] = 'free'; } route(true); };
ACT.avSave = async () => {
  const s = $('#f-avs').value, e = $('#f-ave').value;
  if ((s && !e) || (!s && e)) throw new Error('時段要填開始和結束，或兩個都空白');
  const days = Object.entries(AV).filter(([k, v]) => k.startsWith(AVM) && v).map(([date, kind]) => ({ date, kind, start: kind === 'free' ? s : '', end: kind === 'free' ? e : '' }));
  await api('/api/me/availability', { month: AVM, days }); await refresh(); return `已儲存 ${days.length} 天`;
};

PAGES.me = () => {
  if (S.role !== 'cleaner') return PAGES.more();
  const done = (S.mine || []).filter(x => x.order_status === 'completed' && x.date.startsWith(S.today.slice(0, 7)));
  return phead('我', esc(S.me.name || ME.name), '') + `<div class="grid"><div class="tile stat"><div class="k">這個月完成</div><div class="v">${done.length} 場</div></div><div class="tile stat"><div class="k">這個月預估</div><div class="v">${money(done.reduce((n, x) => n + (x.pay || 0), 0))}</div><div class="s">以示範服務店結算為準</div></div></div>`
    + `<h2>薪資結算</h2><div class="card">${tbl(['月份', '>金額', '狀態'], S.settlements.map(s => `<tr>${td('月份', esc(s.period))}${td('金額', money(s.total), 'r')}${td('狀態', s.status === 'paid' ? st('paid', '已匯款') : st('pending', '結算中'))}</tr>`))}</div>`
    + `<div class="banner blue" data-act="lineBind">💬 綁定 LINE：派工卡、明天提醒會直接傳到你的 LINE，按「我要接單」就搶到 ›</div><div class="btns">${btn('操作手冊', 'goManual', {}, 'sec')}${S.push_devices ? '<span class="small">✓ 這支手機會收到通知</span>' : btn('打開手機通知', 'enablePush')}${btn('改密碼', 'changePw', {}, 'gray')}${btn('重看導覽', 'resetTours', {}, 'gray')}${btn('登出', 'logout', {}, 'gray')}</div>`;
};
ACT.changePw = () => sheet('改密碼', fld('old', '目前密碼', '', 'password') + fld('new', '新密碼', '', 'password'), async f => { await api('/api/password', f); return '已更新'; });

ACT.goManual = () => go('manual');

ACT.lineBind = async () => {
  const r = await api('/api/me/line-bind', {});
  sheet('綁定 LINE', `<p>按下面按鈕會打開示範服務店官方 LINE，並自動帶入綁定碼，直接按「傳送」就完成。</p><div class="copybox" style="font-size:28px;text-align:center;letter-spacing:4px">${esc(r.code)}</div><p class="small">綁定碼 24 小時內有效。還沒加示範服務店官方 LINE 好友的話，打開後先加好友。</p><div class="btns"><a class="btn full" href="${esc(r.url)}">打開 LINE 傳送綁定碼</a></div>`);
};
