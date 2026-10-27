'use strict';
/* 示範商城（中控台＝看數據＋商品設定；負責人：商城網站抽離中控台，客戶、夥伴在網站或官方 LINE 點選購買）。
   訂單處理在派單管理群卡片點選（備好了／已交付／已收款／取消），這裡也可以按。 */
// 稽核 F7：等連 LINE 的單另一個分頁
const NT = n => 'NT$ ' + Number(n || 0).toLocaleString('zh-TW');
const SHOP_ST = { new: '新訂單', ready: '備好了', delivered: '已交付', cancelled: '已取消' };
const SHOP_FIELDS = [['solves', '解決什麼問題（一句話）'], ['suitable', '適用範圍（空間、材質）'], ['avoid', '不適用／注意事項（照瓶身或供應商資料）'], ['usage', '使用方法（用量、步驟、要不要沖洗）'],
  ['spec', '規格與內容物'], ['warning', '成分與警語（照瓶身標示）'], ['faq', '常見問題']];
PAGES.shop = () => {
  const ps = S.shop_products || [], os = S.shop_orders || [], st = S.shop_stats || {}, mode = ['open', 'done', 'all'].includes(SEG.shop) ? SEG.shop : 'open';
  let h = phead('示範商城', '耗材，也帶回家。', '客戶、夥伴在<b>商城網站</b>或官方 LINE 打「商城」點選購買；夥伴看到夥伴價。新訂單會傳到派單管理群，點卡片按鈕處理。',
    `<a class="btn" href="/mall" target="_blank" rel="noopener">打開商城網站</a>`);
  const f = st.funnel || {}, pct = (a, b) => b ? Math.round(a * 100 / b) + '%' : '—';
  h += `<div class="grid"><div class="tile stat"><div class="k">${esc((st.month || '').slice(5))} 月營收</div><div class="v">${NT(st.revenue)}</div><div class="s">已收款 ${NT(st.paid)}</div></div>
    <div class="tile stat"><div class="k">本月訂單</div><div class="v">${st.orders || 0}</div><div class="s">業主 ${st.by_buyer?.customer || 0}・夥伴 ${st.by_buyer?.worker || 0}・網站訪客 ${st.by_buyer?.guest || 0}</div></div>
    <div class="tile stat ${st.open ? 'warn' : 'good'}"><div class="k">待處理</div><div class="v">${st.open || 0}</div><div class="s">${st.open ? '到派單管理群點卡片處理' : '都處理好了'}${st.wait_line ? `・另有 ${st.wait_line} 張等客戶連 LINE` : ''}</div></div>
    <div class="tile stat"><div class="k">30 天：瀏覽 → 加購 → 下單</div><div class="v" style="font-size:22px">${f.view || 0} → ${f.add || 0} → ${f.order || 0}</div><div class="s">加購率 ${pct(f.add, f.view)}・下單率 ${pct(f.order, f.add)}</div></div></div>`;
  h += `<div class="card"><h3 style="margin-top:0">本月熱銷</h3>${(st.top || []).length ? st.top.map(t => `<div class="item"><div class="t"><div class="n">${esc(t.name)}</div></div><div class="a">${t.qty} 瓶・${NT(t.revenue)}</div></div>`).join('') : '<p class="small">本月還沒有訂單。</p>'}
    <p class="small" style="margin-top:10px">通路：官方 LINE ${st.by_channel?.line || 0} 張・網站 ${st.by_channel?.web || 0} 張　｜　30 天團隊領用 ${st.team_use_30d || 0} 瓶（不算營收）</p>
    ${(st.low_stock || []).length ? `<p class="small" style="color:var(--orange)">⚠️ 庫存低：${st.low_stock.map(x => `${esc(x.name)} 剩 ${x.stock}`).join('、')}</p>` : ''}</div>`;
  const WAITL = o => o.status === 'new' && !o.announced_at;      // 網站訂單還沒連 LINE（連上才通知管理群；24 小時沒連自動取消）
  const open = os.filter(o => ['new', 'ready'].includes(o.status) && !WAITL(o)), waitn = os.filter(WAITL).length;
  h += '<h2>訂單</h2>' + segctl('shop', [['open', '待處理', open.length], ...(waitn ? [['wait', '等客戶連 LINE', waitn]] : []), ['done', '已處理'], ['all', '全部']], mode);
  const list = os.filter(o => mode === 'all' || (mode === 'wait' ? WAITL(o) : mode === 'open' ? ['new', 'ready'].includes(o.status) && !WAITL(o) : !['new', 'ready'].includes(o.status)));      // 稽核 F7
  h += '<div class="card">' + (list.map(o => `<div class="item"><div class="t"><div class="n">${esc(o.id)}　${esc(o.name || '—')}（${{ worker: '夥伴', customer: '業主', guest: '網站訪客' }[o.buyer] || '—'}）　${NT(o.total)}</div>
    <div class="m">${WAITL(o) ? '<b class="shop-wait">⏳ 等客戶連上 LINE 才成立</b>（還沒通知管理群；24 小時沒連自動取消、庫存加回）<br>' : ''}${JSON.parse(o.items || '[]').map(i => `${esc(i.name)} × ${i.qty}`).join('、')}・${esc(o.delivery || '')}・${o.channel === 'web' ? '網站' : 'LINE'}${o.channel === 'web' && !o.uid && !WAITL(o) ? '（沒連 LINE，要打電話）' : ''}${o.phone ? '・' + esc(o.phone) : ''}・${SHOP_ST[o.status] || o.status}${o.handled_by ? '（' + esc(o.handled_by) + '）' : ''}・${esc((o.created_at || '').slice(5, 16).replace('T', ' '))}${o.note ? '<br>備註：' + esc(o.note) : ''}</div></div>
    <div class="a">${o.paid_at ? `<span class="st completed">已付款</span>` : (can('money') && o.status !== 'cancelled' ? btn('已收款', 'shopPaid', { id: o.id }, 'sec') : '')}
    ${o.status === 'new' && !WAITL(o) ? btn('備好了', 'shopSt', { id: o.id, s: 'ready' }, 'sec') : ''}${['new', 'ready'].includes(o.status) ? btn('已交付', 'shopSt', { id: o.id, s: 'delivered' }) + btn('取消', 'shopSt', { id: o.id, s: 'cancelled', confirm: '取消這筆訂單？庫存會加回，也會通知買家。' }, 'gray') : ''}</div></div>`).join('') || '<p class="small">沒有訂單。</p>') + '</div>';
  if (can('admin')) {
    h += `<details style="margin-top:24px"${ps.some(p => !p.active) ? ' open' : ''}><summary><h2 style="display:inline">商品設定</h2> <span class="small">價格、夥伴價、上架、說明、照片、庫存</span></summary>
      <p class="small">商品頁只放<b>瓶身標示、供應商資料和店長確認過的文字</b>；沒填的欄位網站上不會出現。「無毒、抗菌、寵物安全」這類說法要有證明才寫。庫存空白＝不控管數量。</p>`;
    h += ps.map(p => `<div class="card"><div class="n" style="font-size:18px;font-weight:700">${esc(p.name)} ${esc(p.size)} ${p.active ? '<span class="pill good">上架中</span>' : '<span class="pill">未上架</span>'}</div>
      <div class="g2" style="margin-top:8px"><label class="small">售價（元）<input type="number" min="0" id="sp-${p.id}" value="${p.price ?? ''}"></label><label class="small">夥伴價（空白＝同售價）<input type="number" min="0" id="sw-${p.id}" value="${p.worker_price ?? ''}"></label></div>
      <label class="small">問題分類（逗號分開，例如：浴室水垢,玻璃鏡面）<input id="sq-${p.id}" value="${esc(p.problems || '')}"></label>
      ${SHOP_FIELDS.map(([k, t]) => `<label class="small">${t}<textarea rows="2" id="sf-${k}-${p.id}">${esc(p[k] || '')}</textarea></label>`).join('')}
      <label class="chk"><input type="checkbox" id="sa-${p.id}"${p.active ? ' checked' : ''}> 上架（要有售價）</label>
      <div class="btns">${btn('儲存', 'shopSave', { id: p.id })}<a class="btn sec" href="/mall?p=${esc(p.id)}" target="_blank" rel="noopener">看網站上的樣子</a></div>
      <div class="g2" style="margin-top:12px"><div><div class="small">商品照片${p.image_id ? '（已上傳）' : '（還沒有）'}</div><input type="file" accept="image/*" id="si-${p.id}"> ${btn('上傳照片', 'shopImg', { id: p.id }, 'sec')}</div>
      <div><div class="small">庫存：<b>${p.stock === null || p.stock === undefined ? '不控管' : p.stock + ' 瓶'}</b></div><input type="number" min="1" id="sn-${p.id}" placeholder="數量" style="width:90px">
      ${btn('入庫', 'shopStock', { id: p.id, k: 'in' }, 'sec')}${btn('團隊領用', 'shopStock', { id: p.id, k: 'use' }, 'gray')}</div></div></div>`).join('') + '</details>';
    const lg = S.shop_stock || [];
    if (lg.length) h += `<details style="margin-top:12px"><summary class="small">最近庫存異動</summary><div class="card">${lg.slice(0, 30).map(x => `<div class="small">${esc((x.at || '').slice(5, 16).replace('T', ' '))}　${esc((ps.find(p => p.id === x.pid) || {}).name || x.pid)}　${{ in: '入庫', sale: '賣出', cancel: '取消加回', use: '團隊領用', adjust: '調整' }[x.kind] || x.kind} ${x.delta > 0 ? '+' : ''}${x.delta}${x.ref ? '　' + esc(x.ref) : ''}${x.by ? '　' + esc(x.by) : ''}</div>`).join('')}</div></details>`;
  }
  return h;
};
ACT.shopSave = async d => {
  const p = { id: d.id, price: $('#sp-' + d.id).value, worker_price: $('#sw-' + d.id).value, active: $('#sa-' + d.id).checked, problems: $('#sq-' + d.id).value };
  for (const [k] of SHOP_FIELDS) p[k] = $(`#sf-${k}-${d.id}`).value;
  const r = await api('/api/shop/product', p); await refresh(); return r.active ? '已儲存，上架中' : '已儲存（沒有價格或沒勾上架，不會出現在網站和 LINE）';
};
ACT.shopImg = async d => {
  const f = $('#si-' + d.id).files[0]; if (!f) return '請先選照片';
  await api('/api/shop/image', { id: d.id, content: 'data:image/jpeg;base64,' + await shrink(f) }); await refresh(); return '照片已上傳';
};
ACT.shopStock = async d => {
  const n = Number($('#sn-' + d.id).value || 0); if (!n) return '請填數量';
  const note = d.k === 'use' ? (prompt('領去哪裡用？（例如：10/12 客戶09案場）', '') || '') : '';
  const r = await api('/api/shop/stock', { id: d.id, kind: d.k, qty: n, note }); await refresh(); return `已記錄，現在庫存 ${r.stock} 瓶`;
};
ACT.shopPaid = async d => { await api('/api/shop/paid', { id: d.id, how: '現場／匯款' }); await refresh(); return '已記錄收款'; };
ACT.shopSt = async d => { const r = await api('/api/shop/status', { id: d.id, status: d.s }); await refresh(); return r.message; };
