"""多人帳號：密碼雜湊、登入節流、角色權限、工作階段。"""
import hashlib
import hmac
import secrets
import time
import core

ROLES = {'admin': '管理員', 'staff': '客服排班', 'finance': '帳務', 'cleaner': '服務人員'}
# 能力：ops 客戶/排班/派工；money 帳款；admin 帳號與串接
CAN = {'admin': {'ops', 'money', 'admin'}, 'staff': {'ops'}, 'finance': {'ops', 'money'}, 'cleaner': set()}
LOCAL_ID = 'u-local'           # 本機啟動器登入用的內建管理員，不能用密碼登入
SESSION_SECONDS = {'admin': 30 * 86400, 'staff': 60 * 86400, 'finance': 30 * 86400, 'cleaner': 90 * 86400}   # 手機常開：有在用就自動續期
INITIAL_PW_HOURS = 72          # 管理員給的初始密碼 72 小時內沒換就失效，避免被別人先登入
MAX_FAILS_PER_IP = 20          # 15 分鐘內
MAX_FAILS_PER_USER = 6         # 連錯 6 次鎖 15 分鐘

def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 310_000).hex()
    return f'pbkdf2${salt}${h}'

def check_pw(pw, stored):
    try:
        kind, salt, h = stored.split('$')
    except ValueError:
        return False
    if kind != 'pbkdf2':
        return False
    return hmac.compare_digest(hash_pw(pw, salt).split('$')[2], h)

def ensure_local(con):
    if not con.execute('SELECT 1 FROM users WHERE id=?', (LOCAL_ID,)).fetchone():
        con.execute("INSERT INTO users(id,username,name,pw,role,must_change) VALUES(?,?,?,?,?,0)",
                    (LOCAL_ID, '__local__', '本機管理員', '!', 'admin'))

def validate_pw(pw, role):
    need = 10 if role in ('admin', 'finance') else 6
    if len(pw or '') < need:
        raise ValueError(f'密碼至少 {need} 碼')
    if pw.isdigit() and role in ('admin', 'finance'):
        raise ValueError('管理員／帳務密碼不能只有數字')

def validate_initial(pw):
    """管理員開帳號／重設時給的臨時密碼：至少 6 碼即可（本人登入後一定要換成正式密碼）。"""
    if len(pw or '') < 6:
        raise ValueError('初始密碼至少 6 碼')

def public(u):
    return {k: u[k] for k in ('id', 'username', 'name', 'role', 'worker_id', 'active', 'must_change', 'last_login', 'created_at')}

_DUMMY = hash_pw('dummy-password-for-timing')

def login(con, username, pw, ip):
    now = int(time.time())
    con.execute('DELETE FROM login_attempts WHERE at<?', (now - 900,))
    if con.execute('SELECT COUNT(*) FROM login_attempts WHERE key=?', ('ip:' + ip,)).fetchone()[0] >= MAX_FAILS_PER_IP:
        raise PermissionError('嘗試次數過多，請 15 分鐘後再試')
    u = con.execute('SELECT * FROM users WHERE username=? AND id!=?', ((username or '').strip(), LOCAL_ID)).fetchone()
    ok = check_pw(pw or '', u['pw']) if u else (check_pw(pw or '', _DUMMY) and False)      # 帳號不存在也算一次，回應時間一樣
    # 資安：密碼錯一律同一句話，不透露帳號存不存在、有沒有被鎖；只有密碼對的本人才看到「鎖定／過期」
    if ok and u['locked_until'] > now:
        raise PermissionError('帳號暫時鎖定，請 15 分鐘後再試或請管理員重設')
    if ok and u['must_change'] and u['pw_set_at'] and now - u['pw_set_at'] > INITIAL_PW_HOURS * 3600:
        raise PermissionError('初始密碼已超過 72 小時沒有更換，已失效，請管理員重設')
    if not ok or not u['active'] or u['locked_until'] > now:
        con.execute('INSERT INTO login_attempts VALUES(?,?)', ('ip:' + ip, now))
        if u:
            fails = u['failed'] + 1
            con.execute('UPDATE users SET failed=?,locked_until=? WHERE id=?',
                        (fails, now + 900 if fails >= MAX_FAILS_PER_USER else 0, u['id']))
        raise PermissionError('帳號或密碼不正確')
    con.execute('UPDATE users SET failed=0,locked_until=0,last_login=? WHERE id=?', (core.now(), u['id']))
    return dict(u)

def new_session(con, user, ip=''):
    token = secrets.token_urlsafe(32); csrf = secrets.token_urlsafe(32); now = int(time.time())
    life = SESSION_SECONDS.get(user['role'], 86400) if user['id'] != LOCAL_ID else 12 * 3600
    con.execute('DELETE FROM sessions WHERE expires<?', (now,))
    con.execute('INSERT INTO sessions(token_hash,expires,csrf,user_id,ip,created) VALUES(?,?,?,?,?,?)',
                (hashlib.sha256(token.encode()).hexdigest(), now + life, csrf, user['id'], ip, now))
    return token, life

MAGIC_MINUTES = 30

def magic_link(con, user_id, minutes=MAGIC_MINUTES):
    """一次性登入連結（不經手密碼）：30 分鐘內有效、只能用一次；登入後一定要自己設定新密碼（must_change=1）。用 LINE 一對一傳給本人。"""
    import json, integrations as ix
    u = con.execute('SELECT id,active FROM users WHERE id=?', (user_id,)).fetchone()
    if not u or not u['active']:
        raise ValueError('帳號不存在或已停用')
    token = secrets.token_urlsafe(32)
    ix.set_setting(con, 'magic:' + hashlib.sha256(token.encode()).hexdigest(), json.dumps({'uid': user_id, 'exp': int(time.time()) + minutes * 60}))
    return token

