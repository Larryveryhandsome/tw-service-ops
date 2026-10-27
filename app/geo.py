"""地址 → 經緯度（內政部國土測繪中心 NLSC 門牌定位，免金鑰）＋ 兩地距離／車程估算。

負責人：「之前我們不是有接過國土精準定位的API嗎，把那套抓給示範服務店用」。
來源＝合作店家「跑開發」已實測可用的那套（不動產仲介的工作/跑開發/跑開發_geocode.py 的 NLSC 路線），
這裡複製一份獨立版本，示範服務店不依賴合作店家資料夾、也不讀合作店家任何資料。

用法：
    import geo
    lat, lng, info = geo.locate("新北市板橋區中正路100號")      # 查不到回 (None, None, 原因)
    km = geo.distance_km(a_lat, a_lng, b_lat, b_lng)              # 直線距離
    mins = geo.drive_minutes("大安區中山路一段1號", "板橋區中正路1號")  # 兩地址估車程（分鐘），查不到回 None

設計：
- 門牌級（精準到號）優先；查不到門牌就退到路段級（info["level"]="路段"），派工估距離夠用，
  但打卡比對到場要看 level，路段級不拿來判「人不在現場」。
- 結果快取在 data/geo_cache.json（同一地址只查一次）；每次真的打 API 間隔 ≥0.6 秒，不連發。
- 政府憑證缺 Subject Key Identifier，Python 3.13+ 的嚴格驗證會擋 → 只放寬那一項，憑證鏈照驗。
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "geo_cache.json")
NLSC_URL = "https://api.nlsc.gov.tw/MapSearch/QuerySearch"
CENTER = "121.75,24.70"        # 臺北中心（只影響排序參考）
THROTTLE = 0.6
TIMEOUT = 20
_lock = threading.Lock()
_last = [0.0]
_sess = [None]
_FW = str.maketrans("０１２３４５６７８９", "0123456789")


def _session():
    if _sess[0] is None:
        import ssl
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.ssl_ import create_urllib3_context

        class _Relax(HTTPAdapter):
            def init_poolmanager(self, *a, **k):
                ctx = create_urllib3_context()
                ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
                k["ssl_context"] = ctx
                return super().init_poolmanager(*a, **k)
        s = requests.Session()
        s.mount("https://", _Relax())
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://maps.nlsc.gov.tw/"})  # 沒帶會退成全國模糊比對
        _sess[0] = s
    return _sess[0]


def _g(pat, s):
    m = re.search(pat, s, re.S)
    return m.group(1).strip() if m else ""


def _dedupe(s):
    """去空白、重複的鄉鎮市（例「○○縣○○市示範路 1 號」以前只查得到路段）。"""
    s = re.sub(r"\s+", "", (s or "").translate(_FW))
    return re.sub(r"^((?:[一-鿿]{2}[縣市])?)([一-鿿]{1,3}[市鄉鎮區])\2", r"\1\2", s)


def _core(s):
    """門牌核心＝路段+段+巷+弄+號（去縣市/鄉鎮/村里/鄰/樓），嚴格比對用。"""
    s = _dedupe(s)
    s = re.sub(r"^[一-鿿]{1,3}[縣市]", "", s)
    s = re.sub(r"^[一-鿿]{1,3}[市鄉鎮區]", "", s)
    s = re.sub(r"[一-鿿]{1,4}(?:里|村)(?!\d+號)|[一-鿿]{1,4}(?=\d+鄰)", "", s)
    s = re.sub(r"\d+鄰", "", s)
    m = re.search(r"號", s)
    return re.sub(r"\s", "", s[:m.end()] if m else s)


def _norm(addr):
    """給搜尋用：去村里、鄰、樓層（截到第一個「號」）；社區名（如『忠孝新村12號』）保留。"""
    a = _dedupe(addr)
    a = re.sub(r"([縣市鄉鎮區])(?:[一-鿿]{1,3}(?:里|村)(?!\d+號)|[一-鿿]{1,4}(?=\d+鄰))", r"\1", a)
    a = re.sub(r"\d+鄰", "", a)
    m = re.search(r"號", a)
    return a[:m.end()] if m else a


def _road(addr):
    """退一步：只到路／街／段（沒有號）——門牌查不到時估距離用。"""
    a = _norm(addr)
    m = re.search(r"^.*?(?:路|街|大道)(?:[一二三四五六七八九十\d]+段)?", a)
    return m.group(0) if m else ""


def _parse(txt, want_core="", any_level=False):
    for it in re.findall(r"<ITEM>(.*?)</ITEM>", txt or "", re.S):
        key, loc, content = _g(r"<KEY>(.*?)</KEY>", it), _g(r"<LOCATION>(.*?)</LOCATION>", it), _g(r"<CONTENT>(.*?)</CONTENT>", it)
        if not loc or "," not in loc or (not any_level and "ADDRESS" not in key):
            continue
        try:
            lng, lat = float(loc.split(",")[0]), float(loc.split(",")[1])
        except ValueError:
            continue
        if 120.0 <= lat <= 122.5 and 21.0 <= lng <= 26.0:       # 經緯顛倒防呆
            lat, lng = lng, lat
        if not (119.0 <= lng <= 122.5 and 21.0 <= lat <= 26.5):
            continue
        if want_core and _core(content) != want_core:           # 門牌要完全一樣，不採隔壁巷弄
            continue
        return round(lat, 6), round(lng, 6), content
    return None, None, ""


def _query(word):
    with _lock:
        wait = THROTTLE - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    r = _session().post(NLSC_URL, data={"word": quote(word), "feedback": "XML", "center": CENTER}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE)


def locate(addr: str, use_cache: bool = True):
    """回 (lat, lng, info)。info = {"level": "門牌"/"路段", "matched": 比對到的正式地址} 或查不到時 {"error": 原因}。"""
    addr = (addr or "").strip()
    if not addr:
        return None, None, {"error": "沒有地址"}
    cache = _load_cache() if use_cache else {}
    if addr in cache and cache[addr].get("lat"):
        c = cache[addr]
        return c["lat"], c["lng"], {"level": c.get("level"), "matched": c.get("matched"), "cached": True}
    try:
        lat, lng, matched = _parse(_query(_norm(addr)), _core(addr))
        level = "門牌"
        if lat is None and _road(addr):
            lat, lng, matched = _parse(_query(_road(addr)), "", any_level=True)
            level = "路段"
    except Exception as e:  # noqa: BLE001
        return None, None, {"error": f"{type(e).__name__}: {e}"[:120]}
    if lat is None:
        return None, None, {"error": "國土測繪查不到這個地址（請確認地址寫法）"}
    with _lock:
        cache = _load_cache()
        cache[addr] = {"lat": lat, "lng": lng, "level": level, "matched": matched, "at": time.strftime("%Y-%m-%d")}
        _save_cache(cache)
    return lat, lng, {"level": level, "matched": matched}


def distance_km(lat1, lng1, lat2, lng2) -> float:
    """兩點直線距離（公里，haversine）。"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 2)


def drive_minutes(addr_a: str, addr_b: str, kmh: float = 35.0, detour: float = 1.3):
    """兩地址估車程（分鐘）：直線距離 × 1.3（道路繞行）÷ 平均時速 35。大安區區機車／汽車大致合理；查不到回 None。"""
    a, b = locate(addr_a), locate(addr_b)
    if a[0] is None or b[0] is None:
        return None
    return max(3, round(distance_km(a[0], a[1], b[0], b[1]) * detour / kmh * 60))


def map_link(addr: str) -> str:
    """Google 地圖導航連結：查得到座標就用座標（比用地址文字準），查不到就用地址。"""
    lat, lng, _ = locate(addr)
    if lat is not None:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    return f"https://www.google.com/maps/search/?api=1&query={quote(addr or '臺北')}"


if __name__ == "__main__":
    import sys
    for a in sys.argv[1:] or ["臺北市大安區中山路二段1號"]:
        print(a, "→", locate(a))
