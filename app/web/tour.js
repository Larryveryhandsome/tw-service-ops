'use strict';
/* 導覽聚光燈（照合作品牌 CRM）：暗下來、挖一個洞照亮按鈕、旁邊一張說明卡。
   第一次登入：全盤導覽；第一次進每個頁面：那一頁的導覽；右上角「?」隨時重看。 */
let TOUR = null;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const step = (sel, title, text, o = {}) => ({ sel, title, text, ...o });
const navSel = k => [`#gnav [data-nav="${k}"]`, `#tabbar [data-tab="${k}"]`, `#tabbar [data-tab="more"]`];
function tourEl(sel) {
  for (const s of (Array.isArray(sel) ? sel : [sel])) { const e = document.querySelector(s); if (e) { const r = e.getBoundingClientRect(); if (r.width > 0 && r.height > 0) return e; } }
  return null;
}
function runTour(key, steps, onDone, strict) {        // strict：要走到最後一步才記成看過（說明書導覽；按略過不算）
  if (TOUR) return; steps = (steps || []).filter(Boolean);
  if (!steps.length) return onDone && onDone();
  closeSheet();
  const ov = document.createElement('div'); ov.className = 'tour';
  ov.innerHTML = '<div class="tour-block"></div><div class="tour-hole"></div><div class="tour-card" role="dialog" aria-live="polite"></div>';
  document.body.appendChild(ov);
  TOUR = { key, steps, i: 0, dir: 1, onDone, ov, strict };
  addEventListener('resize', tourPlace); addEventListener('scroll', tourPlace, true);
  addEventListener('keydown', tourKey, true);       // Esc 也能關（以前卡片太長按鈕被擠出畫面就關不掉）
  tourShow(0);
}
async function tourShow(i) {
  const T = TOUR; if (!T) return;
  if (i < 0) i = 0; if (i >= T.steps.length) return tourEnd(true);
  T.i = i; const s = T.steps[i];
  if (s.go && VIEW !== s.go) { closeSheet(); go(s.go); await sleep(700); if (TOUR !== T) return; }      // 從頭到尾導覽：自動換到那一頁
  if (s.prep && typeof gPrep === 'function') { closeSheet(); try { await gPrep(s.prep); } catch (e) { } T.prepped = true; await sleep(900); if (TOUR !== T) return; }      // 先打開工單／客戶／表單，才圈得到裡面的按鈕
  else if ($('#sheet') && !$('#sheet').hidden && !String(s.sel || '').includes('#sheet')) closeSheet();
  T.el = s.sel ? tourEl(s.sel) : null;
  if (s.sel && !T.el && !s.soft) return tourShow(i + T.dir);        // soft（新功能導覽）：找不到那顆按鈕也照樣講，不跳過
  if (T.el) { T.el.scrollIntoView({ block: 'center' }); await sleep(80); }
  const last = i === T.steps.length - 1;
  T.ov.querySelector('.tour-card').innerHTML = `<button class="tour-x" data-act="tourSkip" aria-label="關閉導覽">✕</button><div class="tour-n">${s.page ? esc(s.page) + '・' : ''}${i + 1} / ${T.steps.length}</div><h4>${s.title}</h4><p>${s.text}</p>
    <div class="tour-btns"><button class="tour-skip" data-act="tourSkip">${last ? (s.skip || '') : (s.skip || '略過導覽')}</button><span style="flex:1"></span>
    ${i ? '<button class="btn gray sm" data-act="tourPrev">上一步</button>' : ''}<button class="btn sm" data-act="tourNext">${last ? (s.last || '完成') : '下一步'}</button></div>`;
  tourPlace();
}
function tourPlace() {
  const T = TOUR; if (!T) return;
  const hole = T.ov.querySelector('.tour-hole'), card = T.ov.querySelector('.tour-card'), vw = innerWidth, vh = innerHeight;
  if (!T.el) { hole.style.cssText = `left:${vw / 2}px;top:${vh / 2}px;width:0;height:0`; card.classList.add('center'); card.style.cssText = ''; return; }
  card.classList.remove('center');
  const r = T.el.getBoundingClientRect(), pad = 6;
  hole.style.cssText = `left:${r.left - pad}px;top:${r.top - pad}px;width:${r.width + pad * 2}px;height:${r.height + pad * 2}px`;
  const cw = Math.min(380, vw - 24); card.style.width = cw + 'px';
  const ch = card.offsetHeight; let top = r.bottom + 14;
  if (top + ch > vh - 12) top = r.top - ch - 14; if (top < 12) top = Math.max(12, vh - ch - 12);
  card.style.left = (vw < 640 ? 12 : Math.min(Math.max(12, r.left + r.width / 2 - cw / 2), vw - cw - 12)) + 'px'; card.style.top = top + 'px';
}
function tourKey(e) { if (e.key === 'Escape' && TOUR) { e.stopPropagation(); tourEnd('skip'); } }
function tourEnd(how) {          // how：true＝走到最後一步、'skip'＝按略過
  const T = TOUR; if (!T) return; TOUR = null;
  if (T.prepped) closeSheet();
  T.ov.remove(); removeEventListener('resize', tourPlace); removeEventListener('scroll', tourPlace, true); removeEventListener('keydown', tourKey, true);
  const finished = how === true;
  if (T.key && (finished || (how === 'skip' && !T.strict))) { ME.tours.push(T.key); api('/api/tour', { key: T.key }).catch(() => { }); }
  T.onDone && T.onDone(finished);
}
ACT.tourNext = () => { TOUR.dir = 1; tourShow(TOUR.i + 1); };
ACT.tourPrev = () => { TOUR.dir = -1; tourShow(TOUR.i - 1); };
ACT.tourSkip = () => tourEnd('skip');

