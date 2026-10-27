"""工單人數比報價少 → 先問店長（原本報價 2 人，派工縮成 1 人，細節做不到；負責人：做人數少於報價時先問店長的防呆）。

- 中控台編輯工單把「需要人數」改得比簽收報價的人數少：店長本人、負責人（管理員）改 → 照改；其他人改 → 人數先不動（其他欄位照存），
  派單管理群／中控台「要決定」問店長：「可以，改成 N 人」／「不行，維持 M 人」。回答「可以」→ 系統自動把工單改成 N 人。
"""
import json
import core as c
import integrations as ix

def quote_people(con, oid):
    r = con.execute("SELECT people FROM quotes WHERE order_id=? AND status='signed' ORDER BY signed_at DESC LIMIT 1", (oid,)).fetchone()
    return int(r[0] or 0) if r else 0

def needs_ask(con, oid, new, user):
    """回傳 (要不要問, 報價人數, 原本人數)。"""
    o = con.execute('SELECT required_workers FROM orders WHERE id=?', (oid,)).fetchone()
    if not o:
        return False, 0, 0
    qp, old = quote_people(con, oid), int(o[0] or 1)
    if not qp or new >= qp or new >= old:
        return False, qp, old
    if user.get('role') == 'admin' or any(n in (user.get('name') or '') for n in ('店長', '負責人')):
        return False, qp, old
    return True, qp, old

def ask(con, oid, new, qp, old, by, drop=()):
    import line_bot as lb
    o = c.get(con, 'orders', oid)
    s = con.execute('SELECT name FROM sites WHERE id=?', (o['site_id'] or '',)).fetchone()
    topic = f'減人 {oid}'
    ix.set_setting(con, f'crewcut:{oid}', json.dumps({'new': new, 'old': old, 'quote': qp, 'by': by, 'drop': list(drop or []), 'at': c.now()}, ensure_ascii=False))
    lb.ask_staff(con, topic, f"{by} 要把 {lb.when(dict(o))}・{s[0] if s else ''} 的人數從 {old} 人改成 {new} 人，但報價是 {qp} 人。\n"
                             f"人力變少，服務細節可能做不到報價的程度（例如報價 2 人卻只派 1 人）。可以減人嗎？",
                 [f'可以，改成 {new} 人', f'不行，維持 {old} 人'], typed=False)
    c.audit(con, 'crewcut_ask', 'orders', oid, after={'new': new, 'old': old, 'quote': qp, 'by': by})
    return f'人數比報價少（報價 {qp} 人），已請店長確認；她按「可以」後系統自動改成 {new} 人' + ('（要拿掉的人到時一起處理）' if drop else '')

def apply(con, oid, ans, who):
    import re, leave
    st = json.loads(ix.setting(con, f'crewcut:{oid}') or '{}')
    if not st:
        return ''
    if not str(ans).startswith('可以'):
        ix.set_setting(con, f'crewcut:{oid}', '')
        c.audit(con, 'crewcut_no', 'orders', oid, after={'by': who})
        return f"維持 {st['old']} 人"
    m = re.search(r'(\d+)\s*人', str(ans))
    if m and int(m.group(1)) != st['new']:            # 舊卡片的答案（之後又問過別的人數）→ 不套用
        return f"這張卡是舊的：現在要問的是改成 {st['new']} 人，請在最新那張卡回答"
    try:                                               # 照中控台「減少人數」同一套：收單、額滿、拿掉的人通知
        msg = leave.set_people(con, oid, st['new'], who, st.get('drop') or [], approved=True)
    except ValueError as e:
        return f'沒有改：{e}（請到中控台工單「減少人數」選拿掉誰）'
    ix.set_setting(con, f'crewcut:{oid}', '')
    c.audit(con, 'crewcut_ok', 'orders', oid, after={'new': st['new'], 'by': who})
    return msg
