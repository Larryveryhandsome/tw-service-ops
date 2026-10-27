"""工作詳細需求審核：報價單上客人打的服務需求，排班表（工單詳情）要直接看得到，不用回去翻報價單；
而且「詳細需求內容需要管理層審核才可以顯示給夥伴」。

- 客人線上簽收報價單建立的工單 → orders.scope_hold=1：夥伴的派工卡、工作頁、主控板都看不到服務範圍、客戶交代、案場備註，只看到服務類型。
- 派單管理群收一張「詳細需求待審核」卡：✅ 沒問題，給夥伴看／✏️ 修改後再給（打字，一行一項）／打開工單。
- 中控台工單詳情也有「審核通過，給夥伴看」；管理層在中控台改了範圍或備註＝已審核。
- 審核後：已接這筆的夥伴收到「工作內容確認」卡（範圍＋打開工作頁）。
- 沒審的：每天 10:00 派單管理群列出來（3 天內開工的 @管理層），直到審核。不自動通過（要管理層看過）。
"""
import datetime as dt
import core as c
import integrations as ix

def held(o):
    return bool((o or {}).get('scope_hold'))

def hold(con, oid):
    """報價單簽收建立的工單：先擋住詳細需求，送管理群審核。"""
    import line_bot as lb
    o = c.get(con, 'orders', oid)
    if not (o.get('scope') or '').strip():          # 備註夥伴看不到，只有服務範圍要審
        return False
    if ix.setting(con, 'scope_auto_same') == '1' and o.get('site_id'):      # 店長決定（同範圍自動通過）：同案場、範圍和備註跟上次審核過的一樣 → 不用再審
        norm = lambda v: ''.join((v or '').split())
        prev = con.execute("SELECT scope,notes FROM orders WHERE site_id=? AND id!=? AND COALESCE(scope_ok,'')!='' AND COALESCE(scope_hold,0)=0 "
                           "ORDER BY date DESC LIMIT 1", (o['site_id'], oid)).fetchone()
        if prev and norm(prev['scope']) == norm(o.get('scope')) and (not norm(o.get('notes')) or norm(prev['notes']) == norm(o.get('notes'))):
            con.execute("UPDATE orders SET scope_hold=0,scope_ok=? WHERE id=?", (f"自動（同案場範圍跟上次一樣）{c.now()[5:16].replace('T', ' ')}", oid))
            return False
    con.execute("UPDATE orders SET scope_hold=1,scope_ok='' WHERE id=?", (oid,))
    g = lb.admin_group(con)
    if g:
        m = card(con, oid)
        lb.queue(con, lb.WORK_ACCOUNT, g, 'scope_review', m[0]['altText'], m, oid)
    return True

def card(con, oid, title='📝 詳細需求待審核'):
    import line_bot as lb, board
    o, s, cu, crew, area = lb.order_ctx(con, oid)
    rows = [lb._row('工作', f"{lb.md(o['date'])} {o['start']}–{o['end']}"), lb._row('客戶', cu.get('name') or '—')]
    sc = [x.strip() for x in board._no_money(o.get('scope') or '').splitlines() if x.strip()]
    if sc:
        rows.append(lb._t('服務範圍', weight='bold', margin='md'))
        rows += [lb._t('・' + x, wrap=True, size='sm') for x in sc[:15]]
    for label, v in (('內部備註（夥伴看不到）', board._no_money(o.get('notes') or '')), ('案場備註（夥伴看不到）', board._no_money(s.get('notes') or ''))):
        if v.strip():
            rows.append(lb._t(label, weight='bold', margin='md')); rows.append(lb._t(v[:400], wrap=True, size='sm'))
    rows.append(lb._t('審核前，夥伴只看得到服務類型；按「沒問題」才會給夥伴看。', size='xs', color=lb.SUB, wrap=True, margin='lg'))
    return lb.flex(f"{title}：{lb.md(o['date'])} {cu.get('name') or ''}", lb._bubble(lb._header(title, f"{lb.md(o['date'])} {s.get('name') or area}", '#C47F00'), rows,
        [lb._btn('✅ 沒問題，給夥伴看', data=f'a=scok&o={oid}', primary=True), lb._btn('✏️ 修改後再給（打字）', data=f'a=scedit&o={oid}'),
         lb._btn('打開工單', uri=f"{lb.BASE_URL}/#o/{oid}")]))

