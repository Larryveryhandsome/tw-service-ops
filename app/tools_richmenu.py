"""客戶圖文選單（每個品牌一份，品牌名稱／顏色／業務型態讀品牌設定表）。
API 設的預設選單優先於官方帳號後台做的；後台那份不用刪，取消這份就會回到後台的。
每顆按鈕都直接進系統流程（postback／網址），不「替客人送出文字」，避免觸發舊的關鍵字自動回應。"""
import io
import urllib.request
from PIL import Image, ImageDraw, ImageFont
import brands
import integrations as ix
import line_bot as lb

W, H = 2500, 1686

def cells(b):
    bid, hourly = b['id'], b.get('kind') == 'hourly'
    return [('線上預約' if hourly else '線上估價', '選時段・馬上估價' if hourly else '拍照就能報價・預約', {'type': 'uri', 'uri': f'{lb.BASE_URL}/book?b={bid}'}, True),
            ('我的預約', '時間・人員・報價單', {'type': 'postback', 'data': 'a=c_my', 'displayText': '我的預約'}, False),
            ('綁定資料', '老客戶 10 秒完成', {'type': 'postback', 'data': 'a=bind', 'displayText': '綁定我的資料'}, False),
            ('服務項目', '內容・價格・更多', {'type': 'postback', 'data': 'a=c_services', 'displayText': '服務項目'}, False),
            ('加入夥伴', '應徵夥伴', {'type': 'postback', 'data': 'a=role&r=w', 'displayText': '我要應徵夥伴'}, False) if bid == 'demoshop' else      # 夥伴只走示範服務店
            ('服務流程', '估價到完工怎麼走', {'type': 'postback', 'data': 'a=c_flow', 'displayText': '服務流程'}, False),
            ('真人客服', '直接找專人', {'type': 'postback', 'data': 'a=c_human', 'displayText': '我要找真人客服'}, False)]

def _font(size, bold=True):
    for f in ((r'C:\Windows\Fonts\msjhbd.ttc',) if bold else ()) + (r'C:\Windows\Fonts\msjh.ttc',):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()

