'use strict';
/* 說明書引擎：
   右上角「?」＝這一頁怎麼用（＋這一頁導覽、從頭到尾導覽、全部說明書）；
   「更多 › 操作手冊」＝全部說明書：每一頁、每顆按鈕按下去會怎樣、會發哪張 LINE 卡、訊息方向；一張單從頭到尾；LINE 卡片總表；
   「新功能導覽」＝改版後自動跳出，只帶看這個人上次看完之後新增的項目（每一項跳到對應頁面），走完記成看過（guide:<版本>）——只要新功能，不要整套；
   「從頭到尾導覽」＝整套一頁一頁帶看，在右上角「?」，想看再按。
   內容在 guide.js（GP 頁面、GFLOW 流程、GCARDS 卡片、GAUTO 自動排程）。有新功能：改 guide.py 的 VERSION／NEW，並更新 guide.js。 */
const gFind = v => (typeof GP !== 'undefined' ? GP : []).find(p => p.v === v || (p.alias || []).includes(v));
const gDir = d => `<span class="gdir">${d}</span>`;
function gBtnTable(rows) {
  return `<div class="tblwrap"><table class="mtbl gtbl"><tr><th>按鈕</th><th>按下去會怎樣</th><th>LINE 訊息（哪張卡 → 誰收到）</th><th>方向</th></tr>`
    + rows.map(r => `<tr><td><b>${r[0]}</b></td><td data-l="按下去">${r[1]}</td><td data-l="LINE">${r[2] || '<span class="small">不發 LINE</span>'}</td><td data-l="方向">${r[3] ? gDir(r[3]) : '—'}</td></tr>`).join('') + '</table></div>';
}
function gPage(p) {
  return `<p>${p.what}</p>` + (p.how ? `<p class="gsub">怎麼用</p><ol class="steps-ol">${p.how.map(x => `<li>${x}</li>`).join('')}</ol>` : '')
    + (p.btns && p.btns.length ? `<p class="gsub">每顆按鈕</p>${gBtnTable(p.btns)}` : '') + (p.note ? `<p class="small">${p.note}</p>` : '');
}
ACT.help = () => {                       // 右上角「?」
  if (S.role === 'cleaner') return ACT.replayTour();
  const p = gFind(VIEW);
  sheet('說明：' + (p ? p.t : '這一頁'), `<div class="btns" style="margin-top:0">${TOURS[VIEW] ? btn('▶ 這一頁導覽', 'replayTour', {}, 'sec') : ''}${btn('✨ 新功能導覽', 'newTour', {}, 'sec')}${btn('▶ 從頭到尾導覽', 'guideTour', {}, 'gray')}${btn('📘 全部說明書', 'goManual', {}, 'gray')}</div>`
    + (p ? `<div class="manual">${gPage(p)}</div>` : '<p class="small">這一頁的說明在「全部說明書」裡。</p>'));
};
ACT.goManual = () => { closeSheet(); go('manual'); };
ACT.gJump = d => { const e = document.getElementById('g-' + d.id); if (!e) return; if (e.tagName === 'DETAILS') e.open = true; setTimeout(() => e.scrollIntoView({ block: 'start' }), 30); };      // 先展開、下一格再捲（平滑捲動在手機會被展開打斷）
/* 從頭到尾導覽：歡迎 → 每一頁（說明＋聚光燈）→ 一張單從頭到尾 → 完成（記錄看過） */
function guideSteps() {
  const out = [step(null, `📘 從頭到尾導覽（${esc(S.guide ? S.guide.version : '')}）`, '一頁一頁帶你看：每頁做什麼、重要按鈕按下去會怎樣、會發哪張 LINE 卡給誰。<br>大約 10 分鐘；中途可以按「略過」，下次再從頭看（要走到最後才算看完）。', { go: 'home' })];
  for (const p of GP.filter(p => p.tour !== false && (!p.admin || S.role === 'admin') && (!p.money || can('money')))) {
    const key = (p.btns || []).filter(b => b[4]).slice(0, 4);
    out.push(step(null, p.t, p.what + (key.length ? '<br><br>' + key.map(b => `・<b>${b[0]}</b>：${b[1]}${b[2] ? `（${b[2]}）` : ''}`).join('<br>') : ''), { go: p.v, page: p.t }));
    if (TOURS[p.v]) out.push(...TOURS[p.v]().filter(Boolean).map(s => ({ ...s, go: p.v, page: p.t })));
  }
  out.push(step(null, '一張單從頭到尾', GFLOW.map(f => `・<b>${f[0]}</b>：${f[2]}`).slice(0, 9).join('<br>') + '<br><br>完整版（含每一步的 LINE 方向）在「全部說明書」。', { go: 'manual', page: '說明書' }));
  out.push(step('#helpbtn', '看完了 🎉', '以後哪一頁不會用，就按右上角「?」。有新功能時，系統會在派單管理群通知，請再看一次。', { last: '完成（記錄已看完）' }));
  return out;
}
ACT.guideTour = () => { closeSheet(); runTour(S.guide ? S.guide.key : '', guideSteps(), done => { if (done) refresh().catch(() => { }); }, true); };
/* 新功能導覽（只要新功能）：只帶看「上次看完之後」新增的；從沒看過的人只看最新這一版（以前的在全部說明書） */
function guideUnseen() {
  const rel = (S.guide && S.guide.releases) || [];
  const seenV = (ME.tours || []).filter(k => k.startsWith('guide:')).map(k => k.slice(6)).sort(), last = seenV[seenV.length - 1] || '';
  const un = rel.filter(r => r.v > last);
  return last ? un : rel.slice(0, 1);
}
/* 導覽要圈出那顆按鈕 → 先打開什麼（工單、客戶、報價、應徵者、表單、分頁） */
async function gPrep(p) {
  const [k, a, b] = String(p).split(':');
  if (k === 'newOrder') return ACT.newOrder();
  if (k === 'order') { const done = S.orders.filter(o => o.status === 'completed').sort((x, y) => (y.date || '').localeCompare(x.date || ''))[0], live = S.orders.find(o => ['review', 'draft', 'confirmed'].includes(o.status)); const o = done || live; if (o) return ACT.order({ id: o.id }); return; }
  if (k === 'customerSite') { const c = (S.customers || []).find(c => c.status === 'active' && (IDX.sitesOf[c.id] || []).length); if (c) return ACT.customer({ id: c.id }); return; }
  if (k === 'quote' || k === 'iv') { await sleep(500); const el = document.querySelector(k === 'quote' ? '[data-act=quoteOpen]' : '[data-act=ivForm]'); if (el) el.click(); return; }
  if (k === 'seg') { SEG[a] = b; route(true); }
}
const gTitle = t => { const i = t.search(/[：:（(；]/); return (i > 3 && i < 32 ? t.slice(0, i) : t.slice(0, 24) + (t.length > 24 ? '…' : '')); };
function newSteps() {
  const items = guideUnseen().flatMap(r => r.items), show = items.slice(0, 30), rest = items.slice(30);      // 每一項都帶看（圈出按鈕），太舊的才只列文字
  if (!items.length) return [step(null, '✨ 新功能導覽', '這一版的新功能你都看過了 👍<br>想看整套，按右上角「?」→「從頭到尾導覽」。', { last: '好' })];
  const out = [step(null, `✨ 新功能導覽（${esc(S.guide.version)}）`, `這次有 <b>${items.length}</b> 項新功能／修正，一項一項帶你看（約 ${Math.max(1, Math.ceil(items.length / 4))} 分鐘），每一項會跳到對應的頁面。看完系統會記錄。<br><span class="small">完整的「從頭到尾導覽」在右上角「?」，想看再按。</span>`, { go: 'home' })];
  show.forEach((x, i) => { const p = x.go ? gFind(x.go) : null, sel = x.sel ? x.sel.split('||') : null;      // 圈出那顆按鈕／操作區，分「改了什麼／怎麼用／在哪裡」
    out.push({ ...step(sel, `${i + 1}／${items.length}　${esc(gTitle(x.t))}`, `<b>改了什麼</b>　${esc(x.t)}` + (x.how ? `<br><br><b>怎麼用</b>　${esc(x.how)}` : '')
      + (p ? `<br><br><span class="small">📍 在「${esc(p.t)}」頁${sel ? '・亮起來的就是' : ''}</span>` : ''), x.go ? { go: x.go, page: p ? p.t : '' } : {}), prep: x.prep || '', soft: true }); });
  if (rest.length) out.push(step(null, `還有 ${rest.length} 項`, rest.map(x => '・' + esc(x.t)).join('<br>'), {}));
  out.push(step('#helpbtn', '看完了 🎉', '以後哪一頁不會用，就按右上角「?」；整套導覽和全部說明書也在那裡。', { last: '完成（記錄已看完）' }));
  return out;
}
ACT.newTour = () => { closeSheet(); runTour(S.guide ? S.guide.key : '', newSteps(), done => { if (done) refresh().catch(() => { }); }, true); };
function guidePrompt() {                 // 新功能上線：登入後直接跳「新功能導覽」（按略過＝今天不再跳）
  let later = ''; try { later = localStorage.getItem('guideLater') || ''; } catch (e) { }      // LINE 內建瀏覽器每次都是新 session → 改記在 localStorage
  if (later === S.guide.version) return false;
  setTimeout(() => runTour(S.guide.key, newSteps(), done => { if (done) refresh().catch(() => { }); else try { localStorage.setItem('guideLater', S.guide.version); } catch (e) { } }, true), 600);
  return true;
}
function guideManual() {
  const g = S.guide || {}, st = g.status || [];
  const toc = [['flow', '一張單從頭到尾'], ...GP.filter(p => (!p.admin || S.role === 'admin') && (!p.money || can('money'))).map(p => [p.v, p.t]), ['cards', 'LINE 卡片總表'], ['auto', '系統自動做的事'], ['acct', '帳號與安全']];
  let h = phead('操作手冊', '店家營運中樞（示範）說明書', '每一頁怎麼用、每顆按鈕按下去會怎樣、會發哪張 LINE 卡給誰、訊息往哪個方向。網址：<b>ops.example.com/#manual</b>',
    btn('✨ 新功能導覽', 'newTour') + btn('▶ 從頭到尾導覽', 'guideTour', {}, 'sec') + btn('複製手冊網址', 'copyManual', {}, 'sec'));
  const rel = g.releases || [];
  h += `<div class="card gnew"><b>📘 ${esc(g.version || '')} 新功能</b><ul>${(g.new || []).map(x => `<li>${esc(x)}</li>`).join('')}</ul>`
    + (rel.length > 1 ? `<details class="fold"><summary>之前的更新（${rel.length - 1} 版）</summary><div class="in">${rel.slice(1).map(r => `<p class="gsub">${esc(r.v)}</p><ul class="small">${r.items.map(x => `<li>${esc(x.t)}</li>`).join('')}</ul>`).join('')}</div></details>` : '')
    + (st.length ? `<p class="gsub">管理層看過了沒（走完「新功能導覽」才算）</p><div class="chips">${st.map(u => `<span class="tag ${u.seen ? 't-ok' : 't-warn'}">${esc(u.name)}：${u.seen ? '已看完' : '還沒'}${u.last_login ? '' : '（沒登入過）'}</span>`).join('')}</div>` : '') + '</div>';
  h += `<div class="card"><b>訊息方向怎麼看</b><ul class="small"><li>${gDir('單向')} 系統只通知對方，對方不用按什麼。</li><li>${gDir('雙向')} 對方會按卡片上的按鈕回覆，系統收到後自動處理（或通知管理群）。</li><li>${gDir('三向')} 業主、管理群、夥伴三方都會收到／會互相影響（例：業主改期 → 管理群確認 → 夥伴收到新時間）。</li><li>「→」是訊息往哪走；「⇄」是對方按了會回來。</li></ul></div>`;
  h += `<div class="chips gtoc">${toc.map(([k, t]) => `<button type="button" class="tag t-blue" data-act="gJump" data-id="${k}">${t}</button>`).join('')}</div>`;
  h += `<details class="fold" id="g-flow" open><summary>🧭 一張單從頭到尾（誰做、系統做什麼、LINE 往哪走）</summary><div class="in manual"><div class="tblwrap"><table class="mtbl gtbl"><tr><th>步驟</th><th>誰做</th><th>系統自動做什麼</th><th>LINE 方向</th></tr>${GFLOW.map(f => `<tr><td><b>${f[0]}</b></td><td data-l="誰做">${f[1]}</td><td data-l="系統做">${f[2]}</td><td data-l="方向">${gDir(f[3])}</td></tr>`).join('')}</table></div></div></details>`;
  for (const p of GP.filter(p => (!p.admin || S.role === 'admin') && (!p.money || can('money'))))
    h += `<details class="fold" id="g-${p.v}"><summary>${p.icon || '📄'} ${p.t}</summary><div class="in manual">${gPage(p)}${p.v !== VIEW && p.tour !== false ? `<div class="btns">${btn('去這一頁', 'goPage', { v: p.v }, 'gray')}</div>` : ''}</div></details>`;
  h += `<details class="fold" id="g-cards"><summary>💬 LINE 卡片總表（什麼時候、誰收到、按了會怎樣）</summary><div class="in manual"><div class="tblwrap"><table class="mtbl gtbl"><tr><th>卡片</th><th>什麼時候</th><th>誰收到</th><th>卡片上的按鈕 → 效果</th><th>方向</th></tr>${GCARDS.map(r => `<tr><td><b>${r[0]}</b></td><td data-l="什麼時候">${r[1]}</td><td data-l="誰收到">${r[2]}</td><td data-l="按鈕">${r[3]}</td><td data-l="方向">${gDir(r[4])}</td></tr>`).join('')}</table></div></div></details>`;
  h += `<details class="fold" id="g-auto"><summary>⏰ 系統自動做的事（時間表）</summary><div class="in manual"><div class="tblwrap"><table class="mtbl gtbl"><tr><th>什麼時候</th><th>做什麼</th><th>誰收到</th></tr>${GAUTO.map(r => `<tr><td><b>${r[0]}</b></td><td data-l="做什麼">${r[1]}</td><td data-l="誰收到">${r[2]}</td></tr>`).join('')}</table></div></div></details>`;
  h += `<details class="fold" id="g-acct"><summary>🔐 帳號與安全</summary><div class="in manual">${GACCT}</div></details>`;
  return h + `<div class="btns">${btn('✨ 新功能導覽', 'newTour')}${btn('▶ 從頭到尾導覽', 'guideTour', {}, 'sec')}${btn('重看畫面導覽', 'resetTours', {}, 'gray')}</div>`;
}
ACT.goPage = d => go(d.v);
const _manualCleaner = PAGES.manual;
PAGES.manual = () => S.role === 'cleaner' ? _manualCleaner() : guideManual();
