'use strict';
/* 填到一半不會不見（負責人 任何地方的填寫，都不要填到一半跳出就沒存檔，不然會讓人不想重填、失單——客人、夥伴、管理都一樣）。
   打字、勾選就自動暫存在這支手機（localStorage，14 天）；離開再回來自動帶回，上面一條提示可以「清除重填」；送出成功就清掉。
   不存密碼、檔案欄位；標 data-nodraft 的欄位也不存。用法：const d = Draft.attach('book:demoshop', form, { extra }); d.restore(); 送出成功 d.clear()。 */
const Draft = (() => {
  const DAYS = 14, PRE = 'draft:';
  const get = k => { try { const v = JSON.parse(localStorage.getItem(PRE + k) || 'null'); return v && Date.now() - v.at < DAYS * 864e5 ? v : null; } catch (e) { return null; } };
  const del = k => { try { localStorage.removeItem(PRE + k); } catch (e) { } };
  const keyOf = el => el.type === 'radio' ? 'r:' + el.name : (el.type === 'checkbox' && !el.id ? 'c:' + el.name + '=' + el.value : 'v:' + (el.id || el.name));
  function fields(root, skip) {
    return [...root.querySelectorAll('input,select,textarea')].filter(el => (el.id || el.name) && !['file', 'password', 'hidden', 'submit', 'button', 'reset', 'image'].includes(el.type)
      && !el.dataset.nodraft && !skip.includes(el.id) && !el.closest('[data-nodraft]'));
  }
  function snap(root, skip) {
    const d = {};
    for (const el of fields(root, skip)) {
      const k = keyOf(el);
      if (el.type === 'radio') { if (el.checked) d[k] = el.value; }
      else if (el.type === 'checkbox') d[k] = el.checked;
      else if (el.tagName === 'SELECT' && el.multiple) d[k] = [...el.selectedOptions].map(o => o.value);
      else d[k] = el.value;
    }
    return d;
  }
  function fill(root, skip, d) {
    let n = 0;
    for (const el of fields(root, skip)) {
      const k = keyOf(el); if (!(k in d)) continue;
      let changed = false;
      if (el.type === 'radio') { if (el.value === d[k] && !el.checked) { el.checked = true; changed = true; } }
      else if (el.type === 'checkbox') { if (el.checked !== !!d[k]) { el.checked = !!d[k]; changed = true; } }
      else if (el.tagName === 'SELECT' && el.multiple) { [...el.options].forEach(o => { o.selected = (d[k] || []).includes(o.value); }); changed = true; }
      else if (el.value !== d[k] && (el.tagName !== 'SELECT' || [...el.options].some(o => o.value === d[k]))) { el.value = d[k]; changed = true; }
      if (changed) { n++; el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }
    }
    return n;
  }
  function banner(root, at, onClear) {
    if (root.querySelector('.draft-note')) return;
    const t = new Date(at), b = document.createElement('div'); b.className = 'draft-note'; b.setAttribute('role', 'status');
    b.innerHTML = `<span>📝 已帶回你上次還沒送出的內容（${t.getMonth() + 1}/${t.getDate()} ${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}）</span><button type="button">清除重填</button>`;
    b.querySelector('button').onclick = onClear;
    root.prepend(b);
    if (!document.getElementById('draftCss')) {
      const s = document.createElement('style'); s.id = 'draftCss';
      s.textContent = '.draft-note{display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:rgba(0,113,227,.08);color:inherit;border-radius:12px;padding:10px 14px;margin:0 0 14px;font-size:14px;line-height:1.4}.draft-note span{flex:1;min-width:12em}.draft-note button{border:0;background:transparent;color:#0071e3;font:inherit;font-weight:600;cursor:pointer;padding:4px 2px}';
      document.head.appendChild(s);
    }
  }
  function attach(key, root, o = {}) {
    const skip = o.skip || []; let timer = null, live = true, shown = false;
    const save = () => {
      if (!live) return; clearTimeout(timer);
      timer = setTimeout(() => {
        const v = { at: Date.now(), d: snap(root, skip) };
        if (o.extra) v.x = o.extra.get();
        try { localStorage.setItem(PRE + key, JSON.stringify(v)); }
        catch (e) { try { delete v.x; localStorage.setItem(PRE + key, JSON.stringify(v)); } catch (e2) { /* 私密視窗：存不了就算了 */ } }      // 照片太大存不下 → 只存文字
      }, 400);
    };
    root.addEventListener('input', save); root.addEventListener('change', save);
    return {
      save,
      restore() {
        const v = get(key); if (!v) return false;
        live = false;
        const n = fill(root, skip, v.d) + fill(root, skip, v.d);      // 第二次：上一層選了才出現的選項（縣市 → 鄉鎮）
        if (o.extra && v.x) o.extra.set(v.x);
        live = true;
        if (!shown && (n || (o.extra && v.x && v.x.length)) && (shown = true)) banner(o.note || root, v.at, () => { del(key); live = false; o.onClear ? o.onClear() : location.reload(); });
        return true;
      },
      clear() { live = false; clearTimeout(timer); del(key); },
      has: () => !!get(key)
    };
  }
  return { attach, clear: del, get };
})();