def approve(con, oid, who, new_scope=None):
    """管理層審核：可以順便改範圍（一行一項）。已接這筆的夥伴收到「工作內容確認」。回傳說明。"""
    import line_bot as lb, worker_portal
    o = c.get(con, 'orders', oid)
    if not held(o) and new_scope is None:
        return f"這筆已經審核過了（{o.get('scope_ok') or '不用審核'}）"
    stamp = f"{who} {c.now()[5:16].replace('T', ' ')}"
    if new_scope is not None:
        lines = [x.strip(' ・-•\t') for x in str(new_scope).splitlines() if x.strip(' ・-•\t')]
        con.execute('UPDATE orders SET scope=?,version=version+1 WHERE id=?', ('\n'.join(lines)[:1500], oid))
    con.execute("UPDATE orders SET scope_hold=0,scope_ok=? WHERE id=?", (stamp, oid))
    c.audit(con, 'scope_ok', 'orders', oid, after={'by': who, 'edited': new_scope is not None})
    o, s, cu, crew, area = lb.order_ctx(con, oid)
    for a in crew:
        m = lb.flex('工作內容確認', lb._bubble(lb._header('工作內容確認 📝', lb.when(o)),
            [lb._row('範圍', lb.scope_text(o, 300) or s.get('service_type') or '服務')] + ([lb._row('備註', __import__('board')._no_money(o.get('notes') or '')[:200])] if (o.get('notes') or '').strip() else []),
            [lb._btn('打開工作頁', uri=worker_portal.work_url(con, a['id']), primary=True)], '有問題直接在這裡問我們 🙏'))
        lb.to_worker(con, a['wid'], 'scope_ready', m[0]['altText'], m, oid)
    return f"✅ 已審核（{who}）：夥伴看得到詳細需求了" + (f"，已通知 {'、'.join(a['name'] for a in crew)}" if crew else '')

def chase(con):
    """每天 10:00：還沒審核的詳細需求（14 天內的工作）→ 派單管理群；3 天內開工的 @管理層。"""
    import line_bot as lb
    now = dt.datetime.now(c.TZ); today = now.date().isoformat()
    if not ('10:00' <= now.strftime('%H:%M') < '21:00') or ix.setting(con, 'scope_chase_day') == today:
        return 0
    ix.set_setting(con, 'scope_chase_day', today)
    rows = c.rows(con, "SELECT id,date FROM orders WHERE scope_hold=1 AND status NOT IN ('cancelled','archived','completed') AND date BETWEEN ? AND ? ORDER BY date,start",
                  (today, (now.date() + dt.timedelta(days=14)).isoformat()))
    g = lb.admin_group(con)
    if not rows or not g:
        return 0
    soon = [r for r in rows if r['date'] <= (now.date() + dt.timedelta(days=3)).isoformat()]
    msgs = []
    if soon:
        msgs.append(lb.ping(con, f"📝 有 {len(soon)} 筆 3 天內的工作，詳細需求還沒審核，夥伴還看不到要清哪裡：請看下面的卡按「沒問題」或修改"))
    cards = [card(con, r['id'], '📝 詳細需求還沒審核')[0]['contents'] for r in rows[:9]]
    msgs += lb._carousel('詳細需求還沒審核', cards)
    lb.queue(con, lb.WORK_ACCOUNT, g, 'scope_chase', f'詳細需求還沒審核 {len(rows)} 筆', msgs)
    return len(rows)
