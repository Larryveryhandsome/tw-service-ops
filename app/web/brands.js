'use strict';
/* 品牌與業務（管理員）：品牌＝官方 LINE 帳號；底下的業務線（服務、陪玩陪讀、私人管家…）各自的型態、服務項目、價格。 */
let BO = null;
PAGES.brands = () => {
  if (S.role !== 'admin') return '<div class="banner warn">只有管理員可以看這頁。</div>';
  if (!BO) { api('/api/brands').then(v => { BO = v; route(true); }).catch(e => toast(e.message, true)); return phead('品牌與業務', '載入中…', ''); }
  let h = phead('品牌與業務', '新業務，加一條就上線。', '品牌＝一個官方 LINE（客戶、對話、定價、報價單抬頭都分開）。新業務放在品牌底下新增「業務線」：服務類用規模＋照片估價；時數制（陪玩陪讀、私人管家⋯）用每小時價格 × 時數 × 人數。', btn('新增品牌（新的官方 LINE）', 'brandEdit', { id: '' }, 'sec'));
  h += BO.brands.map(b => `<div class="card"><div class="secttl" style="margin-top:0"><h3>${esc(b.name)} <span class="small">${esc(b.id)}</span></h3>${b.active ? st('completed', '使用中') : st('archived', '停用')}</div>
    <dl class="kv"><dt>官方 LINE</dt><dd>${b.line_ready ? `✓ 已接上 ${esc(b.basic_id || '')}` : '<span style="color:var(--orange,#c47f00)">還沒接上</span>'}</dd>
      <dt>客戶選單</dt><dd>${b.menu ? '✓ 已建立' : '—'}</dd><dt>服務中客戶</dt><dd>${b.customers}</dd></dl>
    <h3 style="margin:16px 0 6px">業務線</h3>${(b.lines || []).map(l => `<div class="item"><div class="t"><div class="n">${esc(l.name)} ${l.active ? st('completed', l.kind === 'hourly' ? '時數制' : '服務') : st('archived', '停用')}</div>
      <div class="m">${esc((l.services || []).join('、') || '—')}${l.kind === 'hourly' ? `｜每小時 ${l.hourly.rate ? esc(l.hourly.rate) + ' 元' : '（還沒定價）'}・最少 ${esc(l.hourly.min_hours || 2)} 小時` : ''}</div></div>
      <div class="a">${btn('編輯', 'lineEdit', { id: l.id, b: b.id }, 'gray')}${l.active && b.line_ready ? btn('預約網址', 'lineBook', { id: l.id, b: b.id }, 'gray') : ''}</div></div>`).join('')}
    <div class="btns">${btn('＋ 新增業務線', 'lineEdit', { id: '', b: b.id }, 'sec')}${btn('編輯品牌', 'brandEdit', { id: b.id }, 'gray')}${btn(b.line_ready ? '重新接 LINE' : '接上官方 LINE', 'brandLine', { id: b.id }, b.line_ready ? 'gray' : '')}
      ${b.line_ready ? btn(b.menu ? '重建客戶選單' : '建立客戶選單', 'brandMenu', { id: b.id }, 'sec') : ''}</div></div>`).join('');
  return h;
};
ACT.brandEdit = d => {
  const b = BO.brands.find(x => x.id === d.id) || { id: '', name: '', title: '', channel: '', active: 1, sort: 9, intro: '' };
  sheet(d.id ? `編輯 ${esc(b.name)}` : '新增品牌（新的官方 LINE）', (d.id ? `<p class="small">代號 <b>${esc(b.id)}</b>（LINE webhook：…/line/webhook/${esc(b.id)}）</p>` : '<p class="small">品牌＝一個獨立的官方 LINE。只是新服務的話，請在既有品牌下按「新增業務線」。</p>' + fld('id', '代號（英文小寫，建立後不能改）', ''))
    + g2(fld('name', '品牌名稱（對客戶）', b.name), fld('title', '卡片抬頭（例：DEMO 示範服務店）', b.title))
    + fld('channel', '客戶來源標籤（客戶列表上顯示）', b.channel) + fld('intro', '品牌介紹（選填）', b.intro, 'textarea', { rows: 2 })
    + g2(fld('active', '啟用', b.active ? 1 : 0, 'check'), fld('sort', '排序', b.sort, 'number')),
    async f => { await api('/api/brand/save', { id: d.id || f.id, name: f.name, title: f.title, channel: f.channel, intro: f.intro, active: !!f.active, sort: Number(f.sort || 9) });
      BO = null; await refresh(); route(true); return '已儲存'; });
};
ACT.lineEdit = d => {
  const br = BO.brands.find(x => x.id === d.b), l = (br.lines || []).find(x => x.id === d.id) || { name: '', kind: 'hourly', services: [], hourly: {}, intro: '', active: 0, sort: 9 }, hr = l.hourly || {};
  sheet(d.id ? `編輯業務線：${esc(l.name)}` : `${esc(br.name)}：新增業務線`, g2(fld('name', '業務名稱（例：陪玩陪讀）', l.name), fld('kind', '型態', l.kind, 'select', { options: [['cleaning', '服務（規模＋照片估價）'], ['hourly', '時數制（每小時價格）']] }))
    + fld('services', '服務項目（一行一項，客戶預約時可選）', (l.services || []).join('\n'), 'textarea', { rows: 3 })
    + fld('intro', '介紹（客戶看到）', l.intro, 'textarea', { rows: 2 })
    + `<h3>時數制設定</h3>` + g2(fld('h_rate', '每小時價格（元）', hr.rate || '', 'number'), fld('h_min', '最少幾小時', hr.min_hours || 2, 'number', { step: '0.5' }))
    + g2(fld('h_unit', '人數單位（位／名小朋友）', hr.unit || '位'), fld('h_people', '人數欄位名稱', hr.people_label || '人數'))
    + fld('h_note', '需求說明欄位名稱', hr.note_label || '需求說明（年齡、內容、注意事項）') + fld('h_photos', '需要客戶上傳現場照片', hr.photos ? 1 : 0, 'check')
    + g2(fld('active', '啟用（時數制要先填價格）', l.active ? 1 : 0, 'check'), fld('sort', '排序', l.sort, 'number')),
    async f => {
      await api('/api/brand/line-save', { id: d.id || '', brand_id: d.b, name: f.name, kind: f.kind, intro: f.intro, active: !!f.active, sort: Number(f.sort || 9),
        services: f.services.split('\n').map(s => s.trim()).filter(Boolean),
        hourly: { rate: Number(f.h_rate || 0), min_hours: Number(f.h_min || 2), unit: f.h_unit, people_label: f.h_people, note_label: f.h_note, photos: !!f.h_photos } });
      BO = null; route(true); return '已儲存';
    });
};
ACT.lineBook = d => copy(`${location.origin}/book?b=${d.b}&l=${d.id}`, '預約網址');
ACT.brandLine = d => {
  const b = BO.brands.find(x => x.id === d.id);
  sheet(`接上 ${esc(b.name)} 的官方 LINE`, `<ol class="steps-ol"><li>LINE 官方帳號後台 › 設定 › Messaging API › 啟用（提供者選「示範服務股份有限公司」）。</li>
      <li>到 LINE Developers 該頻道：Basic settings 複製 <b>Channel secret</b>；Messaging API 分頁按 Issue 取得 <b>Channel access token</b>。</li>
      <li>貼到下面按「接上」，系統會驗證並自動設定 webhook：<div class="copybox">${esc(b.webhook)}</div></li>
      <li>回 LINE Developers 把「Use webhook」打開。</li></ol>`
    + fld('secret', 'Channel secret（32 碼）', '') + fld('token', 'Channel access token（長期）', '', 'textarea', { rows: 3 }),
    async f => { const r = await api('/api/brand/line', { id: d.id, secret: f.secret, token: f.token }); BO = null; route(true); return `已接上 ${r.basic_id}。${r.note}`; }, '接上');
};
ACT.brandMenu = async d => {
  const b = BO.brands.find(x => x.id === d.id);
  if (!confirm(`建立「${b.name}」的客戶圖文選單並設為預設？（這個官方帳號的所有客戶會看到新選單；後台原本那份不會刪，之後可以取消）`)) return;
  await api('/api/brand/menu', { id: d.id, set_default: true }); BO = null; route(true); return '已建立並設為預設選單';
};
