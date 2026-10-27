"""開發信退訂（開發信改用 Brevo）。

- 每封開發信底下一個退訂連結 https://www.example.com/u/<email 編碼>.<簽章>（也放在 List-Unsubscribe 標頭，信箱的「取消訂閱」按鈕一按就退）。
- 點了 → 記進 mail_unsub，寄信程式（示範服務店/開發信_臺北工商/daily_sender.py）每次寄之前讀這張表，之後不再寄。不用人工看回信。
- 簽章用系統金鑰，別人改不了網址幫別人退訂。
"""
import base64, hashlib, hmac, html as _h
import core as c

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS mail_unsub(email TEXT PRIMARY KEY, at TEXT, source TEXT DEFAULT 'link')")

def _sig(con, e):
    import worker_portal
    return hmac.new(worker_portal._key(con).encode(), f'unsub:{e}'.encode(), hashlib.sha256).hexdigest()[:20]

def token(con, email):
    e = email.strip().lower()
    return base64.urlsafe_b64encode(e.encode()).decode().rstrip('=') + '.' + _sig(con, e)

def url(con, email, base='https://www.example.com'):
    return f'{base}/u/{token(con, email)}'

def parse(con, tok):
    try:
        b, s = str(tok).rsplit('.', 1)
        e = base64.urlsafe_b64decode(b + '=' * (-len(b) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        raise PermissionError('連結無效')
    if not hmac.compare_digest(_sig(con, e), s):
        raise PermissionError('連結無效')
    return e

def unsub(con, tok, source='link'):
    e = parse(con, tok)
    ensure(con)
    con.execute('INSERT OR IGNORE INTO mail_unsub(email,at,source) VALUES(?,?,?)', (e, c.now(), source))
    return e

def confirm_page(email, tok):
    """打開連結先確認（郵件安全掃描只會開連結、不會按按鈕）。"""
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>取消訂閱｜示範服務店</title><style>body{{margin:0;background:#FAF7F2;color:#1D1D1F;font:16px/1.7 -apple-system,"PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif;padding:48px 20px}}
main{{max-width:560px;margin:0 auto;background:#fff;border-radius:18px;padding:28px}}h1{{font-size:22px;margin:0 0 10px}}p{{color:#6E6E73;margin:0 0 18px}}
button{{background:#6B5A4E;color:#fff;border:0;border-radius:999px;padding:12px 22px;font-size:16px;font-weight:700;cursor:pointer}}</style></head>
<body><main><h1>取消訂閱示範服務店的介紹信？</h1><p>{_h.escape(email)} 按下確認後，之後就不會再收到。</p>
<form method="post" action="/u/{_h.escape(tok)}"><input type="hidden" name="confirm" value="1"><button type="submit">確認取消訂閱</button></form></main></body></html>'''

def page(email):
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>已取消訂閱｜示範服務店</title><style>body{{margin:0;background:#FAF7F2;color:#1D1D1F;font:16px/1.7 -apple-system,"PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif;padding:48px 20px}}
main{{max-width:560px;margin:0 auto;background:#fff;border-radius:18px;padding:28px}}h1{{font-size:22px;margin:0 0 10px}}p{{color:#6E6E73;margin:0}}</style></head>
<body><main><h1>已取消訂閱 ✅</h1><p>{_h.escape(email)} 之後不會再收到示範服務店的介紹信。打擾了，謝謝您。</p></main></body></html>'''

def all_(con):
    ensure(con)
    return {r[0] for r in con.execute('SELECT email FROM mail_unsub')}
