"""服務等級＋髒污程度（業主要的服務細節，比當初報價的級別高 → 認知差異）。

- 示範服務店一般到府服務三級（店長定的文字）：Level 1 基礎服務／Level 2 深度服務／Level 3 精緻加強服務。
- 報價單由管理層標「這份報價是 Level 幾」（沒標 → 依服務類型推：定期／單次＝1、年度保養／搬家＝2、進階專案／入住前／交屋＝3）。
- 現場估價後同事代開的報價也一樣：業主在線上報價單簽收前必選「服務等級」「目前髒污程度」「希望頻率」「希望每次時長」。
- 業主選的級別比報價高，或髒污「重度」卻報 Level 1 → 不能簽，改按「請主管依這個級別重新報價」（管理群收到）。
- 簽收後：級別、髒污寫進工單服務範圍（範圍審核後夥伴看得到）＋派工卡、提醒卡一行「級別」。
- 三張範例圖：管理群「我要傳」素材（clean_level_1..3），收到就顯示在報價單上。
"""
import re

LEVELS = {
    1: {'name': '基礎服務', 'type': '日常維護型', 'fit': '適合定期整理、環境整體乾淨的家庭。',
        'content': '地板、桌面、家具表面、廚房檯面、浴室基本服務、垃圾整理。', 'std': '肉眼可見範圍乾淨、整潔。'},
    2: {'name': '深度服務', 'type': '年度保養型', 'fit': '適合長時間未整理、有明顯積垢的家庭。',
        'content': '包含基礎服務，再加強浴室水垢、廚房油污、窗溝、家具邊角、櫃體外部與其他容易忽略的死角。', 'std': '明顯污垢與累積灰塵深入處理。'},
    3: {'name': '精緻加強服務', 'type': '細節精查型', 'fit': '適合入住前、交屋前，或對服務細節要求較高的客戶。',
        'content': '包含深度服務，再加強高處積塵、櫃內死角、抽屜滑軌、窗框細縫、玻璃水痕及局部殘膠等細節。', 'std': '細部檢查、逐區複檢，降低服務遺漏。'},
}
DIRT = {'light': ('輕度', '日常使用痕跡', '平時有整理，只有少量灰塵及輕微污漬。'),
        'mid': ('中度', '明顯灰塵與污垢', '一段時間未完整服務，有明顯積塵、油污或水垢。'),
        'heavy': ('重度', '長期累積頑固髒污', '長期未服務，有厚重油垢、水垢、霉斑或大量積塵。')}
FREQ = ('單次', '每週一次', '每兩週一次', '每月一次', '還不確定')
MAT_KEY = 'clean_level_{}'

def applies(f):
    """時數制（陪玩陪讀）、垃圾代丟、定期駐點服務不用問級別。"""
    st = str((f or {}).get('service_type') or '')
    return not re.search(r'陪玩|陪讀|代丟|垃圾|店家|房務|管家|預約服務|基本服務|標準服務|進階服務|諮詢|加時', st)

def default_level(service_type):
    st = str(service_type or '')
    if re.search(r'施工|入住|交屋|精緻', st):
        return 3
    if re.search(r'年度保養|細清|搬家|深度|年終', st):
        return 2
    return 1

def quoted(f):
    try:
        n = int((f or {}).get('level') or 0)
    except (TypeError, ValueError):
        n = 0
    return n if n in LEVELS else default_level((f or {}).get('service_type'))

def label(n):
    return f"Level {n}｜{LEVELS[n]['name']}" if n in LEVELS else ''

def need_ask(f, signed=False):
    return not signed and applies(f) and not (f or {}).get('dirt')

def images(con, key='clean'):
    """範例圖（管理群「我要傳」收到的第一張）：{等級: 網址}。key：clean／trash／tidy。"""
    import materials
    out = {}
    for n in LEVELS:
        k = MAT_KEY.format(n) if key == 'clean' else f'{key}_level_{n}'; d = materials.c.DATA / 'materials' / k
        names = sorted(p.name for p in d.glob('*.jpg')) if d.is_dir() else []
        if names:
            out[n] = materials.image_url(con, k, names[0])
    return out

def view(con, f, signed=False):
    """給報價單頁面：級別說明＋這份報價的級別＋要不要問。"""
    if not applies(f):
        return None
    return {'ask': need_ask(f, signed), 'quoted': quoted(f), 'chosen': (f or {}).get('level_cust'), 'dirt_chosen': (f or {}).get('dirt'),
            'levels': [{'n': n, **v} for n, v in LEVELS.items()], 'dirt': [[k, *v] for k, v in DIRT.items()], 'freq': list(FREQ), 'img': images(con)}

