'use strict';
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
(async () => {
  const b = new URLSearchParams(location.search).get('b') || '';
  const v = await (await fetch('/api/public/privacy' + (b ? '?b=' + encodeURIComponent(b) : ''))).json();
  document.title = v.title;
  document.getElementById('app').innerHTML = `<h1 class="headline" style="font-size:28px">${esc(v.title)}</h1><p class="small">版本 ${esc(v.version)}</p>` +
    v.sections.map(([h, t]) => `<div class="card"><b>${esc(h)}</b><p style="margin:6px 0 0">${esc(t)}</p></div>`).join('');
})();
