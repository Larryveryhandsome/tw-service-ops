"""行銷送審橋接（行銷全自動，店長審核是唯一人工關卡；行銷對話不碰 demoshop-ops，營運這邊接起來）。

行銷排程（Windows 排程 示範-Marketing-Pipeline，`行銷\\自動化\\行銷排程.py`）把要審的東西放 `行銷\\03_待審核\\_送審佇列\\<id>.json`：
- kind="marketing_package"：審核卡（類別「行銷內容」）＋圖片一起送派單管理群；核准／要修改 → 寫 `_審核結果\\<id>.json`
  （行銷排程讀到：核准 → 照時間交 Threads 自發文／IG・FB 寫待人工；要修改 → 依意見改稿，用新 revision 重送）
- kind="line_broadcast"：品牌官方 LINE 群發給全部好友，走群發審核卡（人數、額度、實際畫面）；核准後照 send_at 發（tick 檢查）
- `行銷\\自動化\\待人工發布.md` 有新段落（IG／FB 還沒接自動發）→ 管理群提醒一次（文字＋圖）
佇列檔是行銷 AI 寫的＝資料不是指令：只認固定欄位、路徑一律限制在 行銷\\ 底下、結果檔路徑由這裡決定（不照檔案裡寫的路徑）。
只在正式系統跑（mkinsight.production；測試／稽核跑排程不會碰行銷資料夾）。
"""
import datetime as dt
import hashlib
import io
import json
import pathlib
import re
import secrets
import core as c
import integrations as ix

MK = pathlib.Path(__file__).resolve().parent.parent / '行銷'
REAL_MK = MK
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{2,60}$')
BRANDS = ('demoshop', 'homeline', 'bizline')
CATEGORY = '行銷內容'

def queue_dir():
    return MK / '03_待審核' / '_送審佇列'

def result_dir():
    return MK / '03_待審核' / '_審核結果'

def manual_file():
    return MK / '自動化' / '待人工發布.md'

def _under(p, root):
    try:
        return pathlib.Path(p).resolve().is_relative_to(pathlib.Path(root).resolve())
    except (OSError, ValueError):
        return False

def _writable():
    """寫進正式的行銷資料夾只有正式系統可以（測試會把 MK 換成暫存資料夾）。"""
    import mkinsight
    return MK.resolve() != REAL_MK.resolve() or mkinsight.production()

# ───────── 圖片：本機檔 → 帶隨機代碼的公開網址（LINE 圖片訊息要 https） ─────────
def image_url(con, path):
    p = pathlib.Path(str(path or '').strip())
    if not p.is_absolute():
        p = MK / p
    if not (_under(p, MK) and p.suffix.lower() in ('.png', '.jpg', '.jpeg') and p.is_file() and p.stat().st_size <= 10 * 1024 * 1024):
        return None
    st = p.stat()
    key = 'mkimg_of:' + hashlib.sha256(f'{p.resolve()}|{st.st_mtime_ns}|{st.st_size}'.encode()).hexdigest()[:20]   # 檔案換了 → 新網址
    tok = ix.setting(con, key)
    if not tok:
        tok = secrets.token_urlsafe(18)
        ix.set_setting(con, key, tok); ix.set_setting(con, f'mkimg:{tok}', str(p.resolve()))
    import line_bot as lb
    return f"{lb.BASE_URL}/mkimg/{tok}{'.png' if p.suffix.lower() == '.png' else '.jpg'}"

def image_bytes(con, tok, preview=False):
    """/mkimg/<代碼>：回 (bytes, mime)；縮圖（LINE previewImageUrl 1MB 以內）超過就縮成 JPEG。"""
    path = ix.setting(con, f'mkimg:{tok}') if re.fullmatch(r'[A-Za-z0-9_-]{20,40}', tok or '') else ''
    p = pathlib.Path(path) if path else None
    if not p or not _under(p, MK) or not p.is_file():
        return None, None
    raw = p.read_bytes(); mime = 'image/png' if p.suffix.lower() == '.png' else 'image/jpeg'
    if preview and len(raw) > 1_000_000:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert('RGB'); im.thumbnail((1024, 1024))
        b = io.BytesIO(); im.save(b, 'JPEG', quality=80); raw, mime = b.getvalue(), 'image/jpeg'
    return raw, mime

