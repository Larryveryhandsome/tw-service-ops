"""合作夥伴應徵表（照《示範服務店｜合作夥伴應徵表》完整版；管理層看了才判斷得出來）。

這裡是唯一的題目設定：應徵頁照它出題（web/apply.js）、伺服器照它檢查必填（validate）、管理層照它看答案（view／short_rows）。
題目或選項要改，只改這裡。
"""
OTHER = '其他'

def _q(k, no, label, typ, opts=None, req=True, **kw):
    return {'k': k, 'no': no, 'label': label, 'type': typ, 'opts': opts or [], 'req': req, **kw}

FORM = [
    {'title': '第一部分｜基本資料', 'qs': [
        _q('role', '01', '請選擇您要應徵的職缺', 'one', ['服務人員', '家庭管家', '陪玩員', '整理師']),
        _q('name', '02', '請填寫您的本名', 'text'),
        _q('phone', '03', '請填寫您的聯絡電話（手機號碼）', 'tel'),
        _q('regions', '04', '您可以接案的地區有哪些？（縣市、行政區可複選）', 'regions'),
        _q('transport', '05', '您的主要交通方式是？', 'one', ['機車', '汽車', '大眾運輸', OTHER], other=OTHER, other_label='請說明交通方式'),
        _q('gender', '05-1', '您的性別（業主有指定時才會參考）', 'one', ['女', '男', '不想說'], req=False),
        _q('tools_own', '05-2', '可以自備哪些工具？（可複選；派工會用來對照現場缺什麼）', 'many', ['基本工具組', '專業工具', '量測工具', '延長線', '手機／平板', '耗材', '防護用品', '推車', '收納箱', '都沒有'], req=False),
        _q('willing', '05-3', '希望接多少工作？', 'one', ['越多越好', '一週 2–3 天', '偶爾有空再接'], req=False),
        _q('has_exp', '06', '是否具備相關工作經驗？', 'one', ['有', '無']),
        _q('years', '07', '相關工作年資為何？', 'one', ['無相關經驗', '未滿1年', '1～3年', '3～5年', '5年以上']),
        _q('cert', '08', '是否持有相關證照或接受過專業訓練？', 'one', ['有', '無'], other='有', other_label='請填寫證照或課程名稱')]},
    {'title': '第二部分｜工作經驗與作品集', 'qs': [
        _q('exp_desc', '09', '請簡單介紹您過去相關的工作經驗。', 'long', hint='可介紹曾經從事的工作、服務內容、工作年資或相關能力。若沒有工作經驗，請填寫「尚無相關工作經驗」。'),
        _q('served', '10', '您曾經服務過哪些客戶或場域類型？（可複選）', 'many',
           ['一般住家／家庭', '親子家庭／兒童照顧', '辦公室／商業空間', '店家／旅宿', '社區／公共區域', OTHER, '尚無相關經驗'], other=OTHER, other_label='請說明其他場域'),
        _q('portfolio', '11', '請提供您的相關作品集（選填）', 'photos', req=False, kind='portfolio', max=8, link='portfolio_link',
           hint='可以直接上傳工作成果照片，也可以提供雲端作品集連結。服務人員：服務前後對比、工作成果；家庭管家：居家整理、收納及家務管理成果；陪玩員：活動企劃、教案、手作作品等。請勿提供未經同意的客戶資料、兒童正面照片或私人住宅資訊。'),
        _q('ability', '12', '如果沒有作品集，請簡述您的能力或實際經驗。', 'long', req=False, req_if_no_portfolio=True, hint='若第 11 題沒有提供任何作品集，請在此說明自己具備的技能、經驗或服務優勢。'),
        _q('feedback', '13', '是否曾獲得客戶好評、推薦或正面回饋？', 'one', ['有', '無'], photos_if='有', kind='feedback', max=6,
           hint='若選擇「有」，請直接附上客戶好評、推薦對話或其他相關紀錄的截圖（必須上傳）。請先遮蔽不必要的客戶個人資料。'),
        _q('specialty', '14', '是否有特殊專長或擅長的服務項目？（選填）', 'long', req=False, hint='例如：整理收納、精細服務、幼兒互動、手作活動、烹飪、寵物照顧等。')]},
    {'title': '第三部分｜服務人員專屬問題', 'role': ['服務人員'], 'qs': [
        _q('skills', '', '想做哪些服務項目？（可複選，派工會優先找你）', 'many', ['一般到府服務', '年度保養／細清', '進階專案', '搬遷服務', '定期駐點服務', '店面／辦公室', '收納整理']),
        _q('a1', 'A1', '是否可接受依工作規範完成服務、回傳完工紀錄？', 'one', ['可以配合', '無法配合'])]},
    {'title': '第四部分｜陪玩員專屬問題', 'role': ['陪玩員'], 'qs': [
        _q('b1', 'B1', '是否有陪伴或照顧兒童的相關經驗？', 'one', ['有', '無']),
        _q('b2', 'B2', '曾接觸哪些年齡層的兒童？（可複選）', 'many', ['0～2歲', '3～5歲', '6～12歲', '13歲以上', '尚無相關經驗']),
        _q('b3', 'B3', '擅長哪些陪玩活動？（可複選）', 'many', ['繪本故事', '美術／手作', '益智遊戲／桌遊', '音樂／律動', '戶外活動', '學習陪伴', OTHER, '尚無相關經驗'], other=OTHER, other_label='請說明其他活動'),
        _q('b4', 'B4', '是否有幼教、保母、兒童急救等相關資格？', 'one', ['有', '無'], other='有', other_label='請填寫資格名稱'),
        _q('b5', 'B5', '請舉例說明曾經設計或帶領的陪玩活動。', 'long', hint='若尚無帶領活動的經驗，請填寫「尚無相關經驗」。'),
        _q('b6', 'B6', '若孩子哭鬧、不願配合或發生意外，你會如何處理？', 'long', hint='請依照您的實際經驗與想法回答。'),
        _q('b7', 'B7', '是否願意於後續甄選階段配合兒童安全相關資格及背景查核？', 'one', ['願意', '不願意'])]},
    {'title': '第五部分｜家庭管家專屬問題', 'role': ['家庭管家'], 'qs': [
        _q('c1', 'C1', '是否有居家管理、家務服務或私人管家經驗？', 'one', ['有', '無']),
        _q('c2', 'C2', '您可以提供哪些服務？（可複選）', 'many', ['一般到府服務', '整理收納', '衣物洗滌／熨燙／管理', '生活採買', '簡易料理', '家庭日常事務協助', OTHER], other=OTHER, other_label='請說明其他服務'),
        _q('c3', 'C3', '是否具備烹飪能力？擅長哪些料理？', 'one', ['具備烹飪能力', '不具備烹飪能力'], other='具備烹飪能力', other_label='請說明擅長的料理'),
        _q('c4', 'C4', '是否有整理收納的實際作品？', 'one', ['有（可在第 11 題補充上傳作品照片）', '無']),
        _q('c5', 'C5', '是否能依照客戶家庭習慣規劃家務流程？', 'one', ['可以', '無法配合']),
        _q('c6', 'C6', '是否具備其他能力？（可複選）', 'many', ['寵物照顧', '活動安排', '行政協助', '生活行程規劃', OTHER, '無其他特殊能力'], other=OTHER, other_label='請說明其他能力')]},
    {'title': '第六部分｜良民證確認', 'role': ['家庭管家', '陪玩員'], 'qs': [
        _q('r1', 'R1', '是否可以提供警察刑事紀錄證明（良民證）？', 'one', ['可以，目前已有良民證', '目前尚未申請，但願意配合辦理', '無法提供'],
           hint='家庭管家及陪玩員的正式合作條件包含提供良民證。尚未申請者仍可先送出應徵資料，但在正式合作媒合前必須完成文件審核。完整文件將於後續以適當的安全方式收取。')]},
    {'title': '面試與時間（選填）', 'qs': [
        _q('avail', '', '通常哪些時段可以工作？（可複選）', 'many', ['平日上午', '平日下午', '假日上午', '假日下午', '晚上', '都可以'], req=False),
        _q('iv_time', '', '電話面試（約 15 分鐘）哪些時間方便？（可複選）', 'many', ['平日白天', '平日晚上', '假日白天', '假日晚上'], req=False)]},
]
INTRO = ('歡迎加入示範服務店！目前招募職缺：服務人員、家庭管家、陪玩員。服務地區涵蓋全台，採案件合作方式進行媒合。'
         '請依照實際情況填寫，資料將用於應徵資格審核與後續聯繫。完成填寫不代表已正式錄取或保證有固定案件。')

