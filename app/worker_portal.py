"""夥伴在 LINE 上用的工作頁（不用登入、不用裝 App）：
- /w/<工作碼>：一筆工作 → 到場打卡（GPS）、服務前／完成照片、完工回報、離場打卡。照片同步到派單管理群；完成照片傳給客戶確認。
- /wp/<夥伴碼>：夥伴主控板 → 我的班表、邀請中的工作、可排時間。
- /apply?t=：LINE「我要應徵」→ 應徵資料 → 建立待審夥伴；/hi?t=：LINE「我要詢問」→ 客戶基本資料（姓名、電話）。
工作碼／夥伴碼是隨機長字串，只發給本人的 LINE；工作碼服務日後 3 天失效。"""
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import time
import core as c
import integrations as ix

def _key(con):
    k = ix.secret(con, 'photo_url_key')
    if not k:
        k = secrets.token_urlsafe(32); ix.put_secret(con, 'photo_url_key', k)
    return k

def photo_url(con, attachment_id):
    """LINE 要能抓到照片才能顯示：用簽章網址公開單張照片（猜不到）。"""
    import line_bot
    sig = hmac.new(_key(con).encode(), attachment_id.encode(), hashlib.sha256).hexdigest()[:24]
    return f'{line_bot.BASE_URL}/p/{attachment_id}.{sig}.jpg'

def gallery_url(con, oid):
    """一場服務的全部照片（服務前、完工）：給業主和管理群看（LINE 一則最多 9 張，負責人 要全部看得到）。"""
    import line_bot
    sig = hmac.new(_key(con).encode(), f'gallery:{oid}'.encode(), hashlib.sha256).hexdigest()[:24]
    return f'{line_bot.BASE_URL}/pg/{oid}.{sig}'

