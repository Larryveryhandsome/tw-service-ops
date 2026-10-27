"""垃圾清收（新增服務，價格先定錨；上限、多袋、夥伴怎麼算等規劃問店長／管理層）。

・單獨預約：官方 LINE「服務項目 › 預約垃圾清收」或線上估價選「垃圾清收」→ 標準袋一袋約 100–120 公升，一袋 NT$1,200；
  一次超過 trash_max_bags 袋（預設 3）由專人報價
・服務加購：服務時一起收，每袋 NT$1,000（報價多一列「垃圾清收 N 袋」）
・夥伴：單獨清收是固定酬勞（預設每袋 600，不看時數）；服務加購預設不另加（算在時薪）
價格、上限、夥伴酬勞都在品牌的 pricing 設定（trash_*；中控台「報價 › 估價與現場估價規則」可改），
店長回答決策卡（TOPICS）會自動套用（decisions.apply → apply_answer）。
"""
import json
import re
import core as c
import integrations as ix

NAME = '垃圾清收'
INTRO = '每週固定幾天，晚上 8 點前把垃圾放門口，當晚收走（月費方案；正常家庭的量）。'      # 業務線改成垃圾代丟
NO = '廚餘、大型家具家電、大型廢棄物、危險物品（可另外詢價）'
TOPICS = ('垃圾清收：多袋與上限', '垃圾清收：夥伴酬勞', '垃圾清收：開放品牌', '垃圾清收：收了怎麼處理')
BRAND_NAMES = (('demoshop', '示範服務店'), ('homeline', '生活線'), ('bizline', '企業線'))
_BAGS = re.compile(r'(\d+)\s*袋')

def _get(o, k):
    try:
        return o[k]
    except (KeyError, IndexError, TypeError):
        return None

def is_trash(o):
    return str(_get(o, 'title') or '').startswith(NAME)

def _quote(con, o):
    ids = [x for x in (_get(o, 'id'), _get(o, 'parent_order_id')) if x]
    if not ids:
        return None
    return con.execute(f"SELECT account,items,form FROM quotes WHERE order_id IN ({','.join('?' * len(ids))}) ORDER BY created_at DESC LIMIT 1", ids).fetchone()

def bags(con, o):
    """(單獨清收袋數, 服務加購袋數)：看這張工單的報價品項（管理層改過報價就照改過的）；沒有報價的垃圾清收工單算 1 袋。"""
    main = add = 0
    q = _quote(con, o)
    if q:
        try:
            its = json.loads(q[1] or '[]')
        except ValueError:
            its = []
        for i, it in enumerate(its if isinstance(its, list) else []):
            t = f"{it.get('title') or ''} {it.get('sub') or ''}"
            if NAME not in t:
                continue
            m = _BAGS.search(t); n = int(m.group(1)) if m else 1
            if i == 0 and is_trash(o):
                main = n
            else:
                add += n
    if is_trash(o) and not main:
        main = 1
    return main, add

def _brand(con, o, q=None):
    if q and q[0]:
        return q[0]
    import brands
    cu = con.execute('SELECT * FROM customers WHERE id=?', (_get(o, 'customer_id') or '',)).fetchone()
    try:
        return brands.of_customer(dict(cu)) if cu else 'demoshop'
    except Exception:
        return 'demoshop'

def cfg(con, o=None, q=None, brand=None):
    import pricing
    return pricing.config(con, brand or (_brand(con, o, q) if o is not None else 'demoshop'))

def worker_fixed(con, o):
    """單獨垃圾清收：夥伴固定酬勞（分，不看時數）；不是垃圾清收工單回 None。"""
    import daidiu
    if daidiu.is_daidiu(o):                    # 垃圾代丟：每次 100 元（店長 ）
        return daidiu.pay(con)
    if not is_trash(o):
        return None
    k = cfg(con, o, _quote(con, o)); n = bags(con, o)[0]
    return int(k.get('trash_pay', 600)) * (n if k.get('trash_pay_mode', 'bag') == 'bag' else 1) * 100

