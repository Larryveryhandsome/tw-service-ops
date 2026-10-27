"""行銷素材／企劃收件匣（「行銷的部分要有地方讓我傳送素材照，還有上傳想要給你參考的文字跟企畫內容」）。

中控台「更多 › 行銷」最上面：選品牌、寫說明（想做什麼、參考文字、企劃內容）、選照片 → 存到
  行銷/素材庫/店長上傳/<日期時間>_<品牌>/（照片＋說明.md）
Claude 做行銷包（每週 Threads、活動檔）前會先讀這個資料夾；用過的在說明.md 最後加一行「已用：<代號>」。
照片只存在本機行銷資料夾（不公開、不傳 GPT）；客戶照片要有授權才能拿去對外用（行銷紅線）。
"""
import base64, datetime as dt, json, pathlib, re
import core as c

ROOT = pathlib.Path(__file__).resolve().parent.parent / '行銷' / '素材庫' / '店長上傳'
BRANDS = {'demoshop': '示範服務店', 'homeline': '生活小幫手', 'bizline': '企業服務線', 'all': '三個品牌都可以'}

def save(con, p, by):
    brand = p.get('brand') if p.get('brand') in BRANDS else 'all'
    note = str(p.get('note', '')).strip()[:8000]
    files = [f for f in (p.get('files') or []) if isinstance(f, str)][:30]
    if not note and not files and not p.get('folder'):
        raise ValueError('寫一點說明或選照片再送出')
    fold = str(p.get('folder') or '')
    if fold:                                           # 同一批的第 2 張以後：加到同一個資料夾
        if not re.fullmatch(r'\d{8}_\d{6}_(demoshop|homeline|bizline|all)', fold) or not (ROOT / fold).is_dir():
            raise ValueError('找不到這一批，請重新上傳')
        d = ROOT / fold
        start = len(list(d.glob('*.jpg')))
    else:
        d = ROOT / f"{dt.datetime.now(c.TZ):%Y%m%d_%H%M%S}_{brand}"
        d.mkdir(parents=True, exist_ok=True); start = 0
    import recruit
    n = 0
    for i, f in enumerate(files, start + 1):
        m = re.match(r'^data:image/(jpeg|jpg|png|webp|heic);base64,(.+)$', f, re.S)
        if not m:
            continue
        raw = base64.b64decode(m.group(2))
        if len(raw) > 15 * 1024 * 1024:
            continue
        try:
            raw = recruit.shrink(raw)
        except Exception:
            pass
        (d / f'{i:02d}.jpg').write_bytes(raw); n += 1
    if fold:
        return {'saved': True, 'folder': d.name, 'photos': start + n}
    (d / '說明.md').write_text(f"# 行銷素材／企劃（{BRANDS[brand]}）\n\n- 上傳：{by}，{c.now()[:16].replace('T', ' ')}\n- 照片：{n} 張\n\n## 說明／參考文字／企劃\n\n{note or '（沒寫）'}\n", encoding='utf-8')
    c.audit(con, 'mk_inbox', 'settings', d.name, after={'by': by, 'brand': brand, 'photos': n})
    return {'saved': True, 'folder': d.name, 'photos': n}

def listing(limit=30):
    if not ROOT.exists():
        return []
    out = []
    for d in sorted([x for x in ROOT.iterdir() if x.is_dir()], reverse=True)[:limit]:
        t = (d / '說明.md').read_text(encoding='utf-8') if (d / '說明.md').exists() else ''
        body = t.split('## 說明／參考文字／企劃', 1)[-1].strip()
        out.append({'folder': d.name, 'brand': BRANDS.get(d.name.rsplit('_', 1)[-1], ''), 'photos': len(list(d.glob('*.jpg'))),
                    'note': body[:300], 'used': '已用：' in t})
    return out
