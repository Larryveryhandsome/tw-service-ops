"""SEO／AEO（「SEO 跟 AEO 有處理嗎？」→ 網域用 example.com：官網＝www.example.com）。

- 官網伺服器端先畫好：node 跑同一份 web/home.js，把完整內容寫進 HTML（Google、ChatGPT／Perplexity 這類 AI 搜尋不用跑 JS 就讀得到）。
  三個品牌各一個網址：/（示範服務店）、/home?b=homeline、/home?b=bizline；各自的標題、描述、主網址（canonical）、分享預覽（og）。
- 結構化資料（JSON-LD）：店家（LocalBusiness：公司、臺北市、官方 LINE、價格區間、服務項目）＋常見問答（FAQPage，店長審核通過的才放）。
- robots.txt：官網網址開放公開頁；營運網址（demoshop-ops）整個不收錄（後台、報價單、工作頁都在那）。sitemap.xml、llms.txt（給 AI 讀的網站說明）。
- 內容都讀系統現有資料（價目、付款／改期／退款規則、公司登記），不另外寫對外文字；常見問答走店長審核（setting home_faq）。
"""
import html as _h, json, os, re, subprocess, time
import core as c
import integrations as ix

SITE = 'https://www.example.com'
BRANDS = ('demoshop', 'homeline', 'bizline')
_CACHE = {}
TTL = 900

SHORT = {'新北市': '新北', '桃園市': '桃園', '臺中市': '台中', '臺南市': '台南', '高雄市': '高雄', '臺北市': '臺北'}

def areas(con, b):
    """這個品牌服務的縣市（regions.service_counties，設定 service_area_<品牌>）。示範服務店、生活線臺北＋六都。"""
    import regions
    return regions.service_counties(con, b)

def area_text(con, b):
    return '・'.join(SHORT.get(x, x) for x in areas(con, b))

def page_url(b):
    return SITE + '/' if b == 'demoshop' else f'{SITE}/home?b={b}'

def faq(con, b):
    """店長審核通過的常見問答：[[問, 答]]。"""
    try:
        d = json.loads(ix.setting(con, 'home_faq') or '{}')
    except ValueError:
        return []
    return [[str(q), str(a)] for q, a in (d.get(b) or []) if str(q).strip() and str(a).strip()]

def _ssr(b, store, testi):
    p = subprocess.run(['node', str(c.ROOT / 'tools' / 'ssr_home.js')], input=json.dumps({'b': b, 'store': store, 'testi': testi}, ensure_ascii=False).encode('utf-8'),
                       capture_output=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if p.returncode:
        raise RuntimeError(p.stderr.decode('utf-8', 'replace')[:300])
    return json.loads(p.stdout.decode('utf-8'))

def _min_price(prices):
    ns = [int(x.replace(',', '')) for _, t in prices for x in re.findall(r'(\d[\d,]{2,})\s*元', t)]
    return min(ns) if ns else None

def jsonld(con, b, v, cp):
    k, co = v['contact'], v['company']
    biz = {'@context': 'https://schema.org', '@type': 'LocalBusiness', '@id': page_url(b) + '#business', 'name': v['brand_name'], 'url': page_url(b),
           'description': cp.get('lead') or '', 'areaServed': [{'@type': 'AdministrativeArea', 'name': x} for x in areas(con, b)],
           'address': {'@type': 'PostalAddress', 'streetAddress': co.get('address', ''), 'addressRegion': '臺北市', 'addressCountry': 'TW'},
           'parentOrganization': {'@type': 'Organization', 'name': co.get('name', ''), 'taxID': co.get('tax_id', '')},
           'sameAs': [k['line']], 'openingHours': 'Mo-Su 09:00-18:00', 'currenciesAccepted': 'TWD', 'paymentAccepted': '銀行匯款, LINE Pay',
           'hasOfferCatalog': {'@type': 'OfferCatalog', 'name': '服務項目與參考價格', 'itemListElement': [
               {'@type': 'Offer', 'itemOffered': {'@type': 'Service', 'name': n, 'areaServed': [x for x in areas(con, b)]}, 'description': t} for n, t in v['prices']]},
           'knowsLanguage': 'zh-Hant-TW', 'image': SITE + '/brand-wordmark.png'}
    if b == 'demoshop':
        biz['logo'] = SITE + '/brand-wordmark.png'
    mp = _min_price(v['prices'])
    if mp:
        biz['priceRange'] = f'NT${mp:,} 起（未稅參考價，以正式報價單為準）'
    out = [biz]
    fq = faq(con, b)
    if fq:
        out.append({'@context': 'https://schema.org', '@type': 'FAQPage', 'mainEntity': [
            {'@type': 'Question', 'name': q, 'acceptedAnswer': {'@type': 'Answer', 'text': a}} for q, a in fq]})
    return out

def render_home(con, b, host='site'):
    """官網完整 HTML（含伺服器端內容、SEO 標頭）。失敗就回原本的 home.html（照樣能用，只是沒先畫好）。"""
    import store, testimonials
    b = b if b in BRANDS else 'demoshop'
    key = (b, host)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    raw = (c.ROOT / 'web' / 'home.html').read_text(encoding='utf-8')
    try:
        v = store.view(con, b); v['faq'] = faq(con, b)
        r = _ssr(b, v, testimonials.public(con, b))
    except Exception:
        return raw
    cp = r.get('copy') or {}
    title = f"{v['brand_name']}｜{area_text(con, b)}到府服務・{'定期駐點服務' if b == 'bizline' else '年度保養・一般到府服務'}"
    desc = (cp.get('lead') or '')[:150]
    url = page_url(b)
    head = (f'<title>{_h.escape(title)}</title>\n<meta name="description" content="{_h.escape(desc)}">\n'
            f'<link rel="canonical" href="{url}">\n'
            + ''.join(f'<link rel="alternate" hreflang="zh-Hant-TW" href="{page_url(x)}">\n' for x in (b,))
            + f'<meta property="og:type" content="website">\n<meta property="og:title" content="{_h.escape(title)}">\n<meta property="og:description" content="{_h.escape(desc)}">\n'
            f'<meta property="og:url" content="{url}">\n<meta property="og:locale" content="zh_TW">\n<meta property="og:site_name" content="{_h.escape(v["brand_name"])}">\n'
            + (f'<meta property="og:image" content="{SITE}/brand-wordmark.png">\n' if b == 'demoshop' else '')
            + ('<meta name="robots" content="noindex">\n' if host != 'site' else '')
            + ''.join('<script type="application/ld+json">' + json.dumps(x, ensure_ascii=False).replace('</', '<\\/') + '</script>\n' for x in jsonld(con, b, v, cp)))      # Python 3.10 相容：f-string 運算式裡不能有反斜線
    out = re.sub(r'<title>.*?</title>\s*<meta name="description"[^>]*>', lambda m: head.rstrip('\n'), raw, count=1, flags=re.S)
    out = out.replace('<div class="site" id="app"><p class="sub" style="padding:40px 0">載入中…</p></div>', f'<div class="site" id="app">{r["html"]}</div>', 1)
    _CACHE[key] = (time.time(), out)
    return out

def robots(host):
    if host != 'site':                       # 營運網址：後台、報價單、工作頁 → 不收錄
        return 'User-agent: *\nDisallow: /\n'
    return ('User-agent: *\nAllow: /$\nAllow: /*.txt$\nAllow: /home\nAllow: /mall\nAllow: /store\nAllow: /privacy\nAllow: /book\nAllow: /llms.txt\nAllow: /sitemap.xml\nAllow: /*.css$\nAllow: /*.js$\nAllow: /*.png$\nAllow: /*.jpg$\nAllow: /*.webp$\nAllow: /*.svg$\n'
            'Disallow: /\n\nSitemap: ' + SITE + '/sitemap.xml\n')

def sitemap():
    d = time.strftime('%Y-%m-%d')
    urls = [page_url(b) for b in BRANDS] + [SITE + '/mall'] + [f'{SITE}/store?b={b}' for b in BRANDS] + [SITE + '/llms.txt']
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + ''.join(f'<url><loc>{_h.escape(u)}</loc><lastmod>{d}</lastmod></url>\n' for u in urls) + '</urlset>\n')

