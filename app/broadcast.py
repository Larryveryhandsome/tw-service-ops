"""群發公告（例如過年價）：先送店長審核，核准才發給該品牌所有業主（已綁官方 LINE 的客戶）。

prepare(con, brand, text) → reviews.submit('群發公告', …, payload={'action': 'broadcast', …})
店長按「核准」→ reviews.decide → after_approve → execute：
- 只發給這個品牌、已綁 LINE、沒封鎖的客戶（品牌不混）
- 發之前看 LINE 本月額度，不夠就不發、通知管理群（不會發一半）
"""
import json
import core as c
import integrations as ix

def audience(con, brand):
    return [r[0] for r in con.execute("SELECT DISTINCT user_id FROM line_users WHERE account=? AND customer_id IS NOT NULL AND is_friend=1", (brand,))]

def prepare(con, brand, text, title='', by='負責人（Claude 擬稿）', messages=None, launch=None, everyone=False):
    """messages：給業主的圖卡（業主訊息以圖卡為主），沒有就發 text。
    launch：核准發出時同步上線的業務線 id（店長沒說可以發布就不公開，公告發出那一刻才上線）。
    everyone：發給這個官方 LINE 的全部好友（含還沒綁定的業主；要發給所有業主，不是只發已綁定的）。"""
    import reviews, brands
    if everyone:
        return _prepare_all(con, brand, text, title, by, messages, launch)
    n = len(audience(con, brand))
    names = [r[0] for r in con.execute("SELECT DISTINCT cu.name FROM line_users l JOIN customers cu ON cu.id=l.customer_id WHERE l.account=? AND l.is_friend=1 ORDER BY cu.name", (brand,))]
    body = (f"要發給【{brands.name(brand)}】已綁官方 LINE 的業主 {n} 位，內容如下（只提自己的品牌）：\n\n{text}\n\n發給誰：" + '、'.join(names[:100])
            + (f' …等 {len(names)} 位' if len(names) > 100 else '')
            + (f"\n\n🚀 核准發出的同時，「{'、'.join((brands.line(con, x) or {}).get('name', x) for x in launch)}」會在{brands.name(brand)}上線（客人選單、預約頁看得到）；沒核准前客人看不到。" if launch else '')
            + "\n\n核准後系統會立刻發出；要改內容或對象請按「要修改」打字。")
    return reviews.submit(con, '群發公告', title or f'{brands.name(brand)} 群發公告（{n} 位業主）', body,
                          {'action': 'broadcast', 'brand': brand, 'text': text, 'messages': messages or None, 'launch': list(launch or []),
                           'preview': messages or [{'type': 'text', 'text': text[:4900]}]}, by)

def _prepare_all(con, brand, text, title, by, messages, launch):
    import reviews, brands, line_bot as lb
    n, q = reach(con, brand), lb.quota(con, brand)
    known = con.execute('SELECT COUNT(*) FROM line_users WHERE account=? AND is_friend=1 AND customer_id IS NOT NULL', (brand,)).fetchone()[0]
    left = (q.get('limit') or 0) - (q.get('used') or 0) if q.get('type') == 'limited' else None
    excl = make_exclude(con, brand, 'workers', f'{brand} 夥伴（業主公告不發）{c.now()[:10]}')      # 認得出是夥伴的先不要發
    body = (f"要用【{brands.name(brand)}】官方 LINE 群發給全部好友（約 {n or '?'} 位可送達：已綁定的業主 {known} 位＋還沒綁定的業主），內容在下一則（實際收到的樣子）。\n"
            + (exclude_text(excl) + '\n\n' if excl['n'] else '\n')
            + ""
            + (f"🚀 核准發出的同時，「{'、'.join((brands.line(con, x) or {}).get('name', x) for x in launch)}」會在{brands.name(brand)}上線（客人選單、預約頁看得到）；沒核准前客人看不到。\n\n" if launch else '')
            + f"額度：本月已用 {q.get('used', '?')}／{q.get('limit', '?')} 則" + (f"，發完約剩 {left - (n or 0)} 則" if left is not None else '') + '。'
            + ('' if lb.mode(con, brand) == 'on' else f"\n⚠️ {brands.name(brand)} 官方 LINE 現在是測試模式，核准了也要改成正式才會發。")
            + "\n\n核准後系統會立刻發出；要改內容請按「要修改」打字。")
    return reviews.submit(con, '群發公告', title or f'{brands.name(brand)} 群發（全部好友約 {n or "?"} 位）', body,
                          {'action': 'broadcast', 'brand': brand, 'text': text, 'messages': messages or None, 'launch': list(launch or []), 'everyone': True, 'exclude': excl,
                           'preview': messages or [{'type': 'text', 'text': text[:4900]}]}, by)

