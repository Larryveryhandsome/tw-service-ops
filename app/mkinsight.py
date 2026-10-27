"""行銷用「客戶洞察」：真實對話去識別後放到 行銷\\06_客戶洞察\\（給 GPT／Codex 讀）。
負責人：「GPT那邊也要知道真實的對話，行銷需要知道的東西他都必須得知道」。客戶個資一律不給 GPT，所以只給去識別後的版本。

來源：示範服務店／企業線／生活線群 官方 LINE 對話匯出（CSV，demoshop-ops 外層資料夾）＋系統收到的近期客戶訊息（line_inbox，保留期內）。
去識別：姓名（客戶、LINE 名稱、夥伴、同事、案場）、電話、地址門牌、email、網址、LINE ID、身分證、帳號／統編（7 位以上數字）、
       門鎖密碼、車牌、社區／店家／公司名稱 → 〔…〕；檔名（有姓名地址）不帶出；每段對話改代號（加鹽雜湊，不能反推）；日期只留月份＋第幾天。
匯出後自動再檢查一次（電話、長數字、email、網址、已知姓名），有漏的整句拿掉；檢查結果寫在 README。
夥伴工作對話（派工、打卡）不匯出；應徵對話另放「應徵」資料夾（招募行銷用）。
另外產生「服務與價格事實.md」（直接讀系統設定，價格改了隔天就跟著更新）。
背景每天 03:00 後跑一次（automation.step → daily）；手動：python -X utf8 tools/mk_insights.py
"""
import collections
import csv
import datetime as dt
import hashlib
import io
import json
import pathlib
import re
import secrets
import core as c
import integrations as ix

ROOT = pathlib.Path(__file__).resolve().parent
SRC = {'demoshop': ROOT.parent / '示範服務店', 'bizline': ROOT.parent / '企業線', 'homeline': ROOT.parent / '生活線群'}
OUT = ROOT.parent / '行銷' / '06_客戶洞察'
LETTER = {'demoshop': 'H', 'bizline': 'G', 'homeline': 'M'}
BNAME = {'demoshop': '示範服務店', 'bizline': '企業服務線', 'homeline': '生活小幫手'}

SURN = ('王李張劉陳楊黃趙吳周徐孫馬朱胡郭何高林羅鄭梁謝宋唐許韓馮鄧曹彭曾蕭田董袁潘蔣蔡余杜葉程蘇魏呂丁任沈姚盧姜崔鍾譚陸汪范'
        '金石廖賈夏韋方白鄒孟熊秦邱江尹薛閻段雷侯龍史陶黎賀顧毛郝龔邵萬錢嚴武戴莫孔向湯游簡賴藍溫洪施翁柯莊巫涂詹凃歐傅連鐘')
STOP = set('''Unknown unknown 自動回應訊息 臺北 大安區 臺北市 板橋 板橋區 蘇澳 蘇澳鎮 頭城 東區 中壢 中壢區 壯圍 壯圍鄉 員山 東區 冬山 三民區 五結 西屯區
三星 三星鄉 大同 大同鄉 南澳 南澳鄉 台北 臺北 新北 桃園 新竹 花蓮 台中 臺中 店家 店面 搬入 搬出 搬家 細清 年度保養 店家 月護 訂閱 客戶 業主 房東 公司 社區 服務
居家 定期 單次 估價 現場 辦公室 施工 交屋 企業線 示範服務店 示範 LINE line 生活線 阿姨 老師 先生 小姐 太太 管家 夥伴 同事 老闆 老闆娘 一般 好的 謝謝 收到 您好 你好
沒問題 可以 了解 大家 我們 你們 他們 今天 明天 照片 時間 價格 方案 預約 服務 服務 Ok OK ok Hi hi 早安 午安 晚安 晴 客服
示範服務店 生活小幫手 企業服務線 示範公司 示範 客服 姓名 地址 稱呼 門牌 連結 號碼 電話 自動回覆 已移除 疑似個資 社區名 店家名 公司名 密碼 數字 車牌 身分證 email'''.split())
STAFF = ('店長', '店長', '負責人', '合夥人', '合夥人', '客服')

def _salt(con):
    s = ix.setting(con, 'mkinsight_salt')
    if not s:
        s = secrets.token_hex(16); ix.set_setting(con, 'mkinsight_salt', s)
    return s

def code(con, brand, key, prefix=''):
    return f"{prefix or LETTER[brand]}-{hashlib.sha256((_salt(con) + brand + '|' + key).encode()).hexdigest()[:6]}"

