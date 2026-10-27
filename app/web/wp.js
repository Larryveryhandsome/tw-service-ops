'use strict';
/* 夥伴主控板（LINE 選單打開）：邀請中的工作（接受／不行）、我的班表（點進工作頁打卡拍照）、回報可排時間。 */
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const T = location.pathname.split('/').pop(), WD = '日一二三四五六';
const md = d => `${+d.slice(5, 7)}/${+d.slice(8, 10)}（${WD[new Date(d + 'T00:00').getDay()]}）`;
let P = null, MONTH = '', AV = {}, AVDIRTY = new Set(), BUSY = false;      // 改了哪幾個月（換月也不會漏存）、處理中鎖
const AV_AM = ['08:00', '13:00'], AV_PM = ['13:00', '18:00'];          // 一天分上午／下午
const avTxt = s => s.kind === 'busy' ? '不行' : s.kind === 'unsure' ? '不確定' : !s.start ? '整天' : s.start === AV_AM[0] && s.end === AV_AM[1] ? '上午' : s.start === AV_PM[0] && s.end === AV_PM[1] ? '下午' : s.start + '–' + s.end;
const avNext = s => !s || !s.kind ? { kind: 'free', start: '', end: '' } : s.kind === 'busy' ? { kind: 'unsure', start: '', end: '' } : s.kind === 'unsure' ? undefined
  : !s.start ? { kind: 'free', start: AV_AM[0], end: AV_AM[1] } : s.start === AV_AM[0] && s.end === AV_AM[1] ? { kind: 'free', start: AV_PM[0], end: AV_PM[1] } : { kind: 'busy', start: '', end: '' };
