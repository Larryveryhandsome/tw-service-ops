"""負責人：「LINE 六顆按鈕應該放日常會用到的前 6 樣常用功能，不是只用一次就沒功能的東西」→ 三方選單 v2。

依 –三方全部功能的使用次數（業主、我們、夥伴）：
- 拿掉一次性的：綁定資料（綁定連結／第一次傳訊息自動請綁／按「我的預約」沒綁會先請綁）、加入夥伴（歡迎卡、打「應徵」）、報到・綁定（看得到夥伴選單＝已綁）、服務流程（0 次）。
- 業主：線上估價・預約／我的預約／服務項目・價格／付款・匯款回報／改期・取消／真人客服
- 夥伴：今天的工作／我的班表／可排時間／現場回報／我的收入／聯絡示範服務店（「新的邀請」跟班表同一頁，合併）
- 管理層：管理群待處理（中控台）／今天／派工・缺人／報價審核／查詢・新增／測試視角
選單點擊另外記錄（postback 帶 src=rm；網址按鈕經 /go/<key> 記一次再轉）→ 約 4 週後用真實數據再調。
換選單要店長審核：submit() 送審（預覽圖），核准後 reviews.after_approve → apply()。
"""
import io
import json
import urllib.request
import core as c
import integrations as ix

W, H = 2500, 1686
SITE = 'https://ops.example.com'

def _pb(data, text):
    return {'type': 'postback', 'data': data + '&src=rm', 'displayText': text}

def customer_cells(b):
    bid, hourly = b['id'], b.get('kind') == 'hourly'
    return [('線上預約' if hourly else '線上估價', '選時段・馬上預約' if hourly else '拍照就能報價・預約', _pb('a=c_book', '線上估價'), True),      # 綁定的業主資料自動帶入
            ('我的預約', '時間・人員・報價單', _pb('a=c_my', '我的預約'), False),
            ('服務項目', '內容・價格・商城' if bid == 'demoshop' else '內容・價格', _pb('a=c_services', '服務項目'), False),      # 示範服務店服務項目卡有商城按鈕
            ('付款', '匯款・回報末五碼', _pb('a=c_pay', '付款・匯款回報'), False),
            ('改期・取消', '先看規則再申請', _pb('a=c_chg_menu', '改期・取消'), False),
            ('真人客服', '直接找專人', _pb('a=c_human', '我要找真人客服'), False)]

WORKER_CELLS = [('今天的工作', '打卡・拍照', _pb('a=w_today', '今天的工作'), True),
                ('我的班表', '邀請・已排的工作', _pb('a=w_board', '我的班表'), False),
                ('可排時間', '回報哪天可以', _pb('a=w_avail', '可排時間'), False),
                ('現場回報', '加時・晚到・狀況', _pb('a=srep_menu', '現場回報'), False),
                ('我的收入', '本月場次・預估', _pb('a=w_income', '我的收入'), False),
                ('聯絡示範服務店', '有問題直接留言', _pb('a=w_help', '聯絡示範服務店'), False)]

STAFF_CELLS = [('管理群待處理', '中控台一次處理', {'type': 'uri', 'uri': f'{SITE}/go/ops-asks'}, True),
               ('今天', '排班・即時狀況', _pb('a=m_today', '今天的排班'), False),
               ('派工・缺人', '7 天內還缺人的', _pb('a=m_short', '缺人的工作'), False),
               ('報價審核', '等確認的報價', _pb('a=m_quotes', '報價審核'), False),
               ('查詢・新增', '客戶・夥伴・工單', _pb('a=m_search', '查詢・新增'), False),
               ('測試視角', '用客戶／夥伴試', _pb('a=m_view', '切換測試視角'), False)]

GO = {'ops-asks': '/#asks', 'ops-home': '/#home'}           # /go/<key> → 轉去哪（book-<品牌> 另外處理）

def go_target(key):
    if key.startswith('book-') and key[5:].isalnum():
        return f'/book?b={key[5:]}'
    import re
    m = re.match(r'^mk-(demoshop|homeline|bizline)-([a-z0-9-]{1,40})$', key or '')
    if m:                            # 行銷追蹤連結：/go/mk-<品牌>-<代號> → 線上估價（記下是哪一篇帶來的）
        return f'/book?b={m.group(1)}&src=mk-{m.group(1)}-{m.group(2)}' + (f'&l={m.group(1)}-trash' if m.group(2).startswith('trash') else '')   # 稽核 L3：代號帶品牌；垃圾清收公告直接開垃圾清收
    return GO.get(key)

