'use strict';
/* 商店資訊（公開）：公司、聯絡、服務價格、付款方式、取消與退款政策、隱私權——金流（LINE Pay）審核與客戶查詢用。 */
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const li = xs => '<ul>' + xs.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>';
(async () => {
  const b = new URLSearchParams(location.search).get('b') || 'demoshop';
  const v = await (await fetch('/api/public/store?b=' + encodeURIComponent(b))).json();
  document.title = `${v.brand_name}｜商店資訊`;
  const c = v.company, k = v.contact;
  document.getElementById('app').innerHTML = `<h1 class="headline" style="font-size:30px">${esc(v.brand_name)}</h1>
    <p class="sub">到府服務・臺北市</p>
    <div class="card"><b>經營公司</b><p style="margin:6px 0 0">${esc(c.name)}（統一編號 ${esc(c.tax_id)}）<br>${esc(c.address)}</p></div>
    <div class="card"><b>聯絡我們</b><p style="margin:6px 0 0">官方 LINE：<a href="${esc(k.line)}">${esc(k.line_id)}</a>${k.phone ? `<br>電話：<a href="tel:${esc(k.phone)}">${esc(k.phone)}</a>` : ''}${k.email ? `<br>Email：<a href="mailto:${esc(k.email)}">${esc(k.email)}</a>` : ''}<br>${esc(k.hours)}</p></div>
    <div class="card"><b>服務項目與價格</b><div class="pt" style="margin-top:8px">${v.prices.map(([a, p]) => `<div>${esc(a)}</div><div>${esc(p)}</div>`).join('')}</div><p class="small" style="margin:10px 0 0">${esc(v.price_note)}</p>
      <div class="btns"><a class="btn" href="${esc(v.book_url)}">線上估價／預約</a></div></div>
    <div class="card"><b>服務流程</b>${li(v.service_flow)}</div>
    <div class="card"><b>付款方式</b>${li(v.pay_methods)}${li(v.payment_rules)}</div>
    <div class="card"><b>改期與取消</b>${li(v.cancel_rules)}</div>
    <div class="card"><b>退款政策</b>${li(v.refund_rules)}</div>
    <div class="card"><b>隱私權政策</b><p style="margin:6px 0 0"><a href="${esc(v.privacy_url)}">個人資料蒐集告知與隱私權政策 ›</a></p></div>`;
})();
