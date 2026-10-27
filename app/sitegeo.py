"""案場精準定位（夥伴接單後就看得到門牌＋精準定位；接單前只到路／巷，不含號、樓）。

- 用 geo.py（內政部國土測繪中心 NLSC 門牌定位，免金鑰）把案場地址換成座標，存在 sites.lat／lng／geo_level（門牌／路段）／geo_addr（查的是哪個地址）。
- 背景每分鐘最多查 3 個：14 天內有工作、還沒查過或地址改過的案場；查不到的隔一天再試。打 API 在鎖外，寫結果才進鎖。
- 導航連結順序：管理層填的「Google 地圖連結／正確入口」→ 國土測繪精準座標 → 用地址搜尋。
"""
import contextlib
import datetime as dt
import os
import threading
import urllib.parse
import core as c

LOCK = None                    # server.py 設成全系統那把鎖
_busy = threading.Event()

def precise(s):
    """座標是不是照現在這個地址查的。"""
    s = s or {}
    return bool(s.get('lat') and s.get('lng') and (s.get('geo_addr') or '') == (s.get('address') or ''))

def nav_url(s):
    s = s or {}
    if (s.get('navigation') or '').startswith('http'):
        return s['navigation']
    if precise(s):
        return f"https://www.google.com/maps/dir/?api=1&destination={s['lat']},{s['lng']}"          # 直接開路線導航
    return 'https://www.google.com/maps/dir/?api=1&destination=' + urllib.parse.quote(s.get('address') or s.get('area') or '臺北')

def level_text(s):
    if ((s or {}).get('navigation') or '').startswith('http'):
        return '管理層標的正確入口'
    return {'門牌': '國土測繪門牌精準定位', '路段': '國土測繪路段定位（門牌查不到，到了再看門牌）'}.get((s or {}).get('geo_level') or '', '') if precise(s) else ''

def due(con, limit=3):
    now = dt.datetime.now(c.TZ); today = now.date()
    return c.rows(con, "SELECT DISTINCT s.id,s.address FROM sites s JOIN orders o ON o.site_id=s.id WHERE o.date BETWEEN ? AND ? "
                       "AND o.status IN ('review','draft','confirmed') AND COALESCE(s.address,'')!='' AND COALESCE(s.geo_addr,'')!=s.address "
                       "AND COALESCE(s.geo_try,'')<? LIMIT ?", (today.isoformat(), (today + dt.timedelta(days=14)).isoformat(),
                                                                 (now - dt.timedelta(days=1)).isoformat(timespec='seconds'), limit))

def due_workers(con, limit=2):
    """夥伴填的住處 → 座標（派工算距離）。"""
    now = dt.datetime.now(c.TZ)
    return [{'id': r['id'], 'address': r['home'], 'kind': 'w'} for r in c.rows(con, "SELECT id,home FROM workers WHERE status='active' AND COALESCE(home,'')!='' AND COALESCE(home_geo,'')!=home "
                                                                              "AND COALESCE(home_try,'')<? LIMIT ?", ((now - dt.timedelta(days=1)).isoformat(timespec='seconds'), limit))]

def tick(con):
    """背景每分鐘：要查的案場丟給執行緒查（測試環境不連網）。"""
    if _busy.is_set() or os.environ.get('OPS_NO_PUSH') or LOCK is None:
        return 0
    rows = [dict(r) for r in due(con)]
    ws = due_workers(con)
    for r in ws:
        con.execute('UPDATE workers SET home_try=? WHERE id=?', (c.now(), r['id']))
    rows += ws
    if not rows:
        return 0
    for r in rows:
        if r.get('kind') != 'w':
            con.execute('UPDATE sites SET geo_try=? WHERE id=?', (c.now(), r['id']))
    _busy.set()
    threading.Thread(target=_bg, args=(rows,), daemon=True).start()
    return len(rows)

def _bg(rows):
    import geo
    try:
        for r in rows:
            lat, lng, info = geo.locate(r['address'])
            if lat is None:
                continue
            with LOCK, contextlib.closing(c.connect()) as con, con:
                if r.get('kind') == 'w':
                    con.execute('UPDATE workers SET home_lat=?,home_lng=?,home_geo=? WHERE id=? AND home=?', (lat, lng, r['address'], r['id'], r['address']))
                    continue
                cur = con.execute('SELECT address FROM sites WHERE id=?', (r['id'],)).fetchone()
                if cur and cur[0] == r['address']:            # 查的時候地址被改了就不寫，下一輪重查
                    con.execute('UPDATE sites SET lat=?,lng=?,geo_level=?,geo_addr=? WHERE id=?', (lat, lng, info.get('level', ''), r['address'], r['id']))
    finally:
        _busy.clear()