def _execute_all(con, payload, by):
    """全部好友群發（LINE broadcast）：正式模式、額度都 OK 才發；發成功才同步上線。"""
    import line_bot as lb, brands
    brand = payload['brand']; name = brands.name(brand)
    msg = payload.get('messages') or [{'type': 'text', 'text': payload['text'][:4900]}]
    if lb.mode(con, brand) != 'on':
        lb.admin_notify(con, f'⚠️ {name} 群發沒發出', [('原因', '這個官方 LINE 還在測試／關閉模式'), ('處理', '改成正式後請重新送審（沒發出就不會上線）')], '/#line', '#C62828')
        return 0
    n, q = reach(con, brand), lb.quota(con, brand)
    if n is None:
        n = con.execute('SELECT COUNT(*) FROM line_users WHERE account=? AND is_friend=1', (brand,)).fetchone()[0]
    left = (q.get('limit') or 0) - (q.get('used') or 0) if q.get('type') == 'limited' else 10 ** 9
    if 'error' in q or (n and n - ((payload.get('exclude') or {}).get('n') or 0) > left):
        lb.admin_notify(con, f'⚠️ {name} 群發沒發出：LINE 額度不夠', [('要發', f'全部好友約 {n} 位'), ('本月剩', f'{left} 則'), ('處理', '到官方帳號後台加購後請重新送審（沒發出就不會上線）')], '/#line', '#C62828')
        return 0
    excl = payload.get('exclude') or {}
    if excl.get('n'):                                  # 扣掉認得的夥伴 → 指定對象群發（LINE 處理完才上線，narrowcast_watch）
        if not excl.get('aid'):
            lb.admin_notify(con, f'⚠️ {name} 群發沒發出', [('原因', '夥伴排除名單沒建成功（照規定：認得出是夥伴的不發，所以整則先不發）'), ('處理', '請重新送審')], '/#line', '#C62828')
            return 0
        queue_narrowcast(con, brand, msg, excl, 'broadcast_all', by, payload.get('launch'), (n or 0) - excl['n'], payload.get('title') or '業主公告')
        c.audit(con, 'broadcast_all', 'line_users', brand, after={'reach': n, 'excluded': excl['n'], 'by': by, 'text': payload['text'][:200]})
        return max(1, (n or 0) - excl['n'])
    try:
        lb.api(con, '/v2/bot/message/validate/broadcast', {'messages': msg}, acc=brand)
        lb.api(con, '/v2/bot/message/broadcast', {'messages': msg}, acc=brand)
    except Exception as e:
        lb.admin_notify(con, f'⚠️ {name} 群發沒發出', [('原因', f'LINE 回錯誤：{str(e)[:80]}'), ('處理', '請重新送審（沒發出就不會上線）')], '/#line', '#C62828')
        return 0
    con.execute('INSERT INTO line_outbox(created_at,account,to_id,kind,alt,payload,status,sent_at) VALUES(?,?,?,?,?,?,?,?)',
                (c.now(), brand, 'broadcast', 'broadcast_all', msg[0].get('altText') or msg[0].get('text', '')[:390], json.dumps(msg, ensure_ascii=False), 'sent', c.now()))
    c.audit(con, 'broadcast_all', 'line_users', brand, after={'reach': n, 'by': by, 'text': payload['text'][:200]})
    if payload.get('launch'):
        launch_lines(con, payload['launch'], by)          # 發成功的同一刻上線
    lb.admin_notify(con, f'📣 {name} 公告已發出', [('對象', f'全部好友（約 {n} 位，含還沒綁定的業主）'), ('核准', by or '—')], '/#line', '#2E7D32')
    return n or 1

# ───────── 指定對象群發：全部好友扣掉排除名單─────────
NC = 'ncast:'