def check(f, p):
    """簽收前檢查業主的選擇；回傳要寫進 form 的欄位。不合 → ValueError（訊息給業主看）。"""
    try:
        lv = int(p.get('level') or 0)
    except (TypeError, ValueError):
        lv = 0
    if lv not in LEVELS:
        raise ValueError('請先選「服務等級」（Level 1／2／3）')
    if p.get('dirt') not in DIRT:
        raise ValueError('請先選「目前居家的髒污程度」')
    if p.get('want_freq') not in FREQ:
        raise ValueError('請先選「希望的服務頻率」')
    q = quoted(f)
    if lv > q:
        raise ValueError(f'LEVEL_UP:這份報價是 {label(q)}，您選的是 {label(lv)}。請按「請主管依這個級別重新報價」，我們會重新估人力和時間再傳給您 🙏')
    if p['dirt'] == 'heavy' and q == 1:
        raise ValueError(f'LEVEL_UP:這份報價是 {label(q)}（日常維護），但您選了「重度」髒污，需要較多人力時間。請按「請主管依這個級別重新報價」🙏')
    hrs = re.sub(r'[^0-9.]', '', str(p.get('want_hours') or ''))[:4]
    return {'level': q, 'level_cust': lv, 'dirt': p['dirt'], 'want_freq': p['want_freq'], 'want_hours': hrs}

def worker_line(f):
    """寫進工單範圍／夥伴卡的一句話（服務等級＋清運／整理等級）。"""
    out = []
    if applies(f) and ((f or {}).get('dirt') or (f or {}).get('level')):      # 業主選過，或管理層標過（代為確認的報價也帶）
        n = quoted(f)
        out.append(f"服務等級：{label(n)}（{LEVELS[n]['std']}）" + (f"；髒污：{DIRT[f['dirt']][0]}" if f.get('dirt') in DIRT else ''))
    for k, s in SCALES.items():
        n = int((f or {}).get(f'{k}_level') or (f or {}).get(f'{k}_cust') or 0)
        if n in s['levels']:
            out.append(f"{s['title']}：{scale_label(k, n)}（{'、'.join(s['levels'][n][1][:2])}）")
    return '\n'.join(out)

def requote_reason(p):
    try:
        lv = int(p.get('level') or 0)
    except (TypeError, ValueError):
        lv = 0
    bits = [f'業主要 {label(lv)}' if lv in LEVELS else '',
            *[f"{s['title']} {scale_label(k, int(p.get(f'{k}_level')))}" for k, s in SCALES.items() if str(p.get(f'{k}_level') or '') in ('1', '2', '3')], f"髒污 {DIRT[p['dirt']][0]}" if p.get('dirt') in DIRT else '',
            f"希望 {p['want_freq']}" if p.get('want_freq') in FREQ else '', f"每次約 {p['want_hours']} 小時" if re.fullmatch(r'[0-9.]{1,4}', str(p.get('want_hours') or '')) else '']
    return '、'.join(b for b in bits if b)

def ask_images(con):
    """還沒有範例圖 → 請管理群用「我要傳」準備（只請一次）。"""
    import materials
    st = materials.load(con)
    todo = [n for n in LEVELS if MAT_KEY.format(n) not in st and n not in images(con)]
    if not todo:
        return 0
    return materials.request(con, [(MAT_KEY.format(n), f'服務等級範例 Level {n}', f"{label(n)}：{LEVELS[n]['std']}（放在報價單讓業主選級別；服務完成的樣子最好）", 1) for n in todo],
                             '報價單「服務等級」範例圖（服務落差改善）')

