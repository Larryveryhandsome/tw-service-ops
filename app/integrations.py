"""LINE read-only; Google restricted to app-created calendar, encrypted at rest."""
import base64
import ctypes
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import core

BASE='http://127.0.0.1:4781'
SCOPES='https://www.googleapis.com/auth/calendar.app.created'

class Blob(ctypes.Structure):
    _fields_=[('cbData',ctypes.c_uint32),('pbData',ctypes.POINTER(ctypes.c_ubyte))]

def protect(text, decrypt=False):
    if os.name!='nt':
        raise RuntimeError('在畫面上儲存金鑰需要 Windows（DPAPI 加密）；Linux／macOS 請改用環境變數 OPS_SECRET_<KEY>，見 docs/金鑰申請.md')
    raw=base64.b64decode(text) if decrypt else text.encode()
    buf=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte))); dest=Blob()
    if decrypt:
        ok=ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(dest))
    else:
        ok=ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source),'Demo Ops',None,None,None,1,ctypes.byref(dest))
    if not ok: raise RuntimeError('Windows 憑證加密失敗')
    try:
        result=ctypes.string_at(dest.pbData,dest.cbData)
        return result.decode() if decrypt else base64.b64encode(result).decode()
    finally:
        free=ctypes.windll.kernel32.LocalFree
        free.argtypes=[ctypes.c_void_p]; free.restype=ctypes.c_void_p
        free(ctypes.cast(dest.pbData,ctypes.c_void_p))

def put_secret(con,key,value):
    con.execute('INSERT INTO secrets(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,protect(value)))

def env_secret_name(key):
    """金鑰對應的環境變數名稱：line_token_demoshop → OPS_SECRET_LINE_TOKEN_DEMOSHOP。"""
    return 'OPS_SECRET_' + ''.join(ch if ch.isalnum() else '_' for ch in str(key)).upper()

def secret(con,key):
    """讀金鑰：先看環境變數（OPS_SECRET_<KEY>，預設空值＝沒設定），沒有才讀資料庫裡用 Windows DPAPI 加密的值。
    開源版不附任何金鑰；要串 LINE／LINE Pay／Google 請照 docs/金鑰申請.md 自己申請。"""
    v=os.environ.get(env_secret_name(key),'').strip()
    if v:
        return v
    r=con.execute('SELECT value FROM secrets WHERE key=?',(key,)).fetchone()
    return protect(r[0],True) if r else None

def setting(con,key,default=''):
    r=con.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return r[0] if r else default

def set_setting(con,key,value):
    con.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
    if value and '_wait:' in key and not key.endswith('@'):        # 稽核：「等他打字」的狀態記時間，過期作廢（line_bot._expire_waits）
        import time
        con.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key+'@',str(int(time.time()))))

def request(url, method='GET', token=None, payload=None, form=False):
    headers={}
    if token: headers['Authorization']='Bearer '+token
    body=None
    if payload is not None:
        body=urllib.parse.urlencode(payload).encode() if form else json.dumps(payload).encode()
        headers['Content-Type']='application/x-www-form-urlencoded' if form else 'application/json'
    try:
        with urllib.request.urlopen(urllib.request.Request(url,data=body,headers=headers,method=method),timeout=20) as res:
            raw=res.read(); return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        # Never propagate tokens or raw third-party payloads.
        raise ValueError(f'外部服務回應 HTTP {e.code}；請检查帳號授權與 API 設定') from None
    except (urllib.error.URLError,TimeoutError):
        raise ValueError('外部服務連線逾時或無法連線，請稍後重試') from None

def line_get(con,account,path):
    if path not in ('/v2/bot/info','/v2/bot/channel/webhook/endpoint'):
        raise ValueError('LINE 目前只允許讀取帳號和 webhook 狀態')
    token=secret(con,'line_token_'+account)
    if not token: raise ValueError('尚未設定此官方帳號 API 憑證')
    return request('https://api.line.me'+path,token=token)

