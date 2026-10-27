'use strict';
/* 客戶頁共用：LINE 按鈕（2026-10-26 介面全面檢視）。
   手機 → 綠色按鈕直接開 LINE；電腦 → 顯示 QR code（用手機 LINE 掃），line.me 連結在電腦打不開。
   lqBlock(網址, 按鈕字)：回傳 HTML。lqContact(容器, 品牌)：放「LINE 問我們」（結尾頁不要是死路）。 */
const LQ_MOBILE = /Android|iPhone|iPad|iPod|Mobile|Line\//i.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && /Macintosh/.test(navigator.userAgent));
const lqEsc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
(() => { const s = document.createElement('style'); s.textContent = `.lq-btn{display:block;text-align:center;background:#06C755;color:#fff!important;border-radius:980px;padding:14px 18px;font-size:17px;font-weight:700;text-decoration:none;margin-top:12px;min-height:48px;box-sizing:border-box}
.lq-qr{display:flex;gap:14px;align-items:center;margin-top:12px;text-align:left;background:#F5F5F7;border-radius:14px;padding:12px}.lq-qr img{width:150px;height:150px;flex:none;background:#fff;border-radius:10px}.lq-qr p{margin:0;font-size:15px;line-height:1.5}
@media (max-width:420px){.lq-qr{flex-direction:column;text-align:center}}`; document.head.appendChild(s); })();
function lqBlock(url, label) {
  if (!url) return '';
  if (LQ_MOBILE) return `<a class="lq-btn" href="${lqEsc(url)}">${lqEsc(label)}</a>`;
  return `<div class="lq-qr"><img src="/qr?u=${encodeURIComponent(url)}" alt="用手機 LINE 掃描" width="150" height="150"><p><b>拿出手機掃這個 QR code</b><br>手機相機或 LINE 的「掃描行動條碼」都可以 → 會打開我們的官方 LINE${/oaMessage/.test(url) ? '，訊息已經打好，按「傳送」就完成' : ''}。<br><a href="${lqEsc(url)}" target="_blank" rel="noopener" style="font-size:13px">（電腦有裝 LINE 的話也可以點這裡）</a></p></div>`;
}
let LQ_OA = null;
async function lqContact(el, brand, label = '💬 LINE 問我們') {
  const box = typeof el === 'string' ? document.querySelector(el) : el; if (!box) return;
  try { LQ_OA = LQ_OA || await (await fetch('/api/public/oa')).json(); } catch (e) { LQ_OA = {}; }
  const url = LQ_OA[brand || 'demoshop'] || LQ_OA.demoshop; if (url) box.innerHTML = lqBlock(url, label);
}