def _parts(name):
    """LINE 名稱／檔名標籤拆成可能是姓名的片段。"""
    name = re.sub(r'[\U0001F000-\U0001FFFF☀-➿️]', '', name or '')
    out = {name.strip()}
    out |= {p.strip() for p in re.split(r'[/／_()\s,，、\-–|｜.。]+', name) if p.strip()}
    return {p for p in out if p and p not in STOP and ((re.search(r'[一-鿿]', p) and len(p) >= 2) or (not re.search(r'[一-鿿]', p) and len(p) >= 3))}

class Scrubber:
    def __init__(self, names, staff, workers, places):
        self.labels = {}
        for toks, lab in ((places, '〔地址〕'), (names, '〔姓名〕'), (workers, '〔夥伴〕'), (staff, '〔同事〕')):
            for t in toks:
                t = (t or '').strip()
                if t and t not in STOP and len(t) >= 2:
                    self.labels[t] = lab
        han = sorted((t for t in self.labels if re.search(r'[一-鿿]', t)), key=len, reverse=True)
        asc = sorted((t for t in self.labels if not re.search(r'[一-鿿]', t) and len(t) >= 3), key=len, reverse=True)
        self.han = re.compile('|'.join(map(re.escape, han))) if han else None
        self.asc = re.compile(r'(?<![A-Za-z0-9])(' + '|'.join(map(re.escape, asc)) + r')(?![A-Za-z0-9])', re.I) if asc else None
        self.low = {k.lower(): v for k, v in self.labels.items()}

    RULES = [
        (re.compile(r'https?://\S+|www\.\S+|line\.me/\S+|\S+\.(?:com|tw|net|org|io)(?:/\S*)?', re.I), '〔連結〕'),
        (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '〔email〕'),
        (re.compile(r'@[A-Za-z0-9_.\-]{3,}'), '〔LINE ID〕'),
        (re.compile(r'(?<![A-Za-z0-9])[A-Za-z][12]\d{8}(?!\d)'), '〔身分證〕'),
        (re.compile(r'(?<!\d)\d{11,}(?!\d)'), '〔號碼〕'),
        (re.compile(r'(?:\+?886[-\s]?|0)9\d{2}[-\s]?\d{3}[-\s]?\d{3}'), '〔電話〕'),
        (re.compile(r'\(?0\d{1,2}\)?[-\s]?\d{3,4}[-\s]?\d{4}'), '〔電話〕'),
        (re.compile(r'(?<![\d,])\d(?:[\d\- ]{5,})\d(?![\d,])'), None),           # 7 位以上數字（帳號、統編、沒分隔的電話）；見 _long
        (re.compile(r'((?:密碼|門禁|密碼鎖|電子鎖|鑰匙盒|大門)[^\n。，,]{0,8}?)[0-9#*＊]{3,}'), r'\1〔密碼〕'),
        (re.compile(r'((?:末五碼|後五碼|末四碼|後四碼|帳號末|尾數)[^\d\n]{0,4})\d{3,6}'), r'\1〔數字〕'),
        (re.compile(r'(?<![A-Za-z0-9])(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z0-9]{2,4}-[A-Z0-9]{1,4}(?![A-Za-z0-9])'), '〔車牌〕'),   # Codex 回報：8099-F7 這種新式車牌以前抓不到
        (re.compile(r'[一-鿿]{1,5}[路街](?:[一二三四五六七八九十\d]+段)?(?:\d+巷)?(?:\d+弄)?\d+(?:之\d+)?號(?:之\d+)?(?:\d+樓)?(?:之\d+)?'), '〔地址〕'),
        (re.compile(r'[一-鿿]{1,5}[路街][一二三四五六七八九十\d]+段'), '〔地址〕'),
        (re.compile(r'\d+\s*(?:巷|弄)(?:\s*\d+\s*(?:弄|號))?|\d+(?:之\d+)?\s*號\s*(?:\d+\s*樓|之\s*\d+)|\d+\s*鄰'), '〔門牌〕'),     # 單獨「3號」多半是日期，不算
        (re.compile(r'[一-鿿A-Za-z]{2,8}(?:社區|大樓|華廈|山莊|新城|大廈|別墅)(?!服務|公設|公共|管理費|管委)'), None),
        (re.compile(r'[一-鿿A-Za-z]{2,8}(?:店家|旅店|旅宿|行館|villa|Villa)(?!服務|房務|業者|老闆|主人|經營|的|管家|管理)'), None),
        (re.compile(r'[一-鿿A-Za-z]{2,10}(?:有限公司|股份有限公司|企業社|工作室|商行|診所|事務所)'), '〔公司名〕'),
        (re.compile(f'[{SURN}][一-鿿]?(?:小姐|先生|太太|女士|老師|阿姨|姐姐|大姐|大哥|經理|主任|老闆娘|老闆|媽媽|爸爸|姐|哥)'), '〔稱呼〕'),
        (re.compile(f'((?:我是|我叫|敝姓|我姓|聯絡人[：:]?|收件人[：:]?|姓名[：:]?)\\s?)[{SURN}][一-鿿]{{0,2}}(?=[，,。！!？?\\s]|$|喔|唷|哦|啦|的)'), r'\1〔姓名〕'),
        (re.compile(r'阿(?![姨嬤公伯婆兵里拉])[一-鿿](?=姐|哥|姨|[，,。！!？?\s～~]|$|會|已|到|在|過去|負責|來|說|的|今天|明天)'), '〔稱呼〕'),   # 阿堅、阿瑋這類暱稱
        (re.compile(r"(?<![A-Za-z])(?!(?:Hello|hello|Hi|hi|HI|Hey|hey|Dear|dear|示範|LINE)(?![A-Za-z]))[A-Za-z][A-Za-z.'’\-]{1,20}(?:\s[A-Za-z][A-Za-z.'’\-]{0,20})?\s*(?=您好|你好|早安|午安|晚安)"), '〔姓名〕'),   # Patrick您好
    ]

    def _long(self, m):
        t = m.group(0).strip()
        if re.fullmatch(r'20\d{2}\s*[-/]\s*\d{1,2}\s*[-/]\s*\d{1,2}', t) or re.fullmatch(r'\d{1,5}\s*[-–~]\s*\d{1,5}', t):
            return m.group(0)                     # 日期、數字區間（預算 5000-8000）留著
        return '〔號碼〕' if len(re.sub(r'\D', '', t)) >= 7 else m.group(0)

    GENERIC = ('臺北', '中壢', '板橋', '頭城', '冬山', '五結', '三星', '員山', '壯圍', '蘇澳', '南澳', '大同', '台灣', '臺灣', '我們', '您的', '你的', '我的', '附近', '其他',
               '一般', '日租', '短租', '專業', '在地', '各大', '小型', '優質', '熟悉', '經營', '管理', '服務', '服務', '合作', '配合', '很多', '許多', '整棟', '整個', '同一',
               '住宅', '公寓', '大樓', '透天', '這個', '那個', '這間', '那間', '每間', '自己')
    FUNC = set('的是在和與跟及依給幫做有開住去到從對為被讓就都也還要想會可能間家各整同這那每')

    def _place(self, m):
        """店家／社區名稱：前面是一般說法（臺北店家、同社區、熟悉店家…）就不遮。"""
        t = m.group(0); name = re.sub(r'(社區|大樓|華廈|山莊|新城|大廈|別墅|店家|旅店|旅宿|行館|villa|Villa)$', '', t)
        lab = '〔店家名〕' if re.search(r'店家|旅店|旅宿|行館|villa', t, re.I) else '〔社區名〕'
        for i in range(len(name) - 1):            # 從前面一個字一個字去掉，找到「不是一般說法」的名稱就遮（我們森活店家 → 我〔店家名〕）
            sub = name[i:]
            if not any(g in sub for g in self.GENERIC) and not any(ch in self.FUNC for ch in sub):
                return t[:i] + lab
        return t

    def __call__(self, s):
        s = str(s or '').replace('\r\n', '\n').replace('\r', '\n')
        for rx, rep in self.RULES[:7]:
            s = rx.sub(rep, s)
        s = self.RULES[7][0].sub(self._long, s)
        if self.han:
            s = self.han.sub(lambda m: self.labels[m.group(0)], s)
        if self.asc:
            s = self.asc.sub(lambda m: self.low.get(m.group(0).lower(), '〔姓名〕'), s)
        for rx, rep in self.RULES[8:]:
            s = rx.sub(rep, s) if rep is not None else rx.sub(self._place, s)
        return s

