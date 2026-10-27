'use strict';

/* 程式錯誤自動回報（功能回報；同一種只記一筆） */
let JSERRN = 0;
function jserr(msg, stack) { if (++JSERRN > 3) return; fetch('/api/public/jserr', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ page: '報價單', msg: String(msg || '').slice(0, 300), stack: String(stack || '').slice(0, 1500) }) }).catch(() => { }); }
window.addEventListener('error', e => jserr(e.message, e.error && e.error.stack));
window.addEventListener('unhandledrejection', e => { const r = e.reason || {}; if (r.name && r.name !== 'Error') jserr(r.message, r.stack); });
/* 線上報價單：主管確認前顯示「確認中」；確認後顯示正式報價單（仿示範服務店報價單模板）→ 簽名簽收。 */
const LPTEST = new URLSearchParams(location.search).get('lptest') === '1';     // LINE Pay 測試模式：只有網址加 ?lptest=1 看得到按鈕
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const nt = n => n == null ? '—' : 'NT $ ' + Number(n);        // 跟 A4 報價單一樣不加千分位
const ymd = d => (d || '').replaceAll('-', '/');
const token = location.pathname.split('/').pop();
const NUM = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫';
const kv = rows => rows.filter(r => r[1]).map(([k, v]) => `<b>${esc(k)}</b><i>|</i><span>${esc(v)}</span>`).join('');
let Q = null;
const PREVIEW = new URLSearchParams(location.search).get('preview') === '1';
async function load() {
  const r = await fetch(PREVIEW ? `/api/quote-preview/${token}?sig=${encodeURIComponent(new URLSearchParams(location.search).get('sig') || '')}` : '/api/public/quote/' + token);
  const v = await r.json().catch(() => ({}));
  if (!r.ok) { $('#msg').hidden = false; $('#msg').innerHTML = `<h2>${esc(v.error || '找不到這張報價單')}</h2><p>請直接在官方 LINE 聯絡我們。</p><div id="lqc"></div>`; lqContact('#lqc', new URLSearchParams(location.search).get('b') || 'demoshop'); return; }
  Q = v; v.pending ? pending() : render();
}
function pending() {
  const q = Q, m = $('#msg'); document.title = `報價確認中｜${(q.company || {}).name || ''}`; m.hidden = false; m.className = 'card wait';
  if (q.cancelled) { document.title = `報價已取消｜${(q.company || {}).name || ''}`; m.innerHTML = `<div class="ic">🗂</div><h2>這張報價已取消</h2><p>${esc(q.name || '')} 您好，這張估價單已經取消了。需要服務的話，可以重新估價，或直接在官方 LINE 跟我們說 🙏</p><div id="lqc"></div><a class="btn" href="/book?b=${encodeURIComponent(q.brand || 'demoshop')}">重新估價</a>`; return; }
  const photosNeeded = q.mode === 'quote' && q.photos < q.min_photos;
  m.innerHTML = `<div class="ic">${q.mode === 'site_visit' ? '📍' : '🧾'}</div><h2>${q.mode === 'site_visit' ? '現場估價安排中' : '報價確認中'}</h2>
    <p>${esc(q.name)} 您好，${q.mode === 'site_visit'
      ? `您的需求需要專人到現場估價（現場估價費 ${q.visit_fee ? nt(q.visit_fee) : '免費'}），專人會在官方 LINE 跟您約時間。`
      : photosNeeded ? `還需要現況照片（目前 ${q.photos} 張，至少 ${q.min_photos} 張）。請直接在官方 LINE 傳照片，每個要服務的區域都要拍。`
        : '照片已收到，報價正在由主管確認。確認後正式報價單會傳到您的 LINE，並在這個頁面開放簽收。'}</p>
    ${q.more ? `<div style="background:#fff4e0;border-radius:12px;padding:12px;text-align:left;margin:10px 0"><b>主管請您補：</b>${esc(q.more)}</div>` : ''}
    ${q.stage === 'photos' || q.stage === 'review' || q.stage === 'site_visit' ? `<label class="btn" style="display:block;cursor:pointer">＋ 補傳照片<input type="file" id="more" accept="image/*" multiple hidden></label><p id="mprog" style="font-size:13px;color:var(--sub)"></p>` : ''}
    <p style="color:var(--sub);font-size:13px">報價單編號 ${esc(q.no)}</p>`
    + (q.line ? `<p style="font-size:14px;text-align:left"><b>還沒用 LINE 傳編號？</b>傳了之後正式報價單才會自動傳給您。</p><div class="code">${esc(q.line.code)}</div>${lqBlock(q.line.url, '用 LINE 傳送編號')}` : '<div id="lqc"></div>');
  if (q.cancelled || !q.line) lqContact('#lqc', q.brand);
}
function shrink(file) {
  return new Promise((ok, bad) => { const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => { const k = Math.min(1, 1600 / Math.max(img.width, img.height)), cv = document.createElement('canvas'); cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k); cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height); URL.revokeObjectURL(url); ok(cv.toDataURL('image/jpeg', 0.82)); };
    img.onerror = () => { URL.revokeObjectURL(url); bad(new Error('照片讀不到')); }; img.src = url; });
}
document.addEventListener('change', async e => {
  if (e.target.id !== 'more') return; const files = [...e.target.files], pr = $('#mprog');
  try { for (let i = 0; i < files.length; i++) { pr.textContent = `上傳中 ${i + 1}/${files.length}…`;
      const r = await fetch(`/api/public/quote/${token}/photo`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: await shrink(files[i]) }) });
      const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '上傳失敗'); }
    await fetch(`/api/public/quote/${token}/submit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    pr.textContent = `已補傳 ${files.length} 張，謝謝！主管會盡快確認。`;
  } catch (x) { pr.textContent = x.message; }
});
function brandDoc(q) {     // 報價單抬頭依品牌（企業線、生活線的報價單不出現 示範／示範服務店）
  const co = q.company || {};
  if (q.brand && q.brand !== 'demoshop') { const l = $('#qlogo'); if (l) { const d = document.createElement('div'); d.className = 'qwm'; d.textContent = co.name; l.replaceWith(d); } }
  $('#qzh').textContent = co.name || ''; $('#qen').textContent = co.tagline || ''; $('#qname').textContent = co.name || ''; document.title = `報價單｜${co.name || ''}`;
}
function tgroups(title) {        // 項目名稱分行：【…】整組不拆、3F／1F 接在前一段（跟 PDF 一樣）
  const out = [];
  for (const t of String(title || '').split(/\s+/).filter(Boolean).flatMap(x => x.split(/(?<=】)(?=【)/))) {
    const last = out[out.length - 1], open = last !== undefined && last.lastIndexOf('【') > last.lastIndexOf('】');
    if (last !== undefined && (open || /^[\x21-\x7e]+$/.test(t))) out[out.length - 1] = last + ' ' + t; else out.push(t);
  }
  return out;
}
function render() {
  const q = Q, cu = q.customer, co = q.company; brandDoc(q);
  $('#doc').hidden = false; $('#tools').hidden = false; pdfLink(q);
  $('#cust').innerHTML = kv([['公司', cu.company], [cu.company ? '聯絡人' : '客戶', cu.company && cu.name === cu.company ? '' : cu.name], ['連絡電話', cu.phone], ['地址', cu.address], ['統一編號', cu.tax_id]]);
  $('#no').textContent = q.no;
  $('#dates').innerHTML = `報價日期<span>${ymd(q.date)}</span><br>報價期限<span>${ymd(q.valid_until)}</span>` + (q.expect ? `<br>期望服務<span>${esc(q.expect)}</span><br><small style="color:var(--sub)">（以夥伴確認為準）</small>` : '');      // 加回客人填的期望日期／時段
  $('#items').innerHTML = (q.crew && q.crew.hours ? `<div style="background:#f5f1ea;border-radius:12px;padding:10px 14px;margin:0 0 10px;font-weight:600">👷 本報價依 ${q.crew.people} 位服務人員 × 約 ${q.crew.hours} 小時估算<span style="display:block;font-weight:400;font-size:13px;color:var(--sub)">實際時間以現場狀況為準</span></div>` : '')
    + (q.total_only ? '<p style="font-size:13px;color:var(--sub);margin:0 0 8px">以下為服務內容，費用以總計為準</p>' : '') + q.items.map(it => `<div class="tr td"><div class="it">${tgroups(it.title).map(esc).join('<br>')}${it.sub ? `<small>${esc(it.sub)}</small>` : ''}${it.separate ? '<small>依當天狀況另計</small>' : it.per_unit ? '<small>單價，不計入小計</small>' : ''}</div>
      <div>${(it.scope || []).length ? `<ol>${it.scope.map((s, i) => `<li>${NUM[i] || (i + 1) + '.'} ${esc(String(s).replace(/^\s*(?:\d{1,2}\s*[.、．)）]|[①-⑳])\s*/, ''))}</li>`).join('')}</ol>` : `<span style="color:var(--sub)">${it.sub === '加購' ? '加購項目' : '—'}</span>`}</div>
      <div class="amt">${it.amount === null ? '' : !it.amount && it.unit ? esc(it.unit) : nt(it.amount)}${it.unit && it.amount ? `<small style="display:block;font-size:13px;letter-spacing:.06em;color:var(--sub)">${esc(it.unit)}</small>` : ''}</div></div>`).join('');
  $('#sum').innerHTML = (q.tax ? `<div><span>小計</span><span>${nt(q.subtotal)}</span></div><div><span>稅額</span><span>${nt(q.tax)}</span></div>` : '')
    + `<div class="t"><span>總計</span><span>${nt(q.total)}</span></div>` + (q.stamp ? `<img src="${q.stamp}" alt="報價章" style="display:block;width:120px;margin:8px 0 0 auto;opacity:.92">` : '') + (q.tax ? '' : '<p style="font-size:12px;color:var(--sub);margin:0">未稅；需要發票請在 LINE 告知</p>');
  const notes = String(q.note || '').split('\n').map(s => s.trim()).filter(Boolean);
  $('#note').textContent = notes.length ? '備註：\n' + notes.map(n => '・' + n).join('\n') : '';
  if (!co.bank) document.querySelector('.pay').style.display = 'none';
  $('#pay').innerHTML = `<span style="grid-column:1/-1">${esc(co.bank)}</span>` + kv([['戶名', co.holder], ['帳號', co.account]]);
  $('#addr').textContent = co.address;
  if (q.status === 'signed') {
    $('#signed').hidden = false;
    const st = q.signed_terms || {};
    $('#signed').innerHTML = `<div class="sigrow"><div><span class="stamp">已簽收</span><div style="font-size:13px;color:var(--sub);margin-top:8px">簽收人 ${esc(q.signer)}・${esc((q.signed_at || '').slice(0, 16).replace('T', ' '))}`
      + `${st.version ? `<br>依 ${esc(st.version)} 版服務合約條款簽署（${esc(st.digest || '')}）` : ''}</div></div>${q.signature ? `<img class="sig" src="${q.signature}" alt="簽名">` : ''}</div>`;
    $('#after').hidden = false; showLine(q.line); afterLinks(q); payBox(q);
  } else if (q.preview) {
    $('#msg').hidden = false; $('#msg').className = 'card noprint'; $('#msg').innerHTML = '<b>內部預覽</b>：這是客戶會看到的樣子（還沒傳給客戶，客戶看不到）。可以按下方「列印／存成 PDF」。';
    document.body.insertBefore($('#msg'), document.body.firstChild);
  } else if (q.valid_until < new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10) || (q.service_date && q.service_date <= new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10))) {
    $('#msg').hidden = false; $('#msg').className = 'card noprint';
    $('#msg').innerHTML = `<h2>這張報價已經過期了</h2><p style="color:var(--sub);font-size:14px">報價期限或服務日期已經過了。按下面按鈕，我們會重新確認時間與價格，再傳新的報價給您。</p><button class="btn" id="rq" type="button">請重新報價</button><div class="err" id="rqe"></div>`;
    $('#rq').onclick = async () => { $('#rq').disabled = true; try { const r = await fetch(`/api/public/quote/${token}/requote`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '送出失敗'); $('#rq').textContent = '已通知，我們會盡快重新報價 🙏'; } catch (e) { $('#rqe').textContent = e.message; $('#rq').disabled = false; } };
  } else { terms(q); toolsAsk(q); levelAsk(q); $('#signbox').hidden = false; pad(); window.SDRAFT = window.SDRAFT || Draft.attach('q:' + token, $('#signbox')); SDRAFT.restore(); }      // 填到一半離開不會不見
}
// 同事代開的報價單，客人沒答過「現場工具」→ 簽收前先回答
function toolsAsk(q) {
  if (!q.ask_tools || $('#toolsAsk')) return;
  const d = document.createElement('div'); d.id = 'toolsAsk'; d.style.marginBottom = '16px';
  d.innerHTML = `<label style="font-weight:700">現場工具（必填）：服務當天需要我們自備工具嗎？</label>
    ${[['full', '現場工具齊全，用現場的'], ['basic', '只有基本工具，其他請自備'], ['none', '沒有工具，請全部自備']].map(([k, t]) => `<label class="chk" style="display:flex;gap:8px;align-items:center;margin-top:8px"><input type="radio" name="qtools" value="${k}"> ${t}</label>`).join('')}
    <input id="qtoolsNote" placeholder="家裡有哪些、放在哪裡（選填）" style="margin-top:8px">`;
  $('#signbox').prepend(d);
}
// 簽收前選服務等級、髒污程度、希望頻率與時長（比報價高 → 請主管重新報價）
function levelAsk(q) {
  const k = q.clean, sc = ((k || {}).scales || []).filter(s => s.ask); if (!k || (!k.ask && !sc.length) || $('#levelAsk')) return;
  const d = document.createElement('div'); d.id = 'levelAsk'; d.style.marginBottom = '16px';
  const lv = l => `<label class="chk" style="display:flex;gap:10px;align-items:flex-start;margin-top:10px;padding:10px;border:1px solid #E5E1DC;border-radius:12px">
      <input type="radio" name="qlevel" value="${l.n}"${l.n === k.quoted ? ' checked' : ''} style="margin-top:4px"><span><b>Level ${l.n}｜${esc(l.name)}</b>　<span style="color:var(--sub)">${esc(l.type)}</span>${l.n === k.quoted ? '　<b style="color:#2E7D32">（這份報價）</b>' : ''}
      <br><span style="font-size:13px">${esc(l.fit)}</span><br><span style="font-size:13px">服務內容：${esc(l.content)}</span><br><span style="font-size:13px;color:var(--sub)">標準：${esc(l.std)}</span>
      ${k.img && k.img[l.n] ? `<br><img src="${k.img[l.n]}?p=1" alt="Level ${l.n} 範例" style="max-width:100%;border-radius:10px;margin-top:6px">` : ''}</span></label>`;
  // 清運等級、整理等級（報價有這類項目才問）
  const scH = s => `<label style="font-weight:700;display:block;margin-top:16px">${esc(s.title)}（必填）：${esc(s.head)}</label>
    <p style="margin:4px 0 0;color:var(--sub);font-size:13px">這份報價是照「${'①②③'[s.quoted - 1]} ${esc(s.levels[s.quoted - 1].name)}」估的；要更高的等級，選了之後按「請主管依這個級別重新報價」。</p>
    ${s.levels.map(l => `<label class="chk" style="display:flex;gap:10px;align-items:flex-start;margin-top:10px;padding:10px;border:1px solid #E5E1DC;border-radius:12px"><input type="radio" name="qs_${s.key}" value="${l.n}"${l.n === s.quoted ? ' checked' : ''} style="margin-top:4px"><span><b>${'①②③'[l.n - 1]} ${esc(l.name)}</b>${l.n === s.quoted ? '　<b style="color:#2E7D32">（這份報價）</b>' : ''}${l.items.map(t => `<br><span style="font-size:13px">・${esc(t)}</span>`).join('')}${s.img && s.img[l.n] ? `<br><img src="${s.img[l.n]}?p=1" alt="${esc(s.title)} ${l.n} 範例" style="max-width:100%;border-radius:10px;margin-top:6px">` : ''}</span></label>`).join('')}`;
  d.innerHTML = (k.ask ? `<label style="font-weight:700">服務等級（必填）：請確認您要的服務程度</label>
    <p style="margin:4px 0 0;color:var(--sub);font-size:13px">這份報價的人力與時間是照「Level ${k.quoted}」估的。要更高的級別，請選了之後按「請主管依這個級別重新報價」。</p>
    ${k.levels.map(lv).join('')}
    <label style="font-weight:700;display:block;margin-top:16px">目前居家的髒污程度（必填）</label>
    ${k.dirt.map(([v, a, b, t]) => `<label class="chk" style="display:flex;gap:8px;align-items:flex-start;margin-top:8px"><input type="radio" name="qdirt" value="${v}" style="margin-top:4px"><span><b>${esc(a)}｜${esc(b)}</b><br><span style="font-size:13px;color:var(--sub)">${esc(t)}</span></span></label>`).join('')}
    <label style="font-weight:700;display:block;margin-top:16px">希望的服務頻率（必填）</label>
    <div style="display:flex;flex-wrap:wrap;gap:4px 14px">${k.freq.map(t => `<label class="chk" style="display:flex;gap:6px;align-items:center;margin-top:6px"><input type="radio" name="qfreq" value="${esc(t)}"> ${esc(t)}</label>`).join('')}</div>
    <label style="font-weight:700;display:block;margin-top:16px">希望每次服務大約幾小時（選填）</label>
    <input id="qhours" inputmode="decimal" placeholder="例：4" style="margin-top:6px;max-width:160px">` : '') + sc.map(scH).join('');
  $('#signbox').prepend(d);
}
function levelPick() {
  const v = n => (document.querySelector(`[name="${n}"]:checked`) || {}).value || '';
  const o = { level: v('qlevel'), dirt: v('qdirt'), want_freq: v('qfreq'), want_hours: ($('#qhours') || {}).value || '' };
  ((Q && Q.clean && Q.clean.scales) || []).forEach(s => { o[s.key + '_level'] = v('qs_' + s.key); });
  return o;
}
function levelUp(msg) {
  $('#err').innerHTML = `${esc(msg)}<br><button class="btn" id="lvrq" type="button" style="margin-top:8px">請主管依這個級別重新報價</button>`;
  $('#lvrq').onclick = async () => { $('#lvrq').disabled = true; try { const r = await fetch(`/api/public/quote/${token}/requote`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(levelPick()) });
    const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '送出失敗'); $('#lvrq').textContent = '已通知，主管會依您選的級別重新報價 🙏'; } catch (e) { $('#lvrq').disabled = false; $('#lvrq').textContent = e.message; } };
}
// 合約條款：逐段閱讀、逐段勾選，全部勾了才能簽
function terms(q) {
  const t = q.terms || []; if (!t.length) return;
  $('#terms').hidden = false;
  $('#terms').innerHTML = `<h2>服務聲明與合約事項</h2><p style="margin:0 0 6px;color:var(--sub);font-size:14px">請逐段閱讀，每一段都勾選同意後，才能在下方簽名。</p>`
    + t.map((s, i) => `<div class="tsec"><h3>${esc(s.title)}</h3><ol>${s.items.map(x => `<li>${esc(x)}</li>`).join('')}</ol>
      <label class="chk"><input type="checkbox" class="tagree" data-i="${i}"> 我已閱讀並同意「${esc(s.title.replace(/^[一二三四五六七八九十]+、/, ''))}」</label></div>`).join('')
    + `<p style="font-size:12px;color:var(--sub);margin:10px 0 0">條款版本 ${esc(q.terms_version)}（${esc(q.terms_digest)}）</p>`;
}
// 收款：列出每筆款項與狀態；待付款的可以回報末五碼
function payBox(q) {
  const ps = q.payments || []; if (!ps.length) return;
  const ST = { pending: '待付款', reported: '已回報，等我們核對', confirmed: '✅ 已收款', cancelled: '已取消（不用付）' };
  const box = $('#paybox'); box.hidden = false;
  box.innerHTML = `<h2>付款進度</h2>` + ps.map(p => `<div class="prow"><div><b>${esc(p.label)}</b>　${nt(p.amount / 100)}<div style="font-size:13px;color:var(--sub)">${p.due ? '期限 ' + ymd(p.due) : esc(p.note || '')}・${ST[p.status] || p.status}</div></div>`
    + ((q.linepay_demo || q.linepay && (q.linepay_mode === 'production' || LPTEST)) && p.status === 'pending' && p.amount > 0 && p.lp && (p.kind !== 'balance' || p.due) ? `<div class="prep"><button class="btn sm" type="button" data-lp="${p.id}" style="background:#06C755;color:#fff">用 LINE Pay 付款</button></div>` : '')
    + (p.status === 'pending' && p.kind !== 'balance' || (p.status === 'pending' && p.kind === 'balance' && p.due) ? `<div class="prep"><input type="text" inputmode="numeric" maxlength="5" placeholder="匯款帳號末五碼" data-p="${p.id}" value="${esc(q.last5 || '')}"><button class="btn sm" type="button" data-pay="${p.id}">回報已匯款</button></div>` : '') + '</div>').join('')
    + (q.company && q.company.bank ? `<p style="font-size:13px;color:var(--sub);margin:10px 0 0">匯款帳戶：${esc(q.company.bank)}　戶名 ${esc(q.company.holder)}　帳號 ${esc(q.company.account)}</p>` : '')
    + '<div class="err" id="payerr"></div>';
  box.onclick = async e => {
    const lp = e.target.closest('[data-lp]');
    if (lp) { lp.disabled = true; $('#payerr').textContent = '';
      try { const r = await fetch(`/api/public/quote/${token}/linepay`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pid: lp.dataset.lp, test: LPTEST || q.linepay_demo ? 1 : 0 }) });
        const v = await r.json().catch(() => ({})); if (!r.ok || !v.url) throw new Error(v.error || 'LINE Pay 暫時無法使用，請改用匯款'); location.href = v.url; }
      catch (x) { $('#payerr').textContent = x.message; lp.disabled = false; } return; }
    const b = e.target.closest('[data-pay]'); if (!b) return; const ref = box.querySelector(`input[data-p="${b.dataset.pay}"]`).value.trim();
    $('#payerr').textContent = ''; if (!/^\d{5}$/.test(ref)) return $('#payerr').textContent = '請填 5 碼數字（匯款帳號末五碼）';
    b.disabled = true; try { const r = await fetch(`/api/public/quote/${token}/paid`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pid: b.dataset.pay, ref }) });
      const v = await r.json().catch(() => ({})); if (!r.ok) throw new Error(v.error || '送出失敗'); await load(); } catch (x) { $('#payerr').textContent = x.message; b.disabled = false; } };
  if (location.hash === '#paid') $('#payerr').innerHTML = '<span style="color:#2E7D32">✅ LINE Pay 付款完成，謝謝您！</span>';
  if (location.hash === '#payfail') $('#payerr').textContent = 'LINE Pay 付款沒有完成（可能取消或逾時），可以再試一次或改用匯款。';
  if (['#pay', '#paid', '#payfail'].includes(location.hash)) setTimeout(() => box.scrollIntoView({ behavior: 'smooth' }), 300);
}
function showLine(l) { if (l && l.url) { $('#lineStep').hidden = false; $('#lineCode').textContent = l.code; $('#lineGo').outerHTML = lqBlock(l.url, '用 LINE 傳送編號'); } }
function afterLinks(q) {          // 簽收完成後：改時間、LINE 問我們（不是死路）
  const box = $('#afterLinks') || (() => { const d = document.createElement('div'); d.id = 'afterLinks'; $('#after').appendChild(d); return d; })();
  box.innerHTML = (q.chg_url ? `<a class="btn" style="display:block;text-align:center;margin-top:12px" href="${esc(q.chg_url)}">需要改時間 ›</a>` : '') + '<div id="lqa"></div>';
  lqContact('#lqa', q.brand);
}
let STROKES = [];                       // 筆畫存成 0～1 的相對座標：畫布大小改變（轉向、鍵盤）時照比例重畫
const drawnOk = () => STROKES.length >= 2 || STROKES.some(s => s.length >= 3);
function pad() {
  const cv = $('#pad'), ctx = cv.getContext('2d');
  const redraw = () => { const r = cv.getBoundingClientRect(), d = devicePixelRatio || 1; cv.width = r.width * d; cv.height = r.height * d;
    ctx.setTransform(d, 0, 0, d, 0, 0); ctx.lineWidth = 2.4; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = '#1d1d1f'; ctx.fillStyle = '#1d1d1f';
    for (const st of STROKES) { const p = st.map(([x, y]) => [x * r.width, y * r.height]);
      if (p.length === 1) { ctx.beginPath(); ctx.arc(p[0][0], p[0][1], 1.6, 0, Math.PI * 2); ctx.fill(); continue; }
      ctx.beginPath(); ctx.moveTo(...p[0]); p.slice(1).forEach(q => ctx.lineTo(...q)); ctx.stroke(); } };
  redraw(); addEventListener('resize', redraw);
  let on = false;
  const pt = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]; };
  cv.addEventListener('pointerdown', e => { e.preventDefault(); on = true; STROKES.push([pt(e)]); cv.setPointerCapture(e.pointerId); redraw(); });
  cv.addEventListener('pointermove', e => { if (!on) return; e.preventDefault(); STROKES[STROKES.length - 1].push(pt(e)); redraw(); });
  ['pointerup', 'pointercancel'].forEach(t => cv.addEventListener(t, () => { on = false; }));
  $('#clr').onclick = () => { STROKES = []; redraw(); };
}
$('#go').addEventListener('click', async () => {
  $('#err').textContent = ''; document.querySelectorAll('.lq-bad').forEach(x => x.classList.remove('lq-bad'));
  const bad = (msg, el) => { if (el) { const t = el.closest('fieldset,.card,section,div') || el; t.classList.add('lq-bad'); t.scrollIntoView({ block: 'center', behavior: 'smooth' }); } $('#err').textContent = msg; };      // 照畫面由上往下檢查，捲到漏填那格
  const tl = (document.querySelector('[name="qtools"]:checked') || {}).value || '';
  if (Q.ask_tools && !tl) return bad('請先回答「現場工具」：服務當天需要我們自備工具嗎？', document.querySelector('[name="qtools"]'));
  const lp = levelPick(), ka = Q.clean && Q.clean.ask;
  if (ka && !lp.level) return bad('請先選「服務等級」', document.querySelector('[name="qlevel"],[name="level"]'));
  if (ka && !lp.dirt) return bad('請先選「目前居家的髒污程度」', document.querySelector('[name="qdirt"],[name="dirt"]'));
  if (ka && !lp.want_freq) return bad('請先選「希望的服務頻率」', document.querySelector('[name="qwfreq"],[name="want_freq"]'));
  const sk = ((Q.clean && Q.clean.scales) || []).filter(s => s.ask), sm = sk.find(s => !lp[s.key + '_level']);
  if (sm) return bad(`請先選「${sm.title}」`, null);
  const ta = [...document.querySelectorAll('.tagree')].find(x => !x.checked);
  if (ta) return bad('請逐段閱讀合約條款，每一段都勾選同意', ta);
  if (!$('#signer').value.trim()) return bad('請填簽收人姓名', $('#signer'));
  if (!drawnOk()) return bad('請在框內簽名', $('#pad'));
  if (!$('#agree').checked) return bad('請勾選同意', $('#agree'));
  $('#go').disabled = true; $('#go').textContent = '簽收中…';
  try {
    const r = await fetch(`/api/public/quote/${token}/sign`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('#signer').value.trim(), agree: true, signature: $('#pad').toDataURL('image/png'), terms_digest: Q.terms_digest, tools: tl, tools_note: ($('#qtoolsNote') || {}).value || '', ...(ka || sk.length ? lp : {}) }) });
    const v = await r.json().catch(() => ({}));
    if (!r.ok && String(v.error || '').startsWith('LEVEL_UP:')) { $('#go').disabled = false; $('#go').textContent = '簽收報價單'; return levelUp(v.error.slice(9)); }
    if (!r.ok) throw new Error(v.error || '簽收失敗，請在官方 LINE 聯絡我們');
    window.SDRAFT && SDRAFT.clear(); $('#signbox').hidden = true; await load(); scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) { $('#err').textContent = e.message; $('#go').disabled = false; $('#go').textContent = '簽收報價單'; }
});
$('#prt').onclick = () => print();
function pdfLink(q) { const a = $('#pdfdl'); if (a && !PREVIEW && (q.stage === 'sent' || q.stage === 'signed' || q.status === 'signed')) { a.href = `/q/${token}.pdf`; a.hidden = false; } }      // 客戶要 PDF／紙本／Email
load();
