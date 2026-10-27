"""管理群素材準備（「有需要提供的東西，都請管理群協助準備，例如前後照」）。

- request(con, items)：派單管理群一張卡（@管理層）：要什麼、拿來做什麼、要幾張；每項一顆「我要傳」。
- 系統讀不到群組裡的照片（群組訊息不處理），所以按「我要傳」之後，請在官方 LINE 一對一聊天室傳：30 分鐘內傳的照片都算這一項。
- 照片存在 data/materials/<項目>/（不公開）；一項收齊 → 通知負責人。每天 10:00 提醒還沒收齊的（最多 7 天）。
"""
import datetime as dt
import json
import core as c
import integrations as ix

KEY = 'materials'
WAIT_MIN = 30

def load(con):
    try:
        return json.loads(ix.setting(con, KEY) or '{}') or {}
    except ValueError:
        return {}

def _save(con, st):
    ix.set_setting(con, KEY, json.dumps(st, ensure_ascii=False))

def _card(con, st, keys, title):
    import line_bot as lb
    rows, btns = [], []
    for k in keys:
        it = st[k]
        rows.append(lb._t(f"・{it['label']}（要 {it['need']} 張，已收 {it.get('got', 0)} 張）", weight='bold', wrap=True, margin='md'))
        rows.append(lb._t(it['desc'], size='xs', color=lb.SUB, wrap=True))
        btns.append(lb._btn(f"我要傳：{it['label']}"[:20], data=f'a=mat&k={k}'))
    rows.append(lb._t(f'按「我要傳」之後，請在官方 LINE 一對一聊天室傳照片（{WAIT_MIN} 分鐘內傳的都算）。照片請避開門牌、人臉和客人的個資。', size='xs', color=lb.SUB, wrap=True, margin='lg'))
    return lb.flex(title, lb._bubble(lb._header('📦 請協助準備素材', title, '#0A84FF'), rows, btns[:4]))

def request(con, items, why=''):
    """items：[(key, 名稱, 用途說明, 要幾張)]。傳一張卡到派單管理群。"""
    import line_bot as lb
    st = load(con)
    for k, label, desc, need in items:
        old = st.get(k, {})
        st[k] = {'label': label, 'desc': desc, 'need': int(need), 'got': old.get('got', 0), 'asked_at': c.now(), 'done_at': old.get('done_at', ''), 'reminded': ''}
    _save(con, st)
    g = lb.admin_group(con)
    if not g:
        return 0
    keys = [k for k, *_ in items]
    m = _card(con, st, keys, why or '系統需要的照片素材')
    lb.queue(con, lb.WORK_ACCOUNT, g, 'materials', m[0]['altText'], [lb.ping(con, '📦 請管理層協助準備素材（' + '、'.join(st[k]['label'] for k in keys) + '），按卡片上的「我要傳」就可以開始傳 🙏')] + m)
    return len(keys)

def start(con, uid, key, name=''):
    """管理層按「我要傳」：之後 30 分鐘內在官方 LINE 一對一傳的照片都算這一項。回傳 (群組回覆, 一對一訊息)。"""
    st = load(con)
    if key not in st:
        return '找不到這一項，可能已經收齊了 👍', None
    it = st[key]
    until = (dt.datetime.now(c.TZ) + dt.timedelta(minutes=WAIT_MIN)).isoformat(timespec='seconds')
    ix.set_setting(con, f'mat_wait:{uid}', json.dumps({'key': key, 'until': until}))
    return (f"好的 {name}，請到官方 LINE 一對一聊天室傳「{it['label']}」📷（{WAIT_MIN} 分鐘內傳的都算）",
            f"📦 請直接在這裡傳「{it['label']}」的照片（要 {it['need']} 張，已收 {it.get('got', 0)} 張）。\n{it['desc']}\n{WAIT_MIN} 分鐘內傳的照片都會收進這一項。")

def waiting(con, uid):
    try:
        w = json.loads(ix.setting(con, f'mat_wait:{uid}') or '{}')
    except ValueError:
        return None
    if not w or w.get('until', '') < c.now():
        return None
    return w