def _img_album(urls, alt='圖片'):
    """送審圖片改成一則可左右滑的相簿（以前一張一則，群組被洗版）；點圖開大圖。"""
    if not urls:
        return []
    return [{'type': 'flex', 'altText': alt[:390], 'contents': {'type': 'carousel', 'contents': [
        {'type': 'bubble', 'size': 'kilo', 'hero': {'type': 'image', 'url': u + '?p=1', 'size': 'full', 'aspectMode': 'fit', 'aspectRatio': '4:5',
                                                   'action': {'type': 'uri', 'uri': u}}} for u in urls[:10]]}}]

def _img_msgs(urls):
    return [{'type': 'image', 'originalContentUrl': u, 'previewImageUrl': u + '?p=1'} for u in urls]

def _to_group(con, kind, alt, msgs):
    """沒有 @ 的一般訊息送管理群（5 則一包）。"""
    import line_bot as lb
    g = lb.admin_group(con)
    to = [g] if g else list(lb.deciders(con))
    for i in range(0, len(msgs), 5):
        for t in to:
            lb.queue(con, lb.WORK_ACCOUNT, t, kind, alt, msgs[i:i + 5])

# ───────── 送審 ─────────
def _when(s):
    try:
        w = dt.datetime.fromisoformat(str(s))
        return w if w.tzinfo else w.replace(tzinfo=c.TZ)
    except (TypeError, ValueError):
        return None

