"""估價練習（估價對照表「給店長分 30 天答題，都對的話你就知道估價的部分是否合適」）。

- 每天 10:30 起，用點選卡問店長 2 題（一題一個情境：品牌、空間、規模、浴室、狀況），問「妳會排幾人、幾小時？」。
- 前一天還沒答完就不出新題，只再提醒一次；答滿 30 天＝60 題結束。
- 每題同時記下兩個系統答案：proposed＝估價對照表的規模表＋加時情況（還沒正式用）；live＝現在線上初估實際用的算法。
- 答案先不公布（避免被系統答案帶著走）；每 7 天、第 30 天給負責人一份對照：一致率、哪個加時情況系統算太少／太多，
  結果寫進 文件/估價練習結果.md。人時差 0.5 以內算一致。
"""
import datetime as dt
import json
import math
import random
import re
import core as c
import integrations as ix
import pricing

DAYS, PER_DAY = 30, 2
KEY = 'estquiz'
PREFIX = '估價練習 '
EXTRAS = ('天花板', '玻璃', '騎樓', '牆面')

def bank():
    """60 題情境（固定亂數，每次產生的題目一樣）。住家日常 40 題、店面／辦公室 12 題、店家 8 題；示範服務店、生活線各半。"""
    rnd = random.Random(20261013)
    out = []
    for i in range(DAYS * PER_DAY):
        kind = '住家' if i % 3 != 2 else ('店面／辦公室' if i % 15 != 14 else '店家')
        if kind == '住家' and i % 10 == 9:
            kind = '店家'
        s = {'id': f'D{i // PER_DAY + 1:02d}-{i % PER_DAY + 1}', 'brand': 'demoshop' if i % 2 else 'homeline', 'space': kind,
             'size': rnd.choice([8, 12, 15, 17, 20, 24, 28, 32, 35, 38, 45, 55]) if kind != '店家' else rnd.choice([20, 30, 40, 60]),
             'baths': rnd.choice([1, 1, 2, 2, 3]), 'rooms': rnd.choice([2, 3, 4, 6, 8]) if kind == '店家' else 0,
             'dirty': rnd.random() < 0.3, 'pets': rnd.random() < 0.25, 'stairs': rnd.random() < 0.2,
             'extras': rnd.sample(EXTRAS, rnd.choice([0, 0, 0, 1, 1, 2]))}
        out.append(s)
    return out

def _round(ph, people):
    return math.ceil(ph / people * 2) / 2

def proposed(cfg, s):
    """估價對照表的建議（人, 時）：規模表當基準＋加時情況（久未整理 +1、多一間浴室 +0.5、寵物 +0.5、沒電梯 +0.5、天花板／玻璃／騎樓／牆面 各 +0.5）。"""
    if s['space'] == '店家':
        base = max(3.0, round(s['rooms'] * 0.6 + s['baths'] * 0.3, 1)); people0 = 1 if s['rooms'] <= 4 else 2; ph = base * people0
    elif s['space'] == '店面／辦公室':
        ph = max(3.0, math.ceil((s['size'] / 12 + s['baths'] * 0.3) * 2) / 2); people0 = 1
    else:
        h, people0 = pricing.suggest_hours(cfg, s['size']) or (4.0, 1); ph = h * people0
    ph += (1 if s['dirty'] else 0) + 0.5 * max(0, s['baths'] - 1) * (s['space'] != '店家') + 0.5 * s['pets'] + 0.5 * s['stairs'] + 0.5 * len(s['extras'])
    people = max(people0, 1 if ph <= 6 else 2)
    return people, _round(ph, people)

def live(con, s):
    """現在線上初估用的（人, 時）：生活線＝規模表（不加時）；示範服務店＝線上估價算法（住家看規模、浴室；店面；店家看房間數）。"""
    if s['brand'] == 'homeline' and s['space'] == '住家':
        r = pricing.suggest_hours(pricing.config(con, 'homeline'), s['size']) or (4.0, 1)
        return r[1], r[0]
    space = {'住家': 'home', '店面／辦公室': 'shop', '店家': 'bnb'}[s['space']]
    f = {'account': 'demoshop', 'space': space, 'condition': 'deep' if s['dirty'] and space == 'home' else 'regular', 'size': s['size'], 'baths': s['baths'],
         'rooms': s['rooms'], 'pets': s['pets'], 'floors': 4 if s['stairs'] else 1, 'lift': '無電梯' if s['stairs'] else ''}
    try:
        e = pricing.estimate(con, f)
    except Exception:
        return None
    if not e.get('hours'):
        return None
    return int(e.get('people') or 1), float(e['hours'])

