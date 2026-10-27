"""在 LINE 確認報價（報價只能進中控台確認，卡了好幾天 → 店長在 LINE 一鍵確認）。

客戶線上估價送出（照片收齊）→ 店長收到報價卡：品項、金額、日期、人數時數
- 確認報價，傳給客戶 → booking.admin_send（跟中控台按「確認並傳送」一樣）
- 改成現場估價 → booking.admin_stage（估價費規則照 decisions）
- 要改金額或品項 → 打開中控台這張報價
同事手動開的報價（source=同事開單）不走這張卡。
"""
import json
import core as c

def card(con, qid, chase=''):
    """chase＝逾時提醒（例：「等了 3 天」）：多「已用其他方式給客戶了」「不用了，取消這張」兩顆。"""
    import line_bot as lb, booking, brands
    q = booking._q(con, qid=qid)
    f = json.loads(q['form'] or '{}')
    items = booking._items(q); sub, tax, total = booking._totals(items, q['taxed'])
    rows = [lb._row('品牌', brands.name(q['account'] or 'demoshop')), lb._row('客戶', f"{f.get('company') or f.get('name', '')} {f.get('phone', '')}"),
            lb._row('地點', f"{f.get('area', '')} {f.get('address', '')}"[:40]), lb._row('服務', f.get('service') or f.get('service_type', '')),
            lb._row('日期', f"{q['date'] or '（客戶沒選）'} {q['start'] or ''}"), lb._row('人力', f"{q['people'] or 1} 人 × {q['hours'] or 0:g} 小時"),
            lb._row('照片', f"{booking._photo_count(con, q['id'])} 張（中控台看得到）")]
    mc = booking.maybe_customers(con, q, f)
    if mc:                         # Lin：還沒對到客戶、但可能是既有客戶 → 先在中控台對到他（不然成立會另建一位、收不到 LINE）
        rows.append(lb._t('⚠️ 可能是既有客戶：' + '；'.join(f"{x['name']}（{x['why']}）" for x in mc) + '。請在中控台報價按「就是他」', color='#C47F00', size='xs', wrap=True, margin='md'))
    for i in items[:6]:
        rows.append(lb._row(i.get('title', '')[:10], f"NT$ {int(i.get('amount') or 0):,}{i.get('unit') or ''}" + ('（另計）' if i.get('separate') or i.get('per_unit') else '')))
    if not items:                  # 系統不估價的（年度保養、交屋、生活線看現場）：請店長在 LINE 打金額
        rows.append(lb._t('這張系統不自動估價，請看照片後打金額報價。', color='#C47F00', weight='bold', size='sm', wrap=True, margin='md'))
        btns = [lb._btn('在 LINE 打金額報價', data=f"a=qa_type&q={q['id']}", primary=True), lb._btn('改成現場估價', data=f"a=qa_site&q={q['id']}"),
                lb._btn('看照片（中控台）', uri=f"{lb.BASE_URL}/#q/{q['id']}")]
    else:
        rows.append(lb._row('總計', f"NT$ {total:,}" + ('（含稅）' if q['taxed'] else '（未稅）')))
        w = booking.scale_warn(f, q)
        if w:                      # 人力比客戶原本填的多很多 → 先提醒
            rows.append(lb._t('⚠️ ' + w, color='#C47F00', size='xs', wrap=True, margin='md'))
        btns = [lb._btn('確認報價，傳給客戶 ✅', data=f"a=qa_ok&q={q['id']}", primary=True), lb._btn('在 LINE 重打品項金額', data=f"a=qa_type&q={q['id']}"),
                lb._btn('改成現場估價', data=f"a=qa_site&q={q['id']}"), lb._btn('看照片／細改（中控台）', uri=f"{lb.BASE_URL}/#q/{q['id']}")]
    if chase:
        btns += [lb._btn('已用其他方式給客戶了', data=f"a=qa_manual&q={q['id']}"), lb._btn('不用了，取消這張', data=f"a=qa_cancel&q={q['id']}")]
    return lb.flex(f"報價待確認 {q['id']}：NT$ {total:,}" + (f'（{chase}）' if chase else ''), lb._bubble(lb._header('🧮 報價待確認' + (f'・{chase}' if chase else ''), f"{q['id']}・NT$ {total:,}", '#C62828' if chase else '#0A84FF'), rows, btns,
        '確認後客戶會收到正式報價單，線上看條款、簽名、付款。'))