def _load(p):
    try:
        d = json.loads(p.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None

def scan(con):
    """掃送審佇列：沒送過、或 revision 變大的 → 送審。回傳送出幾件。"""
    q = queue_dir()
    if not q.is_dir():
        return 0
    n = 0
    for p in sorted(q.glob('*.json'))[:20]:
        req = _load(p)
        if not req:
            continue
        rid_ = str(req.get('id') or p.stem)
        if not ID.match(rid_) or rid_ != p.stem:
            _bad(con, p.name, '編號格式不對或跟檔名不一樣'); continue
        try:
            rev = int(req.get('revision') or 1)
        except (TypeError, ValueError):
            _bad(con, p.name, 'revision 不是數字'); continue
        st = json.loads(ix.setting(con, f'mkq:{rid_}') or '{}')
        if st and rev <= int(st.get('revision') or 0):
            continue
        kind = req.get('kind')
        if rid_.startswith('HW-') and not st and not week_due(rid_):
            continue                                   # 每週發文週日 14:00 才推審核卡（之前先做好放著）
        try:
            if kind == 'marketing_package':
                r = submit_package(con, rid_, rev, req)
            elif kind == 'line_broadcast':
                r = submit_broadcast(con, rid_, rev, req)
            else:
                _bad(con, p.name, f'不認識的 kind：{str(kind)[:30]}'); continue
        except ValueError as e:
            _bad(con, p.name, str(e)); continue
        if st.get('review') and r.get('id'):
            import reviews
            reviews.supersede(con, st['review'], r['id'])            # 舊版的卡再按會提示看新版
        ix.set_setting(con, f'mkq:{rid_}', json.dumps({'revision': rev, 'review': r.get('id'), 'kind': kind, 'at': c.now()}, ensure_ascii=False))
        c.audit(con, 'mk_submit', 'staff_reviews', r.get('id') or rid_, after={'id': rid_, 'revision': rev, 'kind': kind})
        n += 1
    return n

def week_due(rid_, now=None):
    """HW-<週一YYYYMMDD>：前一天（週日）14:00 起才送審。編號看不懂就直接送。"""
    try:
        mon = dt.datetime.strptime(rid_[3:11], '%Y%m%d')
    except ValueError:
        return True
    now = now or dt.datetime.now(c.TZ).replace(tzinfo=None)
    return now >= mon - dt.timedelta(days=1) + dt.timedelta(hours=14)

def _bad(con, name, why):
    """格式不對的佇列檔：管理群說一次（同一個檔同一個原因不重複）。"""
    k = 'mkq_bad:' + hashlib.sha256(f'{name}|{why}'.encode()).hexdigest()[:16]
    if ix.setting(con, k):
        return
    ix.set_setting(con, k, c.now())
    import line_bot as lb
    lb.admin_notify(con, '⚠️ 行銷送審檔讀不懂，沒送審', [('檔案', name[:60]), ('原因', why[:120]), ('處理', '行銷排程會自己重送；一直沒好再跟負責人說')], '', '#C47F00')

def submit_package(con, rid_, rev, req):
    import reviews
    title = str(req.get('title') or rid_).strip()[:110]
    body = str(req.get('body') or '').strip()
    if not body:
        raise ValueError('送審內容是空的')
    imgs = [u for u in (image_url(con, x) for x in (req.get('images') or [])[:10]) if u]
    if len(req.get('images') or []) > len(imgs):
        _bad(con, f'{rid_}.json', f"有 {len(req.get('images') or []) - len(imgs)} 張圖讀不到（要在 行銷 資料夾裡、png/jpg、10MB 以內）")
    if rid_.startswith('HW-'):                # 每週 35 篇 Threads 週日 14:00 在中控台審 → LINE 只發一張卡（省額度），全文和圖在中控台
        return reviews.submit(con, CATEGORY, f'{title}' + (f'（第 {rev} 版）' if rev > 1 else ''),
                              body[:2500] + '\n…\n\n📋 35 篇全文和圖請到中控台：更多 › 行銷 › 待審核 › 看全文＋圖（週日 14:00 審）',
                              {'action': 'mk_package', 'id': rid_, 'revision': rev, 'brand': 'demoshop'}, '行銷排程')
    if len(body) > 4500:                     # 一則 LINE 放不下 → 全文分段先傳
        parts = [body[i:i + 4500] for i in range(0, min(len(body), 20000), 4500)]
        _to_group(con, 'mk_content', f'{rid_} 送審全文', [{'type': 'text', 'text': f'【送審全文 {k + 1}/{len(parts)}】{title}\n\n{t}'} for k, t in enumerate(parts)])
        card_body = body[:3000] + f'\n…（全文 {len(body):,} 字，分 {len(parts)} 則在上面）'
    else:
        card_body = body
    if imgs:
        _to_group(con, 'mk_images', f'{rid_} 送審圖片', [{'type': 'text', 'text': f'🖼 {title}（第 {rev} 版）的 {len(imgs)} 張圖（左右滑、點開看大圖），審核卡在下面 👇'}]
                  + _img_album(imgs, f'{title} 的 {len(imgs)} 張圖'))
    return reviews.submit(con, CATEGORY, f'{title}' + (f'（第 {rev} 版）' if rev > 1 else ''), card_body + (f'\n\n（附圖 {len(imgs)} 張在上面）' if imgs else ''),
                          {'action': 'mk_package', 'id': rid_, 'revision': rev, 'brand': req.get('brand') if req.get('brand') in BRANDS else 'demoshop'}, '行銷排程')

def submit_broadcast(con, rid_, rev, req):
    import broadcast, brands, line_bot as lb, reviews
    brand = req.get('brand') if req.get('brand') in BRANDS else 'demoshop'
    text = str(req.get('text') or '').strip()
    if not text:
        raise ValueError('群發沒有內容')
    if len(text) > 4900:
        raise ValueError('群發內容超過 LINE 一則上限（4,900 字）')
    when = _when(req.get('send_at'))
    n, q = broadcast.reach(con, brand), lb.quota(con, brand)
    name = brands.name(brand)
    body = (f"要用【{name}】官方 LINE 群發給全部好友（約 {n or '?'} 位可送達），"
            + (f"預定 {when.astimezone(c.TZ):%m/%d %H:%M} 發出" if when else '核准後馬上發出') + "。內容如下：\n\n" + text
            + f"\n\n額度：本月已用 {q.get('used', '?')}／{q.get('limit', '?')} 則。（行銷排程送來的 {rid_}" + (f" 第 {rev} 版" if rev > 1 else '') + '）'
            + ('' if lb.mode(con, brand) == 'on' else f"\n⚠️ {name} 官方 LINE 現在是測試模式，核准了也要改成正式才會發。")
            + "\n\n核准後系統會照時間自動發出；要改請按「要修改」打字。")
    return reviews.submit(con, '群發公告', f'{name} 群發：{text[:24]}…（全部好友約 {n or "?"} 位）', body,
                          {'action': 'mk_broadcast', 'id': rid_, 'revision': rev, 'brand': brand, 'text': text, 'send_at': when.isoformat() if when else '',
                           'preview': [{'type': 'text', 'text': text}]}, '行銷排程')

# ───────── 審核結果 ─────────
def write_result(rid_, rev, decision, note, by):
    if not ID.match(str(rid_)) or not _writable():
        return None
    d = result_dir(); d.mkdir(parents=True, exist_ok=True)
    p = d / f'{rid_}.json'
    p.write_text(json.dumps({'id': rid_, 'revision': rev, 'decision': decision, 'note': note or '', 'by': by or '', 'decided_at': c.now()}, ensure_ascii=False, indent=1), encoding='utf-8')
    return p

def on_decision(con, payload, decision, by, note=''):
    """reviews 核准／要修改 → 回寫結果；群發核准 → 排進待發。"""
    rid_, rev = payload.get('id'), int(payload.get('revision') or 1)
    st = json.loads(ix.setting(con, f'mkq:{rid_}') or '{}')
    if st and int(st.get('revision') or 0) > rev:
        return                                                    # 舊版的卡（已經有新版）不回寫
    write_result(rid_, rev, decision, note, by)
    if payload.get('action') == 'mk_broadcast' and decision == 'approved':
        ix.set_setting(con, f'mkbc:{rid_}', json.dumps({**{k: payload.get(k) for k in ('id', 'revision', 'brand', 'text', 'send_at')}, 'by': by, 'status': 'scheduled'}, ensure_ascii=False))
        send_due(con)

def send_due(con, now=None):
    """核准的群發到時間就發（品牌 LINE 正式模式、額度夠才發；不夠就通知管理群，不重試）。"""
    import broadcast, brands, line_bot as lb
    now = now or dt.datetime.now(c.TZ); n_sent = 0
    for key, val in con.execute("SELECT key,value FROM settings WHERE key LIKE 'mkbc:%'").fetchall():
        try:
            it = json.loads(val or '{}')
        except ValueError:
            continue
        if it.get('status') != 'scheduled':
            continue
        when = _when(it.get('send_at'))
        if when and when > now:
            continue
        brand = it.get('brand') if it.get('brand') in BRANDS else 'demoshop'; name = brands.name(brand)
        if lb.mode(con, brand) != 'on':
            if not it.get('held'):
                it['held'] = c.now(); ix.set_setting(con, key, json.dumps(it, ensure_ascii=False))
                lb.admin_notify(con, f'⏸ {name} 行銷群發已核准，等 LINE 改正式才發', [('內容', it['text'][:40]), ('原因', '官方 LINE 還在測試模式')], '', '#C47F00')
            continue
        reach, q = broadcast.reach(con, brand), lb.quota(con, brand)
        left = (q.get('limit') or 0) - (q.get('used') or 0) if q.get('type') == 'limited' else 10 ** 9
        if 'error' in q or (reach and reach > left):
            it['status'] = 'failed'; ix.set_setting(con, key, json.dumps(it, ensure_ascii=False))
            lb.admin_notify(con, f'⚠️ {name} 行銷群發沒發出', [('內容', it['text'][:40]), ('原因', q.get('error') or f'LINE 額度不夠：要 {reach} 則，本月只剩 {left} 則'),
                                                         ('處理', '到官方帳號後台加購，或請行銷改時間重送')], '', '#C62828')
            _mark_result(it, 'send_failed')
            continue
        m = [{'type': 'text', 'text': it['text'][:4900]}]
        lb.api(con, '/v2/bot/message/validate/broadcast', {'messages': m}, acc=brand)
        lb.api(con, '/v2/bot/message/broadcast', {'messages': m}, acc=brand)
        con.execute('INSERT INTO line_outbox(created_at,account,to_id,kind,alt,payload,status,sent_at) VALUES(?,?,?,?,?,?,?,?)',
                    (c.now(), brand, 'broadcast', 'mk_broadcast', it['text'][:60], json.dumps(m, ensure_ascii=False), 'sent', c.now()))
        it['status'] = 'sent'; it['sent_at'] = c.now(); ix.set_setting(con, key, json.dumps(it, ensure_ascii=False))
        c.audit(con, 'mk_broadcast', 'line_users', brand, after={'id': it.get('id'), 'reach': reach, 'by': it.get('by')})
        lb.admin_notify(con, f'📣 {name} 行銷群發已發出', [('對象', f'全部好友（約 {reach or "?"} 位）'), ('核准', it.get('by') or '—'), ('內容', it['text'][:40])], '', '#2E7D32')
        _mark_result(it, 'sent')
        n_sent += 1
    return n_sent

def _mark_result(it, what):
    p = result_dir() / f"{it.get('id')}.json"
    if not ID.match(str(it.get('id') or '')) or not p.is_file() or not _writable():
        return
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
        d[what + '_at'] = c.now(); p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
    except (OSError, ValueError):
        pass

# ───────── 待人工發布提醒 ─────────
IMG_PATH = re.compile(r'(?:[A-Za-z]:\\|\.{0,2}[\\/]?)[^\s`|<>"*?]+?\.(?:png|jpe?g)', re.I)

def manual(con):
    """待人工發布.md 有新段落 → 管理群提醒一次（文字＋圖）。"""
    p = manual_file()
    if not p.is_file():
        return 0
    text = p.read_text(encoding='utf-8')
    seen = set(json.loads(ix.setting(con, 'mkman_seen') or '[]'))
    n = 0
    import line_bot as lb
    for sec in re.split(r'(?m)^(?=## )', text):
        if not sec.startswith('## '):
            continue
        h = hashlib.sha256(sec.strip().encode()).hexdigest()[:16]
        if h in seen:
            continue
        seen.add(h)
        if n >= 5:
            continue                                   # 一次最多提醒 5 段，其他的記成看過（避免洗版）
        imgs = [u for u in (image_url(con, x) for x in IMG_PATH.findall(sec)) if u][:4]
        lb.to_managers(con, 'mk_manual', '行銷：要人工發的貼文',
                       [{'type': 'text', 'text': ('🖐 這則要人工發（IG／FB 還沒接自動發），發完到 行銷\\自動化\\發布排程.csv 把那行改「已發布」並貼連結：\n\n' + sec.strip())[:4800]}]
                       + _img_album(imgs, '要人工發的圖'), tag='行銷貼文要人工發 👇')
        n += 1
    ix.set_setting(con, 'mkman_seen', json.dumps(sorted(seen)[-500:]))
    return n

def tick(con):
    """automation.step 每分鐘呼叫：每 2 分鐘掃一次佇列、到期群發、待人工提醒。只在正式系統跑。"""
    import mkinsight
    if not mkinsight.production():
        return 0
    now = dt.datetime.now(c.TZ)
    last = ix.setting(con, 'mkbridge_at')
    if last and (now - dt.datetime.fromisoformat(last)).total_seconds() < 110:
        return 0
    ix.set_setting(con, 'mkbridge_at', now.isoformat(timespec='seconds'))
    return scan(con) + send_due(con, now) + manual(con)

# ───────── 中控台：審核詳情、行銷總覽（行銷審核也要能在中控台看、按核准） ─────────
PLAT = {'threads': 'Threads', 'instagram': 'IG 貼文', 'instagram_story': 'IG 限動', 'facebook': 'FB', 'line_broadcast': 'LINE 群發'}

def _rel(url):
    import line_bot as lb
    return (url or '').replace(lb.BASE_URL, '')          # 中控台用相對網址（本機、外網都能開）

def review_detail(con, rid):
    """一件審核的全文＋圖片＋每則平台／預定時間（行銷包讀 行銷/03_待審核/<id>/貼文.json）。決定走 staffq.act（跟 LINE 同一個 reviews.decide）。"""
    import reviews
    reviews.ensure(con)
    r = con.execute('SELECT * FROM staff_reviews WHERE id=?', (rid,)).fetchone()
    if not r:
        raise ValueError('找不到這件審核')
    try:
        p = json.loads(r['payload'] or '{}') or {}
    except ValueError:
        p = {}
    out = {k: r[k] for k in ('id', 'category', 'title', 'body', 'status', 'decided_by', 'decided_at', 'note', 'created_at', 'requested_by')}
    out.update(broadcast=r['category'] in reviews.BROADCAST, images=[], posts=[], revision=p.get('revision'), send_at=p.get('send_at') or '',
               preview=[m.get('text') for m in (p.get('preview') or []) if isinstance(m, dict) and m.get('text')][:3])
    mid = str(p.get('id') or '')
    if p.get('action') == 'mk_package' and ID.match(mid):
        q = _load(queue_dir() / f'{mid}.json') or {}
        out['images'] = [_rel(u) for u in (image_url(con, x) for x in (q.get('images') or [])[:10]) if u]
        folder = MK / '03_待審核' / mid
        d = _load(folder / '貼文.json') or {}
        for x in (d.get('posts') or [])[:40]:                 # 每週 35 篇
            if not isinstance(x, dict):
                continue
            out['posts'].append({'key': str(x.get('key') or ''), 'platform': PLAT.get(x.get('platform'), str(x.get('platform') or '')), 'time': str(x.get('time') or '').replace('T', ' '),
                                 'text': str(x.get('text') or '')[:3000], 'link': str(x.get('link') or ''), 'code': str(x.get('code') or ''), 'comment': str(x.get('comment') or '')[:300],
                                 'images': [_rel(u) for u in (image_url(con, folder / str(i)) for i in (x.get('images') or [])[:6]) if u]})
    return out

def overview(con):
    """中控台「更多 › 行銷」：待審、行銷包狀態、發布排程、待人工發、成效漏斗（點擊 → 進 LINE → 估價 → 成立）。"""
    import csv, reviews
    reviews.ensure(con)
    pend = []
    for r in reviews.pending(con):
        try:
            act = (json.loads(r['payload'] or '{}') or {}).get('action')
        except ValueError:
            act = ''
        if r['category'] == CATEGORY or act in ('mk_package', 'mk_broadcast'):
            pend.append({'id': r['id'], 'category': r['category'], 'title': r['title'], 'created_at': r['created_at']})
    pkgs = []
    for f in sorted((MK / '03_待審核').glob('*/貼文.json'))[-40:]:
        d = _load(f) or {}
        posts = [x for x in (d.get('posts') or []) if isinstance(x, dict)]
        pkgs.append({'id': str(d.get('id') or f.parent.name), 'title': str(d.get('title') or ''), 'status': str(d.get('status') or ''), 'revision': d.get('revision'),
                     'posts': len(posts), 'first': min((str(x.get('time') or '') for x in posts), default='').replace('T', ' ')})
    sched = []
    sp = MK / '自動化' / '發布排程.csv'
    if sp.is_file():
        try:
            with sp.open(encoding='utf-8-sig', newline='') as fh:
                sched = [{k: (v or '')[:200] for k, v in row.items() if k} for row in csv.DictReader(fh)][-200:]
        except (OSError, csv.Error):
            sched = []
    manual_ = [ln[3:].strip() for ln in (manual_file().read_text(encoding='utf-8').splitlines() if manual_file().is_file() else []) if ln.startswith('## ')][-20:]
    fun = {}
    def row(k):
        return fun.setdefault(k, {'code': k, 'clicks': 0, 'line': 0, 'quotes': 0, 'signed': 0, 'amount': 0, 'test': k.startswith('mk-') and k.split('-', 2)[-1].startswith('zz')})
    try:
        for k, n in con.execute("SELECT key,SUM(n) FROM menu_clicks WHERE key LIKE 'mk-%' GROUP BY key"):
            row(k)['clicks'] = n
    except Exception:
        pass
    try:
        for k, n in con.execute('SELECT campaign,COUNT(DISTINCT user_id) FROM mk_line_in GROUP BY campaign'):
            row(k)['line'] = n
    except Exception:
        pass
    for form, st, total in con.execute("SELECT form,status,total FROM quotes WHERE form LIKE '%campaign%'"):
        k = (json.loads(form or '{}') or {}).get('campaign')
        if k:
            x = row(k); x['quotes'] += 1
            if st == 'signed':
                x['signed'] += 1; x['amount'] += (total or 0) // 100
    return {'pending': pend, 'packages': pkgs[::-1], 'schedule': sched[::-1], 'manual': manual_[::-1], 'funnel': sorted(fun.values(), key=lambda x: (x['test'], x['code']))}
