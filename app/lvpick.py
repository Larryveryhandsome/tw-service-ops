"""報價單「服務等級」範例圖：管理層傳來的候選照片 → 派單管理群一張卡，店長每張點 Level 1／2／3／不用 → 直接變成那一級的範例（報價單馬上顯示）。
客服：「這些照片請店長判斷給線上報價單做範例使用，等級由店長判斷」。
候選照片存 data/materials/lvex_cand/（不公開，LINE 用隨機代碼網址）；點了就複製到 clean_level_<N>/，cleanlevel.images 讀第一張。"""
import datetime as dt
import shutil
import core as c

CAND = 'lvex_cand'

def offer(con, paths, why=''):
    """把候選照片送到派單管理群（一張多頁卡，每張四顆按鈕）。回傳送了幾張。"""
    import line_bot as lb, materials, recruit
    g = lb.admin_group(con)
    if not g:
        return 0
    d = c.DATA / 'materials' / CAND; d.mkdir(parents=True, exist_ok=True)
    bubbles = []
    for i, p in enumerate(paths[:10], 1):
        name = f"{dt.datetime.now(c.TZ).strftime('%Y%m%d_%H%M%S_%f')}_{i:02d}.jpg"
        (d / name).write_bytes(recruit.shrink(__import__('pathlib').Path(p).read_bytes()))
        url = materials.image_url(con, CAND, name)
        bubbles.append({'type': 'bubble', 'size': 'kilo',
                        'hero': {'type': 'image', 'url': url, 'size': 'full', 'aspectRatio': '1:1', 'aspectMode': 'cover', 'action': {'type': 'uri', 'uri': url}},
                        'body': {'type': 'box', 'layout': 'vertical', 'spacing': 'sm', 'contents': [lb._t(f'第 {i} 張：適合當哪一級的範例？', weight='bold', wrap=True)] +
                                 [lb._btn(f'Level {n}', data=f'a=lvex&f={name}&l={n}', primary=(n == 2)) for n in (1, 2, 3)] + [lb._btn('不用這張', data=f'a=lvex&f={name}&l=0')]}})
    msg = {'type': 'flex', 'altText': '📸 報價單級別範例：請店長選等級', 'contents': {'type': 'carousel', 'contents': bubbles}}
    lb.queue(con, lb.WORK_ACCOUNT, g, 'lvex', msg['altText'],
             [lb.ping(con, '📸 ' + (why or '這幾張照片可以當線上報價單的「服務等級」範例') + '：請店長每張點適合的等級（Level 1／2／3），不適合就點「不用」。點了報價單馬上會顯示。'), msg])
    return len(bubbles)

def pick(con, name, level, who=''):
    """管理層點了等級：複製成那一級的範例（那一級已經有範例就排在後面備用）。回傳一句回覆。"""
    import re, materials
    if not re.fullmatch(r'[0-9_]{10,40}\.jpg', name or ''):
        return '找不到這張照片。'
    src = c.DATA / 'materials' / CAND / name
    if not src.is_file():
        return '這張已經處理過了 👍'
    if level not in (1, 2, 3):
        src.unlink(); c.audit(con, 'lvex_skip', 'settings', name, after={'by': who})
        return '好的，這張不用 👍'
    k = f'clean_level_{level}'
    d = c.DATA / 'materials' / k; d.mkdir(parents=True, exist_ok=True)
    first = not any(d.glob('*.jpg'))
    shutil.move(str(src), str(d / name))
    st = materials.load(con)
    if k in st:
        st[k]['got'] = st[k].get('got', 0) + 1
        st[k]['done_at'] = st[k].get('done_at') or c.now()
        materials._save(con, st)
    c.audit(con, 'lvex_pick', 'settings', k, after={'file': name, 'by': who})
    return f'已設成 Level {level} 的範例 ✅' + ('（報價單已經會顯示）' if first else '（Level %d 已經有範例，這張先備用）' % level)