def pending(con):
    """還沒收齊的素材 {key: item}。"""
    return {k: it for k, it in load(con).items() if not it.get('done_at')}

def add(con, key, raw, who=''):
    """收一張素材照片（LINE 一對一、中控台上傳共用）→ 存檔、計數；收齊通知負責人。回傳 (說明, 收齊了沒)。"""
    import line_bot as lb
    st = load(con); it = st.get(key)
    if not it:
        raise ValueError('找不到這一項，可能已經收齊了')
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('這張太大了（超過 2MB），請換一張')
    d = c.DATA / 'materials' / key; d.mkdir(parents=True, exist_ok=True)
    it['got'] = it.get('got', 0) + 1
    (d / f"{dt.datetime.now(c.TZ).strftime('%Y%m%d_%H%M%S_%f')}_{it['got']:02d}.jpg").write_bytes(raw)
    done = it['got'] >= it['need'] and not it.get('done_at')
    if done:
        it['done_at'] = c.now()
        lb.queue(con, lb.WORK_ACCOUNT, lb.OWNER_UID, 'admin', f"素材收齊：{it['label']}", lb._txt(f"📦 {who or '管理層'} 傳齊了「{it['label']}」（{it['got']} 張），存在 data/materials/{key}/。"))
    _save(con, st)
    return f"收到第 {it['got']} 張（{it['label']}，要 {it['need']} 張）" + ('，收齊了，謝謝！🙏' if done else ''), done

def photo(con, uid, raw, who=''):
    """收一張素材照片 → 存檔、計數；收齊通知負責人。回傳給傳的人的一句話（沒在等就回 None）。"""
    import line_bot as lb
    w = waiting(con, uid)
    if not w:
        return None
    if not load(con).get(w['key']):
        return None
    try:
        return add(con, w['key'], raw, who)[0]
    except ValueError as e:
        return f'{e} 🙏'

def image_url(con, key, name):
    """素材照片給 LINE 用的公開網址（隨機代碼、不列目錄；只限 data/materials 底下）。拍照標準公告用。"""
    import secrets, line_bot as lb
    p = c.DATA / 'materials' / key / name
    if not p.is_file():
        return None
    k = f'matimg_of:{key}/{name}'
    tok = ix.setting(con, k)
    if not tok:
        tok = secrets.token_urlsafe(18)
        ix.set_setting(con, k, tok); ix.set_setting(con, f'matimg:{tok}', f'{key}/{name}')
    return f'{lb.BASE_URL}/matimg/{tok}.jpg'

def image_bytes(con, tok, preview=False):
    import re, io
    rel = ix.setting(con, f'matimg:{tok}') if re.fullmatch(r'[A-Za-z0-9_-]{20,40}', tok or '') else ''
    root = (c.DATA / 'materials').resolve()
    p = (root / rel).resolve() if rel else None
    if not p or root not in p.parents or not p.is_file():
        return None, None
    raw = p.read_bytes()
    if preview and len(raw) > 1_000_000:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert('RGB'); im.thumbnail((1024, 1024))
        b = io.BytesIO(); im.save(b, 'JPEG', quality=80); raw = b.getvalue()
    return raw, 'image/jpeg'

PHOTO_STD_PAIR = ('20261016_152604_994232_03.jpg', '20261016_152604_995709_04.jpg')      # 抽風扇：同一角度的服務前／完工（挑的正確範例）

