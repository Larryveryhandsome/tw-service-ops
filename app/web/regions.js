'use strict';
/* 全台縣市／鄉鎮區＋104 風格地區選擇器。值的格式：「臺北市大安區」或「臺北市全區」。夥伴（可服務區域，可複選）與客戶（服務地點，單選）用同一套。 */
const TW = {
  '基隆市': '仁愛區 信義區 中正區 中山區 安樂區 暖暖區 七堵區',
  '臺北市': '中正區 大同區 中山區 松山區 大安區 萬華區 信義區 士林區 北投區 內湖區 南港區 文山區',
  '新北市': '板橋區 三重區 中和區 永和區 新莊區 新店區 樹林區 鶯歌區 三峽區 淡水區 汐止區 瑞芳區 土城區 蘆洲區 五股區 泰山區 林口區 深坑區 石碇區 \u576a林區 三芝區 石門區 八里區 平溪區 雙溪區 貢寮區 金山區 萬里區 烏來區',
  '桃園市': '桃園區 中壢區 大溪區 楊梅區 蘆竹區 大園區 龜山區 八德區 龍潭區 平鎮區 新屋區 觀音區 復興區',
  '新竹市': '東區 北區 香山區',
  '新竹縣': '竹北市 竹東鎮 新埔鎮 關西鎮 湖口鄉 新豐鄉 芎林鄉 橫山鄉 北埔鄉 寶山鄉 峨眉鄉 尖石鄉 五峰鄉',
  '苗栗縣': '苗栗市 頭份市 苑裡鎮 通霄鎮 竹南鎮 後龍鎮 卓蘭鎮 大湖鄉 公館鄉 銅鑼鄉 南庄鄉 頭屋鄉 三義鄉 西湖鄉 造橋鄉 三灣鄉 獅潭鄉 泰安鄉',
  '臺中市': '中區 東區 南區 西區 北區 西屯區 南屯區 北屯區 豐原區 東勢區 大甲區 清水區 沙鹿區 梧棲區 后里區 神岡區 潭子區 大雅區 新社區 石岡區 外埔區 大安區 烏日區 大肚區 龍井區 霧峰區 太平區 大里區 和平區',
  '彰化縣': '彰化市 員林市 鹿港鎮 和美鎮 北斗鎮 溪湖鎮 田中鎮 二林鎮 線西鄉 伸港鄉 福興鄉 秀水鄉 花壇鄉 芬園鄉 大村鄉 埔鹽鄉 埔心鄉 永靖鄉 社頭鄉 二水鄉 田尾鄉 埤頭鄉 芳苑鄉 大城鄉 竹塘鄉 溪州鄉',
  '南投縣': '南投市 埔里鎮 草屯鎮 竹山鎮 集集鎮 名間鄉 鹿谷鄉 中寮鄉 魚池鄉 國姓鄉 水里鄉 信義鄉 仁愛鄉',
  '雲林縣': '斗六市 斗南鎮 虎尾鎮 西螺鎮 土庫鎮 北港鎮 古坑鄉 大埤鄉 莿桐鄉 林內鄉 二崙鄉 崙背鄉 麥寮鄉 東勢鄉 褒忠鄉 臺西鄉 元長鄉 四湖鄉 口湖鄉 水林鄉',
  '嘉義市': '東區 西區',
  '嘉義縣': '太保市 朴子市 布袋鎮 大林鎮 民雄鄉 溪口鄉 新港鄉 六腳鄉 東石鄉 義竹鄉 鹿草鄉 水上鄉 中埔鄉 竹崎鄉 梅山鄉 番路鄉 大埔鄉 阿里山鄉',
  '臺南市': '中西區 東區 南區 北區 安平區 安南區 永康區 歸仁區 新化區 左鎮區 玉井區 楠西區 南化區 仁德區 關廟區 龍崎區 官田區 麻豆區 佳里區 西港區 七股區 將軍區 學甲區 北門區 新營區 後壁區 白河區 東山區 六甲區 下營區 柳營區 鹽水區 善化區 大內區 山上區 新市區 安定區',
  '高雄市': '新興區 前金區 苓雅區 鹽埕區 鼓山區 旗津區 前鎮區 三民區 楠梓區 小港區 左營區 仁武區 大社區 岡山區 路竹區 阿蓮區 田寮區 燕巢區 橋頭區 梓官區 彌陀區 永安區 湖內區 鳳山區 大寮區 林園區 鳥松區 大樹區 旗山區 美濃區 六龜區 內門區 杉林區 甲仙區 桃源區 那瑪夏區 茂林區 茄萣區',
  '屏東縣': '屏東市 潮州鎮 東港鎮 恆春鎮 萬丹鄉 長治鄉 麟洛鄉 九如鄉 里港鄉 鹽埔鄉 高樹鄉 萬巒鄉 內埔鄉 竹田鄉 新埤鄉 枋寮鄉 新園鄉 崁頂鄉 林邊鄉 南州鄉 佳冬鄉 琉球鄉 車城鄉 滿州鄉 枋山鄉 三地門鄉 霧臺鄉 瑪家鄉 泰武鄉 來義鄉 春日鄉 獅子鄉 牡丹鄉',
  '花蓮縣': '花蓮市 鳳林鎮 玉里鎮 新城鄉 吉安鄉 壽豐鄉 光復鄉 豐濱鄉 瑞穗鄉 富里鄉 秀林鄉 萬榮鄉 卓溪鄉',
  '臺東縣': '臺東市 成功鎮 關山鎮 卑南鄉 鹿野鄉 池上鄉 東河鄉 長濱鄉 太麻里鄉 大武鄉 綠島鄉 海端鄉 延平鄉 金峰鄉 達仁鄉 蘭嶼鄉',
  '澎湖縣': '馬公市 湖西鄉 白沙鄉 西嶼鄉 望安鄉 七美鄉',
  '金門縣': '金城鎮 金湖鎮 金沙鎮 金寧鄉 烈嶼鄉 烏坵鄉',
  '連江縣': '南竿鄉 北竿鄉 莒光鄉 東引鄉',
  '\u5b9c\u862d\u7e23': '\u5b9c\u862d\u5e02 \u7f85\u6771\u93ae \u8607\u6fb3\u93ae \u982d\u57ce\u93ae \u7901\u6eaa\u9109 \u58ef\u570d\u9109 \u54e1\u5c71\u9109 \u51ac\u5c71\u9109 \u4e94\u7d50\u9109 \u4e09\u661f\u9109 \u5927\u540c\u9109 \u5357\u6fb3\u9109'
};
const TW_COUNTIES = Object.keys(TW);
const twTowns = county => TW[county].split(' ');
const _e = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
// 只寫區名的舊資料（「大安區」）轉成新格式
function twNormalize(list) {
  const out = [];
  for (let v of list) {
    v = String(v || '').trim(); if (!v) continue;
    if (TW_COUNTIES.some(c => v.startsWith(c))) { out.push(v); continue; }
    const c = TW_COUNTIES.find(c => twTowns(c).includes(v)) || TW_COUNTIES.find(c => twTowns(c).some(t => t.startsWith(v)));
    if (c) out.push(c + (twTowns(c).find(t => t === v || t.startsWith(v)))); else out.push(v);
  }
  return [...new Set(out)];
}
/* 多選（夥伴可服務區域）：欄位只顯示已選的小標籤，點了才開選擇視窗（左縣市、右鄉鎮區，含「全區」） */
function regionMulti(host, initial, onChange) {
  let sel = new Set(twNormalize(initial || [])), cur = '臺北市';
  const draw = () => {
    host.innerHTML = `<div class="rg-field" tabindex="0" role="button">${sel.size ? [...sel].map(v => `<span class="rg-tag">${_e(v.replace('全區', '（全區）'))}</span>`).join('') : '<span class="rg-ph">點這裡選擇地區（可複選）</span>'}<span class="rg-chev">›</span></div>`;
    host.querySelector('.rg-field').onclick = open; onChange && onChange([...sel]);
  };
  const open = () => {
    const ov = document.createElement('div'); ov.className = 'rg-ov';
    const body = () => {
      const towns = twTowns(cur), all = sel.has(cur + '全區');
      ov.innerHTML = `<div class="rg-panel" role="dialog" aria-label="選擇地區"><div class="rg-head"><b>選擇地區</b><span class="small">已選 ${sel.size}</span><button type="button" class="rg-done">完成</button></div>
        <div class="rg-cols"><div class="rg-left">${TW_COUNTIES.map(c => { const n = [...sel].filter(v => v.startsWith(c)).length; return `<button type="button" data-c="${c}" class="${c === cur ? 'on' : ''}">${c}${n ? `<i>${n}</i>` : ''}</button>`; }).join('')}</div>
        <div class="rg-right"><label class="rg-all"><input type="checkbox" data-all="1"${all ? ' checked' : ''}> ${cur}全區</label>${towns.map(t => `<label><input type="checkbox" data-t="${t}"${all || sel.has(cur + t) ? ' checked' : ''}${all ? ' disabled' : ''}> ${t}</label>`).join('')}</div></div>
        <div class="rg-sel">${[...sel].map(v => `<span class="rg-tag">${_e(v.replace('全區', '（全區）'))}<button type="button" data-rm="${_e(v)}" aria-label="移除">×</button></span>`).join('') || '<span class="small">還沒選</span>'}</div></div>`;
    };
    ov.addEventListener('click', e => {
      const t = e.target;
      if (t === ov || t.classList.contains('rg-done')) { ov.remove(); draw(); return; }
      if (t.dataset.c) { cur = t.dataset.c; body(); return; }
      if (t.dataset.rm) { sel.delete(t.dataset.rm); body(); return; }
    });
    ov.addEventListener('change', e => {
      const t = e.target;
      if (t.dataset.all) { [...sel].filter(v => v.startsWith(cur)).forEach(v => sel.delete(v)); if (t.checked) sel.add(cur + '全區'); }
      else if (t.dataset.t) { t.checked ? sel.add(cur + t.dataset.t) : sel.delete(cur + t.dataset.t); }
      body();
    });
    body(); document.body.appendChild(ov);
  };
  draw();
  return { get: () => [...sel], set: v => { sel = new Set(twNormalize(v)); draw(); } };
}
/* 單選（客戶服務地點）：縣市＋鄉鎮區兩個下拉，預設臺北市 */
function regionSingle(countySel, townSel, value) {
  countySel.innerHTML = TW_COUNTIES.map(c => `<option>${c}</option>`).join('');
  const fill = () => { townSel.innerHTML = twTowns(countySel.value).map(t => `<option>${t}</option>`).join(''); };
  const v = twNormalize([value || ''])[0] || '';
  const c = TW_COUNTIES.find(x => v.startsWith(x)) || '臺北市';
  countySel.value = c; fill(); const t = v.slice(c.length); if (t && twTowns(c).includes(t)) townSel.value = t;
  countySel.addEventListener('change', fill);
  return { get: () => countySel.value + townSel.value, county: () => countySel.value, town: () => townSel.value };
}
/* 對外表單共用：送出遇到網路中斷（例如系統剛好在更新）自動重試 3 次 */
async function postRetry(url, body, tries = 3) {
  for (let i = 0; ; i++) {
    try {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if ([502, 503, 504, 530].includes(r.status) && i < tries) throw new Error('retry');
      return r;
    } catch (e) {
      if (e.pd) throw e;                       // 沒勾個資告知：直接顯示，不重試
      if (i >= tries) throw new Error('網路不穩，送不出去，請稍後再按一次');
      await new Promise(r => setTimeout(r, 2500 * (i + 1)));
    }
  }
}

/* 品牌抬頭：企業線、生活線等品牌的頁面不出現 示範／示範服務店字樣 */
function brandHead(id, name) {
  if (!id || id === 'demoshop' || !name) return;
  document.querySelectorAll('img.wm').forEach(img => { const d = document.createElement('div'); d.className = 'wm-text'; d.textContent = name; img.replaceWith(d); });
  const fix = s => s.replace(/示範服務店|示範服務店|示範服務店/g, name);
  document.title = fix(document.title);
  const walk = n => { for (const k of n.childNodes) { if (k.nodeType === 3) { if (/示範服務店/.test(k.nodeValue)) k.nodeValue = fix(k.nodeValue); } else if (k.nodeType === 1 && !/^(SCRIPT|STYLE)$/.test(k.tagName)) walk(k); } };
  walk(document.body);
  document.querySelectorAll('[placeholder]').forEach(e => e.placeholder = fix(e.placeholder));
}
/* 服務範圍外的說明 */
function outsideNote(county, counties, brandName) {
  return `🌏 我們目前沒有直接服務「${county}」，但${brandName || '我們'}的合作人脈網很廣，可以幫您找到配合的專業夥伴。照樣送出就好，專人會跟您確認。`;
}