async function call(act, body) {
  let r; try { r = await fetch(`/api/public/portal/${T}/${act}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }); } catch (e) { throw new Error('網路不穩，沒有送出。請到訊號好的地方再按一次。'); }
  const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '失敗'); return v;
}
const WPDRAFT = Draft.attach('wp:' + T, $('#app'));
async function load() {
  let r; try { r = await fetch('/api/public/portal/' + T); } catch (e) { $('#app').innerHTML = `<div class="card"><h2 style="margin-top:0">網路不穩，打不開</h2><p class="small">請到訊號好的地方再試一次。</p><button class="btn full" onclick="location.reload()">重新整理</button></div>`; return; }
  const v = await r.json().catch(() => ({}));
  if (!r.ok) { $('#app').innerHTML = `<div class="card"><h2 style="margin-top:0">${esc(v.error || '連結無效')}</h2><button class="btn full" onclick="location.reload()" style="margin-top:8px">重新整理</button></div>`; return; }
  P = v; MONTH = MONTH || v.today.slice(0, 7); AV = {}; AVDIRTY = new Set(); v.availability.forEach(a => AV[a.date] = { kind: a.kind, start: a.start || '', end: a.end || '' }); draw();
  if (location.hash && !window._scrolled) { window._scrolled = 1; const el = document.getElementById(location.hash.slice(1)); if (el) setTimeout(() => el.scrollIntoView({ block: 'start' }), 50); }      // 選單「我的收入」「可排時間」直接跳到那一段
}
function draw() {
  if ($('#prReg') && P.profile) { P.profile.regions = $('#prReg').value; P.profile.skills = $('#prSkill').value; P.profile.availability = $('#prAv').value; }
  if ($('#pfIntro') && P.portfolio) {          // 切換月曆會重畫：先保留還沒儲存的作品集輸入
    P.portfolio.intro = $('#pfIntro').value; P.portfolio.since = $('#pfSince').value;
    const on = new Set($$('.pfpick').filter(x => x.checked).map(x => x.value)); P.portfolio.photos.forEach(x => x.public = on.has(x.id));
  }
  const inv = P.jobs.filter(j => j.status === 'invited'), mine = P.jobs.filter(j => j.status === 'accepted').sort((a, b) => (b.backfill - a.backfill) || 0);
  let h = `<h1 class="headline" style="font-size:32px">${esc(P.name)}，你好</h1><p class="sub">${P.status === 'active' ? '你的工作、班表、可排時間都在這裡。' : '你的資料示範服務店還在確認中，確認後就會開始派工給你。'}</p>`;
  h += `<p class="small" style="margin-top:-6px"><a href="/wguide${P.brand && P.brand !== 'demoshop' ? '?b=' + P.brand : ''}">📖 使用教學（圖文）›</a></p>`;      // 
  h += `<h2>邀請中 <span class="g">${inv.length}</span></h2>` + (inv.map(j => `<div class="card"><b>${md(j.date)} ${j.start}–${j.end}</b><div class="small">${esc(j.area)}・${esc(j.service_type)}</div>
      ${j.pay ? `<div style="margin-top:6px;font-weight:700">這場收入：${esc(j.pay)}</div>` : ''}
      ${j.scope ? `<div class="small" style="margin-top:6px">範圍：${esc(j.scope.split(/\n/).join('、'))}</div>` : ''}
      <div class="btns" style="margin-top:10px"><button class="btn" data-a="accept" data-o="${j.oid}">我要接</button><button class="btn gray" data-a="decline" data-o="${j.oid}">這次不行</button></div></div>`).join('') || '<div class="empty">目前沒有新的邀請</div>');
  h += `<h2>我的班表 <span class="g">${mine.length}</span></h2>` + (mine.map(j => `<a class="card" style="display:block;text-decoration:none;color:inherit" href="${esc(j.url)}">
      <b>${md(j.date)} ${j.start}–${j.end}</b> ${j.redo ? '<span class="pill warn">🔁 補清</span>' : ''}${j.backfill ? '<span class="pill bad">⚠ 待補登打卡</span>' : j.date === P.today ? '<span class="pill warn">今天</span>' : ''} ${j.order_status === 'confirmed' ? '<span class="pill ok">已排定</span>' : '<span class="pill">等業主確認</span>'}
      <div class="small">${esc(j.area)}・${esc(j.service_type)}${j.pay ? `・<b>${esc(j.pay)}</b>` : ''}</div><div class="small" style="margin-top:6px;color:var(--blue,#0071e3)">${j.backfill ? '打開補登實際時間 ›' : '打開工作頁（打卡・拍照）›'}</div></a>`).join('') || '<div class="empty">還沒有排班</div>');
  h += `<h2 id="income">我的收入</h2><div class="card">` + (P.income || []).map(x => `<div style="display:flex;justify-content:space-between;align-items:baseline;padding:8px 0;border-bottom:1px solid #eee">
      <div><b>${+x.month.slice(5)} 月</b><div class="small">已完工 ${x.jobs} 場・約 ${x.hours} 小時・${esc(x.status)}${x.pending_jobs ? `<br>另有 ${x.pending_jobs} 場已排定未完工，預估 $${x.pending_pay.toLocaleString()}` : ''}</div></div><div style="font-size:20px;font-weight:700">$${x.pay.toLocaleString()}${x.partial ? '<span class="small">＋未定</span>' : ''}</div></div>`).join('')
    + '<p class="small" style="margin:8px 0 0">跟示範服務店月底結算同一套算法（已完工＋取消補貼；沒打卡的依排班時間），實際以結算單為準。</p></div>';
  const [y, m] = MONTH.split('-').map(Number), first = new Date(y, m - 1, 1), days = new Date(y, m, 0).getDate();
  h += `<h2 id="avail">可排時間</h2><div class="card"><div class="row" style="display:flex;justify-content:space-between;align-items:center"><button class="btn sm gray" data-m="-1">‹</button><b>${y} 年 ${m} 月</b><button class="btn sm gray" data-m="1">›</button></div>
    <p class="small">點日期切換：<span class="pill ok">整天</span> → <span class="pill ok">上午</span>（08–13 點）→ <span class="pill ok">下午</span>（13–18 點）→ <span class="pill bad">不行</span> → <span class="pill warn">不確定</span> → 未回報。回報後，派工會優先找有空的人；只填上午的，下午的工作不會找你。</p><div class="avcal">`
    + [...WD].map(d => `<div class="h">${d}</div>`).join('') + '<div></div>'.repeat(first.getDay());
  for (let d = 1; d <= days; d++) { const k = `${MONTH}-${String(d).padStart(2, '0')}`, s = AV[k] || {}; h += `<button class="avd ${s.kind === 'free' ? 'free' : s.kind === 'busy' ? 'busy' : s.kind === 'unsure' ? 'unsure' : ''}" data-d="${k}"${k < P.today ? ' disabled' : ''}>${d}${s.kind ? `<small style="display:block;font-size:max(12px,.75em);line-height:1.1">${avTxt(s)}</small>` : ''}</button>`; }
  h += `</div><div class="btns" style="margin-top:10px"><button class="btn sm" data-all="free">整個月都可以</button><button class="btn sm gray" data-all="unsure">整個月都不確定</button><button class="btn sm gray" data-all="">清除</button></div>
    <label class="small" for="avNote" style="display:block;margin-top:12px">其他說明（選填，例：11/13 之後每週五校舍路不接）</label><textarea id="avNote" maxlength="200" rows="2" style="width:100%">${esc(P.av_note || '')}</textarea>
    ${AVDIRTY.size ? `<div class="banner warn" style="margin-top:10px">${[...AVDIRTY].sort().map(x => +x.slice(5) + ' 月').join('、')}改了還沒儲存，請按下面「儲存」</div>` : ''}<button class="btn full" data-a="saveav" style="margin-top:14px">儲存可排時間</button><div id="err" class="err"></div></div>`;      // 一鍵整月、填空說明
  const pr = P.profile || {};
  const chips = (id, opts, val) => `<div class="chips" id="${id}">${opts.map(t => `<label><input type="checkbox" value="${t}"${(val || '').split('、').includes(t) ? ' checked' : ''}> ${t}</label>`).join('')}</div>`;      // 勾選題
  const toolsOther = ((pr.tools_own || '').split('、').find(x => x.startsWith('其他：')) || '').slice(3);
  h += `<h2 id="me">我的資料</h2><div class="card"><p class="small" style="margin-top:0">為了更了解你的狀況、安排最適合你的工作，請把下面每一題都填好（都是必填）。電話 ${esc(pr.phone || '（未填）')}，要改請在 LINE 跟示範服務店說。</p>
    <label class="small">可以服務的地點（例如：大安區、板橋區、中壢區）*</label><input id="prReg" maxlength="400" value="${esc(pr.regions || '')}" style="width:100%;box-sizing:border-box">
    <label class="small">平常可以工作的時間（簡單寫就好，系統會自動分析；例如：平日白天、週末都可、13:00 後）*</label><input id="prAv" maxlength="100" value="${esc(pr.availability || '')}" style="width:100%;box-sizing:border-box">
    <label class="small">會做的服務類型 *</label>${chips('prSkill', ['一般到府服務', '年度保養／細清', '店家服務', '店面／辦公室服務', '大型案場服務', '搬遷前後服務', '交屋前整理', '進階專案', '無經驗'], pr.skills)}
    <label class="small">自備的工具（勾選一次就好，派工會對照現場缺什麼）*</label>${chips('prTools', ['基本工具組', '專業工具', '量測工具', '延長線', '手機／平板', '耗材', '防護用品', '推車', '收納箱', '都沒有'], pr.tools_own)}
    <input id="prToolsOther" maxlength="60" value="${esc(toolsOther)}" placeholder="其他工具（選填，例：蒸氣清洗機）" style="width:100%;box-sizing:border-box;margin-top:6px">
    <label class="small">交通工具 *</label>${chips('prTrans', ['汽車', '機車', '都沒有'], (pr.transport || '').replace(/汽車（可(少量清運|載少量垃圾)）/, '汽車'))}
    <label style="display:block;margin:4px 0 0"><input type="checkbox" id="prHaul"${/可少量清運|可載少量垃圾/.test(pr.transport || '') ? ' checked' : ''}> 開汽車的話：可以順便載運少量物品</label>
    <label class="small">其他可支援項目*</label>${chips('prButler', ['行政文書', '客服接待', '活動支援', '無經驗'], pr.butler_skills)}
    <label class="small">陪玩員（會做的）*</label>${chips('prPlay', ['遊戲設計', '繪本共讀', '故事書陪讀', '戶外活動', '創意手做', '外語陪讀', '無經驗'], pr.play_skills)}
    <label class="small">接單意願（簡單寫：例如一週想接幾天、哪些不想接）*</label><input id="prWill" maxlength="100" value="${esc(pr.willing || '')}" style="width:100%;box-sizing:border-box">
    <label class="small">住哪裡（鄉鎮＋路名就好，不用門牌；派工會優先找離你近的）</label><input id="prHome" maxlength="60" value="${esc(pr.home || '')}" placeholder="例：大安區中山路" style="width:100%;box-sizing:border-box">
    <label class="small">薪資匯款帳號（填一次就好，結算會自動帶入；不是示範銀行會扣跨行手續費 15 元）</label>
    <input id="prBank" maxlength="40" value="${esc(pr.bank_name || '')}" placeholder="銀行（例：示範銀行、郵局 700）" style="width:100%;box-sizing:border-box">
    <input id="prAcct" maxlength="30" inputmode="numeric" value="${esc(pr.bank_account || '')}" placeholder="帳號" style="width:100%;box-sizing:border-box;margin-top:6px">
    <input id="prHolder" maxlength="30" value="${esc(pr.bank_holder || '')}" placeholder="戶名" style="width:100%;box-sizing:border-box;margin-top:6px">
    <label class="small">薪資怎麼領</label><select id="prCycle"><option value="month"${(pr.payout_cycle || 'month') === 'month' ? ' selected' : ''}>月領</option><option value="week"${pr.payout_cycle === 'week' ? ' selected' : ''}>週領</option></select>
    <label class="small">性別（業主有指定時才會參考）</label><select id="prGender">${['', '女', '男', '不想說'].map(x => `<option${x === (pr.gender || '') ? ' selected' : ''}>${x}</option>`).join('')}</select>
    <button class="btn full" data-a="saveprof" style="margin-top:14px">儲存我的資料</button></div>`;
  const pf = P.portfolio || { photos: [] };
  h += `<h2 id="pf">我的作品集</h2><div class="card"><p class="small" style="margin-top:0">客戶看不到你的名字，只會看到「夥伴 A」＋你的介紹、年資和你挑的照片（${pf.jobs || 0} 場服務紀錄會自動算）。<b>請挑看不出是哪位客戶家的照片</b>（不要有門牌、人臉、家人照片、信件）。</p>
    <label class="small">自我介紹（例如擅長的項目、相關證照或經驗）</label><textarea id="pfIntro" rows="3" maxlength="300" style="width:100%;box-sizing:border-box">${esc(pf.intro || '')}</textarea>
    <label class="small">哪一年開始做服務（西元，例如 2018）</label><input id="pfSince" inputmode="numeric" maxlength="4" value="${esc(pf.since || '')}" style="width:100%;box-sizing:border-box">
    <p class="small" style="margin-bottom:4px">點照片選「公開」（最多 12 張，打勾的客戶看得到）：</p>
    <div class="pfg" style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px">${pf.photos.map(x => `<label style="position:relative;display:block"><img src="${esc(x.url)}" loading="lazy" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:10px"><span class="small" style="position:absolute;left:6px;bottom:6px;background:#fff;border-radius:8px;padding:2px 6px"><input type="checkbox" class="pfpick" value="${esc(x.id)}"${x.public ? ' checked' : ''}> ${esc(x.label)}</span></label>`).join('') || '<div class="empty" style="grid-column:1/-1">做完工作拍的「服務前／完工」照片會出現在這裡。</div>'}</div>
    <button class="btn full" data-a="savepf" style="margin-top:14px">儲存作品集</button></div>`;
  $('#app').innerHTML = h;
  WPDRAFT.restore();      // 填到一半離開不會不見
}
document.addEventListener('click', async e => {
  const nt = $('#avNote'); if (nt) P.av_note = nt.value;          // 換月、點日期會重畫：先記住打到一半的說明
  const al = e.target.closest('[data-all]');
  if (al) { const [y, m] = MONTH.split('-').map(Number), n = new Date(y, m, 0).getDate();
    for (let i = 1; i <= n; i++) { const k = `${MONTH}-${String(i).padStart(2, '0')}`; if (k >= P.today) AV[k] = al.dataset.all ? { kind: al.dataset.all, start: '', end: '' } : undefined; }
    AVDIRTY.add(MONTH); draw(); return; }
  const d = e.target.closest('[data-d]'); if (d) { if (BUSY) return; AV[d.dataset.d] = avNext(AV[d.dataset.d]); AVDIRTY.add(d.dataset.d.slice(0, 7)); draw(); return; }
  const mm = e.target.closest('[data-m]'); if (mm) { const [y, m] = MONTH.split('-').map(Number), n = new Date(y, m - 1 + Number(mm.dataset.m), 1); MONTH = `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}`; draw(); return; }
  const b = e.target.closest('[data-a]'); if (!b || BUSY) return;
  if (b.dataset.a === 'decline' && !confirm('這次不接這場？按了就會讓給其他夥伴。')) return;      // 怕誤按
  BUSY = true; b.disabled = true;
  try {
    if (b.dataset.a === 'saveprof') {      // 每一題都必填
      const ck = id => [...document.querySelectorAll(`#${id} input:checked`)].map(x => x.value);
      const other = $('#prToolsOther').value.trim(), tools = ck('prTools').concat(other ? ['其他：' + other] : []);
      const trans = ck('prTrans').map(x => x === '汽車' && $('#prHaul').checked ? '汽車（可載少量垃圾）' : x);      // 不寫「清運」
      const f = { regions: $('#prReg').value.trim(), availability: $('#prAv').value.trim(), skills: ck('prSkill').join('、'), tools_own: tools.join('、'), transport: trans.join('、'),
        butler_skills: ck('prButler').join('、'), play_skills: ck('prPlay').join('、'), willing: $('#prWill').value.trim(), gender: $('#prGender').value, home: $('#prHome').value,
        bank_name: $('#prBank').value.trim(), bank_account: $('#prAcct').value.trim(), bank_holder: $('#prHolder').value.trim(), payout_cycle: $('#prCycle').value };
      const miss = [['regions', '可以服務的地點'], ['availability', '可以工作的時間'], ['skills', '服務類型'], ['tools_own', '自備工具'], ['transport', '交通工具'], ['butler_skills', '居家管家'], ['play_skills', '陪玩員'], ['willing', '接單意願']].filter(([k]) => !f[k]).map(x => x[1]);
      if (miss.length) { b.disabled = false; return alert('還沒填：' + miss.join('、') + '（都是必填，沒經驗的勾「無經驗」）'); }      // 漏填：按鈕要能再按
      await call('profile', f); alert('已儲存，謝謝你 🙏');
    }
    else if (b.dataset.a === 'savepf') {
      const photos = $$('.pfpick').filter(x => x.checked).map(x => x.value);
      if (photos.length > 12) throw new Error('最多選 12 張');
      await call('portfolio', { intro: $('#pfIntro').value, since: $('#pfSince').value.trim(), photos }); alert('已儲存作品集');
    }
    else if (b.dataset.a === 'saveav') { const ms = [...new Set([...AVDIRTY, MONTH])].sort();      // 換過月也一起存
      for (const mo of ms) await call('availability', { month: mo, note: ($('#avNote') || {}).value || '', days: Object.entries(AV).filter(([k, v]) => v && v.kind && k.startsWith(mo)).map(([date, v]) => ({ date, kind: v.kind, start: v.start || '', end: v.end || '' })) });
      AVDIRTY = new Set(); alert('已儲存 ' + ms.map(x => +x.slice(5) + ' 月').join('、')); }      // 時間一起存（以前會把代填的時間清掉）
    else { await call(b.dataset.a, { order_id: b.dataset.o }); if (b.dataset.a === 'accept') alert('已接 ✅ 已放進「我的班表」'); }
    if (['saveprof', 'savepf'].includes(b.dataset.a)) WPDRAFT.clear();
    await load();
  } catch (x) { alert(x.message); b.disabled = false; } finally { BUSY = false; }
});
load();