LEAK = [re.compile(p) for p in (r'(?<![A-Za-z0-9])(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z0-9]{2,4}-[A-Z0-9]{1,4}(?![A-Za-z0-9])', r'09\d{2}[-\s]?\d{3}[-\s]?\d{3}', r'(?<![\d,])\d{7,}(?![\d,])', r'[\w.+-]+@[\w-]+\.[a-z]{2,}', r'https?://', r'[路街巷弄]\s*\d+\s*號')]

def _leaky(s, sc):
    if any(rx.search(s) for rx in LEAK):
        return True
    return bool(sc.han and sc.han.search(s)) or bool(sc.asc and sc.asc.search(s))

TOPICS = {
    '年度保養': r'年度保養|年節|過年|年前|除舊|細清|深度服務|很久沒|整間',
    '價格與方案': r'價格|價錢|多少錢|費用|報價|方案|月護|訂閱|包月|每小時|優惠|折扣|太貴|便宜|預算|NT\$|\d{3,5}\s*元',
    '預約與時間': r'預約|時間|改期|改時間|取消|延期|什麼時候|幾點|週末|假日|檔期|排得到|有空',
    '找到我們的管道': r'看到|介紹|推薦|網路|臉書|FB|IG|Google|谷歌|廣告|搜尋|Threads|朋友說|鄰居',
    '猶豫與比較': r'考慮|再想想|再看看|比較|別家|其他家|不確定|擔心|怕|猶豫|先問問',
    '信任與安全': r'鑰匙|密碼|不在家|安全|保險|信任|陌生|放心|監視器|貴重',
    '品質與評價': r'乾淨|滿意|很棒|好棒|辛苦了|用心|仔細|推薦|不乾淨|沒清|漏|重做|客訴|抱怨|損壞|遲到',
    '服務範圍': r'冷氣|洗衣機|抽油煙機|冰箱|窗戶|紗窗|玻璃|浴室|廚房|陽台|地板|除霉|除黴|收納|整理|寵物|貓|狗|施工|油漆|粉塵|交屋|搬家',
    '垃圾清運': r'垃圾|清運|丟|回收|廚餘',
    '店家': r'店家|退房|入住|房客|床單|備品|房務',
}
TOPIC_RX = {k: re.compile(v, re.I) for k, v in TOPICS.items()}
RECRUIT = re.compile(r'應徵|應征|找工作|履歷|職缺|兼職|徵人|面試|工作機會|想工作|缺人嗎|有在徵')
NICK_GENERIC = {'小姐', '大姐', '大哥', '小哥', '老哥', '美姐', '帥哥', '姐姐', '哥哥', '阿姨', '阿嬤', '阿公', '阿伯', '阿婆', '阿兵', '阿里', '阿拉', '家姐', '表姐', '學姐', '學長', '一姐', '二姐', '三姐', '一哥', '二哥', '三哥', '阿一'}
TAGWORD = re.compile(r'服務|清運|掃除|年節|過年|細清|定清|居清|窗戶|搬入|搬出|搬家|施工|交屋|月護|訂閱|估價|號館|飯店|租屋|兩人|輕量|的家|店家|店面|示範|測試|客訴|報價|預約|追蹤|成交|待確認')
WORKER = re.compile(r'我的主控板|打卡|我要接單|上工|下班|請假')