def verify_line(con,account):
    row=core.get(con,'line_accounts',account)
    info=line_get(con,account,'/v2/bot/info')
    if row['basic_id'] and info.get('basicId')!=row['basic_id']:
        raise ValueError('憑證對應的官方帳號不符')
    hook=line_get(con,account,'/v2/bot/channel/webhook/endpoint')
    con.execute('UPDATE line_accounts SET basic_id=?,verified_at=?,webhook=? WHERE id=?',(info.get('basicId',''),core.now(),hook.get('endpoint',''),account))
    return {'name':info.get('displayName'),'basic_id':info.get('basicId'),'mode':'read_only','webhook_active':hook.get('active')}

def configure_google(con,p):
    config=p.get('installed') or p.get('web') or p
    cid=config.get('client_id',''); client_secret=config.get('client_secret','')
    if not cid.endswith('.apps.googleusercontent.com') or not client_secret:
        raise ValueError('請提供 Google OAuth 用戶端 JSON（建議桌面應用程式）')
    put_secret(con,'google_client_id',cid); put_secret(con,'google_client_secret',client_secret)
    core.audit(con,'configure','integration','google',after={'credentials_saved':True})
    return {'configured':True}

def oauth_start(con,session_hash):
    cid=secret(con,'google_client_id')
    if not cid: raise ValueError('請先在串接設定載入 Google OAuth 用戶端 JSON')
    state=secrets.token_urlsafe(32); verifier=secrets.token_urlsafe(48)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    con.execute('DELETE FROM oauth_states WHERE expires<?',(int(time.time()),))
    con.execute('INSERT INTO oauth_states VALUES(?,?,?,?)',(state,verifier,int(time.time())+600,session_hash))
    return {'url':'https://accounts.google.com/o/oauth2/v2/auth?'+urllib.parse.urlencode({
        'client_id':cid,'redirect_uri':BASE+'/oauth/callback','response_type':'code','scope':SCOPES,
        'access_type':'offline','prompt':'consent','state':state,'code_challenge':challenge,'code_challenge_method':'S256'})}

def oauth_finish(con,p,session_hash):
    row=con.execute('SELECT * FROM oauth_states WHERE state=? AND expires>? AND session_hash=?',(p.get('state',''),int(time.time()),session_hash)).fetchone()
    if not row: raise ValueError('授權流程逾時或不屬於目前登入，請重新連接')
    if not p.get('code'): raise ValueError('Google 尚未授權')
    tokens=request('https://oauth2.googleapis.com/token','POST',payload={
        'client_id':secret(con,'google_client_id'),'client_secret':secret(con,'google_client_secret'),
        'code':p['code'],'code_verifier':row['verifier'],'redirect_uri':BASE+'/oauth/callback','grant_type':'authorization_code'},form=True)
    if not tokens.get('refresh_token'): raise ValueError('未取得持續授權；請重新同意離線存取')
    put_secret(con,'google_refresh_token',tokens['refresh_token'])
    con.execute('DELETE FROM oauth_states WHERE state=?',(p['state'],))
    set_setting(con,'google_connected_at',core.now())
    core.audit(con,'connect','integration','google',after={'scope':SCOPES})

def google_token(con):
    refresh=secret(con,'google_refresh_token')
    if not refresh: raise ValueError('Google 日曆尚未授權')
    return request('https://oauth2.googleapis.com/token','POST',payload={
        'client_id':secret(con,'google_client_id'),'client_secret':secret(con,'google_client_secret'),
        'refresh_token':refresh,'grant_type':'refresh_token'},form=True)['access_token']

def create_calendar(con):
    existing=setting(con,'google_calendar_id')
    if existing: return {'id':existing,'already_created':True}
    result=request('https://www.googleapis.com/calendar/v3/calendars','POST',google_token(con),{'summary':'示範服務店・服務排班','timeZone':'Asia/Taipei','description':'人工確認的服務工單；帳款與門禁資訊不會傳送至日曆。'})
    set_setting(con,'google_calendar_id',result['id'])
    return {'id':result['id']}

def sync_since(days=7):
    import datetime as _dt
    return (_dt.datetime.now(core.TZ).date()-_dt.timedelta(days=days)).isoformat()

