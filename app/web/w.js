'use strict';

/* 程式錯誤自動回報（功能回報；同一種只記一筆） */
let JSERRN = 0;
function jserr(msg, stack) { if (++JSERRN > 3) return; fetch('/api/public/jserr', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ page: '夥伴工作頁', msg: String(msg || '').slice(0, 300), stack: String(stack || '').slice(0, 1500) }) }).catch(() => { }); }
window.addEventListener('error', e => jserr(e.message, e.error && e.error.stack));
window.addEventListener('unhandledrejection', e => { const r = e.reason || {}; if (r.name && r.name !== 'Error') jserr(r.message, r.stack); });
/* 夥伴工作頁（LINE 打開、不用登入）：到場打卡 → 服務前照片 → 完成照片＋回報 → 離場打卡。照片自動傳給示範服務店；完成照片傳給業主確認。 */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const T = location.pathname.split('/').pop();
const md = d => d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : '';
let J = null, BUSY = false;
async function call(act, body) {
  let r; try { r = await fetch(`/api/public/work/${T}/${act}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }); } catch (e) { throw new Error(NETMSG); }
  const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '失敗，請再試一次'); return v;
}
const NETMSG = '網路不穩，沒有送出。請到訊號好的地方再按一次。';      // 不要出現英文「Failed to fetch」
const WDRAFT = Draft.attach('w:' + T, $('#app'));
async function load() {
  let r; try { r = await fetch('/api/public/work/' + T); } catch (e) { $('#app').innerHTML = `<div class="card"><h2 style="margin-top:0">網路不穩，打不開</h2><p class="small">請到訊號好的地方再試一次。</p><button class="btn full" onclick="location.reload()">重新整理</button></div>`; return; }
  const v = await r.json().catch(() => ({}));
  if (!r.ok) { $('#app').innerHTML = `<div class="card"><h2 style="margin-top:0">${esc(v.error || '連結無效')}</h2><button class="btn full" onclick="location.reload()" style="margin-top:8px">重新整理</button></div>`; return; }
  J = v; draw();
}
const step = (n, title, done, body) => `<div class="card wstep ${done ? 'done' : ''}"><div class="wn">${done ? '✓' : n}</div><div style="flex:1"><h3 style="margin:0 0 6px">${title}</h3>${body}</div></div>`;
function draw() {
  const j = J, ok = j.status === 'confirmed' || j.status === 'completed' || !!j.go, today = j.date === j.today, arrived = !!j.arrived_at, left = !!j.left_at;
  const nav = (j.navigation || '').startsWith('http') ? j.navigation : `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(j.address || j.area)}`;
  let h = (j.is_redo ? '<div class="banner warn" style="margin-bottom:8px"><b>🔁 這是補服務</b>：照「服務範圍」裡的補掃項目處理就好。</div>' : '') + (j.portal_url ? `<p style="margin:0 0 6px"><a class="btn sm gray" href="${esc(j.portal_url)}">‹ 我的主控板（班表・收入）</a></p>` : '') + `<h1 class="headline" style="font-size:30px">${md(j.date)} ${j.start}–${j.end}</h1><p class="sub">${esc(j.worker)}・${esc(j.service_type)}${j.hours ? `・約 ${j.hours} 小時` : ''}${j.pay ? `・這場收入 ${esc(j.pay)}` : ''}${j.crew.length ? `・同組：${esc(j.crew.join('、'))}` : ''}</p>`;
  h += `<div class="card"><dl class="kv"><dt>地區</dt><dd>${esc(j.area)}</dd>${j.address ? `<dt>地址</dt><dd><b>${esc(j.address)}</b><br><a class="btn full sec" style="margin-top:8px" href="${esc(nav)}" target="_blank" rel="noopener">🧭 開導航</a>${j.geo ? `<div class="small">${esc(j.geo)}</div>` : ''}</dd>` : '<dt>地址</dt><dd class="small">接單後就會顯示</dd>'}
    ${j.entry_mode ? `<dt>開門</dt><dd>${esc({ home: '業主親自開門（到了按「請業主開門」）', bell: '按門鈴／有人在，可直接進入', self: '密碼鎖／鑰匙，自行進門', contact: '管理室／管理員開門' }[j.entry_mode] || '')}</dd>` : ''}${j.entry_notes ? `<dt>進場</dt><dd>${esc(j.entry_notes)}</dd>` : ''}${(j.contacts || []).length ? `<dt>現場聯絡</dt><dd>${j.contacts.map(x => `${esc(x.name)}（${esc(x.role)}）<a class="btn full sec" style="margin-top:6px" href="tel:${esc(x.phone)}">📞 打給 ${esc(x.name)}</a>`).join('')}</dd>` : ''}${j.scope ? `<dt>服務範圍</dt><dd style="white-space:pre-wrap">${esc(j.scope)}</dd>` : ''}
    ${j.tools ? `<dt>工具</dt><dd><b>${esc(j.tools)}</b></dd>` : ''}${j.trash ? `<dt>垃圾</dt><dd>${esc(j.trash)}</dd>` : ''}${j.scope_pending ? '<dt>範圍</dt><dd class="small">管理層確認詳細需求後會顯示在這裡（也會用 LINE 通知你）</dd>' : ''}${j.site_notes ? `<dt>注意</dt><dd style="white-space:pre-wrap">${esc(j.site_notes)}</dd>` : ''}${j.notes ? `<dt>這次</dt><dd style="white-space:pre-wrap">${esc(j.notes)}</dd>` : ''}${j.site_info ? `<dt>現場須知</dt><dd style="white-space:pre-wrap"><b>${esc(j.site_info)}</b></dd>` : ''}${j.info_url ? `<dt>照片</dt><dd><a href="${esc(j.info_url)}" target="_blank" rel="noopener">看現場須知・照片 ›</a></dd>` : ''}${j.worker_note ? `<dt>提醒</dt><dd style="white-space:pre-wrap"><b>${esc(j.worker_note)}</b></dd>` : ''}${Object.entries(j.extra || {}).map(([k, v]) => `<dt>${esc(k)}</dt><dd style="white-space:pre-wrap"><b>${esc(v)}</b></dd>`).join('')}</dl></div>`;
  if (!ok) { $('#app').innerHTML = h + '<div class="banner">業主還沒確認，確認後這裡會出現打卡和拍照。</div>'; return; }
  if (left && j.redo) { $('#app').innerHTML = h + `<div class="banner warn">示範服務店請你補拍完成照片：每個服務的區域都拍清楚，傳完就好（不用再打卡）。</div>`
    + step(3, '補拍完成照片', false, `<p class="small">已傳 ${j.photos.after} 張</p><label class="photobtn">＋ 拍照／選照片<input type="file" accept="image/*" multiple hidden data-label="after"></label>`)
    + '<div id="err" class="err" role="alert"></div><p id="prog" class="small"></p>'; return; }
  const after = (j.next_link ? `<a class="btn full" style="margin-top:10px" href="${esc(j.next_link.url)}">下一場 ${esc(j.next_link.start)}（${esc(j.next_link.area)}）›</a>` : '') + (j.portal_url ? `<a class="btn full ${j.next_link ? 'sec' : ''}" style="margin-top:8px" href="${esc(j.portal_url)}">回我的主控板 ›</a>` : '');      // 完工後不是死路
  if (left) { $('#app').innerHTML = h + `<div class="banner ok">✓ 已完工（${j.arrived_at.slice(11, 16)}–${j.left_at.slice(11, 16)}），辛苦了！照片已傳給示範服務店與業主。</div>` + after
    + (j.signin_need ? step('簽', j.photos.signin ? '簽到表照片' : '補拍簽到表照片', j.photos.signin > 0, `<p class="small">這個案場要拍簽到表（不會傳給業主）。已傳 ${j.photos.signin} 張。</p><label class="photobtn">＋ 拍簽到表<input type="file" accept="image/*" multiple hidden data-label="signin"></label>`)
      + '<div id="err" class="err" role="alert"></div><p id="prog" class="small"></p>' : ''); return; }      // 離場後也能補拍簽到表
  const NODOOR = { bell: '按門鈴／有人在', self: '密碼鎖／鑰匙', contact: '管理室／管理員開門' }[j.entry_mode];      // 打卡＝已進入
  const doorBtn = today && NODOOR ? `<p class="small" style="margin-top:8px">🚪 ${NODOOR}：到場打卡就算進門，不用請業主開門。</p><button class="btn full sec" data-a="door" style="margin-top:6px">進不去？請業主幫忙開門 🚪</button>` : today ? `<button class="btn full sec" data-a="door" style="margin-top:8px">到門口了，請業主開門 🚪</button><button class="btn full sec" data-a="door_in" style="margin-top:6px">已經進門了 ✅</button><p class="small">${j.entry_mode === 'home' ? (j.door_ack ? '業主已確認會來開門 ✅' : '業主還沒回覆會不會開門') : ''}打卡也會自動通知業主；5 分鐘沒回應會請派單群打電話。</p>` : '';
  h += step(1, '到場打卡', arrived, arrived ? `<p class="small">${j.arrived_at.slice(11, 16)} 已到場</p>` + (today ? doorBtn : '') : today ? (j.ack_need ? `<label class="chk" style="display:flex;gap:8px;align-items:flex-start;margin:0 0 10px;font-size:17px"><input type="checkbox" id="ack" style="width:24px;height:24px;margin-top:2px"> <span><b>我看過上面這場的現場須知</b>（服務範圍、開門方式、注意事項）</span></label>` : '') + `<button class="btn full" data-a="arrive">我到了・定位打卡</button><p class="small">會記錄時間和位置；抓不到定位也能打卡（會標示）。</p>` + doorBtn : j.date < j.today ? '<p class="small"><b>這天已經過了</b>，請用下面「補登實際時間」。</p>' : '<p class="small">服務當天才能打卡。</p>');
  const now = new Date(), hm = String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
  if (!arrived && (j.date < j.today || (today && hm > j.end))) {      // 沒打卡、人已離開現場 → 補登實際時間（不抓定位）
    h += `<div class="card"><details${j.date < j.today || j.photos.after ? ' open' : ''}><summary><b>已經做完、忘了打卡？補登實際時間</b></summary>
      <p class="small">人已經不在現場也可以補登，不用定位；示範服務店會確認時間。</p>
      <p class="small">排班時間 ${esc(j.start)}–${esc(j.end)}；請填<b>實際</b>幾點開始、幾點做完。</p>
      <label for="bst">實際開始</label><input id="bst" type="time" value="">
      <label for="ben">實際結束</label><input id="ben" type="time" value="">
      <label for="bpa">休息（分鐘，沒有填 0）</label><input id="bpa" type="number" min="0" inputmode="numeric" value="0">
      <p class="small" style="margin-top:10px">有拍完成照片可以一起傳（已傳 ${j.photos.after} 張）</p><label class="photobtn">＋ 完成照片<input type="file" accept="image/*" multiple hidden data-label="after"></label>
      <label for="brep">今天的狀況（可空白）</label><textarea id="brep" rows="2"></textarea>
      <button class="btn full" data-a="backfill" style="margin-top:12px">送出補登</button></details></div>`;
  }
  if (j.no_photo) h += step(2, '這個案場不能拍照', true, '<p class="small">業主規定場內不能拍照：不用拍服務前、完成照片，做完直接離場打卡（薪資照常）。</p>');      // 例：客戶03
  else if (j.forgot && !j.photos.before) h += step(2, '服務前照片（已說明忘了拍）', true, `<p class="small">原因：${esc(j.forgot)}。完成照片照常拍；這場薪資等管理層確認後發放。</p>`);      // 
  else h += step(2, '服務前照片', j.photos.before > 0, `${arrived && !j.photos.before ? '<p style="font-weight:700;margin:0 0 6px">開始清之前先拍還沒清的樣子 👇 可以完工時跟完成照片一起上傳</p><p class="small">場內不能拍照？在下面「現場有狀況」選「📵 這裡不能拍照」。</p>' : ''}<p class="small">每個要服務的區域都拍一張（已傳 ${j.photos.before} 張，最多 50 張）</p><p class="small" style="color:#8A5A00">📷 前後照標準：同角度、同部位、同空間；前後對比要明顯（不明顯也要拍）；不能模糊。<b>有服務前、完成照片才會發放薪資。</b></p>${arrived ? `<label class="photobtn">＋ 拍照／選照片<input type="file" accept="image/*" multiple hidden data-label="before"></label>` : ''}${j.forgot_ok && arrived && !j.photos.before ? `<details style="margin-top:8px"><summary class="small">已經清完、忘了拍服務前照片？</summary><label for="fgr">原因</label><textarea id="fgr" rows="2" placeholder="例：一到就先處理漏水，忘了拍"></textarea><button class="btn full sec" data-a="forgot" style="margin-top:8px">送出原因</button><p class="small">送出後可以照常拍完成照片、離場打卡；這場薪資等管理層確認後發放。</p></details>` : ''}`);
  if (j.signin_need) h += step('簽', '簽到表照片', j.photos.signin > 0, `<p class="small">這個案場要拍簽到表（跟服務前後照分開，不會傳給業主）。已傳 ${j.photos.signin} 張。</p>${arrived ? `<label class="photobtn">＋ 拍簽到表<input type="file" accept="image/*" multiple hidden data-label="signin"></label>` : '<p class="small">到場打卡後就能上傳。</p>'}`);      // 
  if (!j.no_photo) h += step(3, '完成照片', j.photos.after > 0, `<p class="small">做完每個區域再拍一張；離場打卡後會自動傳給業主（已傳 ${j.photos.after} 張，最多 50 張）。上傳前會讓你再看一次，誤選的私人照片點掉就好。</p>${arrived && (j.photos.before || j.forgot) ? `<label class="photobtn">＋ 拍照／選照片<input type="file" accept="image/*" multiple hidden data-label="after"></label>` : arrived ? '<p class="small">先上傳服務前照片，這裡就會打開（可以完工時一起傳）。</p>' : ''}
    ${arrived ? `<p class="small" style="margin-top:10px">有損壞、異常？</p><label class="photobtn">⚠️ 拍異常照片<input type="file" accept="image/*" multiple hidden data-label="incident"></label>` : ''}`);
  const forgot = arrived && !left && j.date < j.today;      // 忘了離場打卡 → 補登
  h += step(4, '完工回報・離場打卡', false, arrived ? (forgot ? `<div class="banner warn">忘了離場打卡嗎？請填實際離場時間補登（示範服務店會確認工時）。</div><label for="endt">實際離場時間</label><input id="endt" type="time" value="${esc(j.end)}">` : '') + ((j.routine || []).length && !forgot ? `<div class="banner warn" style="margin-bottom:10px"><b>這次要做的定期項目</b>：做了請逐項勾；沒做到的要寫原因（例：拆不下來、業主說不用），才能離場打卡。${j.routine.map((t, i) => `<label style="display:flex;gap:8px;align-items:flex-start;margin-top:8px"><input type="checkbox" class="rtck" value="${i}" style="margin-top:4px"> <span>${esc(t[2])}<br><span class="small">${esc(t[1])}</span></span></label>`).join('')}<label for="rtwhy" style="margin-top:10px">沒做到的原因（全部勾就不用寫）</label><textarea id="rtwhy" rows="2"></textarea></div>` : '')
    + (j.signin_need && !j.photos.signin ? '<div class="banner warn">這個案場要拍簽到表，還沒拍喔！請先在上面「簽到表照片」拍好再離場。</div>' : '')      // 
    + `<label for="rep">今天的狀況（缺用品、客戶交代、損壞⋯，沒有可空白）</label><textarea id="rep" rows="3"></textarea>
    <button class="btn full" data-a="leave" style="margin-top:12px"${j.photos.after || j.no_photo ? '' : ' disabled'}>完工・離場打卡</button>${j.photos.after || j.no_photo ? '' : `<div class="banner warn" style="margin-top:8px">還缺：${!j.photos.before && !j.forgot ? '服務前照片、' : ''}完成照片。傳好就能離場打卡。<a href="#" data-goto=".wstep:not(.done) .photobtn">去拍 ›</a></div>`}` : '<p class="small">到場打卡後才能完工。</p>');
  if (arrived && !left && today) h += reportBox(j);
  h += '<div id="err" class="err" role="alert"></div><p id="prog" class="small"></p>';
  $('#app').innerHTML = h; syncRep();
  WDRAFT.restore();      // 寫到一半去傳照片、離開再回來，文字不會不見
}
/* 現場回報：掃不完要加時、趕不上下一場、現場跟預期不一樣、損壞、其他 → 派單管理群可點的卡，處理好 LINE 會回覆 */
function reportBox(j) {
  const kinds = [['extra', '⏱ 需要加時（掃不完）'], ...(j.next_job ? [['late', `🚗 下一場（${j.next_job}）會晚到`]] : []), ['scope', '🧹 現場跟預期不一樣'], ['damage', '⚠️ 有東西損壞'], ...(j.no_photo ? [] : [['nophoto', '📵 這裡不能拍照（這一場直接放行）']]), ['other', '💬 其他狀況']];
  const done = (j.reports || []).map(r => `<li>${esc(r.at)} ${esc(r.kind)}・${r.status === 'handled' ? '✅ ' + esc(r.resolution) : '等管理層處理'}</li>`).join('');
  return `<div class="card"><details id="repbox"${(j.reports || []).some(r => r.status !== 'handled') ? ' open' : ''}><summary><b>現場有狀況？按這裡回報管理層</b></summary>
    ${done ? `<ul class="small" style="margin:8px 0 0;padding-left:18px">${done}</ul>` : ''}
    <div class="chips" style="margin-top:10px;display:flex;flex-direction:column;gap:6px">${kinds.map(([k, t]) => `<label><input type="radio" name="rk" value="${k}"> ${t}</label>`).join('')}</div>
    <div id="rx-min" hidden><label for="rmin">大約還需要多久？</label><select id="rmin"><option value="30">30 分鐘</option><option value="60">1 小時</option><option value="90">1.5 小時</option><option value="120">2 小時</option></select>
      <p class="small" style="margin:10px 0 4px">業主知道要加時嗎？</p><div class="chips" style="display:flex;flex-direction:column;gap:6px"><label><input type="radio" name="rown" value="y"> 業主知道、同意</label><label><input type="radio" name="rown" value="n"> 業主還不知道</label></div></div>
    <div id="rx-late" hidden><label for="rlate">下一場大約晚多久？</label><select id="rlate"><option value="15">15 分鐘</option><option value="30">30 分鐘</option><option value="60">1 小時</option><option value="90">1.5 小時</option></select></div>
    <label for="rnote" id="rnote-l">說明（選填）</label><textarea id="rnote" rows="2" maxlength="500"></textarea>
    <p class="small">有照片：上面「拍異常照片」。</p>
    <button class="btn full" data-a="report" style="margin-top:8px">送出回報</button></details></div>`;
}
function syncRep() {
  const k = (document.querySelector('input[name=rk]:checked') || {}).value || '';
  if (!$('#rx-min')) return;
  $('#rx-min').hidden = k !== 'extra'; $('#rx-late').hidden = k !== 'late';
  $('#rnote-l').textContent = ['scope', 'damage', 'other'].includes(k) ? '說明（必填：什麼狀況）' : '說明（選填）';
}
document.addEventListener('change', e => { if (e.target.name === 'rk') syncRep(); });
document.addEventListener('click', e => { const g = e.target.closest('[data-goto]'); if (!g) return; e.preventDefault(); const el = document.querySelector(g.dataset.goto); if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' }); });
const pos = () => new Promise(ok => { if (!navigator.geolocation) return ok({}); navigator.geolocation.getCurrentPosition(p => ok({ lat: p.coords.latitude, lng: p.coords.longitude }), () => ok({}), { timeout: 8000, enableHighAccuracy: true }); });
const rawUrl = file => new Promise((ok, bad) => {          // 瀏覽器解不開（HEIC、超大張）→ 傳原檔，伺服器轉 JPEG
  if (file.size > 10 * 1024 * 1024) return bad(new Error('照片太大（超過 10MB）'));
  const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = () => bad(new Error('照片讀不到')); r.readAsDataURL(file);
});
async function prep(file) { try { return await shrink(file); } catch (e) { return await rawUrl(file); } }
function shrink(file) {
  return new Promise((ok, bad) => { const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => { const k = Math.min(1, 1600 / Math.max(img.width, img.height)), cv = document.createElement('canvas'); cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k); cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height); URL.revokeObjectURL(url); ok(cv.toDataURL('image/jpeg', 0.82)); };
    img.onerror = () => { URL.revokeObjectURL(url); bad(new Error('照片讀不到，請換一張')); }; img.src = url; });
}
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-a]'); if (!b || BUSY) return;
  BUSY = true; b.disabled = true; $('#err').textContent = '';
  try {
    if (b.dataset.a === 'door') { const r = await call('door', {}); alert(r.message || '已通知業主'); b.disabled = false; return; }
    if (b.dataset.a === 'forgot') { const why = ($('#fgr') || {}).value || ''; if (!why.trim()) throw new Error('請寫一下原因'); const r = await call('forgot', { reason: why }); alert(r.message || '已記錄'); }
    if (b.dataset.a === 'door_in') { const r = await call('door_in', {}); alert(r.message || '收到'); b.disabled = false; return; }
    if (b.dataset.a === 'backfill') { const st = $('#bst').value, en = $('#ben').value; if (!st || !en) throw new Error('請填實際開始和結束時間'); if (!confirm(`補登 ${st}–${en}？`)) throw new Error('已取消'); const r = await call('backfill', { start: st, end: en, pause: +($('#bpa').value || 0), report: $('#brep').value || '' }); alert(r.message || '已補登'); }
    if (b.dataset.a === 'report') {
      const kind = (document.querySelector('input[name=rk]:checked') || {}).value; if (!kind) throw new Error('請先選是什麼狀況');
      const body = { kind, note: $('#rnote').value.trim() };
      if (kind === 'extra') { body.minutes = +$('#rmin').value; body.own = (document.querySelector('input[name=rown]:checked') || {}).value || ''; if (!body.own) throw new Error('請選業主知不知道要加時'); }
      if (kind === 'late') body.minutes = +$('#rlate').value;
      if (['scope', 'damage', 'other'].includes(kind) && !body.note) throw new Error('請寫一下是什麼狀況');
      if (kind === 'nophoto' && !confirm('回報「這裡不能拍照」？這一場就不用拍服務前／完成照片，可以直接離場打卡。')) throw new Error('已取消');
      const r = await call('report', body); WDRAFT.clear(); alert(r.message || '已通知管理層');
    }
    if (b.dataset.a === 'arrive') { if (J.ack_need && !($('#ack') || {}).checked) throw new Error('請先勾選「我看過這場的現場須知」'); b.textContent = '定位中…請稍等'; const p = await pos(); p.ack = J.ack_need ? 1 : 0; if (!p.lat && !confirm('抓不到定位，還是要打卡嗎？（會標示「沒定位」）')) throw new Error('已取消'); await call('arrive', p); }
    if (b.dataset.a === 'leave') { if (!confirm('確定完工、離場打卡？完成照片會自動傳給業主。')) throw new Error('已取消'); WDRAFT.save(); await call('leave', { ...(await pos()), report: ($('#rep') || {}).value || '', rt_done: [...document.querySelectorAll('.rtck:checked')].map(x => x.value), rt_why: ($('#rtwhy') || {}).value || '', no_before: ($('#nobf') || {}).checked ? 1 : 0, ...($('#endt') ? { end_time: $('#endt').value } : {}) }); }
    if (['backfill', 'leave'].includes(b.dataset.a)) WDRAFT.clear();
    await load();
  } catch (x) { if (x.message !== '已取消') $('#err').textContent = x.message; b.disabled = false; } finally { BUSY = false; }
});
/* 上傳前先讓夥伴看一次選到的照片，點掉誤選的私人照片再上傳。一次可以選很多張（服務前、完工各最多 50 張）；
   縮圖一張一張做（240px），手機選 50 張也不會吃光記憶體；上傳也是一張一張傳，一張失敗不影響其他張。 */
const CAP = { before: 50, after: 50, incident: 20, signin: 5 }, LBL = { before: '服務前', after: '完工', incident: '異常', signin: '簽到表' };
(() => { const s = document.createElement('style'); s.textContent = `.pkbg{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:50;display:flex;align-items:flex-end;justify-content:center}
.pk{background:#fff;width:100%;max-width:560px;max-height:92vh;border-radius:20px 20px 0 0;padding:18px 16px calc(16px + env(safe-area-inset-bottom));display:flex;flex-direction:column;box-sizing:border-box}
.pk h3{margin:0 0 4px}.pkg{display:grid;grid-template-columns:repeat(3,1fr);grid-auto-rows:max-content;align-content:start;gap:6px;overflow-y:auto;margin:10px 0;flex:1 1 auto;min-height:120px}
.pkit{position:relative;display:block;width:100%;height:0;border:0;padding:0 0 100%;border-radius:10px;overflow:hidden;background:#EEE;cursor:pointer}
.pkit img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}.pkit .pkno{position:absolute;left:6px;bottom:4px;color:#fff;font-size:12px;text-shadow:0 1px 2px #000}
.pkit .pkc{position:absolute;top:6px;right:6px;width:26px;height:26px;border-radius:50%;background:#2E7D32;color:#fff;font-weight:700;display:grid;place-items:center;font-size:15px;border:2px solid #fff;box-sizing:border-box}
.pkit.off img{opacity:.3;filter:grayscale(1)}.pkit.off .pkc{background:rgba(255,255,255,.9);color:transparent;border-color:#999}
.pkbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.pkbar .pkn{flex:1;font-weight:700}.pkbar .btn{padding:6px 12px;font-size:14px}
.pkact{display:flex;gap:10px;margin-top:10px}.pkact .btn{flex:1}`; document.head.appendChild(s); })();
function thumb(file) {
  return new Promise(ok => { const img = new Image(), u = URL.createObjectURL(file);
    img.onload = () => { const k = Math.min(1, 240 / Math.max(img.width, img.height)), cv = document.createElement('canvas'); cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k); cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height); URL.revokeObjectURL(u); ok(cv.toDataURL('image/jpeg', 0.7)); };
    img.onerror = () => { URL.revokeObjectURL(u); ok(''); }; img.src = u; });
}
function pickReview(files, label) {
  return new Promise(done => {
    const left = Math.max(0, CAP[label] - (((J || {}).photos || {})[label] || 0)), on = files.map((_, i) => i < left);
    const box = document.createElement('div'); box.className = 'pkbg'; let closed = false;
    box.innerHTML = `<div class="pk" role="dialog" aria-modal="true" aria-label="確認要上傳的照片"><h3>確認要上傳的${LBL[label]}照片</h3>
      <p class="small" style="margin:0">先看一下有沒有誤選到自己的私人照片 🙏 <b>點照片可以取消勾選</b>，按「確認上傳」才會傳出去。</p>
      ${files.length > left ? `<p class="small" style="color:#C62828;margin:6px 0 0">${LBL[label]}照片最多 ${CAP[label]} 張，已經傳了 ${CAP[label] - left} 張，這次最多再傳 ${left} 張，多的先幫你取消勾選。</p>` : ''}
      <div class="pkg">${files.map((_, i) => `<button type="button" class="pkit" data-i="${i}" aria-label="第 ${i + 1} 張" aria-pressed="${on[i]}"><img alt=""><span class="pkno">${i + 1}</span><span class="pkc">✓</span></button>`).join('')}</div>
      <div class="pkbar"><span class="pkn"></span><button type="button" class="btn sec" data-pk="all">全選</button><button type="button" class="btn sec" data-pk="none">全部取消</button></div>
      <div class="pkact"><button type="button" class="btn sec" data-pk="no">取消</button><button type="button" class="btn" data-pk="ok"></button></div></div>`;
    const paint = () => { const n = on.filter(Boolean).length;
      box.querySelector('.pkn').textContent = `已選 ${n}／${files.length} 張`;
      const go = box.querySelector('[data-pk=ok]'); go.textContent = !n ? '請至少選 1 張' : n > left ? `最多再傳 ${left} 張` : `確認上傳 ${n} 張`; go.disabled = !n || n > left;
      box.querySelectorAll('.pkit').forEach((el, i) => { el.classList.toggle('off', !on[i]); el.setAttribute('aria-pressed', on[i]); }); };
    const end = r => { closed = true; box.remove(); document.body.style.overflow = ''; document.removeEventListener('keydown', esc1); done(r); };
    const esc1 = e => { if (e.key === 'Escape') end([]); };
    box.addEventListener('click', e => { const it = e.target.closest('.pkit'); if (it) { on[+it.dataset.i] = !on[+it.dataset.i]; return paint(); }
      const b = e.target.closest('[data-pk]'); if (!b) return;
      if (b.dataset.pk === 'all') { on.forEach((_, i) => { on[i] = i < left; }); return paint(); }
      if (b.dataset.pk === 'none') { on.fill(false); return paint(); }
      if (b.dataset.pk === 'no') return end([]);
      if (b.dataset.pk === 'ok') return end(files.filter((_, i) => on[i])); });
    document.addEventListener('keydown', esc1);
    document.body.appendChild(box); document.body.style.overflow = 'hidden'; paint();
    (async () => { for (let i = 0; i < files.length && !closed; i++) { const t = await thumb(files[i]); const el = box.querySelector(`.pkit[data-i="${i}"] img`); if (el) { if (t) el.src = t; else el.alt = '讀不到'; } } })();
  });
}
document.addEventListener('change', async e => {
  const inp = e.target.closest('input[type=file][data-label]'); if (!inp || BUSY) return;
  const label = inp.dataset.label, picked = [...inp.files]; inp.value = ''; if (!picked.length) return;
  BUSY = true; $('#err').textContent = '';
  const files = await pickReview(picked, label);
  if (!files.length) { BUSY = false; $('#prog').textContent = '已取消，沒有上傳'; return; }
  let ok = 0; const bad = [];
  try {
    for (let i = 0; i < files.length; i++) {          // 一張失敗不要整批停：跳過，最後告訴他哪幾張
      $('#prog').textContent = `上傳中 ${i + 1}/${files.length}…（請不要關掉這一頁）`;
      try { await call('photo', { label, content: await prep(files[i]) }); ok++; }
      catch (x) { if (/上限|取消|失效|最多/.test(x.message)) throw x; bad.push(`第 ${i + 1} 張（${x.message}）`); }
    }
    if (ok) await call('photos_done', { label });
    if (ok) await load();                              // load() 會重畫頁面，訊息要畫完再寫（以前一閃就不見）
    $('#prog').textContent = ok ? `✅ 已上傳 ${ok} 張，示範服務店看得到了` : ''; setTimeout(() => { const p = $('#prog'); if (p && p.textContent.startsWith('✅')) p.textContent = ''; }, 5000);
    if (bad.length) $('#err').textContent = `${bad.length} 張沒傳到：${bad.join('、')}。可以換一張或用相機重拍。`;
  } catch (x) { const msg = (ok ? `已上傳 ${ok} 張。` : '') + x.message; if (ok) await load().catch(() => {}); $('#err').textContent = msg; } finally { BUSY = false; }
});
load();