def llms(con):
    """給 AI 搜尋讀的網站說明（llms.txt）：只用系統現有資料。"""
    import store
    out = ['# 示範服務店・生活小幫手・企業服務線（全台到府服務）', '',
           '> 到府服務：一般到府服務、年度保養、進階專案、搬遷前後服務、店面辦公室服務、定期駐點服務。示範服務店、生活小幫手服務六都（台北、新北、桃園、台中、台南、高雄）與臺北市；企業服務線專做臺北定期駐點服務。'
           '官方 LINE 線上拍照估價，正式報價單、服務前後照片、完工確認。以下價格皆為未稅參考價，實際以正式報價單為準。', '']
    for b in BRANDS:
        v = store.view(con, b)
        out += [f"## {v['brand_name']}", f"- 官網：{page_url(b)}", f"- 官方 LINE：{v['contact']['line']}（{v['contact']['line_id']}）", f"- 服務時間：{v['contact']['hours']}",
                f"- 服務地區：{'、'.join(areas(con, b))}", f"- 經營公司：{v['company']['name']}（統一編號 {v['company']['tax_id']}），{v['company']['address']}", '', '### 參考價格']
        out += [f'- {n}：{t}' for n, t in v['prices']] + [f"- 說明：{v['price_note']}", '', '### 服務流程'] + [f'{i}. {x}' for i, x in enumerate(v['service_flow'], 1)]
        out += ['', '### 付款'] + [f'- {x}' for x in v['payment_rules']] + ['', '### 改期與取消'] + [f'- {x}' for x in v['cancel_rules']]
        fq = faq(con, b)
        if fq:
            out += ['', '### 常見問答'] + [f'- 問：{q}\n  答：{a}' for q, a in fq]
        out.append('')
    return '\n'.join(out)

def clear_cache():
    _CACHE.clear()

# ── IndexNow（「讓網站、各大 AI 推薦我們」）：Bing／Yandex 等收到就馬上重抓；ChatGPT 搜尋用 Bing 的索引 ──
def indexnow_key(con):
    k = ix.setting(con, 'indexnow_key')
    if not k:
        import secrets
        k = secrets.token_hex(16); ix.set_setting(con, 'indexnow_key', k)
    return k

def indexnow(con, urls=None):
    """通知搜尋引擎這些網址更新了。回傳 HTTP 狀態碼（200／202＝收到）。"""
    import urllib.request
    urls = urls or [page_url(b) for b in BRANDS] + [SITE + '/mall'] + [f'{SITE}/store?b={b}' for b in BRANDS] + [SITE + '/llms.txt']
    body = json.dumps({'host': SITE.split('//')[1], 'key': indexnow_key(con), 'keyLocation': f'{SITE}/{indexnow_key(con)}.txt', 'urlList': urls}).encode()
    req = urllib.request.Request('https://api.indexnow.org/indexnow', data=body, headers={'Content-Type': 'application/json; charset=utf-8'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0