def label(people, hours):
    return f'{people} 人 × {hours:g} 小時'

def options(s, prop):
    """6 個選項：系統答案附近的人時，起點隨題目變（系統答案不會固定在中間）。"""
    ph = prop[0] * prop[1]
    rnd = random.Random(s['id'])
    start = ph - rnd.choice([0.5, 1.0, 1.5, 2.0])
    out = []
    for k in range(6):
        v = max(2.0, start + 0.5 * k)
        p = 1 if v <= 6 else 2
        out.append(label(p, _round(v, p)))
    return list(dict.fromkeys(out))

def question(s):
    brand = '示範服務店' if s['brand'] == 'demoshop' else '生活小幫手'
    lines = [f"{brand}・{s['space']}日常服務", f"・規模：約 {s['size']} 單位"]
    if s['space'] == '店家':
        lines.append(f"・房間：{s['rooms']} 間（換床單、浴室、垃圾）")
    lines.append(f"・浴室：{s['baths']} 間")
    st = ['很久沒徹底整理，油垢水垢比較重' if s['dirty'] else '平常有在整理']
    if s['pets']:
        st.append('有養寵物（毛比較多）')
    lines.append('・狀況：' + '、'.join(st))
    if s['stairs']:
        lines.append('・4 樓公寓，沒有電梯（要爬樓梯搬工具）')
    if s['extras']:
        lines.append('・另外要做：' + '、'.join(s['extras']))
    return '\n'.join(lines) + '\n\n妳會排幾人、幾小時？（照妳平常實際會排的答，不用想系統怎麼算；選不到就按「我打字回答」打，例如：2 人 3.5 小時）'

def parse(ans):
    p = re.search(r'(\d+)\s*(?:人|位)', ans); h = re.search(r'(\d+(?:\.\d+)?)\s*(?:小時|h|H|個小時)', ans)
    if not h:
        return None
    return int(p.group(1)) if p else 1, float(h.group(1))

def state(con):
    try:
        return json.loads(ix.setting(con, KEY) or '{}') or {}
    except ValueError:
        return {}

def _save(con, st):
    ix.set_setting(con, KEY, json.dumps(st, ensure_ascii=False))

def tick(con):
    """每分鐘：10:30–21:00，每天出一次題（前一天沒答完 → 不出新題，只提醒一次）。"""
    import line_bot as lb
    now = dt.datetime.now(c.TZ); today = now.date().isoformat()
    if not ('10:30' <= now.strftime('%H:%M') < '21:00') or ix.setting(con, 'estquiz_off'):
        return 0
    st = state(con)
    if st.get('day') == today or st.get('done'):
        return 0
    st['day'] = today
    sent, ans = st.setdefault('sent', {}), st.setdefault('answers', {})
    todo = [q for q in sent if q not in ans]
    cfg = pricing.config(con, 'homeline')
    if todo:                                       # 還有沒答的：再傳一次（最舊的先），不出新題
        by = {s['id']: s for s in bank()}
        for qid in sorted(todo)[:PER_DAY]:
            s = by[qid]
            lb.ask_staff(con, PREFIX + qid, f"（提醒）第 {int(qid[1:3])}/{DAYS} 天\n" + question(s), sent[qid]['options'], line=False)      # 估價練習只在中控台
        _save(con, st)
        return len(todo)
    nxt = [s for s in bank() if s['id'] not in sent][:PER_DAY]
    if not nxt:
        st['done'] = today; _save(con, st)
        return 0
    for s in nxt:
        prop = proposed(cfg, s); lv = live(con, s); opts = options(s, prop)
        sent[s['id']] = {'at': c.now(), 'proposed': prop, 'live': lv, 'options': opts}
        lb.ask_staff(con, PREFIX + s['id'], f"📐 估價練習 第 {int(s['id'][1:3])}/{DAYS} 天・第 {s['id'][-1]} 題\n" + question(s), opts, line=False)      # 估價練習只在中控台答
    _save(con, st)
    return len(nxt)