def sections(role):
    return [s for s in FORM if not s.get('role') or role in s['role']]

def _txt(v, n=1500):
    return str(v or '').strip()[:n]

def validate(ans, counts):
    """ans：應徵頁送來的答案 {k: 字串或清單, k_other: 說明, portfolio_link}；counts：{portfolio: n, feedback: n}（已上傳的照片）。
    回傳整理好的 app（給管理層看）；缺必填就丟 ValueError（講哪一題）。"""
    role = ans.get('role') if ans.get('role') in ('服務人員', '家庭管家', '陪玩員', '整理師') else ''          # 稽核 H1：整理師以前送不出
    if not role:
        raise ValueError('01｜請選擇您要應徵的職缺')
    app = {}
    for s in sections(role):
        for q in s['qs']:
            k, t, tag = q['k'], q['type'], (q['no'] + '｜' if q['no'] else '')
            if t == 'photos':
                link = _txt(ans.get(q['link']), 300)
                if link:
                    app[q['link']] = link
                n = int(counts.get(q['kind'], 0))
                if n:
                    app[k] = f'已上傳 {n} 張照片'
                continue
            v = ans.get(k)
            if t == 'many':
                vals = [x for x in (v if isinstance(v, list) else str(v or '').split('、')) if x in q['opts']]
                if q.get('other') in vals:
                    o = _txt(ans.get(k + '_other'), 200)
                    if not o:
                        raise ValueError(f"{tag}{q['other_label']}")
                    vals = [f'{x}：{o}' if x == q['other'] else x for x in vals]
                val = '、'.join(vals)
            elif t == 'one':
                val = v if v in q['opts'] else ''
                if val and q.get('other') == val:
                    o = _txt(ans.get(k + '_other'), 200)
                    if not o:
                        raise ValueError(f"{tag}{q['other_label']}")
                    val = f'{val}：{o}'
            else:
                val = _txt(v, 400 if t in ('text', 'tel', 'regions') else 1500)
            if not val and (q['req'] or (q.get('req_if_no_portfolio') and not counts.get('portfolio') and not app.get('portfolio_link'))):
                raise ValueError(f"{tag}{q['label']}" + ('（第 11 題沒有作品集時必填）' if q.get('req_if_no_portfolio') else '（必填）'))
            if q.get('photos_if') and str(val).startswith(q['photos_if']):
                n = int(counts.get(q['kind'], 0))
                if not n:
                    raise ValueError(f"{tag}選「{q['photos_if']}」請上傳至少 1 張截圖（請先遮蔽客戶個資）")
                val = f'{val}（已上傳 {n} 張截圖）'
            if val:
                app[k] = val
    # 06／07 前後要對得上（避免隨手點錯，管理層看了矛盾）
    if app.get('has_exp') == '有' and app.get('years') == '無相關經驗':
        raise ValueError('07｜06 選「有」相關經驗，年資請選實際的年數')
    if app.get('has_exp') == '無' and app.get('years') not in (None, '無相關經驗'):
        raise ValueError('07｜06 選「無」相關經驗，年資請選「無相關經驗」')
    return app