def typed(con, qid, text, by):
    """店長在 LINE 打金額：每行「品項 金額」（例：年度保養 8000），存成報價品項後回傳報價卡讓她再確認一次。"""
    import re, booking
    q = booking._q(con, qid=qid)
    items = []
    for line in (text or '').splitlines():
        m = re.match(r'^\s*(.+?)\s*[:：]?\s*(?:NT\$|\$)?\s*(\d[\d,]*)\s*元?\s*$', line)
        if m:
            items.append({'title': m.group(1).strip()[:60], 'amount': int(m.group(2).replace(',', '')), 'scope': []})
    if not items:
        raise ValueError('看不懂金額，請每行打「品項 金額」，例如：年度保養 8000')
    c.CTX.actor = by + '（LINE 報價）'
    booking.admin_save(con, {'id': qid, 'items': items, 'taxed': q['taxed'], 'date': q['date'], 'start': q['start'], 'people': q['people'], 'hours': q['hours'], 'scope': q['scope'] or '', 'note': q['note'] or ''}, by)
    return card(con, qid)

def notify(con, qid):
    import line_bot as lb, reviews, booking
    q = booking._q(con, qid=qid)
    if json.loads(q['form'] or '{}').get('source') == '同事開單':
        return 0
    m = card(con, qid)
    lb.to_managers(con, 'quote_review', m[0]['altText'], m)          # 送管理群 @店長
    return 1

def approve(con, qid, by):
    import booking
    q = booking._q(con, qid=qid)
    if q['status'] != 'draft' or q['stage'] not in ('drafting', 'review', 'site_visit'):
        return f"這張報價已經處理過了（{ {'sent': '已傳給客戶', 'signed': '已簽約', 'cancelled': '已取消'}.get(q['stage'], q['stage'])}）"
    if not q['date']:
        return '客戶沒選服務日期，請到中控台補日期再傳（或在 LINE 先問客戶）'
    items = booking._items(q)
    if not items:
        return '這張報價還沒有品項金額（系統沒辦法初估），請到中控台填好再傳'
    c.CTX.actor = by + '（LINE 確認報價）'
    out = booking.admin_send(con, {'id': qid, 'items': items, 'taxed': q['taxed'], 'date': q['date'], 'start': q['start'], 'people': q['people'], 'hours': q['hours'], 'scope': q['scope'] or ''}, by)
    if out.get('line_sent'):
        return '✅ 已確認並傳給客戶'
    import line_bot as lb
    return (f"✅ 已確認，但客戶沒綁 LINE、系統傳不過去。\n請把報價單連結傳給他：\n{lb.BASE_URL}/q/{q['token']}\n"
            "傳完到中控台這張報價按「已傳給客戶」。要他綁 LINE：中控台「客戶 › 未綁定 LINE」按「複製綁定訊息」。")

def manual_sent(con, qid, by):
    """報價已經用 PDF、電話、紙本給客戶了：記成「已傳給客戶」，之後過期追蹤照常；不再發 LINE。"""
    import booking
    q = booking._q(con, qid=qid)
    if q['status'] != 'draft' or q['stage'] not in ('drafting', 'review', 'site_visit'):
        return '這張報價已經處理過了'
    con.execute("UPDATE quotes SET stage='sent',sent_at=?,link_sent=?,reviewed_by=? WHERE id=?", (c.now(), c.now(), by, qid))
    c.audit(con, 'quote_sent_manual', 'quotes', qid, after={'by': by})
    return f'✅ 已記成「已用其他方式給客戶」（{qid}），不會再提醒；客戶要線上簽收可以用報價單連結。'

MANUAL_WHY = ['客戶沒有或不用 LINE', '客戶要 PDF／紙本／Email', '已經電話或當面講好', '系統算不出金額', '報價單少了東西']