def _read_csv(p):
    raw = p.read_bytes()
    for enc in ('utf-8-sig', 'utf-8', 'cp950'):
        try:
            t = raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    else:
        t = raw.decode('utf-8', errors='replace')
    rows = [r for r in csv.reader(io.StringIO(t)) if len(r) >= 5 and r[0] in ('Account', 'User')]
    return rows

def _dictionary(con, threads):
    names, staff, workers, places = set(), set(STAFF), set(), set()
    for (n, ln) in con.execute('SELECT name,line_name FROM customers'):
        names |= _parts(n) | _parts(ln)
    for (n,) in con.execute("SELECT display_name FROM line_users WHERE COALESCE(display_name,'')!=''"):
        names |= _parts(n)
    for (n,) in con.execute('SELECT name FROM workers'):
        workers |= _parts(n)
    for (n,) in con.execute("SELECT name FROM users WHERE COALESCE(name,'')!=''"):
        staff |= _parts(n)
    for (n, a) in con.execute('SELECT name,address FROM sites'):
        names |= _parts(n)
        if a and len(a) >= 6:
            places.add(a.strip())
    for t in threads:
        for s in t['senders']:
            names |= _parts(s)
        for p in t['file'].stem.split('_')[3:]:
            names |= _parts(p)
    staff -= {'晴'}
    names -= staff | workers
    fullname = re.compile(f'^[{SURN}][\\u4e00-\\u9fff]{{1,2}}$')     # 全名衍生的暱稱（王小芳 → 小芳、阿芳、芳姐）也要遮
    def nicks(src):
        out = set()
        for n in src:
            if fullname.match(n):
                g = n[1:]
                out |= ({g} if len(g) == 2 else set()) | {f'阿{ch}' for ch in g} | {f'{ch}姐' for ch in g} | {f'{ch}哥' for ch in g}
        return {x for x in out if x not in NICK_GENERIC and x not in STOP and not TAGWORD.search(x)}
    workers |= nicks(set(workers))
    names |= nicks(set(names))
    names -= workers
    full = re.compile(f'^[{SURN}][\\u4e00-\\u9fff]{{1,2}}(小姐|先生|太太)?$')
    cand = [n for n in names if not full.match(n)]
    names -= {n for n in cand if TAGWORD.search(n)}          # 檔名／暱稱裡的服務標籤（年節、一般到府服務、搬入…）不是姓名
    cand = [n for n in names if not full.match(n)]
    if cand:                                  # 檔名標籤、暱稱裡的一般用詞（一般到府服務、年節、窗戶…）：5 段以上對話都提到 → 不是姓名
        han = [n for n in cand if re.search(r'[一-鿿]', n)]; asc = [n for n in cand if n not in han]
        rh = re.compile('|'.join(map(re.escape, sorted(han, key=len, reverse=True)))) if han else None
        ra = re.compile(r'(?<![A-Za-z0-9])(' + '|'.join(map(re.escape, sorted(asc, key=len, reverse=True))) + r')(?![A-Za-z0-9])', re.I) if asc else None
        seen = collections.Counter()
        for t in threads:
            body = '\n'.join(r[4] for r in t['rows'])
            found = set(rh.findall(body)) if rh else set()
            found |= {x.lower() for x in ra.findall(body)} if ra else set()
            seen.update(found)
        low = collections.Counter({k.lower(): v for k, v in seen.items()})
        names -= {n for n in cand if max(seen.get(n, 0), low.get(n.lower(), 0)) >= 5}
    return Scrubber(names, staff, workers - staff, places)

