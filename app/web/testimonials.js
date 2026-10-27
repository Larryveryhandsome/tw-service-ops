'use strict';
/* 客戶見證：客戶確認完工、給 4–5 星 → 這裡＋派單管理群。到官方 LINE 親自回覆謝謝後按「已回覆」。客戶同意不具名分享的，可以連同服務前後照片放官網、社群。 */
PAGES.testimonials = () => {
  const all = S.testimonials || [], mode = SEG.tm || 'all';
  const list = all.filter(t => mode === 'all' || (mode === 'new' ? t.status === 'new' : t.consent === 'public'));
  let h = phead('客戶見證', '客戶滿意的聲音。', '客戶確認完工、給 4–5 星就會出現在這裡（派單管理群也會收到卡）。請到官方 LINE 親自回覆謝謝，回完按「已回覆」。客戶同意「不具名分享」的，可以連同服務前後照片放官網、社群。');
  h += segctl('tm', [['all', '全部', all.length], ['new', '還沒回覆', all.filter(t => t.status === 'new').length], ['public', '可公開', all.filter(t => t.consent === 'public').length]], mode);
  if (!list.length) return h + '<div class="empty">目前沒有。</div>';
  const ph = (ids, lab) => ids.map((i, k) => `<button type="button" class="thumb" data-act="galIds" data-k="p" data-cap="${lab}" data-i="${k}" data-ids="${esc(ids.join(','))}" style="width:88px;height:88px"><img src="/api/photo/${esc(i)}" alt="${lab}" loading="lazy"></button>`).join('');      // 點了左右滑
  return h + list.map(t => `<div class="card"><div class="secttl" style="margin-top:0"><h3>${'⭐'.repeat(t.stars || 0)} ${esc(t.customer || '—')}</h3><span class="small">${esc(md(t.date))} ${esc(t.title || '')}</span></div>
    ${t.text ? `<p style="font-size:17px">「${esc(t.text)}」</p>` : '<p class="small">（只給星星，沒有留言）</p>'}
    <p class="small">${t.consent === 'public' ? '✅ 客戶同意不具名分享' : t.consent === 'private' ? '🔒 只給我們看' : '公開：客戶還沒回答（不要公開）'}・${t.status === 'replied' ? `已回覆（${esc(t.replied_by)} ${esc((t.replied_at || '').slice(5, 16).replace('T', ' '))}）` : '<b>還沒回覆</b>'}</p>
    <div class="small" style="margin-top:8px">服務前</div><div style="display:flex;gap:6px;flex-wrap:wrap">${ph(t.before, '服務前') || '<span class="small">沒有</span>'}</div>
    <div class="small" style="margin-top:8px">完工</div><div style="display:flex;gap:6px;flex-wrap:wrap">${ph(t.after, '完工') || '<span class="small">沒有</span>'}</div>
    <div class="btns">${t.status === 'new' ? btn('已回覆', 'tmDone', { id: t.id }) : ''}${btn('看工單', 'order', { id: t.order_id }, 'gray')}</div></div>`).join('');
};
ACT.tmDone = async d => { await api('/api/testimonial/replied', { id: d.id }); await refresh(); return '已記錄'; };