# 清運等級、整理等級也放報價單（跟服務等級一樣：管理層標報價是幾級，業主簽收前選；選得比報價高 → 請主管重新報價）
SCALES = {
    'trash': {'title': '垃圾代丟等級', 'head': '一般家用垃圾代丟｜三級分類', 'levels': {      # 不寫「清運」，只說代丟
        1: ('少量代丟', ['約 1～5 袋一般家用垃圾', '少量紙箱、回收物', '少量日常生活雜物']),
        2: ('中量代丟', ['約 6～15 袋一般家用垃圾', '較多紙箱、回收物', '衣物、玩具等生活雜物']),
        3: ('大量代丟', ['超過 15 袋一般家用垃圾', '大量紙箱、回收物', '長期累積的生活雜物', '需要較多人力或車輛協助'])}},
    'tidy': {'title': '整理等級', 'head': '居家整理｜三級分類', 'levels': {
        1: ('輕度整理｜基礎物品歸位', ['環境大致整齊，僅有少量物品散落。', '協助物品歸位、衣物摺疊、桌面整理。', '不需要重新規劃收納空間。']),
        2: ('中度整理｜分類與收納', ['物品較多，部分空間凌亂、收納不足。', '協助衣物、雜物分類及收納。', '整理衣櫃、抽屜、儲藏空間。', '需要簡單調整物品擺放位置。']),
        3: ('重度整理｜深度整理與空間規劃', ['物品大量堆積，影響日常生活空間。', '需要大範圍物品分類及斷捨離協助。', '重新規劃收納方式及空間配置。', '可能需要多人或較長時間整理。'])}},
}
_AUTO = {'trash': r'垃圾清收|垃圾清運|垃圾清運', 'tidy': r'收納整理|整理收納|居家整理'}      # 每日檢查：只看服務類型和品項標題（「櫥櫃與收納空間」這種不算）

def _texts(f, items):
    return ' '.join([str((f or {}).get('service_type') or '')] + [str(i.get('title') or '') for i in (items or [])])

def scale_level(f, k, items=()):
    """這份報價的清運／整理等級：管理層標的（0＝不適用）；沒標 → 報價有這類項目就 1，沒有就 0。"""
    v = (f or {}).get(f'{k}_level')
    if v is not None and v != '':
        try:
            v = int(v)
        except (TypeError, ValueError):
            v = 0
        return v if v in (0, 1, 2, 3) else 0
    t = _texts(f, items)
    return 1 if re.search(_AUTO[k], t) and not re.search(r'代丟', t) else 0

def scale_label(k, n):
    return f"{'①②③'[n - 1]} {SCALES[k]['levels'][n][0]}" if n in SCALES[k]['levels'] else ''

def need_any(f, items=(), signed=False):
    return need_ask(f, signed) or any(not signed and scale_level(f, k, items) and not (f or {}).get(f'{k}_cust') for k in SCALES)

def view_all(con, f, signed=False, items=()):
    v = view(con, f, signed) or {'ask': False, 'none': True}
    sc = [{'key': k, 'title': s['title'], 'head': s['head'], 'quoted': scale_level(f, k, items), 'chosen': (f or {}).get(f'{k}_cust'),
           'ask': not signed and not (f or {}).get(f'{k}_cust'),
           'img': images(con, k), 'levels': [{'n': n, 'name': nm, 'items': its} for n, (nm, its) in s['levels'].items()]} for k, s in SCALES.items() if scale_level(f, k, items)]
    if v.get('none') and not sc:
        return None
    return {**v, 'scales': sc}

def check_all(f, p, items=()):
    out = check(f, p) if need_ask(f) else {}
    for k, s in SCALES.items():
        q = scale_level(f, k, items)
        if not q or (f or {}).get(f'{k}_cust'):
            continue
        try:
            lv = int(p.get(f'{k}_level') or 0)
        except (TypeError, ValueError):
            lv = 0
        if lv not in s['levels']:
            raise ValueError(f"請先選「{s['title']}」")
        if lv > q:
            raise ValueError(f"LEVEL_UP:這份報價的{s['title']}是 {scale_label(k, q)}，您選的是 {scale_label(k, lv)}。請按「請主管依這個級別重新報價」，我們會重新估人力和時間再傳給您 🙏")
        out[f'{k}_level'] = q; out[f'{k}_cust'] = lv
    return out

def ask_scale_images(con):
    """清運、整理等級也要附範例圖 → 請管理群用「我要傳」準備（每組一張卡、只請一次）。"""
    import materials
    st, n_ = materials.load(con), 0
    for k, s in SCALES.items():
        have = images(con, k)
        todo = [n for n in s['levels'] if f'{k}_level_{n}' not in st and n not in have]
        if todo:
            n_ += materials.request(con, [(f'{k}_level_{n}', f"{s['title']} {'①②③'[n - 1]}{s['levels'][n][0].split('｜')[0]}", f"報價單「{s['title']}」範例：{'、'.join(s['levels'][n][1][:2])}（請拍符合這一級的現場，避開門牌、人臉和個資）", 1) for n in todo],
                                    f"報價單「{s['title']}」範例圖")
    return n_