def go_log(con, key):
    """網址按鈕被點一次（不知道是誰；分析時另外看）。"""
    con.execute("CREATE TABLE IF NOT EXISTS menu_clicks(day TEXT, key TEXT, n INTEGER DEFAULT 0, PRIMARY KEY(day, key))")
    con.execute("INSERT INTO menu_clicks(day,key,n) VALUES(?,?,1) ON CONFLICT(day,key) DO UPDATE SET n=n+1", (c.now()[:10], key[:40]))

def _font(size, bold=True):
    from PIL import ImageFont
    for f in ((r'C:\Windows\Fonts\msjhbd.ttc',) if bold else ()) + (r'C:\Windows\Fonts\msjh.ttc',):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()

ICON = {'我的班表': '班', '我的收入': '收', '我的預約': '約'}      # 圖示字：同一份選單不要兩格都是「我」

def image(cells, color, name, scale=1.0):
    """六格圖（跟現在的選單同一個樣式），scale<1 給預覽用。"""
    from PIL import Image, ImageDraw
    im = Image.new('RGB', (W, H), '#FAF9F6'); d = ImageDraw.Draw(im); cw, ch = W // 3, H // 2
    for i, (t, s, _, main) in enumerate(cells):
        x, y = (i % 3) * cw, (i // 3) * ch
        d.rounded_rectangle([x + 24, y + 24, x + cw - 24, y + ch - 24], radius=48, fill=color if main else '#FFFFFF', outline='#E5E5EA', width=0 if main else 4)
        fg, sub = ('#FFFFFF', '#F2EDE8') if main else ('#1D1D1F', '#86868B')
        d.ellipse([x + cw // 2 - 70, y + 150, x + cw // 2 + 70, y + 290], fill='#FFFFFF' if main else color)
        d.text((x + cw // 2, y + 220), ICON.get(t, t[0]), font=_font(90), fill=color if main else '#FFFFFF', anchor='mm')
        d.text((x + cw // 2, y + 430), t, font=_font(116 if len(t) <= 5 else 96), fill=fg, anchor='mm')
        d.text((x + cw // 2, y + 565), s, font=_font(64, False), fill=sub, anchor='mm')
    d.text((W - 40, H - 30), name, font=_font(40, False), fill='#C7C7CC', anchor='rd')
    if scale != 1.0:
        im = im.resize((int(W * scale), int(H * scale)))
    out = io.BytesIO(); im.save(out, 'PNG', optimize=True); return out.getvalue()

def worker_cells(bid):
    """生活線帳號的夥伴選單：「聯絡示範服務店」改「聯絡我們」，其他一樣。"""
    if bid == 'demoshop':
        return WORKER_CELLS
    return [('聯絡我們', s, _pb('a=w_help', '聯絡我們'), big) if t == '聯絡示範服務店' else (t, s, a, big) for t, s, a, big in WORKER_CELLS]

def worker_key(bid):
    return 'worker_richmenu_id' if bid == 'demoshop' else f'worker_richmenu_id_{bid}'

def worker_menu(con, bid, by=''):
    """建（或重建）這個帳號的夥伴選單，掛到這個帳號已經是夥伴的人身上。回傳 (richMenuId, 掛了幾位)。"""
    import line_bot as lb, brands
    b = brands.get(con, bid) or {}
    name = '示範 夥伴' if bid == 'demoshop' else f"{b.get('name') or bid} 夥伴"
    old = ix.setting(con, worker_key(bid))
    rid = _create(con, bid, worker_cells(bid), '#6B5A4E' if bid == 'demoshop' else (b.get('color') or '#6B5A4E'), f'{name}選單 v2'[:30], '夥伴選單')
    ix.set_setting(con, worker_key(bid) + '_old', old or ''); ix.set_setting(con, worker_key(bid), rid)
    n = 0
    import automation
    for (uid, wid) in con.execute("SELECT l.user_id,l.worker_id FROM line_users l JOIN workers w ON w.id=l.worker_id WHERE l.account=? AND l.is_friend=1 AND w.status IN ('active','review')", (bid,)).fetchall():
        if uid in lb.staff_users(con) or automation.is_blacklisted(con, wid):      # 每日檢查：婉拒、暫停、停派的不掛回夥伴選單
            continue
        try:
            lb.api(con, f'/v2/bot/user/{uid}/richmenu/{rid}', {}, acc=bid); n += 1
        except ValueError:
            pass
    c.audit(con, 'worker_richmenu', 'settings', worker_key(bid), after={'rid': rid, 'linked': n, 'by': by})
    return rid, n

def menus(con):
    """要建的選單：[(種類, 品牌或 None, 名稱, 顏色, cells)]。客戶選單只建已接上 LINE 的品牌。"""
    import brands
    out = []
    for b in brands.all_(con):
        if ix.secret(con, 'line_token_' + b['id']):
            out.append(('customer', b['id'], b['name'], b.get('color') or '#6B5A4E', customer_cells(b)))
    import line_bot as lb
    for bid in lb.APPLY_ACCOUNTS:              # 開放應徵的帳號都有夥伴選單
        if ix.secret(con, 'line_token_' + bid):
            out.append(('worker', bid, '示範 夥伴' if bid == 'demoshop' else f"{brands.name(bid)} 夥伴", '#6B5A4E', worker_cells(bid)))
    out.append(('staff', 'demoshop', '示範 管理', '#1D1D1F', STAFF_CELLS))
    return out

def previews(con):
    """預覽圖寫到 web/（公開網址給 LINE 群組看）。回傳 [(種類, 品牌, 網址)]。"""
    out = []
    for kind, bid, name, color, cells in menus(con):
        fn = f"rm-v2-{kind}-{bid}.png"
        (c.ROOT / 'web' / fn).write_bytes(image(cells, color, name, 0.5))
        out.append((kind, bid, f'{SITE}/{fn}?v={c.now()[11:16].replace(":", "")}'))
    return out

def _create(con, acc, cells, color, name, chat):
    import line_bot as lb
    cw, ch = W // 3, H // 2
    menu = {'size': {'width': W, 'height': H}, 'selected': True, 'name': name[:30], 'chatBarText': chat,
            'areas': [{'bounds': {'x': (i % 3) * cw, 'y': (i // 3) * ch, 'width': cw, 'height': ch}, 'action': a} for i, (_, _, a, _) in enumerate(cells)]}
    lb.api(con, '/v2/bot/richmenu/validate', menu, acc=acc)
    rid = lb.api(con, '/v2/bot/richmenu', menu, acc=acc)['richMenuId']
    req = urllib.request.Request(f'https://api-data.line.me/v2/bot/richmenu/{rid}/content', data=image(cells, color, name),
                                 headers={'Authorization': 'Bearer ' + ix.secret(con, 'line_token_' + acc), 'Content-Type': 'image/png'}, method='POST')
    urllib.request.urlopen(req, timeout=30).read()
    return rid

def apply(con, by='', only=None):
    """核准後：建新選單並換上（業主＝各品牌預設；夥伴、管理層＝個別綁定）。舊選單不刪（要退回可以把設定改回舊 id）。
    only＝只換這幾份（例：['customer:demoshop']），其他照舊。"""
    import line_bot as lb
    done, fail = [], []
    for kind, bid, name, color, cells in menus(con):
        if only and f'{kind}:{bid}' not in only:
            continue
        try:
            if kind == 'customer':
                rid = _create(con, bid, cells, color, f'{name} 客戶選單 v2', '選單')
                ix.set_setting(con, f'customer_richmenu_{bid}', rid)
                lb.api(con, f'/v2/bot/user/all/richmenu/{rid}', {}, acc=bid)
                done.append(f'{name} 業主選單')
            elif kind == 'worker':
                rid, n = worker_menu(con, bid, by)
                done.append(f'{name}選單（{n} 位）')
            else:
                rid = _create(con, 'demoshop', cells, color, '示範 管理選單 v2', '管理選單')
                ix.set_setting(con, 'staff_richmenu_id', rid)
                n = 0
                for uid in lb.staff_users(con):
                    try:
                        lb.api(con, f'/v2/bot/user/{uid}/richmenu/{rid}', {}); n += 1
                    except ValueError:
                        pass
                done.append(f'管理選單（{n} 位）')
        except Exception as e:                       # 一份失敗不影響其他份；失敗的照舊用原本的選單
            fail.append(f'{name}：{str(e)[:60]}')
    ix.set_setting(con, 'richmenu_v2_at', json.dumps({'at': c.now(), 'by': by, 'done': done, 'fail': fail}, ensure_ascii=False))
    c.audit(con, 'richmenu_v2', 'settings', 'richmenu', after={'by': by, 'done': done, 'fail': fail})
    lb.admin_notify(con, '📱 新的 LINE 選單已換上' if done else '⚠️ LINE 選單沒有換成功', [('已換', '、'.join(done) or '—')] + ([('沒換成', '；'.join(fail))] if fail else [])
                    + ([] if only else [('之後', '選單點擊開始記錄，約 4 週後用真實數據再調一次')]), '/#brands', '#2E7D32' if not fail else '#C47F00')
    return done, fail

BODY = ('依 10/08–10/16 三方全部功能的使用次數，把 LINE 下方六顆按鈕換成「每天、常常會用到」的功能（負責人 10/16）。\n\n'
        '【業主】線上估價・預約／我的預約／服務項目／付款・匯款回報（新）／改期・取消／真人客服\n'
        '　拿掉：綁定資料（一次性，改由綁定連結、第一次傳訊息、按「我的預約」時自動請他綁）、加入夥伴（一次性，歡迎卡和打「應徵」照樣能應徵）、服務流程（8 天 0 次）\n\n'
        '【夥伴】今天的工作／我的班表／可排時間／現場回報／我的收入（新）／聯絡示範服務店\n'
        '　拿掉：新的邀請（跟班表同一頁）、報到・綁定（看得到夥伴選單＝已綁好）；臨時請假不放選單，照舊從工作卡進去\n\n'
        '【管理層】管理群待處理（直接開中控台）／今天／派工・缺人／報價審核／查詢・新增／測試視角\n\n'
        '核准後系統自動換上（業主、夥伴、管理層三份），選單點擊開始另外記錄（管理層測試不算），約 4 週後用真實數據再調一次。\n'
        '樣本說明：8 天、業主約 20–30 人、夥伴 11–17 人；一次性的、重複的看得很清楚，業主第 4–6 名主要依「業主常打字問什麼」排。')

SHOP_BODY = ('客戶反映在 LINE 找不到示範商城（負責人 10/16）。\n\n'
             '已經上線（不用審）：選單「服務項目」點進去的卡片最下面多「示範商城（耗材）」＋「逛示範商城」按鈕；新加好友的歡迎卡也有商城按鈕；打「商城」照樣可以。\n\n'
             '要審的：示範服務店業主選單「服務項目」那一格的小字，從「內容・價格」改成「內容・價格・商城」（預覽圖在上一則）。六顆按鈕不變、位置不變，只改小字，讓客戶知道從這裡進商城。\n'
             '核准後系統自動換上（只換示範服務店業主選單；夥伴、管理層、生活線、企業線都不動）。不核准就維持現在的小字。')

def submit_shop_hint(con, by='負責人（客戶找不到商城）'):
    """送店長審核：只有示範服務店業主選單的預覽圖＋審核卡。核准後只換示範服務店業主選單。"""
    import line_bot as lb, reviews
    b = next(x for x in menus(con) if x[0] == 'customer' and x[1] == 'demoshop')
    fn = 'rm-v2-customer-demoshop.png'
    (c.ROOT / 'web' / fn).write_bytes(image(b[4], b[3], b[2], 0.5))
    u = f'{SITE}/{fn}?v={c.now()[11:16].replace(":", "")}'
    g = lb.admin_group(con)
    if g:
        lb.queue(con, lb.WORK_ACCOUNT, g, 'admin_review', '示範服務店業主選單預覽', [{'type': 'image', 'originalContentUrl': u, 'previewImageUrl': u}])
    return reviews.submit(con, '圖文選單', '示範服務店業主選單「服務項目」小字加「商城」', SHOP_BODY, {'action': 'richmenu_v2', 'only': ['customer:demoshop']}, requested_by=by)

def submit(con, by='負責人（Claude 依點擊統計擬）'):
    """送店長審核（派單管理群）：先傳預覽圖，再送審核卡。回傳審核編號。"""
    import line_bot as lb, reviews
    pv = previews(con)
    imgs = [{'type': 'image', 'originalContentUrl': u, 'previewImageUrl': u} for k, b, u in pv if k != 'customer' or b in ('demoshop', 'homeline')][:4]
    g = lb.admin_group(con)
    if g:
        lb.queue(con, lb.WORK_ACCOUNT, g, 'admin_review', '新的 LINE 選單預覽', [lb.ping(con, '📱 LINE 下方六顆按鈕要換新版（依三方實際點擊）：下面是預覽圖（業主示範服務店、業主生活線、夥伴、管理層），請店長審核 👇')] + imgs)
    r = reviews.submit(con, '圖文選單', 'LINE 下方六顆按鈕換新版（業主／夥伴／管理層）', BODY, {'action': 'richmenu_v2'}, requested_by=by)
    return r
