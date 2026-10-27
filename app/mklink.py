"""行銷追蹤連結導進官方 LINE（行銷最終都導官方 LINE；行銷 Claude 對話提出規格，寫在 行銷\\系統需求.md）。

/go/mk-<品牌>-<代號>：記點擊（richmenu2.go_log）→
  手機（含 LINE 內建瀏覽器）：直接打開該品牌官方 LINE，訊息已打好「您好，我想問XX（代號）」
  電腦：一頁 QR code（手機 LINE 掃 → 一樣打開官方 LINE、訊息已打好）＋「沒有 LINE？用網頁線上估價」
bot 收到含「我想問…（代號）」的那句 → 記「進 LINE」（mk_line_in）＋這位客人的 campaign（settings mkc:<品牌>:<uid>）→ 回拍照估價卡（連結帶 src＝代號）。
之後他用預約編號綁定、沒帶 campaign 的估價單 → 30 天內補上（tag_quote）。成效：tools/mk_report.py 點擊 → 進 LINE → 估價 → 成立。
"""
import datetime as dt
import html
import io
import json
import re
import urllib.parse
import core as c
import integrations as ix

KEY = re.compile(r'^mk-(demoshop|homeline|bizline)-([a-z0-9-]{1,40})$')
IN = re.compile(r'我想問[^（(\n]{0,20}[（(]([a-z0-9][a-z0-9-]{0,39})[）)]')
MOBILE = re.compile(r'iPhone|iPad|Android|Mobile|Line/', re.I)
DAYS = 30

def ensure(con):
    con.execute('CREATE TABLE IF NOT EXISTS mk_line_in(account TEXT, user_id TEXT, campaign TEXT, at TEXT, PRIMARY KEY(account,user_id,campaign))')

def topic(code):
    return '垃圾清收' if code.startswith('trash') else '年度保養估價' if code.startswith('dc') else '服務估價'

def line_url(con, brand, code):
    import line_bot as lb
    bid = lb.basic_id(con, brand)
    return f'https://line.me/R/oaMessage/{bid}/?' + urllib.parse.quote(f'您好，我想問{topic(code)}（{code}）') if bid else ''

def target(con, key, ua):
    """/go/ 的行銷代號：('redirect', 網址) 或 ('html', bytes)；不是行銷代號回 None。"""
    m = KEY.match(key or '')
    if not m:
        return None
    brand, code = m.groups()
    import richmenu2
    web = richmenu2.go_target(key)                  # 網頁線上估價（帶 src；垃圾清收直接開垃圾清收）
    url = line_url(con, brand, code)
    if not url:
        return ('redirect', web)
    if MOBILE.search(ua or ''):
        return ('redirect', url)
    return ('html', page(con, brand, url, web))

def page(con, brand, url, web):
    import brands, qrcode, qrcode.image.svg
    b = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=9, border=2).save(b)
    svg = b.getvalue().decode('utf-8', 'replace')
    svg = svg[svg.find('<svg'):]
    name = html.escape(brands.name(brand))
    return (f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name}｜用 LINE 問我們</title><style>body{{margin:0;font:17px/1.6 system-ui,-apple-system,"Noto Sans TC",sans-serif;background:#F6F4F1;color:#2B2B2B}}
main{{max-width:420px;margin:40px auto;padding:28px 24px;background:#fff;border-radius:18px;box-shadow:0 2px 14px rgba(0,0,0,.06);text-align:center}}
h1{{font-size:22px;margin:0 0 6px}}.qr svg{{width:240px;height:240px}}a.btn{{display:block;margin-top:14px;padding:13px;border-radius:12px;background:#06C755;color:#fff;text-decoration:none;font-weight:700}}
a.web{{display:inline-block;margin-top:16px;color:#6B5A4E}}p.s{{color:#777;font-size:15px}}</style></head><body><main>
<h1>{name}</h1><p>用手機 LINE 掃描，訊息已經幫您打好，按「傳送」就可以 😊</p><div class="qr">{svg}</div>
<a class="btn" href="{html.escape(url)}">已經在手機上？直接打開 LINE</a>
<a class="web" href="{html.escape(web)}">沒有 LINE？用網頁線上估價 →</a>
<p class="s">原則上以照片或現場確認後的正式報價單為準。</p></main></body></html>""").encode('utf-8')

def on_line(con, acc, uid, code, ev, lu):
    """bot 收到「我想問…（代號）」：記進 LINE＋campaign → 回拍照估價卡。"""
    import line_bot as lb, brands
    camp = f'mk-{acc}-{code}'
    ensure(con)
    con.execute('INSERT OR IGNORE INTO mk_line_in(account,user_id,campaign,at) VALUES(?,?,?,?)', (acc, uid, camp, c.now()))
    ix.set_setting(con, f'mkc:{acc}:{uid}', json.dumps({'campaign': camp, 'at': c.now()}))
    url = f'{lb.BASE_URL}/book?b={acc}&src={camp}'
    if code.startswith('trash') and brands.line(con, f'{acc}-trash') and brands.line(con, f'{acc}-trash')['active']:
        url += f'&l={acc}-trash'
    cid = (lu or {}).get('customer_id')
    if cid:
        import worker_portal
        url += '&t=' + worker_portal.cust_token(con, acc, uid, cid)          # 已綁定的客人：資料幫他帶入
    t = topic(code)
    card = lb.flex(f'{brands.name(acc)}：拍照就能線上估價', lb._bubble(lb._header('您好 😊', f'{t}，拍照就能估價', brand=acc),
        [lb._t('拍幾張要服務的地方，填規模就有初估；主管確認後，正式報價單會傳到這個 LINE。' if t != '垃圾清收' else
               '選袋數、垃圾放在哪裡、希望的時間就好（照片選填）；主管確認後，正式報價單會傳到這個 LINE。', wrap=True),
         lb._t('有問題也可以直接在這裡留言 🙌', wrap=True, margin='md', color=lb.SUB)],
        [lb._btn('拍照線上估價' if t != '垃圾清收' else '預約垃圾清收', uri=url, primary=True)], '原則上以照片或現場確認後的正式報價單為準。'))
    return lb.reply(con, acc, ev['replyToken'], card, 'mk_in')

def campaign_of(con, acc, uid):
    """這位客人 30 天內從哪個行銷代號進 LINE（沒有回 ''）。"""
    try:
        d = json.loads(ix.setting(con, f'mkc:{acc}:{uid}') or '{}')
    except ValueError:
        return ''
    if not d.get('campaign') or (dt.datetime.now(c.TZ) - dt.datetime.fromisoformat(d['at'])).days > DAYS:
        return ''
    return d['campaign']

def tag_quote(con, inquiry_id, acc, uid):
    """預約編號綁定時：估價單還沒有 campaign → 補上這位客人的行銷代號。"""
    camp = campaign_of(con, acc, uid)
    if not camp:
        return 0
    n = 0
    for qid, form in con.execute('SELECT id,form FROM quotes WHERE inquiry_id=?', (inquiry_id,)).fetchall():
        f = json.loads(form or '{}')
        if not f.get('campaign'):
            f['campaign'] = camp
            con.execute('UPDATE quotes SET form=? WHERE id=?', (json.dumps(f, ensure_ascii=False), qid)); n += 1
    return n
