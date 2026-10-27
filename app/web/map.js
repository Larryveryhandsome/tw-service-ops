'use strict';
/* 地區分布：全台各縣市的客戶分布與可支援服務人員（浮動泡泡地圖）。人員少、客戶多的地方標紅，派工前一眼看出缺口。 */
// 示意位置（經緯度，北部稍微拉開，泡泡才不會擠在一起）
const TOWNS = [['基隆市', 121.86, 25.20], ['臺北市', 121.52, 25.16], ['新北市', 121.70, 24.93], ['桃園市', 121.18, 24.96], ['新竹市', 120.84, 24.86],
  ['新竹縣', 121.15, 24.70], ['苗栗縣', 120.86, 24.47], ['臺中市', 120.70, 24.18], ['彰化縣', 120.43, 23.98], ['南投縣', 120.98, 23.84],
  ['雲林縣', 120.36, 23.70], ['嘉義市', 120.30, 23.46], ['嘉義縣', 120.66, 23.38], ['臺南市', 120.26, 23.08], ['高雄市', 120.42, 22.73],
  ['屏東縣', 120.62, 22.38], ['臺東縣', 121.08, 22.86], ['花蓮縣', 121.45, 23.82], ['\u5b9c\u862d縣', 121.68, 24.62], ['澎湖縣', 119.62, 23.57]];
const TAIWAN = [[121.0, 25.3], [121.6, 25.3], [122.0, 25.0], [121.85, 24.6], [121.6, 24.0], [121.4, 23.3], [121.0, 22.6], [120.85, 21.9], [120.6, 22.3], [120.2, 22.6],
  [120.1, 23.0], [120.05, 23.4], [120.2, 23.9], [120.6, 24.5], [120.8, 24.9]];
let MAPSEL = '';
const norm = s => String(s || '').replace(/台/g, '臺');
function mapData() {
  const act = (S.workers || []).filter(w => w.status === 'active'), all = {};
  for (const [t] of TOWNS) all[t] = { cust: [], work: [] };
  const custs = (S.customers || []).filter(c => ['active', 'paused'].includes(c.status));
  for (const c of custs) {
    const towns = new Set((IDX.sitesOf[c.id] || []).map(s => TOWNS.find(([t]) => norm(s.area).includes(t) || norm(s.address).includes(t))?.[0]).filter(Boolean));
    for (const t of towns) all[t].cust.push(c);
  }
  for (const w of act) {      // 格式「臺北市大安區」「臺北市全區」；「全台／都可以」＝每個縣市都算
    const txt = norm((w.regions || '') + ' ' + (w.notes || '')), every = /全臺|都可|遠近/.test(txt);
    for (const [t] of TOWNS) if (every || txt.includes(t)) all[t].work.push(w);
  }
  return all;
}
PAGES.map = () => {
  const D = mapData(), W = 760, H = 860;
  const px = lng => 40 + (lng - 119.3) / 2.9 * (W - 80), py = lat => 30 + (25.45 - lat) / 3.7 * (H - 60);
  const maxC = Math.max(1, ...Object.values(D).map(v => v.cust.length));
  const totalC = new Set(Object.values(D).flatMap(v => v.cust.map(c => c.id))).size, act = (S.workers || []).filter(w => w.status === 'active').length;
  let h = phead('地區分布', '哪裡客戶多、哪裡人員夠。', `全台縣市：泡泡大小＝服務中客戶數，綠色外圈＝可支援服務人員（區域含該縣市或全台），紅色＝客戶多但人員少。點泡泡看名單。服務中客戶 ${totalC} 位、可派工人員 ${act} 位。`);
  h += `<div class="mapwrap card"><svg viewBox="0 0 ${W} ${H}" class="ymap" role="img" aria-label="全台客戶與服務人員分布">
    <defs><radialGradient id="sea" cx="50%" cy="50%" r="70%"><stop offset="0" stop-color="#dbeafe"/><stop offset="1" stop-color="#f5f5f7"/></radialGradient></defs>
    <rect width="${W}" height="${H}" fill="url(#sea)" rx="18"/>
    <polygon points="${TAIWAN.map(([a, b]) => px(a) + ',' + py(b)).join(' ')}" fill="#f1ebe3" stroke="#c9bba4" stroke-width="2" stroke-linejoin="round"/>
    ${TOWNS.map(([t, lng, lat], i) => {
      const d = D[t], r = 10 + 20 * Math.sqrt(d.cust.length / maxC), gap = d.cust.length > 0 && d.work.length < Math.max(1, Math.ceil(d.cust.length / 4));
      const col = gap ? '#ff3b30' : d.cust.length ? '#6B5A4E' : '#aeaeb2';
      return `<g class="bub${MAPSEL === t ? ' on' : ''}" data-act="mapPick" data-t="${t}" style="animation-delay:${(i * 0.37).toFixed(2)}s;cursor:pointer" transform="translate(${px(lng).toFixed(1)},${py(lat).toFixed(1)})">
        <circle r="${(r + 4 + Math.min(10, d.work.length * 1.5)).toFixed(1)}" fill="none" stroke="#34c759" stroke-opacity=".55" stroke-width="${Math.min(8, 2 + d.work.length)}"/>
        <circle r="${r.toFixed(1)}" fill="${col}" fill-opacity=".88"/><text y="4" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">${d.cust.length}</text>
        <text y="${(r + 16).toFixed(0)}" text-anchor="middle" font-size="12" font-weight="600" fill="#1d1d1f">${t}</text>
        <text y="${(r + 29).toFixed(0)}" text-anchor="middle" font-size="10" fill="#248a3d">人員 ${d.work.length}</text></g>`;
    }).join('')}</svg></div>`;
  const rows = TOWNS.map(([t]) => [t, D[t]]).sort((a, b) => b[1].cust.length - a[1].cust.length);
  h += '<div class="card">' + tbl(['縣市', '服務中客戶', '可支援人員', '狀況'], rows.map(([t, d]) => `<tr class="click" data-act="mapPick" data-t="${t}">${td('縣市', `<b>${t}</b>`)}${td('客戶', d.cust.length)}${td('人員', d.work.length)}
    ${td('狀況', d.cust.length && d.work.length < Math.max(1, Math.ceil(d.cust.length / 4)) ? st('overdue', '人員不足') : d.cust.length ? st('completed', '足夠') : '<span class="small">—</span>')}</tr>`)) + '</div>';
  return h;
};
ACT.mapPick = d => {
  const D = mapData()[d.t]; MAPSEL = d.t; route(true);
  sheet(`${d.t}：客戶 ${D.cust.length}・人員 ${D.work.length}`, `<h3>可支援服務人員</h3>` + (D.work.map(w => `<div class="item click" data-act="worker" data-id="${w.id}"><div class="t"><div class="n">${esc(w.name)}</div><div class="m">${esc(w.regions || '')}　${esc(w.availability || '')}</div></div></div>`).join('') || '<div class="empty">沒有人員登記這一區</div>')
    + `<h3>服務中客戶</h3>` + (D.cust.map(c => `<div class="item click" data-act="customer" data-id="${c.id}"><div class="t"><div class="n">${esc(c.name)} <span class="small">${esc(c.channel || '')}</span></div><div class="m">${esc((IDX.sitesOf[c.id] || []).map(s => s.address || s.name).join('、'))}</div></div></div>`).join('') || '<div class="empty">這一區目前沒有服務中客戶</div>'));
};