def addon_pay(con, o):
    """服務加購垃圾清收：夥伴另加（分）；預設 0（算在時薪），店長定了每袋另加多少就照算。"""
    if is_trash(o):
        return 0
    q = _quote(con, o)
    if not q or NAME not in (q[1] or ''):
        return 0
    per = int(cfg(con, o, q).get('trash_addon_pay') or 0)
    return bags(con, o)[1] * per * 100 if per else 0

def pay_text(con, o):
    """派工卡「酬勞」那一行。"""
    import daidiu
    if daidiu.is_daidiu(o):
        return f'{daidiu.pay(con) // 100} 元（固定、不看時數）'
    k = cfg(con, o, _quote(con, o)); n = bags(con, o)[0]; per = int(k.get('trash_pay', 600))
    if k.get('trash_pay_mode', 'bag') == 'bag' and n > 1:
        return f'{per * n:,} 元（每袋 {per:,} × {n} 袋，固定、不看時數）'
    return f'{per:,} 元（固定、不看時數）'

def dispose(con):
    """派工卡「清運」：店長回答「收了怎麼處理」之前寫「照派工備註」。"""
    return ix.setting(con, 'trash_dispose') or '照派工備註（管理層會寫處理方式）'

def price_line(con, brand):
    """服務項目卡、表單用的一句價格。"""
    k = cfg(con, brand=brand); one = int(k.get('trash_bag', 1200)); more = int(k.get('trash_bag_more') or one)
    return (f'標準袋一袋約 100–120 公升・單次一袋 NT$ {one:,}' + (f'，第二袋起每袋 NT$ {more:,}' if more != one else '')
            + f"；服務時加購每袋 NT$ {int(k.get('trash_addon', 1000)):,}")

def announce_text(con, brand):
    """給業主的群發公告（送店長審核，核准才發）：改成垃圾代丟（月費方案）。給業主的文字不寫「清運」。"""
    import brands, line_bot as lb, daidiu
    plans = '\n'.join(f"・{daidiu.label(p)}：{p[2]:,} 元{'／月' if p[1] else ''}（服務會員 {p[3]:,}）" for p in daidiu.PLANS)
    return (f'您好，我是{brands.name(brand)} 😊\n\n我們新增了「垃圾代丟」服務 🗑️\n'
            '・選固定星期幾，晚上 8 點前把垃圾放門口，當晚幫您收走\n・正常家庭的量；不收' + daidiu.NO + '\n\n' + plans +
            '\n（服務會員＝有訂閱定期服務的業主；月方案每月以四週計，每月 5 號前付當月）\n\n'
            f'線上預約：{lb.BASE_URL}/book?b={brand}&l={brand}-trash&src=mk-{brand}-trash-bc\n也可以直接在這裡留言告訴我們 🙌\n\n※ 價格原則上以正式報價單為準。')

def announce_card(con, brand):
    """給業主的公告圖卡（業主訊息以圖卡為主；寫在哪裡找得到、怎麼買）：垃圾代丟。"""
    import brands, line_bot as lb, daidiu
    rows = [lb._row('怎麼收', '選固定星期幾，晚上 8 點前把垃圾放門口，當晚收走'), lb._row('收多少', f'正常家庭的量（不收{daidiu.NO}）')]
    rows += [lb._row(daidiu.label(p).replace('（', '\n（', 1), f"{p[2]:,} 元{'／月' if p[1] else ''}\n服務會員 {p[3]:,}") for p in daidiu.PLANS]
    rows += [lb._t('服務會員＝有訂閱定期服務的業主；月方案每月以四週計，每月 5 號前付當月。', size='xs', color=lb.SUB, wrap=True, margin='sm'),
             {'type': 'separator', 'margin': 'lg'},
             lb._t('在哪裡找', weight='bold', size='sm', margin='lg'), lb._t('LINE 下方選單「服務項目」→ 垃圾代丟', size='sm', wrap=True),
             lb._t('怎麼預約', weight='bold', size='sm', margin='md'), lb._t('按下面「線上預約」選方案、星期幾 → 我們確認後傳報價單 → 按確認就完成', size='sm', wrap=True)]
    return lb.flex(f'{brands.name(brand)} 新服務：垃圾代丟', lb._bubble(lb._header('新服務上線 🗑️', '垃圾代丟', '#2E7D32', brand=brand), rows,
                   [lb._btn('線上預約垃圾代丟', uri=f'{lb.BASE_URL}/book?b={brand}&l={brand}-trash&src=mk-{brand}-trash-bc', primary=True),
                    {'type': 'button', 'style': 'secondary', 'color': '#EFEDE8', 'height': 'sm', 'action': {'type': 'message', 'label': '我想先問問', 'text': '我想問垃圾代丟'}}],
                   '價格原則上以正式報價單為準。'))

