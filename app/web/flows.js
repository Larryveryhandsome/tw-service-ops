'use strict';
/* 工作流頁（更多 › 工作流圖，網址 /#flows）＋聚光燈導覽。資料在 flows-data.js。
   每一步：左邊大編號、中間五個角色的圖示列（箭頭＝誰傳給誰；實線＝傳卡片、虛線＝按鈕回覆、⏰＝系統自動）、右邊內容＋「⚡ 觸發」「💬 LINE 卡」標籤。 */
const flRole = (f, k) => (f.roles && f.roles[k]) || FL_ROLES[k];
function flLane(f, st) {
  const [a, b, type] = st, ia = FL_ORDER.indexOf(a), ib = FL_ORDER.indexOf(b);
  const slots = FL_ORDER.map((k, i) => `<i class="${i === ia || i === ib ? 'on' : ''} r-${k}" title="${esc(flRole(f, k)[1])}">${flRole(f, k)[0]}</i>`).join('');
  if (type === 'auto' || ia === ib) return `<div class="fs-lane">${slots}<span class="fs-self" style="left:${(ia + .5) * 20}%">⏰</span></div>`;
  const lo = Math.min(ia, ib), hi = Math.max(ia, ib);
  return `<div class="fs-lane">${slots}<span class="fs-arrow ${type}${ib < ia ? ' rev' : ''}" style="left:calc(${(lo + .5) * 20}% + 15px);width:calc(${(hi - lo) * 20}% - 30px)"></span></div>`;
}
function flSteps(f) {
  let n = 0;
  return f.steps.map(st => {
    if (st[2] === 'br') return `<div class="fs-br">↳ ${esc(st[3])}</div>`;
    n++; const [a, b, type, txt, trig, card] = st, ty = FL_TYPE[type];
    const who = type === 'auto' ? '系統自動' : `${flRole(f, a)[0]} ${flRole(f, a)[1]} → ${flRole(f, b)[0]} ${flRole(f, b)[1]}`;
    return `<div class="fs fs-${type}" data-fs="${f.id}-${n}"><div class="fs-n">${n}</div>${flLane(f, st)}<div class="fs-t"><div class="fs-who">${ty[0]} ${esc(who)}</div><b>${esc(txt)}</b>`
      + `<div class="fs-tags">${trig ? `<span class="fs-trig">⚡ 觸發：${esc(trig)}</span>` : ''}${card ? `<span class="fs-card">💬 LINE 卡：${esc(card)}</span>` : ''}</div></div></div>`;
  }).join('');
}
function flHead(f) { return `<div class="fl-lanehead">${FL_ORDER.map(k => `<span class="r-${k}">${flRole(f, k)[0]}<small>${esc(flRole(f, k)[1].replace('派單', '').split('／')[0])}</small></span>`).join('')}</div>`; }
PAGES.flows = () => {      // 蘋果官網視覺：置中大標、黏著的目錄列、章節大字、大圓角白卡
  const gsub = { 接案: '從詢價到簽約付款。', 排班: '找夥伴、確認時間、前一天提醒。', 服務: '到場、完工、驗收。', 例外: '請假、加時、改期、換館。', 管理: '配對、招募、決策、安全網。' };
  const no = i => String(i).padStart(2, '0');
  let h = `<div class="fx-hero"><div class="fx-eyebrow">工作流</div><h1>每一條工作流，<br>誰觸發、誰收到。</h1>`
    + `<p>${FLOWS.length} 條工作流加一張總覽。每一步都標了順序、誰傳給誰、什麼時候發生，和會發哪張 LINE 卡。</p>`
    + `<div class="fx-cta">${btn('▶ 全部工作流導覽', 'flTour', { id: 'all' }, 'fx-pill')}<button type="button" class="fx-link" data-act="goManual">看說明書 ›</button></div></div>`;
  h += `<nav class="fx-nav" aria-label="工作流目錄"><button type="button" data-act="flJump" data-id="f0"><span>00</span>總覽</button>`
    + FLOWS.map((f, i) => `<button type="button" data-act="flJump" data-id="${f.id}"><span>${no(i + 1)}</span>${esc(f.t)}</button>`).join('') + '</nav>';
  h += `<div class="fx-legend"><div class="fx-roles">${FL_ORDER.map(k => `<span class="fx-role r-${k}"><i>${FL_ROLES[k][0]}</i>${FL_ROLES[k][1]}</span>`).join('')}</div>`
    + `<div class="fl-keys"><span><b class="k-msg"></b>傳出訊息／卡片</span><span><b class="k-tap"></b>對方按卡片按鈕回覆</span><span>⏰ 系統自動</span><span class="fs-trig">⚡ 觸發</span><span class="fs-card">💬 LINE 卡</span></div></div>`;
  h += `<h2 class="fx-chapter">一張單，從頭到尾。<small>主線 13 個階段，點一個看那條工作流。</small></h2>`
    + `<div class="fl" id="fl-f0"><div class="fl-h"><div><div class="fl-no">00 · 總覽</div><h2>一張單從頭到尾</h2></div>${btn('聚光燈導覽 ›', 'flTour', { id: 'f0' }, 'fx-pill sec')}</div>`
    + `<p class="fl-sum">例外狀況各有一條：請假（07）、現場回報（08）、改期取消（09）、換館別（10）。</p>`
    + `<div class="fx-journey">${FL_STAGES.map(([t, id], i) => { const fi = FLOWS.findIndex(f => f.id === id);
      return `<button type="button" class="fx-stage" data-act="flJump" data-id="${id}" data-fs="f0-${i + 1}"><b>${i + 1}</b><span>${esc(t)}</span><small>${no(fi + 1)} ${esc(FLOWS[fi].t)}</small></button>`; }).join('')}</div></div>`;
  FLOWS.forEach((f, i) => {
    if (!i || FLOWS[i - 1].g !== f.g) h += `<h2 class="fx-chapter">${esc(f.g)}。<small>${esc(gsub[f.g] || '')}</small></h2>`;
    h += `<div class="fl" id="fl-${f.id}"><div class="fl-h"><div><div class="fl-no">${no(i + 1)} · ${esc(f.g)}</div><h2>${esc(f.t)}</h2></div>${btn('聚光燈導覽 ›', 'flTour', { id: f.id }, 'fx-pill sec')}</div>`
      + `<p class="fl-sum">${esc(f.sum)}</p>${flHead(f)}${flSteps(f)}</div>`;
  });
  return h;
};
ACT.flJump = d => { const e = document.getElementById('fl-' + d.id); if (e) setTimeout(() => e.scrollIntoView({ block: 'start' }), 30); };
function flTourSteps(f, i) {
  let n = 0, br = '';
  const out = [step(`#fl-${f.id} .fl-h`, `${String(i + 1).padStart(2, '0')} ${esc(f.t)}`, esc(f.sum) + '<br><br>每一步：大數字＝順序、箭頭＝誰傳給誰、⚡＝什麼時候發生、💬＝哪張 LINE 卡。', { go: 'flows', page: '工作流' })];
  for (const st of f.steps) {
    if (st[2] === 'br') { br = st[3]; continue; }
    n++; const [a, b, type, txt, trig, card] = st;
    const who = type === 'auto' ? '⏰ 系統自動' : `${flRole(f, a)[0]} ${flRole(f, a)[1]} → ${flRole(f, b)[0]} ${flRole(f, b)[1]}（${FL_TYPE[type][0]} ${FL_TYPE[type][1]}）`;
    out.push(step(`[data-fs="${f.id}-${n}"]`, `${n}. ${esc(txt)}`, (br ? `<b>${esc(br)}</b><br>` : '') + `${esc(who)}${trig ? `<br>⚡ 觸發：${esc(trig)}` : ''}${card ? `<br>💬 LINE 卡：${esc(card)}` : ''}`, { go: 'flows', page: f.t }));
  }
  return out;
}
ACT.flTour = d => {
  closeSheet();
  if (d.id === 'f0') return runTour('', [step('#fl-f0 .fl-h', '一張單從頭到尾', '主線 13 個階段，點任一個可以跳到那條工作流。', { go: 'flows', page: '總覽' }),
    ...FL_STAGES.map(([t, id], i) => step(`[data-fs="f0-${i + 1}"]`, `${i + 1}. ${t}`, `詳細在「${esc(FLOWS.find(f => f.id === id).t)}」。`, { go: 'flows', page: '總覽' }))]);
  const list = d.id === 'all' ? FLOWS : FLOWS.filter(f => f.id === d.id);
  runTour('', list.flatMap(f => flTourSteps(f, FLOWS.indexOf(f))));
};