def image(b):
    col = b.get('color') or '#6B5A4E'
    im = Image.new('RGB', (W, H), '#FAF9F6'); d = ImageDraw.Draw(im); cw, ch = W // 3, H // 2
    for i, (t, s, _, main) in enumerate(cells(b)):
        x, y = (i % 3) * cw, (i // 3) * ch
        d.rounded_rectangle([x + 24, y + 24, x + cw - 24, y + ch - 24], radius=48, fill=col if main else '#FFFFFF', outline='#E5E5EA', width=0 if main else 4)
        fg, sub = ('#FFFFFF', '#F2EDE8') if main else ('#1D1D1F', '#86868B')
        d.ellipse([x + cw // 2 - 70, y + 150, x + cw // 2 + 70, y + 290], fill='#FFFFFF' if main else col)
        d.text((x + cw // 2, y + 220), t[0], font=_font(90), fill=col if main else '#FFFFFF', anchor='mm')
        d.text((x + cw // 2, y + 430), t, font=_font(116), fill=fg, anchor='mm')
        d.text((x + cw // 2, y + 565), s, font=_font(64, False), fill=sub, anchor='mm')
    d.text((W - 40, H - 30), b['name'], font=_font(40, False), fill='#C7C7CC', anchor='rd')
    out = io.BytesIO(); im.save(out, 'PNG', optimize=True); return out.getvalue()

def create(con, bid, set_default=False):
    b = brands.get(con, bid)
    if not b:
        raise ValueError('找不到這個品牌')
    if not ix.secret(con, 'line_token_' + bid):
        raise ValueError(f"{b['name']} 的官方 LINE 還沒接上系統")
    cw, ch = W // 3, H // 2
    menu = {'size': {'width': W, 'height': H}, 'selected': True, 'name': f"{b['name']} 客戶選單"[:30], 'chatBarText': '選單',
            'areas': [{'bounds': {'x': (i % 3) * cw, 'y': (i // 3) * ch, 'width': cw, 'height': ch}, 'action': a} for i, (_, _, a, _) in enumerate(cells(b))]}
    lb.api(con, '/v2/bot/richmenu/validate', menu, acc=bid)
    rid = lb.api(con, '/v2/bot/richmenu', menu, acc=bid)['richMenuId']
    req = urllib.request.Request(f'https://api-data.line.me/v2/bot/richmenu/{rid}/content', data=image(b),
                                 headers={'Authorization': 'Bearer ' + ix.secret(con, 'line_token_' + bid), 'Content-Type': 'image/png'}, method='POST')
    urllib.request.urlopen(req, timeout=30).read()
    ix.set_setting(con, f'customer_richmenu_{bid}', rid)
    if set_default:
        lb.api(con, f'/v2/bot/user/all/richmenu/{rid}', {}, acc=bid)
    return {'richMenuId': rid, 'default': set_default}

def cancel_default(con, bid):
    lb.api(con, '/v2/bot/user/all/richmenu', method='DELETE', acc=bid)
    return {'cancelled': True}


# 負責人：重新調整六個按鈕；加「客戶視角」「夥伴視角」，隨時用客戶、夥伴的 LINE 操作看看（示範資料），打「回管理」切回
STAFF_CELLS = [('今天', '排班・營運總覽', {'type': 'postback', 'data': 'a=m_today', 'displayText': '今天的排班'}, True),
               ('派工・缺人', '7 天內還缺人的', {'type': 'postback', 'data': 'a=m_short', 'displayText': '缺人的工作'}, False),
               ('待處理', '決定・審核・報價', {'type': 'postback', 'data': 'a=m_todo', 'displayText': '待處理的事'}, False),
               ('查詢・新增', '客戶・夥伴・工單', {'type': 'postback', 'data': 'a=m_search', 'displayText': '查詢・新增'}, False),
               ('客戶視角', '用客戶的 LINE 試', {'type': 'postback', 'data': 'a=view&v=customer', 'displayText': '切換成客戶視角'}, False),
               ('夥伴視角', '用夥伴的 LINE 試', {'type': 'postback', 'data': 'a=view&v=worker', 'displayText': '切換成夥伴視角'}, False)]

def create_staff(con):
    """管理層（負責人、店長、客服）專用選單：直接在 LINE 查今天、缺人、報價、客戶，或打開新增表單。"""
    b = {'id': 'demoshop', 'name': '示範 管理', 'color': '#1D1D1F', 'kind': 'cleaning'}
    global cells
    orig = cells
    cells = lambda _b: STAFF_CELLS
    try:
        png = image(b)
    finally:
        cells = orig
    cw, ch = W // 3, H // 2
    menu = {'size': {'width': W, 'height': H}, 'selected': True, 'name': '示範 管理選單', 'chatBarText': '管理選單',
            'areas': [{'bounds': {'x': (i % 3) * cw, 'y': (i // 3) * ch, 'width': cw, 'height': ch}, 'action': a} for i, (_, _, a, _) in enumerate(STAFF_CELLS)]}
    lb.api(con, '/v2/bot/richmenu/validate', menu)
    rid = lb.api(con, '/v2/bot/richmenu', menu)['richMenuId']
    req = urllib.request.Request(f'https://api-data.line.me/v2/bot/richmenu/{rid}/content', data=png,
                                 headers={'Authorization': 'Bearer ' + ix.secret(con, 'line_token_' + lb.WORK_ACCOUNT), 'Content-Type': 'image/png'}, method='POST')
    urllib.request.urlopen(req, timeout=30).read()
    ix.set_setting(con, 'staff_richmenu_id', rid)
    linked = []
    for uid in lb.staff_users(con):
        try:
            lb.api(con, f'/v2/bot/user/{uid}/richmenu/{rid}', {}); linked.append(uid[:8])
        except ValueError:
            pass
    return {'richMenuId': rid, 'linked': linked}