def view(app):
    """管理層看：[(段落, [(題號＋題目, 答案)])]，只列有回答的。舊格式（版、LINE 報到）也照樣顯示。"""
    out, seen = [], set()
    for s in sections(app.get('role') or '服務人員') + [s for s in FORM if s.get('role') and (app.get('role') or '服務人員') not in s['role']]:
        rows = []
        for q in s['qs']:
            if q['type'] == 'photos' and app.get(q['link']):
                rows.append((f"{q['no']}｜作品集連結", app[q['link']])); seen.add(q['link'])
            if app.get(q['k']):
                rows.append(((q['no'] + '｜' if q['no'] else '') + q['label'], app[q['k']])); seen.add(q['k'])
        if rows:
            out.append((s['title'], rows))
    OLD = {'regions': '可接案地區', 'skills': '想做的項目', 'availability': '可做時段', 'portfolio_text': '作品集說明', 'avail': '可做時段', 'days': '每週可做', 'start': '可以開始',
           'exp': '經驗', 'exp_types': '做過', 'abilities': '可以接受', 'intro': '自我介紹'}
    rest = [(OLD.get(k, k), v) for k, v in app.items() if k not in seen and v and k not in ('name', 'phone')]
    if rest:
        out.append(('其他（舊版表單／LINE 報到）', rest))
    return out

def short_rows(app, n=8):
    """派單管理群卡片：最重要的幾題。"""
    keys = ['role', 'regions', 'has_exp', 'years', 'cert', 'served', 'feedback', 'portfolio', 'skills', 'a1', 'b1', 'b4', 'b7', 'c1', 'c3', 'r1', 'exp_desc']
    labels = {'role': '應徵', 'regions': '地區', 'has_exp': '相關經驗', 'years': '年資', 'cert': '證照／訓練', 'served': '服務過', 'feedback': '客戶好評', 'portfolio': '作品集',
              'skills': '想做', 'a1': '照規範回傳', 'b1': '兒童經驗', 'b4': '幼教／急救資格', 'b7': '願意背景查核', 'c1': '管家經驗', 'c3': '烹飪', 'r1': '良民證', 'exp_desc': '經驗介紹'}
    return [(labels[k], str(app[k])[:80]) for k in keys if app.get(k)][:n]

def complete(app):
    """這份應徵資料是不是完整版（有第 06 題以後的答案）。"""
    return bool(app.get('has_exp') and app.get('exp_desc'))