def prepare_launch(con, brand, by):
    """這個品牌的垃圾清收：業務線先建好（停用）＋公告圖卡送審；核准發出時同步上線。已經有在等審核的就不重送。"""
    import broadcast, brands, reviews, json as _j
    reviews.ensure(con)
    lid = f'{brand}-trash'
    if not con.execute('SELECT 1 FROM business_lines WHERE id=?', (lid,)).fetchone():
        con.execute('INSERT INTO business_lines(id,brand_id,name,kind,services,intro,active,sort,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (lid, brand, '垃圾代丟', 'trash', _j.dumps(['垃圾代丟'], ensure_ascii=False), INTRO, 0, 2, c.now()))
        brands.reload(con)
    for rid, pl in con.execute("SELECT id,payload FROM staff_reviews WHERE status='pending' AND category='群發公告'").fetchall():
        if lid in (_j.loads(pl or '{}').get('launch') or []):
            return {'id': rid, 'status': 'pending', 'existing': True}
    return broadcast.prepare(con, brand, announce_text(con, brand), f'{brands.name(brand)}：垃圾代丟新服務公告（全部好友・核准＝同步上線）', by,
                             messages=announce_card(con, brand), launch=[lid], everyone=True)      # 發給所有業主（含還沒綁定的）

def worker_text(con):
    """給夥伴的公告（送店長審核，核准才發）：垃圾代丟。"""
    import daidiu
    return ('📣 新工作：垃圾代丟 🗑️\n\n'
            '・晚上 8 點後到業主家門口收垃圾（正常家庭的量），收走交給配合廠商（派工卡會寫）\n'
            f'・酬勞：每次 {daidiu.pay(con) // 100} 元（固定，不看時數）\n'
            '・第一次照分級發派工卡，先按先得；接的人之後固定收這一戶（每週同一天、自動排進班表），哪一次不行就按「臨時請假」\n'
            '・不用拍前後照：收完按「離場打卡」，系統會跟業主說已收走\n'
            f'・不收{daidiu.NO}；跟平常不一樣（例：量特別多）請拍照傳「回報」給管理層\n\n'
            '有問題直接在這裡留言 🙏')

def on_brands(con):
    return [r[0] for r in con.execute("SELECT brand_id FROM business_lines WHERE kind='trash' AND active=1 ORDER BY sort,brand_id")]

def set_line(con, bid, on, by=''):
    """開／關某個品牌的「垃圾清收」業務線（沒有就建一條）。回傳 True＝這次新開。"""
    lid = f'{bid}-trash'
    r = con.execute('SELECT active FROM business_lines WHERE id=?', (lid,)).fetchone()
    if on and not r:
        con.execute('INSERT INTO business_lines(id,brand_id,name,kind,services,intro,active,sort,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (lid, bid, NAME, 'trash', json.dumps([NAME], ensure_ascii=False), '到府收取打包好的垃圾（標準袋一袋約 100–120 公升）。', 1, 2, c.now()))
    elif r and bool(r[0]) != bool(on):
        con.execute('UPDATE business_lines SET active=? WHERE id=?', (1 if on else 0, lid))
    else:
        return False
    c.audit(con, 'trash_line', 'business_lines', lid, after={'active': bool(on), 'by': by})
    import brands; brands.reload(con)
    return bool(on)

def _set_all(con, **kv):
    import pricing
    for (bid,) in con.execute('SELECT id FROM brands').fetchall():
        k = pricing.key('pricing', bid)
        try:
            v = json.loads(ix.setting(con, k) or '{}') or {}
        except ValueError:
            v = {}
        v.update(kv); ix.set_setting(con, k, json.dumps(v, ensure_ascii=False))

def apply_answer(con, topic, ans, who):
    """店長回答決策卡 → 自動生效。回傳套用說明；看不懂回 ''（通知負責人）。"""
    a = re.sub(r'^[A-D]\s*', '', (ans or '').strip())
    if topic == TOPICS[0]:                       # 多袋與上限
        mx = re.search(r'最多\s*(\d+)', a); more = re.search(r'第二袋起每袋\s*(\d+)', a); one = re.search(r'(?<!起)每袋\s*(\d+)', a)
        nolimit = '不設上限' in a or '不限' in a
        if not (mx or more or one or nolimit):
            return ''
        kv = {'trash_bag_more': int(more.group(1)) if more else 0}
        if one:
            kv['trash_bag'] = int(one.group(1))
        if mx or nolimit:
            kv['trash_max_bags'] = int(mx.group(1)) if mx else 0
        _set_all(con, **kv)
        k = cfg(con, brand='demoshop'); m = int(k.get('trash_max_bags') or 0)
        return (f"已設定垃圾清收：{price_line(con, 'demoshop')}；" + (f'一次 {m} 袋以內線上初估，超過由專人報價' if m else '袋數不設上限'))
    if topic == TOPICS[1]:                       # 夥伴酬勞
        per = re.search(r'(?:每袋|每次)\s*(\d+)', a); add = re.search(r'另加\s*(\d+)', a)
        if not per and not add and '不另加' not in a:
            return ''
        mode = 'visit' if ('每次' in a or '不論' in a) else 'bag'
        kv = {'trash_pay_mode': mode, 'trash_addon_pay': int(add.group(1)) if add else 0}
        if per:
            kv['trash_pay'] = int(per.group(1))
        _set_all(con, **kv)
        k = cfg(con, brand='demoshop')
        return (f"已設定夥伴酬勞：單獨垃圾清收{'每袋' if mode == 'bag' else '每次'} {int(k['trash_pay']):,} 元（固定、不看時數）；服務加購"
                + (f"每袋另加 {kv['trash_addon_pay']:,} 元" if kv['trash_addon_pay'] else '不另加（算在時薪）'))
    if topic == TOPICS[2]:                       # 開放品牌
        if '三個' in a or '都開' in a:
            want = {b for b, _ in BRAND_NAMES}
        else:
            want = {b for b, nm in BRAND_NAMES if nm in a and not re.search(nm + r'[^，,、；;]{0,3}(維持|不開|不要|不用)', a)}
        if not want:
            return ''
        before = set(on_brands(con)); out = []
        import brands
        for b, nm in BRAND_NAMES:
            if not brands.get(con, b):
                continue
            if b in want and b not in before:              # 先不公開 → 公告圖卡送審，核准發出時同步上線
                r = prepare_launch(con, b, '系統（店長決定開放）')
                out.append(f"{nm}：公告{'已經在等審核' if r.get('existing') else '已送審'}，核准發出時同步上線")
            elif b in before and b not in want:
                set_line(con, b, False, who); out.append(f'{nm}關閉')
            if b not in want:                              # 不開的品牌：還在等審核的上線公告撤掉
                __import__('reviews').ensure(con)
                for rid, pl in con.execute("SELECT id,payload FROM staff_reviews WHERE status='pending' AND category='群發公告'").fetchall():
                    if f'{b}-trash' in (json.loads(pl or '{}').get('launch') or []):
                        con.execute("UPDATE staff_reviews SET status='superseded',note=TRIM(COALESCE(note,'') || ' 店長決定不開放') WHERE id=?", (rid,))
        return '已設定垃圾清收開放：' + '、'.join(nm for b, nm in BRAND_NAMES if b in want) + ('；' + '；'.join(out) if out else '')
    if topic == TOPICS[3]:                       # 收了怎麼處理
        if not a:
            return ''
        ix.set_setting(con, 'trash_dispose', a[:80])
        return f'已設定：垃圾代丟的派工卡「代丟」會寫「{a[:80]}」'
    return ''