def collect(con):
    """讀匯出的對話。kind：customer／recruit／worker（夥伴工作對話）／staff（管理層自己測試、審核，內部資料）——後兩種不匯出。"""
    staffn, workern = set(STAFF), set()
    for (n,) in con.execute("SELECT name FROM users WHERE COALESCE(name,'')!=''"):
        staffn |= _parts(n)
    for (n,) in con.execute('SELECT name FROM workers'):
        workern |= _parts(n)
    staffn -= {'晴'}
    threads = []
    for brand, d in SRC.items():
        if not d.exists():
            continue
        for p in sorted(d.glob('*.csv')):
            rows = _read_csv(p)
            if not rows:
                continue
            users = {r[1] for r in rows if r[0] == 'User'}
            utext = '\n'.join(r[4] for r in rows if r[0] == 'User')
            who = set().union(*(_parts(u) for u in users)) if users else set()
            cmds = sum(1 for r in rows if r[0] == 'User' and r[4].lstrip().startswith('【'))
            kind = ('staff' if who & staffn or cmds >= 2 or len(re.findall(r'中控台|送審|派單管理群|管理選單|審核卡|要修改（打字說明）', utext)) >= 3
                    else 'worker' if who & workern or len(WORKER.findall(utext)) >= 3 else 'recruit' if RECRUIT.search(utext) else 'customer')
            first = rows[0][2]
            threads.append({'brand': brand, 'file': p, 'rows': rows, 'senders': users, 'kind': kind,
                            'key': f"{first} {rows[0][3]}|{'/'.join(sorted(users))}|" + hashlib.sha256('\n'.join(r[4] for r in rows[:5]).encode()).hexdigest()[:12]})   # 舊訊息不會變 → 代號固定；Unknown 同一天也分得開
    return threads

def _day(s):
    try:
        return dt.date(*map(int, re.findall(r'\d+', s)[:3]))
    except (TypeError, ValueError):
        return None

def _line(r, sc, d0):
    who = '客服（自動回覆）' if r[0] == 'Account' and r[1] in ('自動回應訊息', 'AI 聊天機器人（β）') else '客服' if r[0] == 'Account' else '客戶'
    text = r[4]
    if who == '客服（自動回覆）':
        text = re.sub(r'^[^\n]{1,40}?(您好|你好)', r'〔姓名〕\1', text)      # 「Xiu 王🍠您好」整段名稱都換掉（以前遇到空白就停）
        text = text if len(text) <= 120 else text[:120] + '…（自動回覆，下略）'
    t = sc(text).strip()
    d = _day(r[2]); n = (d - d0).days if d and d0 else 0
    return f"D+{n} {str(r[3])[:5]} {who}：{t}" if t else ''

def production():
    """只有正式系統（正式資料庫、沒有測試旗標）才能寫進行銷資料夾。
    教訓：測試／稽核跑 automation.step 也觸發了匯出，測試資料庫沒有真實名單 → 7 個姓名沒遮到就寫進行銷資料夾。"""
    import os
    return not os.environ.get('OPS_NO_PUSH') and pathlib.Path(c.DATA).resolve() == (ROOT / 'data').resolve()