def record(con, topic, ans, who):
    """店長回答【估價練習 Dxx-n】→ 記錄（答案先不公布）；每 7 天、第 30 天給負責人對照報告。回傳給她看的一句話。"""
    import line_bot as lb
    qid = topic[len(PREFIX):].strip()
    st = state(con); sent = st.setdefault('sent', {})
    if qid not in sent:
        return ''
    pa = parse(ans)
    if not pa:
        return '看不懂幾人幾小時，請再按一次「我打字回答」，例如：1 人 4 小時'
    st.setdefault('answers', {})[qid] = {'people': pa[0], 'hours': pa[1], 'by': who, 'at': c.now()}
    _save(con, st)
    write_report(con)
    day = int(qid[1:3]); left = [q for q in sent if q not in st['answers']]
    msg = f"記下來了（第 {day}/{DAYS} 天）。答案先不公布，30 天後一起看系統準不準 🙏"
    if not left and day % 7 == 0 or not left and day == DAYS:
        r = report(con)
        lb.queue(con, lb.WORK_ACCOUNT, lb.OWNER_UID, 'admin', f'估價練習 {day}/{DAYS} 天對照', lb._txt(r['text']))
    return msg

def report(con):
    st = state(con); sent, ans = st.get('sent', {}), st.get('answers', {})
    by = {s['id']: s for s in bank()}
    rows, hit_p, hit_l, n_l = [], 0, 0, 0
    factor = {k: [] for k in ('久未整理', '多一間浴室', '寵物', '沒電梯', '天花板／玻璃／騎樓／牆面')}
    for qid in sorted(ans):
        a, q, s = ans[qid], sent[qid], by[qid]
        aph = a['people'] * a['hours']; pph = q['proposed'][0] * q['proposed'][1]
        lph = q['live'][0] * q['live'][1] if q.get('live') else None
        ok_p = abs(aph - pph) <= 0.5; hit_p += ok_p
        if lph is not None:
            n_l += 1; hit_l += abs(aph - lph) <= 0.5
        d = aph - pph
        for k, on in (('久未整理', s['dirty']), ('多一間浴室', s['baths'] > 1 and s['space'] != '店家'), ('寵物', s['pets']), ('沒電梯', s['stairs']),
                      ('天花板／玻璃／騎樓／牆面', bool(s['extras']))):
            if on:
                factor[k].append(d)
        rows.append(f"| {qid} | {s['brand'] == 'demoshop' and '示範服務店' or '生活線'}・{s['space']} {s['size']} 單位 | {label(a['people'], a['hours'])} | "
                    f"{label(*q['proposed'])} | {label(*q['live']) if q.get('live') else '—'} | {'✅' if ok_p else f'{d:+g} 人時'} |")
    n = len(ans)
    tips = [f"- {k}：系統（對照表）比店長平均{'少' if sum(v) / len(v) > 0 else '多'} {abs(sum(v) / len(v)):.1f} 人時（{len(v)} 題）"
            for k, v in factor.items() if len(v) >= 3 and abs(sum(v) / len(v)) >= 0.5]
    text = (f"📐 估價練習對照（已答 {n}/{DAYS * PER_DAY} 題）\n"
            f"・估價對照表（規模表＋加時）跟店長一致：{hit_p}/{n}（{hit_p * 100 // max(n, 1)}%）\n"
            f"・現在線上初估跟店長一致：{hit_l}/{n_l}（{hit_l * 100 // max(n_l, 1)}%）\n"
            + ('・要調整的加時情況：\n' + '\n'.join(tips) if tips else '・加時情況目前沒有明顯偏差') + '\n（人時差 0.5 以內算一致；完整表在中控台文件「估價練習結果」）')
    return {'text': text, 'rows': rows, 'n': n, 'hit_proposed': hit_p, 'hit_live': hit_l, 'n_live': n_l, 'tips': tips}

def write_report(con):
    r = report(con)
    body = ['# 估價練習結果（店長 30 天答題 × 系統估價）', '', '自動產生，每答一題更新。人時＝人數 × 時數；差 0.5 人時以內算一致。', '',
            r['text'].replace('\n', '\n\n'), '', '| 題號 | 情境 | 店長 | 對照表建議 | 現在線上初估 | 對照表 |', '|---|---|---|---|---|---|'] + r['rows']
    if c.DATA != c.ROOT / 'data':          # 測試資料不覆蓋正式紀錄
        return r
    try:
        (c.ROOT / '文件' / '估價練習結果.md').write_text('\n'.join(body) + '\n', encoding='utf-8')
    except OSError:
        pass
    return r