def calendar_tick(con):
    """雲端搬本機：取代 Apps Script「Demo Calendar Sync」。設定 google_calendar_sync=on 且已授權才跑，每 10 分鐘一次。"""
    if setting(con,'google_calendar_sync')!='on' or not setting(con,'google_calendar_id'):
        return 0
    if not con.execute("SELECT 1 FROM secrets WHERE key='google_refresh_token'").fetchone():
        return 0
    last=setting(con,'google_calendar_last_try')
    if last and time.time()-float(last)<600:
        return 0
    set_setting(con,'google_calendar_last_try',str(time.time()))
    r=sync_calendar(con,since=sync_since())
    set_setting(con,'google_calendar_last_sync',core.now()+(f"（{len(r['errors'])} 筆失敗）" if r['errors'] else ''))
    return r['synced']

def sync_calendar(con,since=None):
    calendar=setting(con,'google_calendar_id')
    if not calendar: raise ValueError('請先建立示範服務店專用日曆')
    token=google_token(con); base='https://www.googleapis.com/calendar/v3/calendars/'+urllib.parse.quote(calendar,safe='')+'/events'
    synced=0; errors=[]
    for o in core.rows(con,"SELECT * FROM orders WHERE version!=calendar_revision"+(" AND date>=?" if since else ""),((since,) if since else ())):
        if o['status'] not in ('confirmed','completed'):
            if o['calendar_event_id']:
                try:
                    try: request(base+'/'+o['calendar_event_id']+'?sendUpdates=none','DELETE',token)
                    except ValueError as e:
                        if 'HTTP 404' not in str(e) and 'HTTP 410' not in str(e):raise
                    con.execute('INSERT INTO calendar_operations(order_id,generation) VALUES(?,2) ON CONFLICT(order_id) DO UPDATE SET generation=generation+1',(o['id'],))
                    con.execute("UPDATE orders SET calendar_event_id='',calendar_revision=? WHERE id=?",(o['version'],o['id']))
                    synced+=1
                except ValueError as e: errors.append({'order_id':o['id'],'error':str(e)})
            continue
        gen=con.execute('SELECT generation FROM calendar_operations WHERE order_id=?',(o['id'],)).fetchone()
        event_id=o['calendar_event_id'] or hashlib.sha256(('demoshop-ops:'+o['id']+':'+str(gen[0] if gen else 1)).encode()).hexdigest()
        payload={'summary':'服務 · '+o['id'],'description':'請至店家營運中樞（示範）查看工單。','start':{'dateTime':o['date']+'T'+o['start']+':00+08:00','timeZone':'Asia/Taipei'},'end':{'dateTime':o['date']+'T'+o['end']+':00+08:00','timeZone':'Asia/Taipei'},'extendedProperties':{'private':{'ops_order_id':o['id']}},'reminders':{'useDefault':False,'overrides':[{'method':'popup','minutes':1440}]}}
        try:
            # Deterministic IDs make uncertain retries safe. A 404 is distinguished explicitly.
            target=base+'/'+event_id+'?sendUpdates=none'
            try:
                request(target,'GET',token)
                exists=True
            except ValueError as e:
                if 'HTTP 404' not in str(e): raise
                exists=False
            if exists:
                request(target,'PUT',token,payload)
            else:
                request(base+'?sendUpdates=none','POST',token,{**payload,'id':event_id})
            con.execute('UPDATE orders SET calendar_event_id=?,calendar_revision=? WHERE id=?',(event_id,o['version'],o['id']))
            synced+=1
        except ValueError as e:
            errors.append({'order_id':o['id'],'error':str(e)})
    core.audit(con,'sync','integration','google',after={'synced':synced,'errors':errors})
    return {'synced':synced,'errors':errors}

def status(con):
    return {'line_mode':'read_only','line_send_enabled':False,
            'google_configured':bool(con.execute("SELECT 1 FROM secrets WHERE key='google_client_id'").fetchone()),
            'google_connected':bool(con.execute("SELECT 1 FROM secrets WHERE key='google_refresh_token'").fetchone()),
            'google_calendar_id':setting(con,'google_calendar_id'),'google_scope':SCOPES,
            'google_calendar_sync':setting(con,'google_calendar_sync')=='on','google_calendar_last_sync':setting(con,'google_calendar_last_sync')}