def run(con, out=None, now=None):
    """全部重新產生。回傳摘要 dict。"""
    out = pathlib.Path(out) if out else OUT          # 每次讀模組的 OUT（測試會換成暫存資料夾）
    if out.resolve() == (ROOT.parent / '行銷' / '06_客戶洞察').resolve() and not production():
        raise RuntimeError('測試環境不能寫行銷資料夾（名單不完整會漏個資）')
    now = now or dt.datetime.now(c.TZ)
    threads = collect(con)
    sc = _dictionary(con, threads)
    out.mkdir(parents=True, exist_ok=True)
    index, topic_hits, leaks, kept = [], collections.defaultdict(list), 0, collections.Counter()
    used = set()
    for t in threads:
        if t['kind'] in ('worker', 'staff'):
            kept['夥伴工作對話（不匯出）' if t['kind'] == 'worker' else '管理層內部對話（不匯出）'] += 1
            continue
        cd = code(con, t['brand'], t['key']); k = 2
        while cd in used:                   # 開頭完全一樣的兩段對話：加 -2、-3
            cd = code(con, t['brand'], t['key']) + f'-{k}'; k += 1
        used.add(cd)
        d0 = _day(t['rows'][0][2]); d1 = _day(t['rows'][-1][2])
        lines, topics = [], collections.Counter()
        for i, r in enumerate(t['rows']):
            ln = _line(r, sc, d0)
            if not ln:
                continue
            if _leaky(ln, sc):
                leaks += 1; ln = ln.split('：', 1)[0] + '：〔已移除：疑似個資〕'
            lines.append(ln)
            if r[0] == 'User':
                for k, rx in TOPIC_RX.items():
                    if rx.search(r[4]):
                        topics[k] += 1
                        if len(topic_hits[k]) < 5000:
                            topic_hits[k].append((t['brand'], cd, len(lines) - 1))
        sub = out / '對話' / ('應徵' if t['kind'] == 'recruit' else BNAME[t['brand']])
        sub.mkdir(parents=True, exist_ok=True)
        head = (f"# {cd}（{BNAME[t['brand']]}・{'應徵' if t['kind'] == 'recruit' else '客戶'}對話）\n\n"
                f"- 期間：{d0.strftime('%Y-%m') if d0 else '?'} ～ {d1.strftime('%Y-%m') if d1 else '?'}（D+N＝第一則訊息後第幾天）\n"
                f"- 訊息數：{len(lines)}\n- 主題：{'、'.join(k for k, _ in topics.most_common()) or '—'}\n\n---\n\n")
        (sub / f'{cd}.md').write_text(head + '\n'.join(lines) + '\n', encoding='utf-8')
        t['lines'] = lines; t['code'] = cd
        index.append([cd, BNAME[t['brand']], '應徵' if t['kind'] == 'recruit' else '客戶', d0.strftime('%Y-%m') if d0 else '', d1.strftime('%Y-%m') if d1 else '',
                      len(lines), '、'.join(k for k, _ in topics.most_common())])
        kept['應徵對話' if t['kind'] == 'recruit' else f"{BNAME[t['brand']]} 客戶對話"] += 1
    with (out / '索引.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(['代號', '品牌', '類型', '開始', '最後', '訊息數', '主題（多到少）']); w.writerows(sorted(index, key=lambda x: (x[1], x[3])))
    by = {t['code']: t for t in threads if t.get('code')}
    (out / '主題摘錄').mkdir(exist_ok=True)
    for k, hits in topic_hits.items():
        step = max(1, len(hits) // 400)          # 每個主題最多約 400 段，平均抽（新舊、品牌都有）
        parts = [f"# 主題：{k}\n\n客戶提到「{k}」的段落（前後各 2 則）。共 {len(hits)} 處，這裡平均抽 {len(hits[::step])} 段。全文在「對話」資料夾，用代號找。\n"]
        for brand, cd, i in hits[::step]:
            ls = by[cd]['lines']
            parts.append(f"\n## {BNAME[brand]}｜{cd}\n" + '\n'.join(ls[max(0, i - 2): i + 3]))
        (out / '主題摘錄' / f'{k}.md').write_text('\n'.join(parts) + '\n', encoding='utf-8')
    recent = _recent(con, sc, out, now)
    facts(con, out, now)
    summary = {'at': now.isoformat(timespec='minutes'), 'threads': dict(kept), 'removed_lines': leaks, 'recent': recent, 'topics': {k: len(v) for k, v in topic_hits.items()}}
    _readme(out, summary)
    ix.set_setting(con, 'mkinsight_last', json.dumps(summary, ensure_ascii=False))
    return summary

def _recent(con, sc, out, now):
    """系統收到的近期客戶訊息（line_inbox 保留期內；不含夥伴、管理層、群組）。"""
    cut = (now - dt.timedelta(days=30)).isoformat()
    staff = set(json.loads(ix.setting(con, 'line_staff_users') or '[]'))
    rows = con.execute("SELECT i.at,i.user_id,i.text,i.account FROM line_inbox i LEFT JOIN line_users l ON l.user_id=i.user_id AND l.account=i.account "
                       "WHERE i.type='message' AND COALESCE(i.group_id,'')='' AND COALESCE(i.text,'')!='' AND i.at>=? AND l.worker_id IS NULL ORDER BY i.at", (cut,)).fetchall()
    by = collections.defaultdict(list)
    for at, uid, text, acc in rows:
        if uid in staff or acc not in LETTER or str(text).startswith('【'):
            continue
        t = sc(text).strip()
        if not t or _leaky(t, sc):
            continue
        by[acc].append(f"- {str(at)[5:10]}｜{code(con, acc, uid, LETTER[acc] + 'L')}：{t[:300]}")
    parts = [f"# 近 30 天客戶在官方 LINE 傳的訊息（去識別）\n\n只有客戶傳來的文字（系統收得到的部分）；代號 xL-… 是同一位客戶。更新：{now.strftime('%Y-%m-%d')}\n"]
    for acc in LETTER:
        parts.append(f"\n## {BNAME[acc]}（{len(by[acc])} 則）\n" + ('\n'.join(by[acc]) or '（無）'))
    (out / '近期客戶訊息.md').write_text('\n'.join(parts) + '\n', encoding='utf-8')
    return sum(len(v) for v in by.values())

def facts(con, out, now):
    """服務與價格事實（直接讀系統設定）。"""
    import brands, pricing, contract, regions
    L = [f"# 服務與價格事實（系統自動產生，{now.strftime('%Y-%m-%d')} 更新）\n",
         '> 從營運系統的設定直接讀出來；價格改了隔天這裡就會更新。對外文案寫價格請加「原則上以正式報價單為準」。',
         '> 金額原則上未稅（客戶要發票另加 5%）。三個品牌分開，不要混用。\n',
         '## 共通流程',
         '- 線上估價（拍照）→ 系統初估 → 主管確認 → 正式報價單傳到客戶 LINE → 客戶線上簽收 → 收款 → 派夥伴 → 前一天提醒 → 完成照片 → 客戶驗收',
         '- 報價單期限：預設傳出日起 5 天。單次超過線上報價上限、或社區／廠房：專人到現場估價。',
         '- 付款（依方案）：' + '；'.join(v.rstrip('。') for v in contract.PAY_TEXT.values()) + '。',
         f"- 取消：{contract.PENDING_4_7}\n"]
    for b in ('demoshop', 'homeline', 'bizline'):
        k = pricing.config(con, b); bb = brands.get(con, b) or {}
        L.append(f"## {BNAME[b]}（{bb.get('company') or '示範服務股份有限公司'}）")
        L.append(f"- 直接服務縣市：{'、'.join(regions.service_counties(con, b))}（範圍外也收，會幫忙找配合夥伴）")
        L.append(f"- 線上估價：/book?b={b}　官網：/home?b={b}（網址前面是 https://ops.example.com；文案請用需求單給的 /go/ 追蹤連結）")
        for ln in brands.lines(con, b):
            L.append(f"- 業務線「{ln['name']}」：{'、'.join(ln.get('services') or [])}" + (f"（{ln['intro']}）" if ln.get('intro') else ''))
        if b == 'demoshop':
            L += [f"- 一般到府服務：單次每人每小時 {k['rate_single']}、定期每月 4 次以上每人每小時 {k['rate_plan']}；單次最低 {k['min_single']}",
                  f"- 年度保養／搬家前後：每人每小時 {k['rate_deep']}，最低 {k['min_deep']}；進階專案：每人每小時 {k['rate_reno']}，最低 {k['min_reno']}",
                  f"- 店面／辦公室：每人每小時 {k['rate_shop']}；店家：每間房 {k['bnb_per_room']}，最低 {k['min_bnb']}",
                  f"- 加價：寵物 +{k['pet_pct']}%、透天樓層 +{k['townhouse_pct']}%、高處梯子 {k['ladder']}、無電梯每層 {k['no_lift_per_floor']}",
                  f"- 線上報價上限：單次 {k['low_value']}，加購不算（店家 {k.get('low_value_bnb')}），超過請專人現場估價（估價費 {k['site_visit_fee']}；社區 {k['community_visit_fee'] or '免費'}）",
                  '- 加購：' + '、'.join(f'{a} {v}' for a, v in k['addons'].items())]
        if b == 'homeline':
            L += [f"- 日常服務：每人每小時 {k.get('homeline_rate', 450)}，至少 {k.get('homeline_min_hours', 3):g} 小時（依規模建議時數）",
                  '- 月方案：' + '、'.join(f'{n}（{v[1]:,}／月）' for n, v in pricing.MAMA_PLANS.items()),
                  '- 單次服務、年度保養／進階專案／交屋前服務：看照片或免費到府估價']
        if b == 'bizline':
            L += [f"- 日常房務（按出勤人數）：淡季 1 人 {k['bnb_off_1']}／2 人以上每人 {k['bnb_off_n']}；旺季 {k['bnb_peak_1']}／{k['bnb_peak_n']}；國定連假 {k['bnb_hol_1']}／{k['bnb_hol_n']}",
                  f"- 旺季：{'、'.join(k['bnb_peak'])}；1 位約可完成 {k['bnb_rooms_per_person']} 間雙人房；超時每小時 {k['bnb_overtime']}",
                  f"- 旺季後年度保養 {k['bnb_deep']}；垃圾代丟單次 {k['bnb_trash']}／包月（4 次）{k['bnb_trash_month']}；代管由專人說明"]
        tl = [ln for ln in brands.lines(con, b, active_only=False) if ln['kind'] == 'trash']
        if tl:
            more = int(k.get('trash_bag_more') or k.get('trash_bag', 1200))
            if not any(ln['active'] for ln in tl):          # 公告核准才上線，上線前行銷不要先宣傳
                L.append('- ⚠️ 垃圾清收「待上線」：店長核准公告後才開放，上線前請不要宣傳')
            L.append(f"- 垃圾清收（10/19 新服務）：標準袋一袋約 100–120 公升；單次第一袋 {k['trash_bag']}" + (f"、第二袋起每袋 {more}" if more != k['trash_bag'] else '')
                     + (f"，一次 {k['trash_max_bags']} 袋以內線上初估（超過專人報價）" if k.get('trash_max_bags') else '') + f"；服務時加購每袋 {k['trash_addon']}；原則上不收{k.get('trash_no')}")
        sc_ = pricing.scopes(con, b)
        offer = set(bb.get('services') or []) | {x for ln in brands.lines(con, b) for x in (ln.get('services') or [])}
        L.append('- 標準服務範圍（報價單寫的）：')
        for st, items in sc_.items():
            if st not in offer:
                continue                          # 只列這個品牌有的服務
            L.append(f"  - {st}：{'、'.join(items)}")
        L.append('')
    (out / '服務與價格事實.md').write_text('\n'.join(L) + '\n', encoding='utf-8')

def _readme(out, s):
    t = s['threads']
    (out / 'README.md').write_text(f"""# 06_客戶洞察（給 GPT 讀的真實資料，已去識別）

更新：{s['at']}（營運系統每天 03:00 後自動重新產生；不要改這裡的檔案，改了會被蓋掉）

## 這裡有什麼
- `服務與價格事實.md`：三個品牌現在的服務、價格、流程（直接讀營運系統設定，最準）
- `對話\\示範服務店\\`、`對話\\生活小幫手\\`、`對話\\企業服務線\\`：官方 LINE 的真實客戶對話，一段對話一個檔（代號 H-／M-／G-）
- `對話\\應徵\\`：想來當夥伴的人的對話（招募文案用）
- `主題摘錄\\`：客戶提到某主題的段落（年度保養、價格與方案、猶豫與比較、找到我們的管道…），先看這裡最快
- `索引.csv`：每段對話的品牌、期間、訊息數、主題
- `近期客戶訊息.md`：近 30 天客戶傳來的文字

數量：{'、'.join(f'{k} {v} 段' for k, v in t.items())}；近期客戶訊息 {s['recent']} 則。

## 已經去識別
姓名、LINE 名稱、電話、地址門牌、email、網址、LINE ID、身分證、帳號／統編、門鎖密碼、車牌、社區／店家／公司名稱都換成〔…〕；
同事寫成「客服」、夥伴寫成〔夥伴〕；日期只留月份，訊息前面的 D+N 是第一則後第幾天。
自動檢查：{s['removed_lines']} 句疑似還有個資，整句拿掉了（寫成〔已移除：疑似個資〕）。

## 使用規則（重要）
1. **不要嘗試猜或還原是誰**；看到疑似漏掉的個資，不要用，寫進 `系統需求.md` 讓 Claude 修。
2. 客戶原話可以拿來**理解痛點、用詞、猶豫點**；放進對外文案要改寫，不要整句照抄，也不能寫成某位客戶的推薦／評價（那要客戶同意）。
3. 價格、服務內容以 `服務與價格事實.md` 為準；優惠、活動只能用需求單寫的。
4. 三個品牌分開看、分開寫。
""", encoding='utf-8')

def daily(con):
    """automation.step 每分鐘呼叫：每天 03:00 後跑一次。"""
    now = dt.datetime.now(c.TZ)
    if not production() or now.strftime('%H:%M') < '03:00' or ix.setting(con, 'mkinsight_day') == now.date().isoformat():
        return 0
    ix.set_setting(con, 'mkinsight_day', now.date().isoformat())
    run(con, now=now)
    return 1