def known_ids(con, brand, who):
    """who='workers'：認得出是夥伴的（不是業主、不是管理層）；'bound'：已綁定的（業主、夥伴、管理層）。"""
    cond = ("worker_id IS NOT NULL AND customer_id IS NULL AND COALESCE(is_staff,0)=0" if who == 'workers'
            else "(customer_id IS NOT NULL OR worker_id IS NOT NULL OR COALESCE(is_staff,0)=1)")
    return [r[0] for r in con.execute(f"SELECT user_id FROM line_users WHERE account=? AND is_friend=1 AND user_id NOT LIKE 'demo%' AND {cond}", (brand,))]

def make_exclude(con, brand, who, label):
    """建 LINE 受眾名單（排除用）。回傳 {'who','n','aid','error'}；沒有要排除的人 → n=0。"""
    import line_bot as lb
    ids = known_ids(con, brand, who)
    out = {'who': who, 'n': len(ids), 'aid': None, 'error': ''}
    if not ids:
        return out
    try:
        r = lb.api(con, '/v2/bot/audienceGroup/upload', {'description': label[:110], 'isIfaAudience': False, 'audiences': [{'id': x} for x in ids]}, acc=brand)
        out['aid'] = r.get('audienceGroupId')
        if not out['aid']:
            out['error'] = 'LINE 沒有回名單代號'
    except Exception as e:
        out['error'] = str(e)[:120]
    return out

def exclude_text(excl):
    if not excl or not excl.get('n'):
        return ''
    who = '系統認得的夥伴' if excl['who'] == 'workers' else '已經綁定的人（業主、夥伴、管理層）'
    return (f"🚫 {who} {excl['n']} 位不會收到（LINE 指定對象群發：全部好友扣掉這份名單）。" if excl.get('aid') else
            f"⚠️ {who}的排除名單沒建成功（{excl.get('error') or '原因不明'}），核准了也不會發，請跟負責人說。")

def queue_narrowcast(con, brand, msg, excl, kind, by, launch=None, n=0, title=''):
    """排一筆指定對象群發，馬上試送一次；LINE 處理完才上線、通知（narrowcast_watch）。"""
    key = f"{NC}{brand}:{c.uid('nc')[-8:]}"
    ix.set_setting(con, key, json.dumps({'brand': brand, 'msg': msg, 'aid': excl['aid'], 'excluded': excl['n'], 'who': excl['who'], 'kind': kind, 'by': by,
                                         'launch': list(launch or []), 'n': n, 'title': title, 'stage': 'queued', 'at': c.now()}, ensure_ascii=False))
    narrowcast_watch(con, only=key)
    return key