function tourWelcome() {
  if (S.role === 'cleaner') return [
    step(null, '歡迎使用示範服務店 App 👋', '以後接工作、看地址、打卡、拍照、回報都在這裡，不用一直翻 LINE 群組。大約 30 秒帶你看一遍。'),
    step(navSel('jobs'), '接單', '示範服務店邀請你的工作、開放報名的工作都在這。按「接受」或「我要報名」。'),
    step(navSel('mine'), '我的班', '排定後才看得到地址和注意事項。當天到場按「到場打卡」、拍服務前照；做完拍完成照片、寫回報、按「離場打卡」。'),
    step(navSel('avail'), '可排時間', '每個月點一點哪幾天可以，示範服務店會優先排給你，不用再一個個私訊問。'),
    step(navSel('me'), '我', '看薪資結算、打開手機通知、改密碼。'),
    step('#helpbtn', '不會用就按「?」', '每一頁都可以重看說明。', { last: '開始使用' })];
  return [
    step(null, '歡迎使用店家營運中樞（示範） 👋', '客戶、排班、派工、帳款都在同一個地方。示範資料都已經建好了。帶你看一遍，大約 1 分鐘。'),
    step(navSel('home'), '今天', '每天打開先看這頁：缺人、等客戶確認、明天要提醒、待完工登記、待收款⋯⋯處理完就會消失。'),
    step(navSel('schedule'), '排班', 'Apple 行事曆的樣子：藍色已排定、橘色待核對、綠色已完工。點空白日期就能新增工單。'),
    step(navSel('dispatch'), '派工', '缺人的工作按「挑人派工」，系統排序建議人選，你勾選後只發給那幾位；30 分鐘沒人回會通知你改派。'),
    step(navSel('customers'), '客戶', '每位客戶的地點、地址、進場方式、服務重點、價格、歷史服務和 LINE 對話都在這。'),
    can('money') ? step(navSel('billing'), '結帳', '月底打開這頁：每位客戶這個月做了幾次、實際幾小時、應收已收未收，一鍵開帳、複製請款和催款訊息。') : null,
    step(navSel('workers'), '夥伴', '開 App 帳號給夥伴，他們就能自己接單、打卡、拍照回報。'),
    step(navSel('more'), '更多', '客戶預約表單、方案次數、收款對帳、客訴補掃、LINE 對話檔案、資料核對、設定。'),
    step('#helpbtn', '不會用就按「?」', '每一頁都有自己的導覽。派工卡、報價單、提醒、完成照片都由系統用官方 LINE 自動發送。', { last: '開始使用' })];
}
const TOURS = {
  home: () => [step('[data-tour^="todo-"]', '要處理的事', '一組一組列出來，每一筆旁邊就有按鈕：挑人派工、複製確認訊息、排定、完工登記⋯⋯'), step('.grid', '今天的數字', '今天幾場、要處理幾件、7 天內缺幾個人、還有多少沒收。')],
  schedule: () => [step('.segctl', '三種看法', '月曆、本週清單、全部清單。清單可以勾選多筆一次排定。'), step('.pills', '看某位夥伴', '點名字只看他的班。'), step('[data-tour="cal"]', '行事曆', '點一場看詳情（派工、LINE 文字、完工登記都在裡面）；點空白日期新增。'), step('[data-act="recurring"]', '固定排程', '每週固定的客戶（社區、月方案）一次排好幾週。')],
  dispatch: () => [step('[data-tour="lane-need"]', '待派工', '還沒找人的。「挑人派工」看排名和理由勾選要發給誰；「系統自動派」會邀請最合適的 2 倍人數。'), step('[data-tour="lane-open"]', '招募中', '夥伴報名或接受後出現在這，按「選他」。額滿自動收單。'), step('[data-tour="lane-wait"]', '人到齊', '複製確認訊息給客戶，客戶說好就按「排定」。'), step('[data-act="copyShortage"]', '缺額總表', '一次複製兩週內所有缺額，貼到夥伴群組（取代一則一則貼）。')],
  customers: () => [step('input[type=search]', '搜尋', '姓名、LINE 名稱、電話、地址、排班表上的別名都可以搜。'), step('.tbl', '客戶總表', '點一位客戶看全部資料、地點、接下來的服務、帳單。')],
  billing: () => [step('[data-tour="bill-tiles"]', '這個月的錢', '應收、已收、未收、完工還沒開帳。'), step('[data-tour="bill-card"]', '每位客戶一張卡', '預排和實際時數並排，金額依案場價或時薪算好。按「開帳」確認金額、「複製請款訊息」貼給客戶、收到錢「登記收款」。'), step('.segctl', '夥伴薪資', '切到「夥伴薪資」看每個人做了幾場、幾小時、應付多少，複製明細給夥伴核對。')],
  workers: () => [step('.segctl', '夥伴分類', '只有「可派工」的人會出現在挑人名單。'), step('.tbl', '點一位夥伴', '編輯資料、開 App 帳號、看可排時間。')],
  jobs: () => [step('[data-tour="invite"]', '邀請你的工作', '按「接受」，示範服務店確認客戶後會通知你地址。')],
  mine: () => [step('[data-tour="job"]', '你的工作', '地址、導航、進場方式、服務重點都在這。當天到場打卡、拍照、回報。')],
  triage: () => [step('.segctl', '待決定／回收桶', '舊資料裡拿不準的都在「待決定」；刪掉的會先進「回收桶」，可以還原。'), step('[data-tour="tri-group"]', '一組一組處理', '每一筆可以：保留、移到某分類、合併到另一位、刪除。也可以勾選多筆一起處理。點名字可以先看詳細資料。')],
  avail: () => [step('[data-tour="avcal"]', '點日期', '綠色可以、紅色不行。上面可以填可以的時段，最後按儲存。')]
};
function maybeTour(v) {
  if (TOUR || !ME.tours || window.DEEP || !$('#sheet').hidden) return;      // 從 LINE 點進來看某一筆、或視窗開著 → 不跳導覽
  if (!ME.tours.includes('welcome')) return setTimeout(() => runTour('welcome', tourWelcome(), () => maybeTour(v)), 600);
  if (S.role !== 'cleaner' && S.guide && !ME.tours.includes(S.guide.key) && typeof guidePrompt === 'function' && guidePrompt()) return;      // 新功能上線 → 請大家看一遍
  if (!ME.tours.includes('page:' + v) && TOURS[v] && !(S.guide && ME.tours.includes(S.guide.key))) setTimeout(() => { if (VIEW === v && $('#sheet').hidden) runTour('page:' + v, TOURS[v]()); }, 500);      // 看完整本導覽的人不再自動跳單頁導覽
}
ACT.replayTour = () => runTour('', TOURS[VIEW] ? TOURS[VIEW]() : tourWelcome());
ACT.resetTours = async () => { await api('/api/tour', { reset: true }); ME.tours = []; go(S.role === 'cleaner' ? 'jobs' : 'home'); maybeTour(VIEW); };
