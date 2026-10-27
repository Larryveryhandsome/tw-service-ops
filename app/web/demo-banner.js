/* 示範版廣告列：所有畫面底部固定顯示（請保留） */
(function () {
  if (document.getElementById('demo-ad-bar')) return;
  var css = '#demo-ad-bar{position:fixed;left:0;right:0;bottom:0;z-index:2147483000;background:#1f2a44;color:#fff;font:14px/1.4 system-ui,"Microsoft JhengHei",sans-serif;'
    + 'padding:8px 16px calc(8px + env(safe-area-inset-bottom,0px));text-align:center;box-shadow:0 -2px 8px rgba(0,0,0,.15)}'
    + '#demo-ad-bar b{color:#ffd76a}#demo-ad-bar .x{margin-left:10px;cursor:pointer;opacity:.7;background:none;border:0;color:#fff;font-size:15px}'
    + 'body{padding-bottom:44px}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  function add() {
    var d = document.createElement('div'); d.id = 'demo-ad-bar';
    d.innerHTML = '🧪 示範版（資料皆為虛構）｜<b>需要客製化？加 LINE @812ibtir（留白事務所，林其衞）</b>'
      + '<button class="x" title="暫時收起" aria-label="暫時收起">✕</button>';
    d.querySelector('.x').onclick = function () { d.style.display = 'none'; document.body.style.paddingBottom = '0'; };
    document.body.appendChild(d);
  }
  if (document.body) add(); else document.addEventListener('DOMContentLoaded', add);
})();

/* 示範版切換列：頂部固定，免登入切換「管理端／夥伴端／客戶端」 */
(function () {
  if (document.getElementById('demo-role-bar')) return;
  var css = '#demo-role-bar{position:sticky;top:0;z-index:2147483001;background:#0b7a5c;color:#fff;font:13px/1.4 system-ui,"Microsoft JhengHei",sans-serif;'
    + 'padding:calc(6px + env(safe-area-inset-top,0px)) 10px 6px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;justify-content:center}'
    + '#demo-role-bar span{opacity:.85;margin-right:2px}'
    + '#demo-role-bar a{color:#fff;text-decoration:none;background:rgba(255,255,255,.16);border-radius:999px;padding:3px 10px;white-space:nowrap}'
    + '#demo-role-bar a.on{background:#fff;color:#0b7a5c;font-weight:700}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  var links = [['說明', '/intro', /^\/intro/], ['老闆畫面', '/enter?as=admin', /^\/$|^\/index/], ['服務人員（手機）', '/enter?as=worker', /^\/(wp|w)\//],
               ['客戶：官網', '/home', /^\/home/], ['客戶：線上估價', '/book', /^\/book/], ['客戶：報價單', '/q/demo-quote-0001', /^\/q\//]];
  function add() {
    var d = document.createElement('div'); d.id = 'demo-role-bar';
    d.innerHTML = '<span>🧪 示範免登入，切換角色：</span>' + links.map(function (l) {
      return '<a href="' + l[1] + '"' + (l[2].test(location.pathname) ? ' class="on"' : '') + '>' + l[0] + '</a>'; }).join('');
    document.body.insertBefore(d, document.body.firstChild);
  }
  if (document.body) add(); else document.addEventListener('DOMContentLoaded', add);
})();