def narrowcast_watch(con, only=None):
    """每分鐘：排隊的 → 排除名單準備好（READY）就送出；送出的 → 查 LINE 進度，發完（succeeded）才上線＋通知管理群；失敗 → 說原因、不上線。"""
    import datetime as dt, line_bot as lb, brands
    done = 0
    late = (dt.datetime.now(c.TZ) - dt.timedelta(hours=2)).isoformat(timespec='seconds')
    q = f"SELECT key,value FROM settings WHERE key LIKE '{NC}%' AND value!=''" + (' AND key=?' if only else '')
    for key, v in con.execute(q, (only,) if only else ()).fetchall():
        try:
            st = json.loads(v)
        except ValueError:
            ix.set_setting(con, key, ''); continue
        b = st['brand']; name = brands.name(b)
        def fail(why):
            ix.set_setting(con, key, '')
            lb.admin_notify(con, f"⚠️ {name}「{st.get('title') or '群發'}」沒發出", [('原因', why[:120]), ('處理', '沒發出就不會上線；排除問題後請重新送審')], '/#line', '#C62828')
        try:
            if st['stage'] == 'queued':
                g = lb.api(con, f"/v2/bot/audienceGroup/{st['aid']}", acc=b)
                status = (g.get('audienceGroup') or {}).get('status')
                if status in ('FAILED', 'EXPIRED', 'INACTIVE'):
                    fail(f'排除名單狀態 {status}'); continue
                if status != 'READY':
                    if st['at'] < late:
                        fail('排除名單 2 小時還沒準備好')
                    continue
                lb.api(con, '/v2/bot/message/validate/narrowcast', {'messages': st['msg']}, acc=b)
                _, h = lb.api_headers(con, '/v2/bot/message/narrowcast', {'messages': st['msg'], 'recipient': {'type': 'operator', 'not': {'type': 'audience', 'audienceGroupId': st['aid']}}}, acc=b)
                st.update(stage='sent', req=h.get('x-line-request-id') or '', sent_at=c.now())
                ix.set_setting(con, key, json.dumps(st, ensure_ascii=False))
                con.execute('INSERT INTO line_outbox(created_at,account,to_id,kind,alt,payload,status,sent_at) VALUES(?,?,?,?,?,?,?,?)',
                            (c.now(), b, 'narrowcast', st['kind'], (st['msg'][0].get('altText') or st['msg'][0].get('text', ''))[:390], json.dumps(st['msg'], ensure_ascii=False), 'sent', c.now()))
                c.audit(con, 'narrowcast', 'line_users', b, after={'excluded': st['excluded'], 'who': st['who'], 'by': st['by'], 'kind': st['kind']})
            if st['stage'] == 'sent':
                p = lb.api(con, f"/v2/bot/message/progress/narrowcast?requestId={st['req']}", acc=b) if st.get('req') else {'phase': 'succeeded'}
                ph = p.get('phase')
                if ph == 'succeeded':
                    ix.set_setting(con, key, '')
                    if st.get('launch'):
                        launch_lines(con, st['launch'], st['by'])          # LINE 發完的同一刻上線
                    who = '夥伴' if st['who'] == 'workers' else '已綁定的人'
                    lb.admin_notify(con, f"📣 {name}「{st.get('title') or '群發'}」已發出", [('對象', f"全部好友，排除{who} {st['excluded']} 位"), ('實際送達', f"{p.get('successCount', '?')} 位"), ('核准', st['by'] or '—')], '/#line', '#2E7D32')
                    done += 1
                elif ph == 'failed':
                    fail(f"LINE 處理失敗：{p.get('failedDescription') or p.get('errorCode') or '—'}")
                elif st.get('sent_at', '') < late:
                    fail('LINE 2 小時還沒處理完')
        except Exception as e:
            if st.get('stage') == 'queued' or st.get('at', '') < late:
                fail(str(e))
    return done

def bind_due_tick(con, now=None):
    """排好日期的綁定群發（例：企業線額度不夠 → 11/1 額度重置後自動送審）。settings bind_due:<品牌>＝YYYY-MM-DD；到期 10:00 後送一次審核卡（排除已綁定的），送完清掉。"""
    import datetime as dt
    now = now or dt.datetime.now(c.TZ)
    if now.strftime('%H:%M') < '10:00':
        return 0
    n = 0
    for key, due in con.execute("SELECT key,value FROM settings WHERE key LIKE 'bind_due:%' AND value!=''").fetchall():
        if due > now.date().isoformat():
            continue
        brand = key.split(':', 1)[1]
        ix.set_setting(con, key, '')
        prepare_bind(con, brand, f'系統（{due} 排定：額度重置後送審）')
        n += 1
    return n

def launch_lines(con, ids, by=''):
    """公告核准 → 業務線同步上線（啟用），管理群一張卡：客人在哪裡找得到、怎麼買。回傳上線的名稱。"""
    import brands, line_bot as lb
    done = []
    for lid in ids or []:
        ln = brands.line(con, lid)
        if not ln or ln['active']:
            continue
        con.execute('UPDATE business_lines SET active=1 WHERE id=?', (lid,))
        c.audit(con, 'line_launch', 'business_lines', lid, after={'by': by or '公告核准'})
        done.append((ln['brand_id'], ln['name']))
    if done:
        brands.reload(con)
        for bid, nm in done:
            lb.admin_notify(con, f'🚀 {brands.name(bid)}「{nm}」上線了', [('客人在哪裡找', 'LINE 下方選單「服務項目」→ ' + nm + '（預約頁也看得到）'), ('怎麼買', '選好送出 → 主管確認報價 → 客人收到報價卡確認就成立'),
                            ('公告', '已發給這個品牌已綁 LINE 的業主')], '', '#2E7D32')
    return [nm for _, nm in done]

