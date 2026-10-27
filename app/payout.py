"""夥伴薪資匯款：結算單登記已匯款 → 自動通知夥伴（金額、手續費、匯到哪個帳戶末四碼）。"""
import core as c

def notify_paid(con, sid):
    import line_bot as lb
    s = con.execute('SELECT s.*,COALESCE((SELECT SUM(amount) FROM settlement_lines WHERE settlement_id=s.id),0) total FROM settlements s WHERE s.id=?', (sid,)).fetchone()
    if not s:
        return False
    w = dict(con.execute('SELECT * FROM workers WHERE id=?', (s['worker_id'],)).fetchone() or {})
    fee = s['fee'] or 0
    net = (s['total'] or 0) - fee
    per = ('週 ' + s['period'][6:] + ' 起' if str(s['period']).startswith('W') else s['period'] + ' 月')
    acct = (w.get('bank_account') or '')
    rows = [lb._row('期間', per), lb._row('應付', f"NT$ {(s['total'] or 0) // 100:,}")] + ([lb._row('手續費', f'−NT$ {fee // 100:,}（非示範銀行跨行）')] if fee else []) + \
           [lb._row('實匯', f'NT$ {net // 100:,}'), lb._row('帳戶', f"{w.get('bank_name') or ''} 末四碼 {acct[-4:]}" if acct else '—')]
    m = lb.flex(f'💰 薪資已匯款 NT$ {net // 100:,}', lb._bubble(lb._header('💰 薪資已匯款', per, '#2E7D32'), rows, [], '金額有疑問直接在 LINE 問示範服務店；明細在主控板「我的收入」。'))
    lb.to_worker(con, s['worker_id'], 'payout', m[0]['altText'], m)
    return True


def mark_paid(con, sid, reference, by, silent=False):
    """登記結算單已匯款。silent＝之前已經匯過、只是補登 → 不傳 LINE 給夥伴。"""
    s = c.get(con, 'settlements', sid)
    if s['status'] != 'draft' or not (reference or '').strip():
        raise ValueError('請填付款憑證／備註，已結清者不能再次登記')
    con.execute("UPDATE settlements SET status='paid',paid_at=?,reference=? WHERE id=?", (c.now(), reference, sid))
    c.audit(con, 'mark_paid_silent' if silent else 'mark_paid', 'settlements', sid, s, {'reference': reference, 'by': by})
    if not silent:
        notify_paid(con, sid)          # 匯款會自動通知夥伴
    return {'saved': True, 'money_transferred': False, 'notified': not silent}