def use_magic(con, token):
    import json, integrations as ix
    key = 'magic:' + hashlib.sha256((token or '').encode()).hexdigest()
    raw = ix.setting(con, key)
    con.execute('DELETE FROM settings WHERE key=?', (key,))           # 只能用一次（不管成功與否）
    try:
        d = json.loads(raw or '{}')
    except ValueError:
        d = {}
    if not d or time.time() > d.get('exp', 0):
        raise PermissionError('登入連結已使用或已過期，請再跟管理員要一次')
    u = con.execute('SELECT * FROM users WHERE id=? AND active=1', (d['uid'],)).fetchone()
    if not u:
        raise PermissionError('帳號已停用')
    con.execute('UPDATE users SET must_change=1,failed=0,locked_until=0 WHERE id=?', (u['id'],))      # 進去先設定自己的密碼
    return dict(con.execute('SELECT * FROM users WHERE id=?', (u['id'],)).fetchone())

def session_user(con, token):
    hashed = hashlib.sha256(token.encode()).hexdigest()
    row = con.execute('SELECT s.token_hash,s.csrf,s.expires,s.created AS s_created,u.* FROM sessions s JOIN users u ON u.id=s.user_id '
                      'WHERE s.token_hash=? AND s.expires>?', (hashed, int(time.time()))).fetchone()
    if not row or not row['active']:
        return None
    cap = {'admin': 14, 'finance': 14}.get(row['role'])          # 資安：看得到全部資料的帳號，登入最長 14 天就要重新登入
    if cap and row['s_created'] and time.time() - int(row['s_created']) > cap * 86400:
        con.execute('DELETE FROM sessions WHERE token_hash=?', (hashed,)); con.commit()
        return None
    life = SESSION_SECONDS.get(row['role'], 86400) if row['id'] != LOCAL_ID else 12 * 3600
    if row['expires'] - time.time() < life / 2:          # 有在用就續期（手機長時間開著不會被登出）
        con.execute('UPDATE sessions SET expires=? WHERE token_hash=?', (int(time.time()) + life, hashed)); con.commit()
    return dict(row)

def logout(con, token_hash):
    con.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash,))

def change_pw(con, user, old, new):
    u = core.get(con, 'users', user['id'])
    if not u['must_change'] and not check_pw(old or '', u['pw']):
        raise ValueError('目前密碼不正確')
    validate_pw(new, u['role'])
    if check_pw(new, u['pw']):
        raise ValueError('新密碼不能跟目前的密碼一樣')
    if new.strip().lower() == u['username'].lower():
        raise ValueError('密碼不能跟帳號一樣')
    con.execute('UPDATE users SET pw=?,must_change=0 WHERE id=?', (hash_pw(new), u['id']))
    # 其他裝置的登入全部登出
    con.execute('DELETE FROM sessions WHERE user_id=? AND token_hash!=?', (u['id'], user['token_hash']))
    core.audit(con, 'change_password', 'users', u['id'])
    return {'saved': True}

def save_user(con, p):
    """管理員建立／修改帳號。新帳號或重設密碼一律首次登入強制改。"""
    role = p.get('role')
    if role not in ROLES:
        raise ValueError('請選擇角色')
    name = (p.get('name') or '').strip(); username = (p.get('username') or '').strip()
    if not name or not username:
        raise ValueError('姓名、帳號必填')
    if not username.replace('_', '').replace('.', '').isalnum() or not username.isascii() or len(username) < 3:
        raise ValueError('帳號請用 3 個以上英文字母或數字')
    worker = p.get('worker_id') or None
    if role == 'cleaner':
        if not worker:
            raise ValueError('服務人員帳號必須對應一位夥伴')
        core.get(con, 'workers', worker)
    else:
        worker = None
    if p.get('id'):
        u = core.get(con, 'users', p['id'])
        if u['id'] == LOCAL_ID:
            raise ValueError('內建本機帳號不能修改')
        active = 1 if p.get('active', True) else 0
        # 管理員就算全被停用，本機啟動器（開啟示範服務店.vbs）永遠能進來，不會被鎖在外面
        con.execute('UPDATE users SET name=?,username=?,role=?,worker_id=?,active=? WHERE id=?', (name, username, role, worker, active, u['id']))
        if p.get('password'):
            validate_initial(p['password'])
            con.execute('UPDATE users SET pw=?,must_change=1,failed=0,locked_until=0,pw_set_at=? WHERE id=?', (hash_pw(p['password']), int(time.time()), u['id']))
        if not active or p.get('password') or role != u['role']:
            con.execute('DELETE FROM sessions WHERE user_id=?', (u['id'],))   # 停權／改權限立即生效
        key = u['id']; core.audit(con, 'update', 'users', key, public(u), {'role': role, 'active': active, 'password_reset': bool(p.get('password'))})
    else:
        validate_initial(p.get('password', ''))
        key = core.uid('u')
        con.execute('INSERT INTO users(id,username,name,pw,role,worker_id,pw_set_at) VALUES(?,?,?,?,?,?,?)',
                    (key, username, name, hash_pw(p['password']), role, worker, int(time.time())))
        core.audit(con, 'create', 'users', key, after={'username': username, 'role': role})
    return public(core.get(con, 'users', key))

def list_users(con):
    return [public(dict(r)) for r in con.execute('SELECT * FROM users WHERE id!=? ORDER BY role,name', (LOCAL_ID,))]
