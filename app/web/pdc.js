'use strict';
/* 個資蒐集告知（個資法）：在表單送出鈕前加「已閱讀並同意」勾選；送出時自動帶 consent，沒勾就擋下來。後端也會檢查並留存。 */
(() => {
  const V = '2026-10-09', EP = ['/api/public/quote', '/api/public/join', '/api/public/site', '/api/public/apply', '/api/public/hello', '/api/public/mall/order'];
  const brand = () => new URLSearchParams(location.search).get('b') || '';
  function box() {
    let cb = document.getElementById('agree');
    if (cb) {                                   // 預約頁原本就有勾選：改成連到完整告知
      const l = cb.closest('label');
      if (l) {                                  // 文字包成一段（label 是 flex，以前每段字各佔一欄 → 手機上直排擠成好幾欄）
        const t = document.createElement('span'); [...l.childNodes].filter(n => n !== cb).forEach(n => t.appendChild(n)); l.appendChild(t);
        t.insertAdjacentHTML('beforeend', `　<a href="/privacy?b=${encodeURIComponent(brand())}" target="_blank" rel="noopener">個資蒐集告知</a>`);
      }
      return cb;
    }
    const go = document.getElementById('go'); if (!go) return null;
    go.insertAdjacentHTML('beforebegin', `<label class="chk" style="margin-top:18px;display:block"><input type="checkbox" id="pdAgree"> 我已閱讀並同意<a href="/privacy?b=${encodeURIComponent(brand())}" target="_blank" rel="noopener">個人資料蒐集告知</a>（用於估價、派工、聯繫與收款）</label>`);
    return document.getElementById('pdAgree');
  }
  let CB = null;
  document.addEventListener('DOMContentLoaded', () => { CB = box(); });
  if (document.readyState !== 'loading') CB = box();
  const F = window.fetch.bind(window);
  window.fetch = (url, opt) => {
    const u = String(url).split('?')[0];
    if (opt && opt.method === 'POST' && EP.includes(u)) {
      if (!CB || !CB.checked) { const e = new Error('請先勾選「我已閱讀並同意個人資料蒐集告知」'); e.pd = true; return Promise.reject(e); }
      try { const b = JSON.parse(opt.body || '{}'); b.consent = true; b.consent_version = V; opt = { ...opt, body: JSON.stringify(b) }; } catch (e) { /* 非 JSON 不處理 */ }
    }
    return F(url, opt);
  };
})();