def gallery_page(con, name):
    """全部照片頁（HTML）：服務前、完工兩區縮圖，點開放大左右滑。不顯示夥伴姓名、地址。"""
    import html as _h, brands, json as _j
    m = re.match(r'^([\w-]+)\.([0-9a-f]{24})$', name or '')
    if not m or not hmac.compare_digest(hmac.new(_key(con).encode(), f'gallery:{m.group(1)}'.encode(), hashlib.sha256).hexdigest()[:24], m.group(2)):
        raise PermissionError('連結無效')
    oid = m.group(1)
    o = con.execute('SELECT o.date,o.customer_id,s.name sname FROM orders o LEFT JOIN sites s ON s.id=o.site_id WHERE o.id=?', (oid,)).fetchone()
    if not o:
        raise ValueError('找不到這筆服務')
    cu = con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() if o['customer_id'] else None
    bname = brands.name(brands.of_customer(dict(cu)) if cu else 'demoshop')
    groups = [(lab, [photo_url(con, r[0]) for r in con.execute('SELECT id FROM attachments WHERE order_id=? AND label=? ORDER BY created_at', (oid, key))])
              for key, lab in (('before', '服務前'), ('after', '完工'))]
    allp = [(lab, u) for lab, us in groups for u in us]
    md = f"{int(o['date'][5:7])}/{int(o['date'][8:10])}" if o['date'] else ''
    sec = ''.join(f'<section><h2>{lab}<span>{len(us)} 張</span></h2><div class="grid">'
                  + ''.join(f'<button type="button" data-i="{sum(len(x[1]) for x in groups[:gi]) + i}"><img loading="lazy" src="{u}?t=1" alt="{lab} 第 {i + 1} 張" onerror="r(this)"></button>' for i, u in enumerate(us))
                  + '</div></section>' for gi, (lab, us) in enumerate(groups) if us) or '<p class="empty">還沒有照片。</p>'
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{_h.escape(bname)}・服務照片</title><meta name="robots" content="noindex">
<style>
:root{{--bg:#fbfbfd;--ink:#1d1d1f;--sub:#6e6e73;--tile:#fff;--line:#e5e5ea}}
@media (prefers-color-scheme:dark){{:root{{--bg:#000;--ink:#f5f5f7;--sub:#a1a1a6;--tile:#1c1c1e;--line:#2c2c2e}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:17px/1.5 -apple-system,BlinkMacSystemFont,"PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif;padding:max(24px,env(safe-area-inset-top)) 18px 48px}}
main{{max-width:980px;margin:0 auto}}.k{{font-size:13px;font-weight:600;color:var(--sub);letter-spacing:.02em}}
h1{{font-size:clamp(30px,6vw,48px);line-height:1.08;letter-spacing:-.015em;margin:6px 0 4px;font-weight:700}}.lede{{color:var(--sub);margin:0 0 28px}}
section{{margin:0 0 34px}}h2{{font-size:22px;margin:0 0 12px;display:flex;gap:10px;align-items:baseline}}h2 span{{font-size:15px;color:var(--sub);font-weight:500}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}}
.grid button{{padding:0;border:0;border-radius:14px;overflow:hidden;aspect-ratio:1;background:var(--tile);cursor:zoom-in}}.grid img{{width:100%;height:100%;object-fit:cover;display:block}}
.empty{{color:var(--sub)}}#v{{position:fixed;inset:0;background:rgba(0,0,0,.94);display:flex;align-items:center;justify-content:center;touch-action:pan-y}}#v[hidden]{{display:none}}
#v img{{max-width:100vw;max-height:calc(100vh - 110px);object-fit:contain}}#v .top{{position:absolute;top:0;left:0;right:0;display:flex;align-items:center;padding:max(14px,env(safe-area-inset-top)) 16px 12px;color:#fff;font-size:15px}}
#v .top span{{flex:1}}#v button{{background:rgba(255,255,255,.16);color:#fff;border:0;border-radius:50%;width:44px;height:44px;font-size:24px;cursor:pointer}}
#v .p,#v .n{{position:absolute;top:50%;transform:translateY(-50%)}}#v .p{{left:10px}}#v .n{{right:10px}}#v button:disabled{{opacity:.2}}
</style></head><body><main><div class="k">{_h.escape(bname)}</div><h1>服務照片</h1><p class="lede">{_h.escape(md)}{'　' + _h.escape(o['sname']) if o['sname'] else ''}・共 {len(allp)} 張。點照片放大，左右滑看下一張。</p>{sec}</main>
<div id="v" hidden role="dialog"><div class="top"><span id="c"></span><button type="button" id="x" aria-label="關閉">✕</button></div><button type="button" class="p" aria-label="上一張">‹</button><img id="im" alt=""><button type="button" class="n" aria-label="下一張">›</button></div>
<script>
function r(img){{const n=+(img.dataset.r||0);if(n<3){{img.dataset.r=n+1;setTimeout(()=>{{img.src=img.src.replace(/&r=\\d+$/,'')+'&r='+(n+1)}},800*(n+1))}}}}
const P={_j.dumps(allp, ensure_ascii=False)};let i=0,sx=null;const v=document.getElementById('v'),im=document.getElementById('im');
function show(){{im.src=P[i][1];document.getElementById('c').textContent=P[i][0]+'・'+(i+1)+' / '+P.length;v.querySelector('.p').disabled=!i;v.querySelector('.n').disabled=i>=P.length-1;if(P[i+1])new Image().src=P[i+1][1];}}
function mv(k){{const n=i+k;if(n>=0&&n<P.length){{i=n;show();}}}}
document.querySelectorAll('.grid button').forEach(b=>b.onclick=()=>{{i=+b.dataset.i;v.hidden=false;show();}});
document.getElementById('x').onclick=()=>v.hidden=true;v.querySelector('.p').onclick=()=>mv(-1);v.querySelector('.n').onclick=()=>mv(1);
v.addEventListener('touchstart',e=>{{sx=e.touches[0].clientX}},{{passive:true}});v.addEventListener('touchend',e=>{{if(sx===null)return;const d=e.changedTouches[0].clientX-sx;sx=null;if(Math.abs(d)>40)mv(d<0?1:-1);}});
addEventListener('keydown',e=>{{if(v.hidden)return;if(e.key==='ArrowLeft')mv(-1);if(e.key==='ArrowRight')mv(1);if(e.key==='Escape')v.hidden=true;}});
</script></body></html>'''

def public_photo(con, name):
    m = re.match(r'^([\w-]+)\.([0-9a-f]{24})\.jpg$', name or '')
    if not m or not hmac.compare_digest(hmac.new(_key(con).encode(), m.group(1).encode(), hashlib.sha256).hexdigest()[:24], m.group(2)):
        raise PermissionError('連結無效')
    r = con.execute('SELECT a.mime,b.content FROM attachments a JOIN attachment_blobs b ON b.id=a.id WHERE a.id=?', (m.group(1),)).fetchone()
    if not r:
        raise ValueError('找不到照片')
    return r[0], r[1]

# ───────────── 工作碼 ─────────────
def work_token(con, assignment_id):
    a = con.execute('SELECT work_token FROM assignments WHERE id=?', (assignment_id,)).fetchone()
    if a and a[0]:
        return a[0]
    t = secrets.token_urlsafe(18)
    con.execute('UPDATE assignments SET work_token=? WHERE id=?', (t, assignment_id))
    return t

def work_url(con, assignment_id):
    import line_bot
    return f'{line_bot.BASE_URL}/w/{work_token(con, assignment_id)}'

def _assignment(con, t):
    a = con.execute("SELECT * FROM assignments WHERE work_token=? AND status='accepted'", (t or '',)).fetchone()
    if not a:
        raise PermissionError('這個工作連結已失效，請在 LINE 選單按「我的主控板」，從班表重新打開')
    o = c.get(con, 'orders', a['order_id'])
    if o['status'] in ('cancelled', 'archived') or (o['date'] and dt.date.fromisoformat(o['date']) + dt.timedelta(days=3) < dt.datetime.now(c.TZ).date()):
        raise PermissionError('這筆工作已結束或取消')
    return dict(a), o

PROFILE_NEED = (('regions', '可以服務的地點'), ('availability', '可以工作的時間'), ('skills', '服務類型'), ('tools_own', '自備工具'), ('transport', '交通工具'),
                ('butler_skills', '居家管家'), ('play_skills', '陪玩員'), ('willing', '接單意願'))      # 夥伴基本資料都必填

def profile_missing(w):
    """這位夥伴還沒填的必填項目。"""
    w = dict(w)
    return [t for k, t in PROFILE_NEED if not (w.get(k) or '').strip()]

def work_view(con, t):
    """夥伴看得到：時間、地區、範圍、時數；排定後才有地址、進場方式。看不到價格與客戶資料。"""
    import board
    a, o = _assignment(con, t)
    s = dict(con.execute('SELECT * FROM sites WHERE id=?', (o['site_id'],)).fetchone() or {}) if o['site_id'] else {}
    w = c.get(con, 'workers', a['worker_id'])
    ok = o['status'] in ('confirmed', 'completed') or board.goes(dict(o), w['id'])          # 補人中：只有原本的人看得到地址，新補的等審核
    ck = con.execute('SELECT * FROM checkins WHERE assignment_id=?', (a['id'],)).fetchone()
    ph = {r[0]: r[1] for r in con.execute('SELECT label,COUNT(*) FROM attachments WHERE order_id=? GROUP BY label', (o['id'],))}
    crew = [r[0] for r in con.execute("SELECT w.name FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='accepted' AND w.id!=?", (o['id'], w['id']))]
    v = {'worker': w['name'], 'date': o['date'], 'start': o['start'], 'end': o['end'], 'status': o['status'], 'go': bool(ok), 'no_photo': board.no_photo(con, o), 'forgot_ok': ix.setting(con, 'forgot_before') == 'reason',
            'forgot': (json.loads(ix.setting(con, f"forgot_before:{o['id']}") or '{}') or {}).get('reason', ''), 'area': board.street_of(s),
            'service_type': s.get('service_type') or '服務', 'scope': '' if o['scope_hold'] else board._no_money(o['scope'] or s.get('checklist') or ''),
            'is_redo': bool(o['parent_order_id'] or (o['title'] or '').startswith('補清')),      # 補清的工單要看得出來（'redo' 是補拍照片，不能重名）
            'scope_pending': bool(o['scope_hold']), 'tools': __import__('sitetools').worker_text(s),
            'hours': round(c.minutes(o['start'], o['end']) / 60, 1) if o['start'] and o['end'] else None, 'crew': crew,
            'pay': board.est_pay(con, o, w['id'])[1],                      # 自己這場的收入（不是客戶價格）
            'address': s.get('address', '') if a['status'] == 'accepted' else '', 'navigation': __import__('sitegeo').nav_url(s) if a['status'] == 'accepted' else '',      # 接單後就給門牌＋精準定位
            'geo': __import__('sitegeo').level_text(s) if a['status'] == 'accepted' else '', 'entry_notes': board._no_money(s.get('entry_notes', '')) if ok else '',
            'notes': '', 'site_notes': '',                 # 工單／案場備註是內部用，夥伴看不到
            'entry_mode': __import__('door').mode_of(o, s), 'worker_note': (o['worker_note'] if 'worker_note' in o.keys() else '') if ok else '',
            'extra': _extra(o) if ok else {}, 'door_ack': bool(o['door_ack']) if 'door_ack' in o.keys() else False,
            'contacts': [dict(name=r[0], role=r[1], phone=r[2]) for r in con.execute("SELECT name,role,phone FROM contacts WHERE customer_id=? AND role!='請款窗口' AND phone!='' LIMIT 3",
                                                                                      (o['customer_id'],))] if ok and o['customer_id'] else [],
            'arrived_at': ck['arrived_at'] if ck else '', 'left_at': ck['left_at'] if ck else '', 'signin_need': bool(s.get('signin_photo')), 'trash': __import__('prepq').trash_text(s),
            'photos': {'signin': ph.get('signin', 0), 'before': ph.get('before', 0), 'after': ph.get('after', 0), 'incident': ph.get('incident', 0)},
            'reports': _reports(con, o['id'], w['id']), 'next_job': _next_job(con, w['id'], o),
            'portal_url': portal_url(con, w['id']), 'next_link': _next_link(con, w['id'], o),
            'acked': ix.setting(con, f"job_ack:{a['id']}") or '',      # 工作頁 ↔ 主控板 ↔ 下一場直接連
            'site_info': board._no_money(__import__('siteinfo').info(con, o)) if ok else '',      # 現場須知＋照片
            'routine': __import__('siteroutine').tasks(con, dict(o)) if ok else [],      # 定期項目逐項勾，沒勾要寫原因
            'info_url': __import__('siteinfo').url(con, o['id'], 'f' if ok else 'q') if __import__('siteinfo').has(con, o, 'f' if ok else 'q') else '',
            'report': o['field_report'], 'redo': bool(ck and ck['left_at'] and _redo_pending(con, o['id'], ck['left_at'])), 'today': dt.datetime.now(c.TZ).date().isoformat()}
    v['ack_need'] = ack_need(v)                         # 須知要打勾確認
    return v

def _redo_pending(con, oid, left_at):
    """管理群請他補拍、而且補拍後還沒審核通過（稽核：以前審核通過後「請補拍」橫幅還一直在）。"""
    r = con.execute("SELECT MAX(at) FROM dispatch_log WHERE order_id=? AND action='photo_redo' AND at>?", (oid, _utc(left_at))).fetchone()[0]
    return bool(r) and not con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='photo_approved' AND at>?", (oid, r)).fetchone()

def _reports(con, oid, wid):
    import site_report
    return [{'kind': site_report.KINDS[r['kind']][1], 'at': r['created_at'][11:16], 'status': r['status'], 'resolution': r['resolution']}
            for r in site_report.for_order(con, oid) if r['worker_id'] == wid]

def ack_need(v):
    """這場有夥伴看得到的須知（進場、服務範圍、工具、現場須知、提醒、額外資料）→ 打卡前要勾「看過了」。"""
    return bool(v.get('go') and any(v.get(k) for k in ('entry_notes', 'scope', 'tools', 'site_info', 'worker_note', 'extra', 'trash')))

def _next_link(con, wid, o):
    """同一天接下來的下一場（不管這場完工沒）：{'start','area','url'}。"""
    r = con.execute("SELECT a.id,o.start,s.area FROM assignments a JOIN orders o ON o.id=a.order_id LEFT JOIN sites s ON s.id=o.site_id WHERE a.worker_id=? AND a.status='accepted' "
                    "AND o.date=? AND o.start>? AND o.id!=? AND o.status NOT IN ('cancelled','archived') ORDER BY o.start LIMIT 1", (wid, o['date'], o['start'] or '', o['id'])).fetchone()
    return {'start': r['start'], 'area': r['area'] or '', 'url': work_url(con, r['id'])} if r else None

def _next_job(con, wid, o):
    import site_report
    nx = site_report.next_job(con, wid, o) if o['status'] == 'confirmed' else None
    return nx['start'] if nx else ''

EXTRA_LABEL = {'kid_ages': '兒童年齡', 'allergy': '過敏／活動限制', 'parent': '在場家長', 'emergency': '緊急聯絡', 'tasks': '授權項目', 'buy_limit': '單筆採買上限', 'keys': '鑰匙與財物交接'}

def _extra(o):
    """陪玩／管家的額外資料（排定後才給夥伴看）。"""
    try:
        e = json.loads((o['extra'] if 'extra' in o.keys() else '') or '{}')
    except ValueError:
        e = {}
    return {EXTRA_LABEL[k]: str(v)[:300] for k, v in e.items() if k in EXTRA_LABEL and str(v).strip()}

def _utc(s):
    """台灣時間字串（c.now()）→ SQLite datetime('now') 的 UTC 格式，才能直接比大小。"""
    try:
        t = dt.datetime.fromisoformat(s)
        return (t if t.tzinfo else t.replace(tzinfo=c.TZ)).astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return (s or '')[:19].replace('T', ' ')

def _user(con, a):
    w = c.get(con, 'workers', a['worker_id'])
    return {'worker_id': w['id'], 'name': w['name'], 'role': 'cleaner', 'id': 'line:' + w['id']}

def work_action(con, t, act, p):
    import board, photos, line_bot
    a, o = _assignment(con, t)
    u = _user(con, a); c.CTX.actor = u['name'] + '（LINE 工作頁）'
    if not board.goes(dict(o), a['worker_id']) and act == 'arrive' or (act == 'leave' and not board.goes(dict(o), a['worker_id']) and not con.execute(
            "SELECT 1 FROM checkins WHERE assignment_id=? AND arrived_at!=''", (a['id'],)).fetchone()):
        raise ValueError('這筆還沒排定（客戶確認後才能打卡）')
    if act == 'arrive':
        if o['date'] != dt.datetime.now(c.TZ).date().isoformat():
            raise ValueError('只能在服務當天打到場卡')
        v_ = work_view(con, t); v_['ack_need'] = ack_need(v_)
        if v_.get('ack_need') and not p.get('ack'):      # 這場的須知要打勾確認看過才能打卡
            raise ValueError('請先勾選「我看過這場的現場須知」再打卡')
        if v_.get('ack_need'):
            ix.set_setting(con, f"job_ack:{a['id']}", c.now()); c.audit(con, 'job_ack', 'assignments', a['id'], after={'by': u['name']})
        r = board.checkin(con, u, {'assignment_id': a['id'], 'kind': 'arrive', 'lat': p.get('lat'), 'lng': p.get('lng')})
        nowm = dt.datetime.now(c.TZ).hour * 60 + dt.datetime.now(c.TZ).minute
        late = max(0, nowm - (int(o['start'][:2]) * 60 + int(o['start'][3:5])))      # 晚到幾分鐘（不用 c.minutes，它限制 16 小時內）
        geo = f"https://www.google.com/maps?q={p['lat']},{p['lng']}" if p.get('lat') and p.get('lng') else ''
        line_bot.on_arrived(con, o, u['name'], a['worker_id'])          # 同步通知業主：人到了，請開門或單純告知
        import automation
        automation.ask_done_earlier(con, a['worker_id'], o)            # 同一天前一場沒打卡（例：早上那場忘了打）→ 馬上問他補登
        line_bot.admin_notify(con, f"🟢 {u['name']} 已到場" + (f"（晚 {late} 分）" if late > 5 else ''),
                              [('工作', f"{o['date'][5:].replace('-', '/')} {o['start']}–{o['end']}"), ('打卡', dt.datetime.now(c.TZ).strftime('%H:%M')),
                               ('定位', '有（按下方看地圖）' if geo else '沒定位')], '', '#2E7D32' if late <= 5 else '#C47F00', uri=geo, oid=o['id'])
        return r
    if act == 'backfill':                # 沒打到場卡、人已經離開現場（忘了打卡）→ 自填實際時間，管理群確認
        return backfill(con, a, o, u, p)
    if act == 'door':                    # 開門流程：到門口了，請業主開門
        import door
        return {'message': door.request_open(con, o['id'], a['worker_id'], u['name'])}
    if act == 'photo':
        label = p.get('label') if p.get('label') in ('before', 'after', 'incident', 'signin') else 'before'
        came = con.execute("SELECT 1 FROM checkins WHERE assignment_id=? AND arrived_at!=''", (a['id'],)).fetchone()
        if label == 'after' and came and not con.execute("SELECT 1 FROM attachments WHERE order_id=? AND label='before'", (o['id'],)).fetchone() \
                and not ix.setting(con, f"forgot_before:{o['id']}"):
            raise ValueError('請先上傳服務前照片，再傳完成照片（可以完工時一起傳）')        # 負責人 先拍照才可以開工
        cap = {'before': 50, 'after': 50}.get(label, 20)          # 負責人 服務前、完工各 50 張（共 100）
        if con.execute('SELECT COUNT(*) FROM attachments WHERE order_id=? AND label=?', (o['id'], label)).fetchone()[0] >= cap:
            raise ValueError(f"{ {'before': '服務前', 'after': '完工'}.get(label, '異常')}照片最多 {cap} 張，已經夠了 👍")
        r = photos.upload(con, {'order_id': o['id'], 'label': label, 'service_date': o['date'], 'name': f"{u['name']} {label}", 'content': str(p.get('content', '')).split(',', 1)[-1]})
        return r
    if act == 'forgot':           # 已經清完、忘了拍服務前照片（管理群決定開放時）→ 寫原因；完成照片、離場照常，這場薪資等管理層確認
        if ix.setting(con, 'forgot_before') != 'reason':
            raise ValueError('忘了拍服務前照片，請聯絡派單同事處理')
        if not con.execute("SELECT 1 FROM checkins WHERE assignment_id=? AND arrived_at!=''", (a['id'],)).fetchone():
            raise ValueError('請先到場打卡')
        why = str(p.get('reason', '')).strip()[:300]
        if len(why) < 2:
            raise ValueError('請寫一下原因')
        if not ix.setting(con, f"forgot_before:{o['id']}"):
            ix.set_setting(con, f"forgot_before:{o['id']}", json.dumps({'reason': why, 'by': u['name'], 'at': c.now()}, ensure_ascii=False))
            c.audit(con, 'forgot_before', 'orders', o['id'], after={'reason': why, 'by': u['name']})
            line_bot.forgot_card(con, o, u['name'], why)
        return {'message': '已記錄 ✅ 完成照片照常拍；這場薪資等管理層確認後發放'}
    if act == 'door_in':          # 已經進門了（業主開了／自己進去了）→ 停止催業主、管理群不會誤報
        import door
        return {'message': door.worker_inside(con, o['id'], u['name'])}
    if act == 'report':           # 現場回報（加時、下一場晚到、現場狀況）→ 派單管理群可點的卡
        import site_report
        kind = str(p.get('kind', ''))
        if kind in site_report.NOTE_KINDS and not str(p.get('note', '')).strip():
            raise ValueError('請寫一下是什麼狀況')
        if kind == 'extra' and p.get('own') not in ('y', 'n'):
            raise ValueError('請選業主知不知道要加時')
        return site_report.create(con, o['id'], a['worker_id'], kind, int(p.get('minutes') or 0), str(p.get('own') or ''), str(p.get('note', '')).strip()[:500], u['name'])
    if act == 'photos_done':      # 一批照片傳完 → 一張卡送到派單管理群（省推播則數）
        label = p.get('label') if p.get('label') in ('before', 'after', 'incident', 'signin') else 'before'
        ids = [r[0] for r in con.execute('SELECT id FROM attachments WHERE order_id=? AND label=? ORDER BY created_at DESC LIMIT 10', (o['id'], label))][::-1]
        ck0 = con.execute('SELECT left_at FROM checkins WHERE assignment_id=?', (a['id'],)).fetchone()
        redo = label == 'after' and ck0 and ck0['left_at'] and _redo_pending(con, o['id'], ck0['left_at'])
        if not redo and con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='photos' AND alt LIKE ? AND created_at>?",
                       (o['id'], f"%{ {'before': '服務前', 'after': '完工', 'incident': '異常', 'signin': '簽到表'}[label]}%", (dt.datetime.now(c.TZ) - dt.timedelta(minutes=5)).isoformat())).fetchone():
            return {'sent': 0, 'note': '5 分鐘內已通知過'}
        ck = con.execute('SELECT left_at FROM checkins WHERE assignment_id=?', (a['id'],)).fetchone()
        if ids and label == 'after' and ck and ck['left_at']:       # 已完工後補拍 → 重新送管理群審核（可直接傳給業主）
            line_bot.on_finished(con, o, u['name'] + '（補拍）', [photo_url(con, i) for i in ids][-9:], o['field_report'] or '')
        elif ids:
            line_bot.on_photos(con, o, u['name'], label, [photo_url(con, i) for i in ids])
        return {'sent': len(ids)}
    nophoto = board.no_photo(con, o)     # 場內不能拍照（例：客戶03）；夥伴當場回報「這裡不能拍照」也放行這一場
    if act == 'leave':
        s_ = con.execute('SELECT signin_photo FROM sites WHERE id=?', (o['site_id'] or '',)).fetchone()
        if s_ and s_[0] and not con.execute("SELECT 1 FROM attachments WHERE order_id=? AND label='signin'", (o['id'],)).fetchone():
            line_bot.admin_notify(con, f"📋 {u['name']} 離場了但沒拍簽到表", [('工作', line_bot.when(dict(o))), ('處理', '已自動傳 LINE 請他在工作頁補拍；補拍了這裡會收到照片')], '', '#C47F00', oid=o['id'])
            m = line_bot.flex('請補拍簽到表', line_bot._bubble(line_bot._header('📋 請補拍簽到表', line_bot.when(dict(o))),
                [line_bot._t(f"{u['name']} 辛苦了！這個案場要拍簽到表，剛剛離場還沒拍到 🙏", wrap=True),
                 line_bot._t('按下面按鈕到工作頁「補拍簽到表照片」，拍好就完成了。', size='sm', color=line_bot.SUB, wrap=True, margin='md')],
                [line_bot._btn('補拍簽到表', uri=work_url(con, a['id']), primary=True)]))
            line_bot.to_worker(con, a['worker_id'], 'signin_miss', m[0]['altText'], m, o['id'])      # 直接請夥伴補，少一步人工
            # 擋離場會讓已拍完前後照的夥伴卡住（舊版頁面看不到簽到表欄）→ 不擋，通知管理群補
        if not nophoto and not con.execute("SELECT 1 FROM attachments WHERE order_id=? AND label='after'", (o['id'],)).fetchone():
            raise ValueError('請先上傳完成照片，再離場打卡')
        if not nophoto and not con.execute("SELECT 1 FROM attachments WHERE order_id=? AND label='before'", (o['id'],)).fetchone() \
                and not ix.setting(con, f"forgot_before:{o['id']}"):
            raise ValueError('還沒有服務前照片：請先上傳服務前照片（還沒清的樣子），再離場打卡')
        if not p.get('end_time') and 'rt_done' in p:          # 舊版頁面（沒有勾選欄）不擋，日期過了由管理群待處理追
            import siteroutine; siteroutine.on_leave(con, dict(o), u['name'], p.get('rt_done') or [], p.get('rt_why', ''))
        if str(p.get('report', '')).strip():
            board.field_report(con, u, {'assignment_id': a['id'], 'report': str(p['report'])[:2000]})
        late = ''
        if p.get('end_time'):
            if not re.fullmatch(r'\d\d:\d\d', str(p['end_time'])) or o['date'] >= dt.datetime.now(c.TZ).date().isoformat():
                raise ValueError('補登只能用在之前忘了離場打卡的工作，請填實際離場時間（例如 16:30）')
            late = str(p['end_time'])
        r = board.checkin(con, u, {'assignment_id': a['id'], 'kind': 'leave', 'pause': int(p.get('pause') or 0), 'notes': ('【補登】' if late else '') + str(p.get('report', ''))[:500],
                                   'lat': p.get('lat'), 'lng': p.get('lng'), **({'end_time': late} if late else {})})
        import daidiu
        if daidiu.is_daidiu(o) and not late:
            daidiu.done_message(con, o)                # 垃圾代丟收完只傳業主一則「已收走」
        if late:
            line_bot.admin_notify(con, f"🕒 {u['name']} 補登離場 {late}", [('處理', '忘了離場打卡，隔天補登；請確認工時是否正確')], '', '#C47F00', oid=o['id'])
        # 完成照片傳給客戶確認（每筆工單只傳一次）
        ids = [r2[0] for r2 in con.execute("SELECT id FROM attachments WHERE order_id=? AND label='after' ORDER BY created_at DESC LIMIT 10", (o['id'],))][::-1]
        line_bot.on_finished(con, o, u['name'], [photo_url(con, i) for i in ids], str(p.get('report', ''))[:200])
        return r
    raise ValueError('動作不符')

def backfill(con, a, o, u, p):
    """客戶12：早上中壢做完沒打卡，下午人不在現場 → 定位打卡時間位置都不對。改成自填實際開始／結束時間（不抓定位），
    記成工時（標【補登】），派單管理群收到卡確認；有完成照片就照常送業主確認。"""
    import board, line_bot, workflow
    st, en = str(p.get('start', ''))[:5], str(p.get('end', ''))[:5]
    if not (re.fullmatch(r'\d\d:\d\d', st) and re.fullmatch(r'\d\d:\d\d', en)) or en <= st:
        raise ValueError('請填實際開始、結束時間（結束要晚於開始）')
    now = dt.datetime.now(c.TZ)
    if o['date'] > now.date().isoformat() or (o['date'] == now.date().isoformat() and en > now.strftime('%H:%M')):
        raise ValueError('補登只能填已經做完的時間；還在現場請用「我到了・定位打卡」')
    ck = con.execute('SELECT * FROM checkins WHERE assignment_id=?', (a['id'],)).fetchone()
    if ck and ck['arrived_at']:
        raise ValueError('已經有到場紀錄，請用「完工・離場打卡」')
    pause = max(0, int(p.get('pause') or 0))
    con.execute('INSERT INTO checkins(assignment_id,arrived_at,left_at) VALUES(?,?,?) ON CONFLICT(assignment_id) DO UPDATE SET arrived_at=excluded.arrived_at,left_at=excluded.left_at',
                (a['id'], f"{o['date']}T{st}:00+08:00", f"{o['date']}T{en}:00+08:00"))
    if str(p.get('report', '')).strip():
        board.field_report(con, u, {'assignment_id': a['id'], 'report': str(p['report'])[:2000]})
    old = con.execute('SELECT version FROM timesheets WHERE assignment_id=?', (a['id'],)).fetchone()
    workflow.command(con, 'timesheet', {'assignment_id': a['id'], 'start': st, 'end': en, 'pause': pause,
                                        'notes': '【補登】沒打卡，夥伴自填實際時間', 'version': old[0] if old else None})
    board.log(con, o['id'], 'leave', u['name'] + f'（補登 {st}–{en}）')
    ids = [r[0] for r in con.execute("SELECT id FROM attachments WHERE order_id=? AND label='after' ORDER BY created_at DESC LIMIT 10", (o['id'],))][::-1]
    line_bot.admin_notify(con, f"🕒 {u['name']} 補登工時 {st}–{en}", [('排班', f"{o['date'][5:].replace('-', '/')} {o['start']}–{o['end']}"), ('補登', f"{st}–{en}" + (f"（休息 {pause} 分）" if pause else '')),
                          ('原因', '沒打卡、人已離開現場，夥伴自填'), ('完成照片', f"{len(ids)} 張" if ids else '沒有（業主不會收到完成照片）'), ('處理', '時間不對請按下方打開工單改')], '', '#C47F00', oid=o['id'])
    nophoto = board.no_photo(con, o)
    if ids or nophoto:                 # 稽核 L8：場內不能拍照的案場，補登也送業主（沒有照片的）完工卡
        line_bot.on_finished(con, o, u['name'] + '（補登）', [photo_url(con, i) for i in ids], str(p.get('report', ''))[:200])
    ask_hours(con, a['id'], u['name'], o, st, en, pause)          # 工時＝薪資 → 店長確認（點「時間對」或打正確時間，自動改核定工時）
    return {'ok': True, 'message': f'已補登 {st}–{en}，示範服務店會確認，辛苦了 🙏'}

def ask_hours(con, aid, name, o, st, en, pause=0, extra=''):
    """補登的工時（沒打卡、夥伴自填）送店長確認：回答由 decisions.apply「補登工時」自動套用到核定工時（assignments.approved_minutes）。"""
    import line_bot, integrations as ix
    topic = f"補登工時 {name} {o['date'][5:].replace('-', '/')} {o['start']}"[:30]
    ix.set_setting(con, f'askref:{topic}', aid)
    return line_bot.ask_staff(con, topic, f"{name} 沒打卡、自己補登：{o['date'][5:].replace('-', '/')} {o['title'][:14]}\n"
                              f"排班 {o['start']}–{o['end']}；補登 {st}–{en}" + (f"（休息 {pause} 分）" if pause else '') + (f"\n{extra}" if extra else '') +
                              "\n\n時間對就點「時間對」；不對請按「我打字回答」打實際時間（例如 09:00-12:30），薪資會照妳打的算。", ['時間對，照這樣算'])

# ───────────── 夥伴主控板 ─────────────
def portal_token(con, worker_id):
    r = con.execute('SELECT portal_token FROM workers WHERE id=?', (worker_id,)).fetchone()
    if r and r[0]:
        return r[0]
    t = secrets.token_urlsafe(18); con.execute('UPDATE workers SET portal_token=? WHERE id=?', (t, worker_id)); return t

def portal_url(con, worker_id):
    import line_bot
    return f'{line_bot.BASE_URL}/wp/{portal_token(con, worker_id)}'

def portal(con, t):
    import board
    w = con.execute('SELECT * FROM workers WHERE portal_token=?', (t or '',)).fetchone()
    if not w:
        raise PermissionError('連結無效，請在 LINE 選單重新打開')
    import automation
    if w['status'] not in ('active', 'review') or automation.is_blacklisted(con, w['id']):      # 資安：暫停／黑名單的看不到過去的案場照片和收入
        raise PermissionError('這個連結已停用，有問題請直接在 LINE 留言')
    today = dt.datetime.now(c.TZ).date().isoformat()
    jobs = []
    for a in c.rows(con, "SELECT a.id,a.status,o.id oid,o.date,o.start,o.end,o.status ost,o.scope,o.scope_hold,o.title,o.parent_order_id,s.area,s.address,s.service_type FROM assignments a "
                         "JOIN orders o ON o.id=a.order_id LEFT JOIN sites s ON s.id=o.site_id WHERE a.worker_id=? AND a.status IN ('accepted','invited') "
                         "AND (o.date>=? OR (a.status='accepted' AND o.date>=? AND NOT EXISTS (SELECT 1 FROM checkins k WHERE k.assignment_id=a.id AND COALESCE(k.left_at,'')!=''))) "
                         "AND o.status NOT IN ('cancelled','archived') ORDER BY o.date,o.start LIMIT 60",
                         (w['id'], today, (dt.datetime.now(c.TZ).date() - dt.timedelta(days=3)).isoformat())):      # 近 3 天忘了打卡的也列出來（置頂、標待補登）
        jobs.append({'status': a['status'], 'order_status': a['ost'], 'date': a['date'], 'start': a['start'], 'end': a['end'], 'area': a['address'] if a['status'] == 'accepted' and a['address'] else board.street_of({'area': a['area'], 'address': a['address']}),      # 接單後完整地址；邀請中只到路／巷
                     'redo': bool(a['parent_order_id'] or (a['title'] or '').startswith('補清')),
                     'service_type': a['service_type'] or '服務', 'scope': '' if a['scope_hold'] else board._no_money(a['scope']), 'url': work_url(con, a['id']) if a['status'] == 'accepted' else '',
                     'oid': a['oid'] if a['status'] == 'invited' else '', 'backfill': a['date'] < today, 'pay': board.est_pay(con, c.get(con, 'orders', a['oid']), w['id'])[1]})      # 每場能賺多少
    av = c.rows(con, 'SELECT date,kind,start,end FROM availability WHERE worker_id=? AND date>=?', (w['id'], today[:7] + '-01'))
    import automation
    return {'name': w['name'], 'status': w['status'], 'jobs': jobs, 'availability': av, 'today': today, 'income': income(con, w['id'], today),
            'portfolio': automation.my_portfolio(con, w['id']),
            'profile': {'phone': w['phone'] or '', 'regions': w['regions'] or '', 'skills': w['skills'] or '', 'availability': w['availability'] or '', 'gender': dict(w).get('gender') or '', 'tools_own': dict(w).get('tools_own') or '', 'willing': dict(w).get('willing') or '', 'home': dict(w).get('home') or '',
                        'transport': dict(w).get('transport') or '', 'butler_skills': dict(w).get('butler_skills') or '', 'play_skills': dict(w).get('play_skills') or '',
                        'bank_name': dict(w).get('bank_name') or '', 'bank_account': dict(w).get('bank_account') or '', 'bank_holder': dict(w).get('bank_holder') or '', 'payout_cycle': dict(w).get('payout_cycle') or 'month'},
            'av_note': dict(w).get('av_note') or '',
            'brand': (con.execute('SELECT account FROM line_users WHERE worker_id=? AND is_friend=1 ORDER BY last_seen DESC LIMIT 1', (w['id'],)).fetchone() or ['demoshop'])[0]}

def income(con, wid, today):
    """夥伴自己的收入：跟示範服務店月結同一套算法（board.pay_lines）。應付＝已完工＋取消補貼；已排定還沒完工的另列預估。"""
    import board
    d = dt.date.fromisoformat(today); months = [today[:7], (d.replace(day=1) - dt.timedelta(days=1)).isoformat()[:7]]
    out = []
    for m in months:
        lines = board.pay_lines(con, wid, m, today)
        done = [x for x in lines if x['counted']]; est = [x for x in lines if not x['counted']]
        st = con.execute('SELECT status FROM settlements WHERE worker_id=? AND period LIKE ? ORDER BY created_at DESC LIMIT 1', (wid, m + '%')).fetchone()
        out.append({'month': m, 'jobs': len(done), 'hours': round(sum(x['minutes'] or 0 for x in done) / 60, 1), 'pay': sum(x['pay'] or 0 for x in done) // 100,
                    'partial': any(x['pay'] is None for x in done), 'pending_jobs': len(est), 'pending_pay': sum(x['pay'] or 0 for x in est) // 100,
                    'status': {'paid': '已匯款', 'approved': '已結算，等匯款', 'draft': '結算中'}.get(st[0], st[0]) if st else ('月底結算' if m == today[:7] else '結算中')})
    return out

def portal_action(con, t, act, p):
    import dispatch, board
    w = con.execute('SELECT * FROM workers WHERE portal_token=?', (t or '',)).fetchone()
    if not w:
        raise PermissionError('連結無效')
    c.CTX.actor = w['name'] + '（LINE 主控板）'
    if act == 'availability':
        return dispatch.set_availability(con, w['id'], p)
    if act in ('accept', 'decline'):
        a = con.execute("SELECT id FROM assignments WHERE order_id=? AND worker_id=? AND status='invited'", (p.get('order_id', ''), w['id'])).fetchone()
        if not a:
            raise ValueError('這筆邀請已處理或已額滿')
        return board.respond(con, {'worker_id': w['id'], 'name': w['name'], 'role': 'cleaner'}, a[0], act == 'accept')
    if act == 'profile':                 # 夥伴自己更新：可服務區域、會做的項目、平常可以的時間（電話要改請聯絡示範服務店）
        f = {k: str(p.get(k, '')).strip()[:n] for k, n in (('regions', 400), ('skills', 200), ('availability', 100), ('gender', 10), ('tools_own', 200), ('willing', 100), ('home', 60),
                                                         ('transport', 60), ('butler_skills', 100), ('play_skills', 120), ('bank_name', 40), ('bank_account', 30), ('bank_holder', 30), ('payout_cycle', 10)) if k in p}
        if 'payout_cycle' in f and f['payout_cycle'] not in ('week', 'month'):
            f['payout_cycle'] = 'month'
        if 'bank_account' in f:
            f['bank_account'] = re.sub(r'[^0-9-]', '', f['bank_account'])
        miss = [t for k, t in PROFILE_NEED if k in p and not f.get(k)]
        if miss:
            raise ValueError('還沒填：' + '、'.join(miss) + '（都是必填，沒經驗的勾「無經驗」）')      # 
        c.save(con, 'workers', {'id': w['id'], 'version': w['version'], **f})
        return {'saved': True}
    if act == 'portfolio':               # 夥伴自己建作品集（介紹、入行年份、公開哪幾張照片）
        import automation
        return automation.save_portfolio(con, w['id'], p)
    raise ValueError('動作不符')

# ───────────── LINE 首次加入：應徵／詢問 ─────────────
# ───────────── 已綁定客戶：預約帶入資料、補案場資料 ─────────────
def cust_token(con, acc, uid, cid):
    """客戶專屬連結碼（14 天）：預約表單自動帶入資料、補案場資料。只發給已綁定的本人。"""
    old = con.execute("SELECT code FROM line_bind WHERE kind='cust' AND ref_id=? AND account=? AND expires>?", (f'{uid}|{cid}', acc, int(time.time()) + 86400)).fetchone()
    if old:
        return old[0][1:]
    t = secrets.token_urlsafe(16)
    con.execute('INSERT INTO line_bind(code,kind,ref_id,expires,account) VALUES(?,?,?,?,?)', ('C' + t, 'cust', f'{uid}|{cid}', int(time.time()) + 14 * 86400, acc))
    return t

def _cust(con, t):
    b = con.execute("SELECT * FROM line_bind WHERE code=? AND kind='cust' AND expires>?", ('C' + (t or ''), int(time.time()))).fetchone()
    if not b:
        raise PermissionError('連結已過期，請在 LINE 選單按「我的預約」重新打開')
    uid, cid = b['ref_id'].split('|', 1)
    if not con.execute('SELECT 1 FROM line_users WHERE account=? AND user_id=? AND customer_id=?', (b['account'], uid, cid)).fetchone():
        raise PermissionError('這個 LINE 已不是這位客戶，請重新綁定')
    return b['account'], uid, c.get(con, 'customers', cid)

def prefill(con, t):
    acc, uid, cu = _cust(con, t)
    sites = c.rows(con, "SELECT id,name,address,area,service_type,entry_notes,notes,tools,tools_note FROM sites WHERE customer_id=? AND COALESCE(address,'')!='' ORDER BY rowid DESC", (cu['id'],))
    return {'brand': acc, 'brand_name': __import__('brands').name(acc), 'name': cu['name'], 'phone': cu['phone'], 'line_name': cu['line_name'],
            'sites': [{k: s[k] for k in ('id', 'name', 'address', 'area', 'service_type', 'tools', 'tools_note')} for s in sites]}

def save_site(con, t, p):
    """客戶自己補案場資料（地址、規模、房衛、樓層電梯、進場方式、停車、寵物、注意事項）。"""
    acc, uid, cu = _cust(con, t)
    address = str(p.get('address', '')).strip()[:200]
    if not address:
        raise ValueError('請填地址')
    detail = '；'.join(x for x in (
        f"{p['kind']}" if p.get('kind') else '', f"{p['size']} 單位" if p.get('size') else '',
        f"{p['rooms']} 房 {p.get('baths') or '?'} 衛" if p.get('rooms') else '', f"{p['floors']} 樓／層" if p.get('floors') else '',
        '無電梯' if p.get('nolift') else '', '有寵物' if p.get('pets') else '', f"停車：{p['parking']}" if p.get('parking') else '',
        str(p.get('notes', '')).strip()[:300]) if x)
    c.CTX.actor = f"客戶 {cu['name']}（LINE 補案場資料）"
    data = {'address': address, 'area': str(p.get('area', ''))[:20], 'entry_notes': str(p.get('entry_notes', '')).strip()[:300], 'notes': detail[:500]}
    sid = p.get('site_id')
    if sid and con.execute('SELECT 1 FROM sites WHERE id=? AND customer_id=?', (sid, cu['id'])).fetchone():
        s = c.get(con, 'sites', sid)
        for k, tag in (('entry_notes', '客戶更新'), ('notes', '客戶填')):       # 不覆蓋同事寫的備註：空白不改，有新內容就附在後面
            old, new = (s.get(k) or '').strip(), data[k]
            data[k] = old if not new or new in old else (f"{old}\n{tag}：{new}" if old else new)[-(300 if k == 'entry_notes' else 500):]
        out = c.save(con, 'sites', {'id': sid, 'version': s['version'], **data})
        import leave; leave.site_changed(con, s, out)          # 客戶改了地址／進場方式 → 通知有排的夥伴
    else:
        out = c.save(con, 'sites', {'customer_id': cu['id'], 'name': (str(p.get('name', '')).strip() or f"{cu['name']} {data['area']}").strip()[:60], **data})
    cn, cp = str(p.get('contact_name', '')).strip()[:30], ''
    if p.get('contact_phone'):
        try:
            cp = _clean_phone({'phone': p.get('contact_phone', '')})
        except ValueError:
            raise ValueError('現場聯絡人的電話不完整，請再確認一次') from None
    if cn and cp and not con.execute('SELECT 1 FROM contacts WHERE customer_id=? AND phone=?', (cu['id'], cp)).fetchone():
        c.save(con, 'contacts', {'customer_id': cu['id'], 'name': cn, 'role': str(p.get('contact_role') or '家人')[:20], 'phone': cp, 'notes': '客戶在 LINE 補案場資料時留的現場聯絡人'})
    import line_bot
    line_bot.admin_notify(con, '🏠 客戶補了案場資料', [('客戶', f"{cu['name']}（{cu['phone']}）"), ('地址', f"{data['area']} {address}"[:40]), ('內容', detail[:80] or '—')], f"/#c/{cu['id']}", '#0A84FF')
    return {'ok': True, 'site_id': out['id']}

def onboard_token(con, acc, uid, kind):
    t = secrets.token_urlsafe(16)
    con.execute('INSERT INTO line_bind(code,kind,ref_id,expires,account) VALUES(?,?,?,?,?)', (kind[0].upper() + t, kind, uid, int(time.time()) + 7 * 86400, acc))
    import line_bot
    return f"{line_bot.BASE_URL}/{'apply' if kind == 'apply' else 'hi'}?t={t}"

def _bind(con, kind, t):
    b = con.execute('SELECT * FROM line_bind WHERE code=? AND kind=? AND expires>?', (kind[0].upper() + (t or ''), kind, int(time.time()))).fetchone()
    if not b:
        raise PermissionError('連結已過期，請回到 LINE 重新點選')
    return b

def onboard_info(con, kind, t):
    b = _bind(con, kind, t)
    lu = con.execute('SELECT display_name FROM line_users WHERE account=? AND user_id=?', (b['account'], b['ref_id'])).fetchone()
    import regions as rg
    out = {'display_name': lu[0] if lu else '', 'brand': b['account'], 'brand_name': __import__('brands').name(b['account']), 'service_counties': rg.service_counties(con, b['account'])}
    if kind == 'apply':                    # 應徵表題目（apply_form.py 是唯一的題目設定）；補填時帶入他之前留的資料
        import apply_form
        out.update(form=apply_form.FORM, intro=apply_form.INTRO.replace('示範服務店', out['brand_name']))      # 在哪個品牌應徵就寫哪個品牌
        w = con.execute('SELECT w.name,w.phone,w.regions FROM line_users l JOIN workers w ON w.id=l.worker_id WHERE l.account=? AND l.user_id=?', (b['account'], b['ref_id'])).fetchone()
        if w:
            out['prefill'] = {'name': w['name'], 'phone': w['phone'] or '', 'regions': w['regions'] or ''}
    return out

def _clean_phone(p):
    phone = re.sub(r'[^\d+\-]', '', str(p.get('phone', '')))[:20]
    if len(re.sub(r'\D', '', phone)) < 9:
        raise ValueError('請填手機號碼')
    return phone

def apply(con, t, p):
    """我要應徵 → 建立待審夥伴（店長在「待整理」決定），通知派單管理群。"""
    b = _bind(con, 'apply', t)
    import line_bot as _lb
    if b['account'] not in _lb.APPLY_ACCOUNTS:          # 示範服務店、生活線開放應徵
        raise PermissionError('這裡沒有開放夥伴應徵')
    ans = p.get('answers') if isinstance(p.get('answers'), dict) else p          # 完整版應徵表送 answers；舊頁面送平的欄位
    name = str(ans.get('name', '')).strip()[:30]
    if not name:
        raise ValueError('02｜請填寫您的本名')
    phone = _clean_phone(ans)
    digits = re.sub(r'\D', '', phone)[-9:]
    import apply_form, recruit
    recruit.ensure(con)
    counts = {k: n for k, n in con.execute('SELECT kind,COUNT(*) FROM worker_files WHERE ref=? GROUP BY kind', ('apply:' + str(t),))}
    full = apply_form.validate(ans, counts) if p.get('answers') else None
    mine = con.execute('SELECT worker_id FROM line_users WHERE account=? AND user_id=? AND worker_id IS NOT NULL', (b['account'], b['ref_id'])).fetchone()
    if full is not None and mine:          # 已經在面試流程裡的人補填完整應徵表（例如之前從「報到 → 新夥伴」進來、資料不夠）→ 更新那一筆，重新給管理層看
        import interviews
        iv = con.execute(f"SELECT id FROM interviews WHERE worker_id=? AND status IN ('screening','invited','scheduled','need_time','hold') ORDER BY created_at DESC LIMIT 1", (mine[0],)).fetchone()
        if iv:
            w0 = c.get(con, 'workers', mine[0])
            skills = full.get('skills') or {'家庭管家': '私人管家', '陪玩員': '陪玩陪讀', '整理師': '收納整理'}.get(full['role'], '')
            c.CTX.actor = f'LINE 應徵補填：{name}'
            c.save(con, 'workers', {'id': w0['id'], 'version': w0['version'], 'phone': phone, 'regions': str(full.get('regions', ''))[:400], 'skills': skills[:200],
                                    'availability': str(full.get('avail', ''))[:100]})
            con.execute("UPDATE interviews SET application=?,role=? WHERE id=?", (json.dumps(full, ensure_ascii=False), full['role'], iv[0]))
            if full.get('role'):                       # 應徵的職缺記到夥伴職類（可以在中控台再加）
                con.execute("UPDATE workers SET roles=CASE WHEN COALESCE(roles,'')='' THEN ? WHEN instr(roles, ?)>0 THEN roles ELSE roles||'、'||? END WHERE id=?",
                            (full['role'], full['role'], full['role'], w0['id']))
            con.execute('UPDATE worker_files SET worker_id=? WHERE ref=? AND worker_id IS NULL', (w0['id'], 'apply:' + str(t)))
            con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],))
            interviews.card_admin(con, iv[0], '📝 補填了完整應徵資料')
            return {'ok': True, 'name': name, 'existing': False, 'updated': True}
    if mine:                               # 稽核 M1：這個 LINE 已經是夥伴、又沒有進行中的面試 → 不另建新人（換電話會繞過黑名單），請管理群確認
        import automation
        w0 = c.get(con, 'workers', mine[0])
        _lb.admin_notify(con, '🙋 已是夥伴的 LINE 又送了應徵表', [('夥伴', f"{w0['name']}（{w0['status']}）" + ('・🚫 黑名單／停派' if automation.is_blacklisted(con, w0['id']) else '')),
                              ('這次填', f"{name} {phone}"), ('處理', '確認是不是同一人、要不要重新面試；系統沒有另建新夥伴')], f"/#w/{w0['id']}", '#C47F00')
        con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],))
        return {'ok': True, 'name': name, 'existing': True}
    old = next((w for w in c.rows(con, "SELECT id,name,phone,status FROM workers WHERE phone!=''") if re.sub(r'\D', '', w['phone'])[-9:] == digits), None)
    if not old:                            # 舊資料常常沒電話：同名、沒電話的舊夥伴 → 也請管理群確認是不是同一人（有 4 位因此重複）
        old = next(iter(c.rows(con, "SELECT id,name,phone,status FROM workers WHERE name=? AND COALESCE(phone,'')=''", (name,))), None)    # 稽核：完整版表單 name 在 answers 裡
    c.CTX.actor = f'LINE 應徵：{name}'
    if full is not None:                   # 完整版應徵表
        fields = {'regions': str(full.get('regions', ''))[:400], 'skills': str(full.get('skills', ''))[:200], 'availability': str(full.get('avail', ''))[:100],
                  'gender': str(full.get('gender') or '')[:10], 'tools_own': '、'.join(full.get('tools_own') or []) if isinstance(full.get('tools_own'), list) else str(full.get('tools_own') or '')[:200],
                  'willing': str(full.get('willing') or '')[:20]}      # 性別、自備工具、接單意願
        p = {**ans, 'role': full['role']}
    else:
        fields = {'regions': str(p.get('regions', ''))[:400], 'skills': str(p.get('skills', ''))[:200], 'availability': str(p.get('availability', ''))[:100]}
    role = p.get('role') if p.get('role') in ('服務人員', '家庭管家', '陪玩員', '整理師') else '服務人員'          # 客服 應徵資料表 01–09；稽核 H1
    if role != '服務人員':
        fields['skills'] = '、'.join(x for x in ({'家庭管家': '私人管家', '陪玩員': '陪玩陪讀', '整理師': '收納整理'}[role], fields['skills']) if x)
    app = {'role': role, 'has_exp': p.get('has_exp'), 'years': p.get('years'), 'cert': (f"有：{p.get('cert_name') or '（沒寫名稱）'}" if p.get('cert') == '有' else p.get('cert')),
           'exp_desc': p.get('exp_desc'), 'served': p.get('served'), 'portfolio_link': p.get('portfolio_link'), 'portfolio_text': p.get('portfolio_text'), 'ability': p.get('ability'),
           'feedback': p.get('feedback'), 'specialty': p.get('specialty'), 'avail': p.get('availability'), 'transport': p.get('transport'), 'iv_time': p.get('iv_time'),
           'days': p.get('days'), 'start': p.get('start'), 'exp': p.get('experience'), 'exp_types': p.get('exp_types'), 'abilities': p.get('abilities'), 'intro': p.get('intro')}
    app = {k: str(v).strip()[:500] for k, v in app.items() if v and str(v).strip()}        # 應徵表單（重新設計）：給管理層看資料用
    if full is not None:
        app = full
    note = '；'.join(x for x in (f"經驗：{str(p.get('experience', ''))[:200]}" if p.get('experience') else '', f"交通：{p.get('transport', '')}" if p.get('transport') else '',
                                 f"每週：{app['days']}" if app.get('days') else '', f"可開始：{app['start']}" if app.get('start') else '',
                                 f"做過：{app['exp_types']}" if app.get('exp_types') else '', f"可以接受：{app['abilities']}" if app.get('abilities') else '',
                                 f"自我介紹：{app['intro'][:200]}" if app.get('intro') else '', f"LINE 應徵 {c.now()[:10]}") if x)
    if old:                                # 電話對到既有夥伴：不直接綁（可能是冒用），請派單管理群確認
        import line_bot
        con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],))
        lu = con.execute('SELECT display_name FROM line_users WHERE account=? AND user_id=?', (b['account'], b['ref_id'])).fetchone()
        line_bot.request_bind(con, b['account'], b['ref_id'], 'worker', old['id'], name, phone, lu[0] if lu else '')
        last = con.execute("SELECT status,decided_at,note FROM interviews WHERE worker_id=? ORDER BY created_at DESC LIMIT 1", (old['id'],)).fetchone()
        if ix.setting(con, 'reapply_mode') == 'screen' and old['status'] in ('paused', 'review') and last and last['status'] in ('rejected', 'reject_pending') \
                and not con.execute("SELECT 1 FROM interviews WHERE worker_id=? AND status IN ('screening','invited','scheduled','need_time','hold','check','trial','training')", (old['id'],)).fetchone():
            import interviews                  # 被婉拒過的人再應徵 → 直接建新的審資料卡（附上次婉拒原因）
            app['上次應徵'] = f"{(last['decided_at'] or '')[:10]} 婉拒" + (f"：{last['note'][:100]}" if last['note'] else '')
            # 資安：電話對得到不代表是本人。只有這個 LINE 已經綁定這位夥伴才直接審資料、掛照片；
            # 否則先存起來，等管理層在 decide_bind 確認「是本人」之後才做（防止知道電話就冒用別人身分）。
            if bound_to(con, b['account'], b['ref_id'], old['id']):
                con.execute('UPDATE worker_files SET worker_id=? WHERE ref=? AND worker_id IS NULL', (old['id'], 'apply:' + str(t)))
                interviews.screen(con, old['id'], b['ref_id'], b['account'], app)
                return {'ok': True, 'name': name, 'existing': True, 'rescreen': True}
            ix.set_setting(con, f"rescreen_wait:{b['account']}:{b['ref_id']}", json.dumps({'worker_id': old['id'], 't': str(t), 'app': app}, ensure_ascii=False))
        if ix.setting(con, 'pair_push') != 'on':           # 配對推播開著時，綁定待確認卡已經送群組了
            line_bot.admin_notify(con, '🙋 既有夥伴重新報到', [('名字', name), ('手機', phone), ('對到', f"{old['name']}（{old['status']}）"),          # 稽核：以前這段在 return 後面，永遠不會發
                                                         ('處理', '確認是本人：中控台「更多 › LINE 配對」最上面按「是本人」')], '/#pairing', '#0A84FF')
        return {'ok': True, 'name': name, 'existing': True}
    else:
        # 面試系統：預設面試錄取後才可派工（店長可改「面試前接單」）；不需要面試時照 規則直接可派工
        import interviews
        need_iv = interviews.required(con)
        wid = c.save(con, 'workers', {'name': name, 'phone': phone, 'status': 'review' if need_iv else 'active', 'notes': note, **fields})['id']
        if full and full.get('role'):          # 應徵的職缺記到夥伴職類
            con.execute('UPDATE workers SET roles=? WHERE id=?', (full['role'], wid))
        if not need_iv:
            import trash
            trash.flag(con, 'workers', wid, '5. 新應徵者', f'{name}（{phone}）從官方 LINE 應徵：{fields["regions"]}／{fields["skills"]}', '已自動設為可派工；不適合請改暫停或設黑名單')
    con.execute('UPDATE line_users SET worker_id=?,onboard=? WHERE account=? AND user_id=?', (wid, 'applicant', b['account'], b['ref_id']))
    if not old:
        import interviews
        import recruit; recruit.ensure(con)
        con.execute('UPDATE worker_files SET worker_id=? WHERE ref=? AND worker_id IS NULL', (wid, 'apply:' + str(t)))     # 應徵時上傳的作品集照片
        interviews.screen(con, wid, b['ref_id'], b['account'], app)     # 管理層先看資料 → OK 邀請電話面試／不 OK 婉拒
    import line_bot
    line_bot.worker_menu_link(con, b['ref_id'], b['account'])        # 應徵送出就換成夥伴選單，不再給客戶介面
    con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],))
    import line_bot, notify
    if old:                                   # 新應徵只發一張「審資料」卡，不重複
        line_bot.admin_notify(con, '🙋 既有夥伴重新報到', [('名字', name), ('手機', phone), ('區域', fields['regions'] or '—'), ('項目', fields['skills'] or '—'),
                                                                                   ('時間', fields['availability'] or '—')], f'/#w/{wid}', '#0A84FF')
    notify.staff(con, '新的夥伴應徵', f'{name} {phone}', f'/#w/{wid}')
    return {'ok': True, 'name': name, 'existing': bool(old)}

def hello(con, t, p):
    """我要詢問 → 先留姓名、電話，建立潛在客戶並對應這個 LINE；之後才進一般對話。"""
    b = _bind(con, 'hi', t)
    name = str(p.get('name', '')).strip()[:40]
    if not name:
        raise ValueError('請填稱呼')
    phone = _clean_phone(p)
    digits = re.sub(r'\D', '', phone)[-9:]
    import brands as _b
    cu = next((x for x in c.rows(con, f"SELECT id,phone FROM customers WHERE phone!='' AND {_b.cust_sql()}=?", (b['account'],)) if re.sub(r'\D', '', x['phone'])[-9:] == digits), None)
    c.CTX.actor = f'LINE 新客戶：{name}'
    lu = con.execute('SELECT display_name,customer_id FROM line_users WHERE account=? AND user_id=?', (b['account'], b['ref_id'])).fetchone()
    if lu and lu['customer_id']:           # 這個 LINE 已經是客戶：只把留言轉給同事，不另建、不改綁
        import line_bot, notify
        me = c.get(con, 'customers', lu['customer_id'])
        con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],))
        line_bot.admin_notify(con, '💬 既有客戶又留了資料', [('客戶', me['name']), ('這次填', f'{name} {phone}'), ('想問', str(p.get('need', '') or '—')[:60])], f"/#c/{me['id']}", '#0A84FF')
        notify.staff(con, '既有客戶留言', f"{me['name']}：{str(p.get('need', ''))[:40]}", f"/#c/{me['id']}")
        return {'ok': True, 'name': me['name']}
    if cu and con.execute('SELECT 1 FROM line_users WHERE customer_id=? AND account=?', (cu['id'], b['account'])).fetchone():
        cu = None                          # 這位客戶已綁別的 LINE：不覆蓋，另建潛在客戶給店長判斷
    if cu:                                 # 電話對到舊客戶：先不綁，請派單管理群確認是本人
        import line_bot
        con.execute("UPDATE line_users SET suggest_cid=?,onboard='asked' WHERE account=? AND user_id=?", (cu['id'], b['account'], b['ref_id']))
        con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],))
        line_bot.request_bind(con, b['account'], b['ref_id'], 'customer', cu['id'], name, phone, lu[0] if lu else '')
        return {'ok': True, 'name': name, 'pending': True}
    cid = c.save(con, 'customers', {'name': name, 'phone': phone, 'line_name': (lu[0] if lu else ''), 'status': 'lead', 'line_account': b['account'],
                                                        'channel': __import__('brands').channel(con, b['account']),
                                                        'notes': '；'.join(x for x in (f"地區：{p.get('area')}" if p.get('area') else '', f"想問：{str(p.get('need', ''))[:200]}" if p.get('need') else '',
                                                                                      f"官方 LINE 首次詢問 {c.now()[:10]}") if x)})['id']
    con.execute('UPDATE line_users SET customer_id=?,onboard=? WHERE account=? AND user_id=?', (cid, 'customer', b['account'], b['ref_id']))
    con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],))
    import line_bot, notify
    line_bot.admin_notify(con, '👋 新客戶留了基本資料', [('稱呼', name), ('電話', phone), ('地區', p.get('area') or '—'), ('想問', str(p.get('need', '') or '—')[:60])], f'/#c/{cid}', '#0A84FF')
    notify.staff(con, '新客戶留資料', f'{name} {phone}', f'/#c/{cid}')
    line_bot.on_hello_done(con, b['account'], b['ref_id'], name)
    return {'ok': True, 'name': name}


def bound_to(con, account, user_id, worker_id):
    """這個 LINE 帳號是否已經（經過核准）綁定這位夥伴。"""
    return bool(con.execute('SELECT 1 FROM line_users WHERE account=? AND user_id=? AND worker_id=?', (account, user_id, worker_id)).fetchone())


def rescreen_after_bind(con, account, user_id, worker_id):
    """綁定核准後：補做先前因為還沒綁定而暫緩的「再應徵審資料」與照片歸屬。回傳是否有做。"""
    key = f'rescreen_wait:{account}:{user_id}'
    raw = ix.setting(con, key)
    if not raw:
        return False
    ix.set_setting(con, key, '')
    d = json.loads(raw)
    if d.get('worker_id') != worker_id or not bound_to(con, account, user_id, worker_id):
        return False
    import interviews
    con.execute('UPDATE worker_files SET worker_id=? WHERE ref=? AND worker_id IS NULL', (worker_id, 'apply:' + str(d.get('t', ''))))
    interviews.screen(con, worker_id, user_id, account, d.get('app') or {})
    return True