def execute(con, payload, by=''):
    import line_bot as lb, brands
    if payload.get('everyone'):
        return _execute_all(con, payload, by)
    brand, text = payload['brand'], payload['text']
    to = audience(con, brand)
    q = lb.quota(con, brand)
    left = (q.get('limit') or 0) - (q.get('used') or 0) if q.get('type') == 'limited' else 10 ** 9
    if 'error' not in q and len(to) > left:
        lb.admin_notify(con, f'⚠️ {brands.name(brand)} 群發沒發出：LINE 額度不夠', [('要發', f'{len(to)} 位'), ('本月剩', f'{left} 則'), ('處理', '到官方帳號後台加購訊息後，請再核准一次')], '/#line', '#C62828')
        return 0
    if payload.get('launch'):
        launch_lines(con, payload['launch'], by)          # 公告發出的同一刻上線
    msg = payload.get('messages') or [{'type': 'text', 'text': text[:4900]}]
    for u in to:
        lb.queue(con, brand, u, 'broadcast', f'{brands.name(brand)} 公告', msg)
    c.audit(con, 'broadcast', 'line_users', brand, after={'count': len(to), 'by': by, 'text': text[:200]})
    lb.admin_notify(con, f'📣 {brands.name(brand)} 公告已發出', [('對象', f'{len(to)} 位業主'), ('核准', by or '—')], '/#line', '#2E7D32')
    return len(to)

# ───────── 請「還沒綁定的好友」綁定（官方 LINE 群發：發給全部好友，已綁的人按了只會說已綁好）─────────
def reach(con, brand):
    """LINE 統計的可送達好友數（兩天前的資料；拿不到回 None）。"""
    import datetime as dt, line_bot as lb
    try:
        f = lb.api(con, f"/v2/bot/insight/followers?date={(dt.date.today() - dt.timedelta(days=2)).strftime('%Y%m%d')}", acc=brand)
        return f.get('targetedReaches') or f.get('followers')
    except Exception:
        return None

def prepare_bind(con, brand, by='負責人（Claude 擬稿）'):
    """負責人 沒互動過的好友（系統拿不到 userId）→ 群發請他們綁定。先送店長審核；核准後才發。"""
    import reviews, brands, line_bot as lb
    n, q = reach(con, brand), lb.quota(con, brand)
    left = (q.get('limit') or 0) - (q.get('used') or 0) if q.get('type') == 'limited' else None
    mode = lb.mode(con, brand)
    excl = make_exclude(con, brand, 'bound', f'{brand} 已綁定（綁定群發不發）{c.now()[:10]}')      # 只發給還沒綁定的
    body = (f"要用【{brands.name(brand)}】官方 LINE 群發給全部好友（約 {n or '?'} 位可送達），請還沒綁定的人綁定。\n" + (exclude_text(excl) + '\n' if excl['n'] else '') + "內容：\n\n"
            f"「您好，我是{brands.name(brand)} 😊 請花 10 秒綁定 🙏\n{lb.BIND_REMINDER}\n已經綁定過的朋友請忽略這則，謝謝您！」\n"
            f"按鈕：綁定我的資料（10 秒）" + ('／我是示範服務店夥伴，報到' if brand == lb.WORK_ACCOUNT else '') + "／我有需求想詢問\n\n"
            f"額度：本月已用 {q.get('used', '?')}／{q.get('limit', '?')} 則" + (f"，發完約剩 {left - (n or 0)} 則" if left is not None else '') + '。'
            + ('' if mode == 'on' else f"\n⚠️ {brands.name(brand)} 官方 LINE 現在是測試模式（好友按了綁定系統不會回），核准後會等妳把它改成正式發送才自動發出。")
            + "\n\n核准後系統會自動發出；要改請按「要修改」打字。")
    return reviews.submit(con, '群發公告', f'{brands.name(brand)} 群發：請還沒綁定的好友綁定（約 {max(0, (n or 0) - excl["n"]) or "?"} 位）', body,
                          {'action': 'bind_broadcast', 'brand': brand, 'preview': lb.card_bind_reminder(brand), 'exclude': excl}, by)