def reason_ask(qid):
    """按了「已用其他方式給客戶了」→ 接著問為什麼（負責人 問原因 → 檢查系統有沒有針對這個原因優化 → 下次全自動）。"""
    t = f'其他方式原因 {qid}'
    items = [{'type': 'action', 'action': {'type': 'message', 'label': o[:20], 'text': f'【{t}】{o}'}} for o in MANUAL_WHY]
    items.append({'type': 'action', 'action': {'type': 'postback', 'label': '✏️ 我打字回答', 'data': f'a=ask_type&t={t}', 'displayText': f'【{t}】我打字回答'}})
    import line_bot as lb            # 檢查：改卡片按鈕（快速回覆被後面的訊息蓋掉就按不到）；快速回覆照樣附上
    bs = [{'type': 'button', 'action': {'type': 'message', 'label': o[:20], 'text': f'【{t}】{o}'}, 'style': 'secondary', 'color': '#EFEDE8', 'height': 'sm'} for o in MANUAL_WHY]
    m = lb.flex(f'【{t}】為什麼用其他方式給客戶？', lb._bubble(lb._header('請說原因 ✏️', '為什麼這張要用其他方式給客戶？', '#C47F00'),
                [lb._t(f'【{t}】點一個（或按「✏️ 我打字回答」）——系統會檢查能不能改好，下次全自動 🙏', wrap=True, size='sm')],
                bs + [lb._btn('✏️ 我打字回答', data=f'a=ask_type&t={t}')]))[0]
    m['quickReply'] = {'items': items}
    return m

def next_reason_ask(con, skip=''):
    """還沒說原因的「已用其他方式給客戶」報價（14 天內）→ 下一題（LINE 快速回覆只顯示在最後一則，所以一題答完再問下一題）。"""
    import datetime as _dt
    since = (_dt.datetime.now(c.TZ) - _dt.timedelta(days=14)).strftime('%Y-%m-%d')
    for (qid,) in con.execute("SELECT DISTINCT entity_id FROM audit WHERE action='quote_sent_manual' AND at>=? ORDER BY id", (since,)).fetchall():
        if qid == skip:
            continue
        r = con.execute('SELECT form FROM quotes WHERE id=?', (qid,)).fetchone()
        if r and not json.loads(r[0] or '{}').get('manual_reason'):
            return reason_ask(qid)
    return None

def manual_reason(con, qid, ans, who):
    """記下原因（報價單 form.manual_reason）＋開一筆功能回報（晚上檢查系統能不能讓下次全自動）。回傳一句說明。"""
    import booking, brands, sysreport
    q = booking._q(con, qid=qid)
    f = json.loads(q['form'] or '{}'); f['manual_reason'] = {'reason': ans[:200], 'by': who, 'at': c.now()}
    con.execute('UPDATE quotes SET form=? WHERE id=?', (json.dumps(f, ensure_ascii=False), qid))
    key = next((i for i, o in enumerate(MANUAL_WHY) if o == ans), 'typed')
    rid = sysreport.report(con, f'報價用其他方式給客戶：{ans[:60]}', f"報價 {qid}（{brands.name(q['account'] or 'demoshop')}・{f.get('company') or f.get('name') or ''}・NT$ {q['total'] // 100:,}）"
                           f"由 {who} 按「已用其他方式給客戶了」，原因：{ans}。\n請檢查：①系統有沒有針對這個原因優化 ②下次能不能全自動（不用再走其他方式）。",
                           who=who, source='staff', kind='feature', ref=qid, fp=f'quote_manual:{key}' if key != 'typed' else None)
    if ans == '客戶要 PDF／紙本／Email':          # 系統直接給 PDF 連結（貼到 Email、或下載列印）
        import line_bot as lb
        return f"📄 報價單 PDF：{lb.BASE_URL}/q/{q['token']}.pdf\n直接貼到 Email 給客戶，或打開下載列印（客戶的線上報價也有「下載 PDF」）。（也記到功能回報 {rid}）"
    return f'已記到功能回報（{rid}）：每天晚上檢查系統能不能讓這種情況下次全自動，處理好會通知'

def cancel(con, qid, by):
    import booking
    c.CTX.actor = by + '（LINE）'
    r = booking.admin_stage(con, {'id': qid, 'stage': 'cancelled'})
    return f'✅ 已取消報價 {qid}' + ('，已用 LINE 通知客戶' if r.get('notified') else '；客戶沒綁 LINE，請打電話告知' if r.get('knew') else '')

def site_visit(con, qid, by):
    import booking
    c.CTX.actor = by + '（LINE）'
    booking.admin_stage(con, {'id': qid, 'stage': 'site_visit'})
    return '✅ 已改成現場估價，請跟客戶約時間（估價費依規則提醒）'