def photo_standard(con):
    """夥伴拍照標準公告（不合格範例不拍，改文字）：①規則圖卡 ②正確範例照片（左右滑）。回傳 (文字版, LINE 訊息)。"""
    import line_bot as lb
    do = ['同一個位置、同一個角度、同一個地方：服務前拍一張，完工站回同一個位置再拍一張', '每個服務的區域都要拍（廚房、浴室、客廳、房間…）',
          '光線要夠、對焦清楚，手拿穩再按', '拍得出差別：對準髒的地方（油垢、水垢、灰塵）']
    dont = ['模糊、晃到、太暗', '服務前和完工的角度、位置不一樣，看不出是同一個地方', '太遠或只拍一角，看不出清了什麼', '拍到門牌、人臉、家人照片、信件或客人的個資']
    text = ('📸 前後照怎麼拍（夥伴拍照標準）\n\n前後照片是業主驗收、也是這一場發薪的依據：服務前、完工都要有，才會發這一場的薪資。\n\n✅ 要這樣拍\n'
            + '\n'.join(f'{i}. {t}' for i, t in enumerate(do, 1)) + '\n\n❌ 不要這樣\n' + '\n'.join(f'{i}. {t}' for i, t in enumerate(dont, 1))
            + '\n\n怎麼傳：打開這場的工作頁 →「服務前」「完工」上傳（現場不能拍照的案場照舊不用拍）。\n下一則是正確範例：同一個抽風扇、同一個角度。')
    rows = [lb._t('服務前、完工都要有，才會發這一場的薪資（也是業主驗收的依據）。', size='sm', wrap=True),
            lb._t('✅ 要這樣拍', weight='bold', size='sm', margin='lg', color='#2E7D32')] + \
           [lb._t(f'{i}. {t}', size='sm', wrap=True, margin='sm') for i, t in enumerate(do, 1)] + \
           [lb._t('❌ 不要這樣', weight='bold', size='sm', margin='lg', color='#C62828')] + \
           [lb._t(f'{i}. {t}', size='sm', wrap=True, margin='sm') for i, t in enumerate(dont, 1)]
    card = lb.flex('夥伴拍照標準：前後照怎麼拍', lb._bubble(lb._header('📸 夥伴拍照標準', '前後照怎麼拍', '#0A84FF'), rows, [],
                   '打開這場的工作頁 →「服務前」「完工」上傳；現場不能拍照的案場照舊不用拍。'))
    urls = [image_url(con, 'ba_good', n) for n in PHOTO_STD_PAIR]
    pics = []
    if all(urls):
        bubbles = [{'type': 'bubble', 'size': 'kilo', 'hero': {'type': 'image', 'url': u + '?p=1', 'size': 'full', 'aspectRatio': '3:4', 'aspectMode': 'cover', 'action': {'type': 'uri', 'uri': u}},
                    'body': {'type': 'box', 'layout': 'vertical', 'paddingAll': '12px', 'contents': [lb._t(lab, weight='bold', size='sm'), lb._t(sub, size='xs', color=lb.SUB, wrap=True)]}}
                   for u, (lab, sub) in zip(urls, (('① 服務前', '先站好位置拍一張'), ('② 完工', '站回同一個位置、同一個角度再拍')))]
        pics = [{'type': 'flex', 'altText': '正確範例：同一個抽風扇，服務前／完工', 'contents': {'type': 'carousel', 'contents': bubbles}}]
    return text, card + pics

def remind(con):
    """每天 10:00：還沒收齊、7 天內要的素材 → 派單管理群一張卡（每天一次）。"""
    import line_bot as lb
    now = dt.datetime.now(c.TZ); today = now.date().isoformat()
    if now.strftime('%H:%M') < '10:00' or now.strftime('%H:%M') >= '21:00' or ix.setting(con, 'standing_push') != 'on':
        return 0                          # 常態提醒併進 10:30 那一則（中控台可以直接上傳）
    st = load(con)
    week = (now - dt.timedelta(days=7)).isoformat()
    keys = [k for k, it in st.items() if not it.get('done_at') and it.get('asked_at', '') >= week and it.get('asked_at', '')[:10] < today and it.get('reminded') != today]
    if not keys:
        return 0
    for k in keys:
        st[k]['reminded'] = today
    _save(con, st)
    g = lb.admin_group(con)
    if g:
        m = _card(con, st, keys, '還沒收齊的素材')
        lb.queue(con, lb.WORK_ACCOUNT, g, 'materials', m[0]['altText'], m)
    return len(keys)