def execute_bind(con, payload, by=''):
    """核准後：品牌 LINE 是正式模式、額度夠才群發；測試模式先保留，改正式時自動發（decisions「LINE 正式發送」）。"""
    import json, line_bot as lb, brands
    brand = payload['brand']; name = brands.name(brand)
    if lb.mode(con, brand) != 'on':
        ix.set_setting(con, f'bind_bc_hold:{brand}', json.dumps({**payload, 'by': by}, ensure_ascii=False))
        lb.admin_notify(con, f'⏸ {name} 綁定群發已核准，等 LINE 改正式才發', [('原因', f'{name} 官方 LINE 還在測試模式，好友按了綁定系統不會回'), ('之後', '店長回答「LINE 正式發送」改成正式後，系統會自動發出')], '', '#C47F00')
        return 0
    n, q = reach(con, brand), lb.quota(con, brand)
    left = (q.get('limit') or 0) - (q.get('used') or 0) if q.get('type') == 'limited' else 10 ** 9
    excl = payload.get('exclude') or {}
    if 'error' in q or (n and n - (excl.get('n') or 0) > left):
        lb.admin_notify(con, f'⚠️ {name} 綁定群發沒發出', [('原因', q.get('error') or f'LINE 額度不夠：要 {n} 則，本月只剩 {left} 則'), ('處理', '到官方帳號後台升級方案／加購，或下個月再核准一次')], '', '#C62828')
        return 0
    m = lb.card_bind_reminder(brand)
    if excl.get('n'):                                  # 扣掉已綁定的 → 指定對象群發
        if not excl.get('aid'):
            lb.admin_notify(con, f'⚠️ {name} 綁定群發沒發出', [('原因', '已綁定的排除名單沒建成功（整則先不發，避免已綁的人又收到）'), ('處理', '請重新送審')], '', '#C62828')
            return 0
        ix.set_setting(con, f'bind_bc_hold:{brand}', '')
        queue_narrowcast(con, brand, m, excl, 'bind_broadcast', by, None, (n or 0) - excl['n'], '請還沒綁定的好友綁定')
        c.audit(con, 'bind_broadcast', 'line_users', brand, after={'reach': n, 'excluded': excl['n'], 'by': by})
        return max(1, (n or 0) - excl['n'])
    lb.api(con, '/v2/bot/message/validate/broadcast', {'messages': m}, acc=brand)
    lb.api(con, '/v2/bot/message/broadcast', {'messages': m}, acc=brand)
    con.execute('INSERT INTO line_outbox(created_at,account,to_id,kind,alt,payload,status,sent_at) VALUES(?,?,?,?,?,?,?,?)',
                (c.now(), brand, 'broadcast', 'bind_broadcast', m[0]['altText'], json.dumps(m, ensure_ascii=False), 'sent', c.now()))
    ix.set_setting(con, f'bind_bc_hold:{brand}', '')
    c.audit(con, 'bind_broadcast', 'line_users', brand, after={'reach': n, 'by': by})
    lb.admin_notify(con, f'📣 {name} 已群發「請綁定」', [('對象', f'全部好友（約 {n or "?"} 位）'), ('核准', by or '—'), ('之後', '綁定的人會出現在系統；沒綁的可以在官方 LINE 聊天室提醒')], '', '#2E7D32')
    return n or 1

def prepare_workers(con, text, title='', by='負責人（Claude 擬稿）', messages=None):
    """夥伴公告（系統更新、新規定）：先送店長審核，核准後發給所有可派工、已綁官方 LINE 一對一的夥伴（老闆不算）。"""
    import reviews, line_bot as lb
    ts = lb.guide_targets(con); n = len(ts)
    body = (f"要發給已綁官方 LINE 的夥伴 {n} 位，內容如下：\n\n{text}\n\n發給誰：" + '、'.join(sorted({r['name'] for r in ts}))
            + "\n\n核准後系統會立刻發出；要改內容或對象請按「要修改」打字。")
    return reviews.submit(con, '夥伴公告', title or f'夥伴公告（{n} 位）', body,
                          {'action': 'worker_broadcast', 'text': text, 'messages': messages or None, 'preview': messages or [{'type': 'text', 'text': text[:4900]}]}, by)

def execute_workers(con, payload, by=''):
    import line_bot as lb
    to = lb.guide_targets(con)
    msg = payload.get('messages') or [{'type': 'text', 'text': payload['text'][:4900]}]      # 可以是圖卡＋照片
    for r in to:
        lb.queue(con, r['account'], r['user_id'], 'worker_notice', '示範服務店夥伴公告', msg)
    c.audit(con, 'worker_broadcast', 'line_users', 'workers', after={'count': len(to), 'by': by, 'text': payload['text'][:200]})
    lb.admin_notify(con, '📣 夥伴公告已發出', [('對象', f'{len(to)} 位夥伴'), ('核准', by or '—')], '', '#2E7D32')
    return len(to)
