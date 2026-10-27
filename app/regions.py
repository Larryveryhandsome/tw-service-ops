"""地區：夥伴可服務區域與客戶服務地點用同一種格式（「臺北市大安區」「臺北市全區」），全台縣市通用；「全台」＝所有縣市。"""
import json
import re

COUNTIES = ['基隆市', '臺北市', '新北市', '桃園市', '新竹市', '新竹縣', '苗栗縣', '臺中市', '彰化縣', '南投縣', '雲林縣', '嘉義市', '嘉義縣',
            '臺南市', '高雄市', '屏東縣', '花蓮縣', '臺東縣', '澎湖縣', '金門縣', '連江縣', '\u5b9c\u862d縣']

def split(area):
    """「臺北市大安區」→（臺北市, 大安區）；「台北市大安區」也認得；只寫區名 →（'', 區名）。"""
    a = (area or '').strip().replace('台', '臺')
    county = next((c for c in COUNTIES if a.startswith(c)), '')
    town = a[len(county):] if county else a
    return county, town.replace('全區', '')

def county_of(area):
    return split(area)[0]

def covers(regions, area):
    """夥伴的服務區域文字是否涵蓋這個地點（鄉鎮區相符、該縣市全區、或「全台／都可以」）。"""
    text = (regions or '').replace('台', '臺')
    county, town = split(area)
    if not county and not town:
        return False
    if county and county + '全區' in text:
        return True
    if re.search(r'全台|全臺|都可|遠近', text) or (county and re.search(county + r'(?![^\s、,，;；]*[市鎮鄉區])', text)):
        return True
    key = re.sub(r'[市鎮鄉區]$', '', town)
    if not key:
        return False
    for part in re.split(r'[、,，;；\s/]+', text):
        p_c, p_t = split(part)
        if p_c and county and p_c != county:
            continue
        if re.sub(r'[市鎮鄉區]$', '', p_t) == key:
            return True
    return False

def service_counties(con, brand='demoshop'):
    """這個品牌直接服務的縣市（示範版預設全台）；範圍外的客戶也收，但要先說明會幫忙找配合的夥伴。"""
    import integrations as ix
    try:
        v = json.loads(ix.setting(con, f'service_area_{brand}', '') or '[]')
    except ValueError:
        v = []
    return v or list(COUNTIES)

def outside(con, brand, area):
    c = county_of(area)
    return bool(c) and c not in service_counties(con, brand)
