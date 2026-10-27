"""店長的回答自動生效（「其他工作都繼續進行，做完他」）。

她在 LINE 點選回答後（line_bot._staff_answer），這裡把能自動套用的直接套上，套不了的通知負責人：
- Google評論連結：貼的連結 → 示範服務店報價／評論設定 review_url（客人給 4–5 星會被請去寫評論）。
- 日曆隱私：calendar_privacy（nameless＝標題不放姓名、地址只到鄉鎮）；選「每季換連結」→ 每季提醒負責人換 Apps Script 網址。
- 現場估價費：visit_fee_rule（deduct_charge／deduct_free／charge／free）→ 報價單傳出時自動加「現場估價費折抵」；免費就不記費用。
- 取消車馬費：cancel_subsidy（24h_200／day_200／day_half／none）→ 客戶取消時自動給「取消時已接單」的夥伴補貼（核定金額），進月結與薪資。
"""
import json, re
import datetime as dt
import core as c
import integrations as ix

VISIT = {'成交折抵，沒成交照收': 'deduct_charge', '成交折抵，沒成交免收': 'deduct_free', '一律照收不折抵': 'charge', '一律免費': 'free'}
SUBSIDY = {'24小時內取消補200': '24h_200', '當天取消補200': 'day_200', '當天取消補半天工資': 'day_half', '不補，看情況再說': 'none'}

def apply(con, topic, ans, who):
    """回傳一句說明（套用了什麼）；空字串＝這題沒有自動套用。"""
    import booking, pricing
    if topic in ('日常服務最少時數', '日常服務規模時數'):        # 生活線日常服務估價（某客戶乙 17 單位填 3 小時 → 實際 4 小時）
        k = pricing.key('pricing', 'homeline'); cfg = json.loads(ix.setting(con, k) or '{}')
        if topic == '日常服務最少時數':
            n = re.findall(r'\d+(?:\.\d+)?', ans)
            if not n:
                return ''
            cfg['homeline_min_hours'] = float(n[0]); ix.set_setting(con, k, json.dumps(cfg, ensure_ascii=False))
            return f"生活線日常服務最少 {float(n[0]):g} 小時（估價表單、初估都改了；記得也改官方 LINE 常見問答）"
        pairs = re.findall(r'(\d+(?:\.\d+)?)\s*[:：]\s*(\d+(?:\.\d+)?)', ans)
        if pairs:
            cfg['homeline_hours_by_size'] = ','.join(f'{a}:{b}' for a, b in pairs)
        elif '照' not in ans:
            return ''
        cfg.setdefault('homeline_hours_by_size', pricing.DEFAULT['homeline_hours_by_size']); ix.set_setting(con, k, json.dumps(cfg, ensure_ascii=False))
        return f"日常服務規模時數：{cfg['homeline_hours_by_size']}（規模上限:小時；超過最後一格 2 人 × 4 小時起）"
    if topic.startswith('減人 '):                      # 工單人數比報價少，店長回答可不可以
        import crewguard; return crewguard.apply(con, topic.split(' ', 1)[1].strip(), ans, who)
    if topic.startswith('其他方式原因 '):              # 報價「已用其他方式給客戶」的原因 → 記錄＋功能回報（晚上檢查能不能下次全自動）
        import quickquote
        try:
            return quickquote.manual_reason(con, topic.split(' ', 1)[1].strip(), ans, who)
        except ValueError:
            return ''
    if topic.startswith('館別'):                       # 多館業主某幾場是哪一館（店長確認）→ 直接改工單館別、通知夥伴新地址
        ctx = json.loads(ix.setting(con, f'house_ask:{topic}') or '{}')
        if not ctx:
            return ''
        import line_bot as lb
        done = []
        for m, d, house in re.findall(r'(\d{1,2})/(\d{1,2})[^\d/]{0,6}?(\d+\s*號館)', ans):
            day = f"{ctx['year']}-{int(m):02d}-{int(d):02d}"
            o = con.execute("SELECT id,site_id FROM orders WHERE customer_id=? AND date=? AND status IN ('review','draft','confirmed')", (ctx['cid'], day)).fetchone()
            s = next((x for x in lb.multi_sites(con, ctx['cid']) if x['name'].replace(' ', '') == house.replace(' ', '')), None)
            if o and s and o['site_id'] != s['id']:
                lb.set_house(con, o['id'], s['id'], f'店長確認（{who}）'); done.append(f"{m}/{d} → {s['name']}")
        return ('已改館別：' + '、'.join(done) + '（夥伴收到新地址）') if done else '館別照系統原本的，不用改'
    if topic.startswith('服務範圍'):                   # 業主傳的服務範圍要不要寫進案場服務重點（夥伴工作卡看得到）
        ctx = json.loads(ix.setting(con, f'scope_ask:{topic}') or '{}')
        if not ctx or '先不要' in ans:
            return '好，先不寫進服務重點' if ctx else ''
        for sid in ctx['sites']:
            s = c.get(con, 'sites', sid)
            if ctx['text'] not in (s['checklist'] or ''):
                c.save(con, 'sites', {'id': sid, 'version': s['version'], 'checklist': ((s['checklist'] or '') + '\n' + ctx['text']).strip()[:800]})
        return f"已寫進 {len(ctx['sites'])} 館的服務重點（夥伴工作卡看得到）"
    if topic == 'Google評論連結':
        m = re.search(r'https?://\S+', ans)
        if not m:
            return ''
        co = json.loads(ix.setting(con, pricing.key('company', 'demoshop'), '{}') or '{}'); co['review_url'] = m.group(0)[:300]
        ix.set_setting(con, pricing.key('company', 'demoshop'), json.dumps(co, ensure_ascii=False))
        return '已設定：客人給 4–5 星時會請他到這個連結寫 Google 評論'
    if topic == '日曆隱私':
        mode = 'nameless' if '不放姓名' in ans else 'full'
        ix.set_setting(con, 'calendar_privacy', mode); ix.set_setting(con, 'calendar_rotate', '1' if '換連結' in ans else '')
        return '已套用：日曆標題不放客戶姓名、地址只到鄉鎮' + ('；每季會提醒負責人換日曆同步網址' if '換連結' in ans else '') if mode == 'nameless' else '已套用：日曆顯示客戶姓名與完整地址'
    if topic == '現場回報通知業主':                  # 店長決定：夥伴現場回報要不要通知業主（site_report.OWNER_RULES）
        import site_report
        return site_report.apply_rule(con, ans)
    if topic in ('現場加時收費', '時數順延'):           # 店長決定：加時收費、沒做到的時數怎麼補（顯示在現場回報卡）
        import site_report
        return site_report.apply_money(con, topic, ans)
    if topic == '現場估價費' and ans in VISIT:
        ix.set_setting(con, 'visit_fee_rule', VISIT[ans])
        return f'已套用現場估價費規則：{ans}'
    if topic == '客服電話與Email':                    # 商店資訊頁（LINE Pay 審核）要的聯絡方式
        ph = re.search(r'0\d[\d\- ]{7,12}\d', ans); em = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', ans)
        if ph:
            ix.set_setting(con, 'store_phone', ph.group(0).strip())
        if em:
            ix.set_setting(con, 'store_email', em.group(0))
        return '已放上商店資訊頁：' + '、'.join(x for x in ((ph and ph.group(0)), (em and em.group(0))) if x) if (ph or em) else ''
    if topic == 'LINE 訊息保留':                        # 客戶在 LINE 打的原文保留多久（automation.retention 每天清）
        m = re.search(r'(\d+)\s*(天|個月|年)', ans)
        days = 0 if '不' in ans and '清' in ans else (int(m.group(1)) * {'天': 1, '個月': 30, '年': 365}[m.group(2)] if m else 0)
        ix.set_setting(con, 'inbox_keep_days', str(days))
        return f'已設定：LINE 訊息原文保留 {days} 天，超過自動清空（只留時間和次數）' if days else '已設定：LINE 訊息先不自動清'
    if topic == 'LINE 正式發送':                       # 企業線／生活線官方 LINE 從測試改正式（測試模式：客戶、夥伴都收不到，只有測試名單和管理群收得到）
        on = [] if ('維持' in ans or '測試' in ans or '不' in ans) else ['homeline', 'bizline'] if '都' in ans else [b for b, n in (('homeline', '生活線'), ('bizline', '企業線')) if n in ans]
        for b in on:
            ix.set_setting(con, f'line_mode_{b}', 'on')
            held = ix.setting(con, f'bind_bc_hold:{b}')
            if held:                                       # 已核准的「請綁定」群發：改正式後自動發
                import broadcast
                broadcast.execute_bind(con, json.loads(held), json.loads(held).get('by', who))
        return ('已改成正式發送：' + '、'.join({'homeline': '生活小幫手', 'bizline': '企業服務線'}[b] for b in on) + '（之前沒傳到的完成照片會自動補傳）') if on else ''
    if topic == '夥伴提醒方式':                         # prep.py：案件前要不要問她補充提醒
        mode = 'all' if '每場' in ans else 'new' if '第一次' in ans else 'off' if '不用' in ans else ''
        if mode:
            ix.set_setting(con, 'prep_mode', mode)
            return {'all': '已設定：每天 15:00 一張卡問妳明天每場要不要補充提醒', 'new': '已設定：只有第一次去的案場，前一天 15:00 問妳', 'off': '已設定：不問，妳自己傳'}[mode]
        return ''
    if topic == '商城價格':                           # shop：「水垢 350 夥伴 280、油垢 350 夥伴 280、磁磚 380 夥伴 300」
        import shop
        shop.ensure(con); done = []
        for key, pid in (('水垢', 'shp-scale'), ('油垢', 'shp-grease'), ('磁磚', 'shp-tile')):
            m = re.search(key + r'[^\d]{0,12}(\d{2,5})(?:[^\d、，,；;]{0,8}?夥伴[^\d]{0,6}(\d{2,5}))?', ans)
            if m:
                shop.set_product(con, pid, {'price': m.group(1), 'worker_price': m.group(2) or '', 'active': True}, who)
                done.append(f"{key} {m.group(1)}" + (f"（夥伴 {m.group(2)}）" if m.group(2) else ''))
        return ('已上架：' + '、'.join(done) + '。客人、夥伴在官方 LINE 打「商城」就能點選購買') if done else ''
    if topic == '招募個資':                           # ①告知補良民證、作品集照片 ②不錄取的應徵資料幾天後刪
        add = '補告知' in ans or '補' in ans
        days = 90 if '90' in ans else 180 if '180' in ans else 0
        if '都先不要' in ans:
            add, days = False, 0
        ix.set_setting(con, 'privacy_recruit', '1' if add else '0'); ix.set_setting(con, 'applicant_retention_days', str(days))
        return ('個資告知已補上良民證、作品集照片' if add else '個資告知不變') + '；' + (f'不錄取的應徵資料 {days} 天後自動刪除（良民證、作品集照片、應徵表內容）' if days else '不自動刪除應徵資料')
    if topic == '舊應徵者再應徵':                     # 被婉拒過的人再來應徵
        if '直接' in ans or '新的' in ans:
            ix.set_setting(con, 'reapply_mode', 'screen'); return '已設定：被婉拒過的人再應徵 → 直接建新的審資料卡（附上次婉拒原因），身分照樣請管理群確認綁定'
        if '維持' in ans:
            ix.set_setting(con, 'reapply_mode', 'bind'); return '維持：先確認綁定，再由管理層處理'
        return ''
    if topic == '招募實作關':                         # 招募「實作」那一關（良民證之後、訓練之前）要不要留
        v = 'skip' if ('拿掉' in ans or '全部免' in ans) else 'exp' if '有經驗' in ans else 'keep' if ('保留' in ans or '都要' in ans) else ''
        if not v:
            return ''
        ix.set_setting(con, 'recruit_trial', v)
        return {'keep': '維持：每位應徵者都要實作', 'exp': '已設定：有相關經驗的應徵者免實作（系統自動跳過，沒經驗的照樣要實作）',
                'skip': '已設定：招募流程拿掉實作（良民證／面試後直接到訓練或試派案）'}[v]
    if topic == '忘了拍服務前照片':                   # 夥伴忘了拍服務前照片（forgot_before：reason＝寫原因可離場，薪資等管理層確認）
        if '開放' in ans or '寫原因' in ans:
            ix.set_setting(con, 'forgot_before', 'reason')
            return '已開放：夥伴在工作頁寫原因就能拍完成照片、離場打卡；管理群收到卡片，按「確認，照常發薪」後這場薪資才發'
        if '維持' in ans:
            ix.set_setting(con, 'forgot_before', '')
            return '維持：忘了拍服務前照片一律請派單同事處理（薪資暫不發）'
        return ''
    if topic == '缺人完工怎麼算':                     # 兩人的工作只到 1 人（short_crew_rule：full／ratio／ask）
        v = 'full' if '原價' in ans else 'ratio' if '比例' in ans else 'ask' if ('看狀況' in ans or '我決定' in ans) else ''
        if not v:
            return ''
        ix.set_setting(con, 'short_crew_rule', v)
        return {'full': '已設定：缺人完工 → 客戶照原價、夥伴照實際工時（群組只通知，不用處理）',
                'ratio': '已設定：缺人完工 → 客戶照到場人數比例少收（帳單自動扣；已預收的提醒退款或抵下次）、夥伴照實際工時',
                'ask': '維持：缺人完工每次在群組請管理層決定'}[v]
    if topic == '訂閱月費改期取消':                   # 訂閱客戶某一場取消（sub_cancel_rule：keep／deduct／ask）
        v = 'keep' if '照收' in ans else 'deduct' if '扣' in ans else 'ask' if ('看狀況' in ans or '我決定' in ans) else ''
        if not v:
            return ''
        ix.set_setting(con, 'sub_cancel_rule', v)
        return {'keep': '已設定：訂閱的場次取消，月費照收（不變）', 'deduct': '已設定：訂閱的場次取消 → 這個月的月費照少掉的次數自動扣（已付的提醒退款或抵下次）',
                'ask': '維持：訂閱的場次取消，每次在群組請管理層決定月費'}[v]
    if topic == '綁定連結傳給別人':                   # 客戶專屬綁定連結有沒有被轉傳給別的客戶（被問的那位客戶 id 記在 settings shared_link_ask）
        SHARED_LINK_CUSTOMER = ix.setting(con, 'shared_link_ask')
        if '沒有' in ans:
            return '好的：每位客戶自己的綁定連結都有效（14 天），客戶送出就完成'
        live = c.rows(con, "SELECT b.code,b.ref_id,cu.name FROM line_bind b JOIN customers cu ON cu.id=b.ref_id WHERE b.kind='customer' AND b.expires>?", (int(__import__('time').time()),))
        hit = [x for x in live if x['ref_id'] == SHARED_LINK_CUSTOMER] + [x for x in live if x['ref_id'] != SHARED_LINK_CUSTOMER and len(x['name'] or '') >= 2
                                                           and (f"{x['name']}的連結" in ans or f"{x['name']}那條" in ans or f"{x['name']}的那條" in ans)]      # 稽核 L10：寫收件人名字不會誤作廢
        out = []
        for x in hit:
            con.execute('DELETE FROM line_bind WHERE code=?', (x['code'],))
            c.audit(con, 'bind_code_void', 'customers', x['ref_id'], after={'by': who, 'why': '同一條傳給多位客戶'})
            out.append(x['name'])
        bound = [(x, con.execute('SELECT display_name FROM line_users WHERE customer_id=? AND is_friend=1', (x,)).fetchone()) for x in ([h['ref_id'] for h in hit] or [SHARED_LINK_CUSTOMER])]
        warn = ''.join(f"\n⚠️ 已經有 LINE「{b[0] or '（沒有名稱）'}」綁成「{c.get(con, 'customers', cid)['name']}」：請確認是不是本人，不是的話到中控台「更多 › LINE 配對」按「解除」" for cid, b in bound if b)
        return (f"已作廢「{'、'.join(out)}」的綁定連結（別人再按會顯示已過期）。本人請重新到中控台客戶資料按「複製綁定訊息」（會是新的連結）；其他客戶請用「通用綁定訊息」" if out
                else '那條連結已經失效（用過或過期），不用處理') + warn
    if topic == '同範圍自動通過':                     # 負責人說問店長
        if '不' in ans:
            ix.set_setting(con, 'scope_auto_same', '0'); return '維持：報價單的詳細需求每張都要管理層審核'
        if '好' in ans or '自動' in ans:
            ix.set_setting(con, 'scope_auto_same', '1'); return '已設定：同一個案場、範圍和備註跟上次審核過的一樣 → 自動通過，不用再按'
        return ''
    if topic == '已排工作問業主工具':                 # 負責人說這個店長決定
        import sitetools
        if '不' in ans:
            return '好，不問；這些工作的夥伴卡維持「請自備工具」（新的單照常在派工前問）'
        if '問' in ans or '好' in ans:
            names = sitetools.ask_pending(con)
            return f"已用 LINE 問 {len(names)} 位業主（{'、'.join(names)}）；回覆後記在案場，已排的夥伴會收到更新" if names else '沒有要問的了（都已經有答案，或業主沒綁 LINE）'
        return ''
    if topic.startswith('估價練習 '):                 # 店長 30 天估價練習（答案先不公布）
        import estquiz
        return estquiz.record(con, topic, ans, who)
    if topic == '商城夥伴價':                         # 6 款上架後問店長夥伴價（「8 折」「跟客人一樣」或逐項）
        import shop
        return shop.set_worker_prices(con, ans, who)
    if topic == '商城取貨付款':
        ix.set_setting(con, 'shop_terms', ans[:200])
        return '已設定：結帳時會顯示這段取貨付款說明'
    if topic == '良民證代辦費用':                     # recruit.fee：0＝示範服務店吸收
        n = re.findall(r'\d+', ans)
        v = 0 if ('吸收' in ans or '不收' in ans or '免費' in ans) else sum(int(x) for x in n) if '＋' in ans or '+' in ans else int(n[-1]) if n else None
        if v is None:
            return ''
        ix.set_setting(con, 'police_fee', str(v))
        return f'已設定：良民證代辦收 {v} 元（夥伴可用 LINE Pay 或現場付）' if v else '已設定：良民證代辦免費（示範服務店吸收）'
    if topic == '面試方式':                           # interviews.mode
        ix.set_setting(con, 'interview_mode', ans[:60])
        return f'已設定面試方式：{ans[:60]}（之後邀約面試都會這樣寫）'
    if topic == '面試時段':                           # interviews.slots：「平日 10:00、14:00、16:00」
        times = sorted({f'{int(h):02d}:{m}' for h, m in re.findall(r'(\d{1,2})[:：](\d{2})', ans)})
        if not times:
            return ''
        ix.set_setting(con, 'interview_slots', json.dumps(times))
        ix.set_setting(con, 'interview_days', '1234567' if '每天' in ans else '123456' if '週六' in ans or '六' in ans else '12345')
        return f"已設定面試時段：{'每天' if '每天' in ans else '平日'}" + '、'.join(times)
    if topic == '面試前接單':                         # 應徵者要不要面試錄取後才能接單
        need = '錄取' in ans or '面試後' in ans
        ix.set_setting(con, 'interview_required', '1' if need else '0')
        return '已設定：應徵者面試錄取後才會派工' if need else '已設定：應徵者應徵完就可以接單，面試補做'
    if topic == '業主沒按確認完工':                   # automation.accept_chase：幾天沒回覆視為驗收
        n = re.findall(r'\d+', ans)
        d = 0 if ('不' in ans and not n) else int(n[0]) if n else None
        if d is None:
            return ''
        ix.set_setting(con, 'auto_accept_days', str(d))
        return f'已設定：業主收到完成照片 {d} 天沒回覆就視為驗收（隔天會先自動提醒一次）' if d else '已設定：不自動視為驗收，只在隔天自動提醒一次'
    if topic.startswith('補登工時'):                   # 夥伴沒打卡自己補登 → 店長確認或打正確時間（worker_portal.ask_hours）
        return fix_hours(con, ix.setting(con, f'askref:{topic}'), ans, who)
    if topic == '同天兩場間隔':                       # 同一位夥伴同一天 A 班、B 班：中間至少隔多久（同鄉鎮／跨鄉鎮分開）
        g = gap_rule_from(ans)
        if g is None:
            return ''
        ix.set_setting(con, 'shift_gap', json.dumps(g))
        return '已設定：沒有固定間隔，間隔短的每次問妳趕不趕得上（妳的回答系統會記住，越問越少）' if not (g['same'] or g['cross']) else \
            f"已設定：同鄉鎮隔 {g['same']} 分、跨鄉鎮隔 {g['cross']} 分以上直接排；比這短的會問妳趕不趕得上（回答會記住，越問越少）"
    if topic.startswith('垃圾清收：'):                 # 新增垃圾清收，價格先定錨；多袋、上限、夥伴酬勞、開放品牌、清運方式問店長
        import garbage
        return garbage.apply_answer(con, topic, ans, who)
    if topic == '請假後沒選補人方式':                # （leave.auto_handle 看 leave_auto_mode）
        a = ans.strip()
        v = 'pressed' if a.startswith('A') else 'never' if a.startswith('B') else 'auto' if a.startswith('C') else None
        if v is None:
            return ''
        ix.set_setting(con, 'leave_auto_mode', v)
        return {'pressed': '已設定：請假卡有人按過任何按鈕＝管理層在處理，系統不自動派、只提醒選②；都沒人按才自動派',
                'never': '已設定：請假後一律不自動派，每 30 分鐘提醒派單管理群選②（最多 4 次）',
                'auto': '已設定：維持 30 分鐘沒人選就自動另外派工'}[v]
    if topic.startswith('夥伴升級 '):                  # D／C 跟 A／B 做過 2 人案場 → 店長確認升級（tiers.upgrade_check）
        import tiers
        return tiers.upgrade_answer(con, topic, ans, who)
    if topic.startswith('派工分級：'):               # abc／abcd 定義 → 派工改版（tiers.py 讀這些設定）
        a = ans.strip(); key = {'派工分級：客戶等級怎麼派': 'tier_route', '派工分級：一次問幾位': 'tier_batch', '派工分級：新人與升級': 'tier_rules'}.get(topic)
        if not key:
            return ''
        if key == 'tier_batch':
            v = '3' if a.startswith('A') else '5' if a.startswith('B') else '0' if a.startswith('C') else ''.join(ch for ch in a if ch.isdigit())[:2]
            if not v:
                return ''
        else:
            if not a.startswith('A'):
                return ''                              # 要改的（打字）→ 通知負責人照意見改
            v = '1'
        ix.set_setting(con, key, v)
        return '已記下，派工分級改版會照這個做'
    if topic == '付款時間：服務後結帳':              # 回報「照單改成服務後結帳」→ 管理群決定（payments.after_scope）
        a = ans.strip()
        v = 'amount' if ('一萬' in a or '10000' in a or '1萬' in a) else 'all' if (a.startswith('A') or '全部' in a) else 'single' if (a.startswith('B') or '單次' in a) else '' if (a.startswith('C') or '維持' in a) else None
        if v is None:
            return ''
        ix.set_setting(con, 'pay_after_scope', v)
        import payments, time as _t; payments._AS.update(v=v, t=_t.time())      # 直接換快取（還沒存檔的交易，其他連線讀不到）
        return {'all': '已設定：新的報價一律完工後 3 天內付清（不收訂金、不用預付；月訂閱照舊）',
                'single': '已設定：單次一般到府服務改完工後付；年度保養仍收 30% 訂金；月訂閱照舊', '': '維持：單次預付、年度保養訂金、月訂閱照舊',
                'amount': '已設定：一萬以下先結清（公司行號完工後付）；一萬以上先收 30% 訂金；管理層指定的付款方式、月訂閱照舊'}[v]
    if topic == '家庭管家要良民證嗎':               # （recruit.needs_police 看 police_roles）
        both = ans.strip().startswith('A') or ('也要' in ans and '只有' not in ans)
        ix.set_setting(con, 'police_roles', '陪玩,管家' if both else '陪玩')
        return '已設定：陪玩員、家庭管家都要良民證' if both else '已設定：只有陪玩員要良民證'
    if topic == HOURLY_TOPIC:                      # 陪玩陪讀、私人管家開放線上預約（要有每小時價格才能啟用）
        return hourly_lines(con, ans, who)
    if topic == '取消車馬費' and ans in SUBSIDY:
        ix.set_setting(con, 'cancel_subsidy', SUBSIDY[ans])
        return f'已套用取消補貼規則：{ans}'
    return ''

HOURLY_TOPIC = '陪玩陪讀、私人管家開放線上預約'
HOURLY_TASK = 'tas-ed3cfd4baf78'

def hourly_lines(con, ans, who):
    """「先不要開」→ 維持關閉；否則讀出每小時價格（「兩個都開 400」「陪玩 400、管家 450」）→ 開通那條業務線。讀不出價格就不動、回空字串（負責人會看到答案）。"""
    import re, json, brands
    a = (ans or '').replace('，', ',').replace('、', ',')
    def close(res):
        if con.execute("SELECT 1 FROM tasks WHERE id=? AND status='open'", (HOURLY_TASK,)).fetchone():
            con.execute("UPDATE tasks SET status='resolved',resolution=?,version=version+1 WHERE id=?", (f'{who} 回答：{ans}｜{res}'[:500], HOURLY_TASK))
        return res
    if re.search(r'先不要|不開|暫不', a):
        return close('已記錄：陪玩陪讀、私人管家先不開放線上預約（維持關閉）')
    nums = [int(x) for x in re.findall(r'(\d{3,5})', a)]
    def near(word):
        m = re.search('(?:' + word + r')[^\d]{0,12}(\d{3,5})', a)
        return int(m.group(1)) if m else None
    rate = {'demoshop-play': near('陪玩|陪讀'), 'demoshop-butler': near('管家')}
    both = re.search(r'兩個|都開|全部', a)
    if both and len(set(nums)) == 1:
        rate = {k: v or nums[0] for k, v in rate.items()}
    if not any(rate.values()):
        return ''
    done = []
    for lid, r in rate.items():
        if not r:
            continue
        ln = brands.line(con, lid)
        if not ln:
            continue
        h = {**(ln.get('hourly') or {}), 'rate': r}
        con.execute('UPDATE business_lines SET hourly=?,active=1 WHERE id=?', (json.dumps(h, ensure_ascii=False), lid))
        c.audit(con, 'line_open', 'business_lines', lid, after={'rate': r, 'by': who})
        done.append(f"{ln['name']} 每小時 {r:,} 元（每位，最少 {h.get('min_hours', 2):g} 小時）")
    brands.reload(con)
    return close('已開放線上預約：' + '、'.join(done) + '。客人在官方 LINE「立即預約」選得到；報價一樣先給店長確認才傳客戶。')

GAP = {'同鄉鎮30分、跨鄉鎮60分': (30, 60), '一律至少30分': (30, 30), '一律至少60分': (60, 60), '不設限，系統標示我判斷': (0, 0)}

def short_crew(con, o, arrived):
    """缺人完工（例：兩人的工作只到 1 人）：照管理群決定的 short_crew_rule 處理。o＝完工前的工單。"""
    import line_bot, payments
    need = o['required_workers'] or 1
    head = f"⚠️ 這場只到 {arrived}/{need} 人就完工了"
    rule = ix.setting(con, 'short_crew_rule') or 'ask'
    if rule == 'full':
        line_bot.admin_notify(con, head, [('處理', '照規則：客戶照原價、夥伴照實際工時（不用處理）')], '', '#C47F00', oid=o['id'])
        return 'full'
    if rule == 'ratio':
        o2 = c.get(con, 'orders', o['id'])
        site = con.execute('SELECT visit_price FROM sites WHERE id=?', (o2['site_id'],)).fetchone() if o2['site_id'] else None
        base = o2['approved_amount'] if o2['approved_amount'] is not None else (site[0] if site and site[0] is not None else None)
        if base is None:                   # 照時數計費：帳單本來就照實際到場的人的工時算（自然少收）
            line_bot.admin_notify(con, head, [('處理', '照規則：照時數計費的單，帳單本來就只算實際到場的人（自動少收），不用處理')], '', '#C47F00', oid=o['id'])
            return 'ratio'
        cut = round(base * (need - arrived) / need)
        if payments.prepaid(con, o2):
            line_bot.admin_notify(con, head, [('處理', f"照規則要少收 NT$ {cut // 100:,}，但這場已經用收款單收過了（{payments.prepaid(con, o2)}）"),
                                              ('請做', '退款，或抵用下次服務（中控台「收款」）')], '', '#C62828', oid=o['id'], tag='缺人完工：要退款或抵用')
            return 'ratio'
        if '缺人' not in (o2['adjustment_reason'] or ''):
            c.CTX.actor = '系統（缺人完工規則）'
            c.save(con, 'orders', {'id': o2['id'], 'version': o2['version'], 'adjustment': (o2['adjustment'] or 0) - cut,
                                   'adjustment_reason': ((o2['adjustment_reason'] + '；') if o2['adjustment_reason'] else '') + f'缺人完工依比例少收（{arrived}/{need} 人）'})
        line_bot.admin_notify(con, head, [('處理', f"照規則：客戶照比例少收 NT$ {cut // 100:,}（已在帳單扣掉）、夥伴照實際工時")], '', '#C47F00', oid=o['id'])
        return 'ratio'
    line_bot.admin_notify(con, head, [('處理', '請確認客戶金額與夥伴薪資（還沒定規則：派單管理群「缺人完工怎麼算」那題回答後就自動照做）')], '', '#C47F00', oid=o['id'], tag='缺人完工請確認')
    return 'ask'

def _visits(con, r, mon, cid):
    """訂閱每月幾次：先看報價單（每月 N 次），沒有就數這個月排的場次（含剛取消的那場）。"""
    if r['quote_id']:
        q = con.execute('SELECT form FROM quotes WHERE id=?', (r['quote_id'],)).fetchone()
        m = re.search(r'每月\s*(\d+)\s*次', (q[0] if q else '') or '')
        if m and int(m.group(1)) > 0:
            return int(m.group(1))
    n = con.execute("SELECT COUNT(*) FROM orders WHERE customer_id=? AND date LIKE ? AND status NOT IN ('archived')", (cid, mon + '%')).fetchone()[0]
    return max(1, n)

def month_on_cancel(con, o):
    """訂閱客戶的場次取消：這個月的月費照管理群決定的 sub_cancel_rule（keep 照收／deduct 照少掉的次數扣／ask 每次問）。"""
    import line_bot as lb
    mon = (o.get('date') or '')[:7]
    if not mon:
        return ''
    rows = c.rows(con, "SELECT * FROM payment_requests WHERE kind='month' AND status!='cancelled' AND (order_id=? OR (customer_id=? AND label LIKE ?)) ORDER BY created_at",
                  (o['id'], o.get('customer_id') or '-', mon + '%'))
    if not rows:
        return ''
    r = rows[0]
    rule = ix.setting(con, 'sub_cancel_rule') or 'ask'
    amt = f"{r['label']} NT$ {r['amount'] // 100:,}"
    if rule == 'keep':
        lb.admin_notify(con, '💰 訂閱單的場次取消了：月費照收', [('款項', amt), ('規則', '管理群決定：取消的場次月費不變（不用處理）')], '', '#2E7D32', oid=o['id'])
        return 'keep'
    if rule == 'deduct':
        if con.execute("SELECT 1 FROM audit WHERE action='month_deduct' AND entity_id=?", (o['id'],)).fetchone():
            return 'deduct'                # 同一場不重複扣（取消後又封存）
        n = _visits(con, r, mon, o.get('customer_id') or '')
        per = r['amount'] // n
        c.audit(con, 'month_deduct', 'orders', o['id'], after={'payment': r['id'], 'per': per, 'visits': n})
        if r['status'] == 'pending':
            con.execute('UPDATE payment_requests SET amount=?,note=TRIM(COALESCE(note,\'\')||\' \'||?) WHERE id=?',
                        (max(0, r['amount'] - per), f"{o['date'][5:]} 取消扣 {per // 100}", r['id']))
            lb.admin_notify(con, '💰 訂閱單的場次取消了：月費已照次數扣', [('款項', f"{amt} → NT$ {max(0, r['amount'] - per) // 100:,}（每月 {n} 次，少 1 次）"),
                                                              ('規則', '管理群決定：照少掉的次數扣（不用處理）')], '', '#2E7D32', oid=o['id'])
        else:
            lb.admin_notify(con, '💰 訂閱單的場次取消了：月費已經付了', [('款項', f"{amt}（每月 {n} 次，少 1 次＝NT$ {per // 100:,}）"),
                                                             ('請做', '退 NT$ ' + f"{per // 100:,}" + '，或抵用下個月（中控台「收款」）')], '', '#C62828', oid=o['id'], tag='訂閱月費：要退款或抵用')
        return 'deduct'
    if r['status'] == 'pending':
        lb.admin_notify(con, '💰 取消的是訂閱單的場次：月費要不要照收？', [('款項', amt),
                                                              ('處理', '月費沒有自動作廢；請決定照收、少收或取消（中控台「收款」可改）。派單管理群「訂閱月費改期取消」那題回答後就自動照做')], '', '#C47F00', oid=o['id'], tag='訂閱月費請確認')
    return 'ask'

def pending(con):
    """還沒回答的題目卡（中控台「管理層要決定」）：[{topic, question, options, asked_at}]，新的在前。估價練習是店長自己的測驗，不列。"""
    answered = {r[0][7:] for r in con.execute("SELECT key FROM settings WHERE key LIKE 'answer:%'")}
    out = []
    for k, v in con.execute("SELECT key,value FROM settings WHERE key LIKE 'ask:%'"):
        t = k[4:]
        if t in answered or t.startswith('估價練習'):
            continue
        try:
            d = json.loads(v)
        except ValueError:
            continue
        out.append({'topic': t, 'question': d.get('question', ''), 'options': d.get('options') or [], 'asked_at': d.get('asked_at', '')})
    return sorted(out, key=lambda x: x['asked_at'], reverse=True)

def answer_from_web(con, topic, ans, who):
    """中控台回答題目卡：跟 LINE 一樣記錄＋自動生效，派單管理群看到誰答了（群組的卡就不用再按）。"""
    import line_bot as lb
    topic, ans = str(topic or '').strip(), str(ans or '').strip()[:300]
    if not ix.setting(con, f'ask:{topic}'):
        raise ValueError('找不到這一題')
    if ix.setting(con, f'answer:{topic}'):
        raise ValueError('這一題已經有人回答了')
    if not ans:
        raise ValueError('請選一個選項或寫下回答')
    done = lb.record_answer(con, topic, ans, who)
    g = lb.admin_group(con)
    if g:
        lb.queue(con, lb.WORK_ACCOUNT, g, 'admin', f'{who} 在中控台回答了：{topic}', lb._txt(f'✅ {who}（中控台）回答了【{topic}】：{ans}' + (f'\n→ {done}' if done else '') + '\n（群組那張卡不用再按了）'))
    return done

def gap_rule_from(ans):
    if ans in GAP:
        a, b = GAP[ans]; return {'same': a, 'cross': b}
    if '不設' in ans or '不限' in ans:
        return {'same': 0, 'cross': 0}
    ans = re.sub(r'(\d+)\s*個半小時', lambda m: f'{int(m.group(1)) * 60 + 30}分', ans).replace('半小時', '30分')
    ans = re.sub(r'(\d+)\s*小時\s*(\d+)\s*分',lambda m: f'{int(m.group(1)) * 60 + int(m.group(2))}分', ans)     # 1小時30分 → 90分
    ans = re.sub(r'(\d+(?:\.\d+)?)\s*(?:個)?小時', lambda m: f'{round(float(m.group(1)) * 60)}分', ans)
    n = [int(x) for x in re.findall(r'\d+', ans)]
    if not n:
        return None
    return {'same': n[0], 'cross': n[1] if len(n) > 1 else n[0]}

def gap_rule(con):
    """店長設定的同天兩場最少間隔（分鐘）；沒設定回 None（只標示、不擋）。"""
    try:
        g = json.loads(ix.setting(con, 'shift_gap', '') or 'null')
    except ValueError:
        return None
    return g if g and (g.get('same') or g.get('cross')) else None

def _times(s):
    out = []
    for h, mm, p in re.findall(r'(\d{1,2})(?:\s*[:：]\s*(\d{2})|\s*點\s*(半|\d{1,2})?)', s or ''):
        m = int(mm) if mm else 30 if p == '半' else int(p) if p else 0
        if int(h) < 24 and m < 60:
            out.append(f'{int(h):02d}:{m:02d}')
    return out

def fix_hours(con, aid, ans, who):
    if not aid:
        return ''
    a = c.get(con, 'assignments', aid); w = c.get(con, 'workers', a['worker_id']); o = c.get(con, 'orders', a['order_id'])
    ts = con.execute('SELECT start,end,pause FROM timesheets WHERE assignment_id=?', (aid,)).fetchone()
    t = _times(ans)
    if not t:
        return '已確認，照夥伴補登的時間算' if '對' in ans else ''
    st, en = (t[0], t[1]) if len(t) > 1 else ((ts['start'] if ts else o['start']), t[0])
    mins = c.minutes(st, en) - ((ts['pause'] or 0) if ts else 0)
    c.CTX.actor = who + '（LINE 確認補登工時）'
    c.save(con, 'assignments', {'id': aid, 'version': a['version'], 'approved_minutes': mins})
    import line_bot as lb
    m = lb.card_text('工時已確認', f"{lb.md(o['date'])} {o['title'][:14]}：經理確認你的工時 {st}–{en}（{mins / 60:g} 小時），月結照這個算 🙏", '#2E7D32')
    lb.to_worker(con, w['id'], 'hours_fixed', m[0]['altText'], m, o['id'])
    return f"已更正 {w['name']} 工時 {st}–{en}（{mins / 60:g} 小時），薪資照這個算，也通知他了"

GAP_DEFAULT = {'same': 60, 'cross': 90}        # 店長還沒定規則前：間隔比這短就問她「趕得上嗎？」

def _pair(a, b):
    return '|'.join(sorted([(a or '？')[:2], (b or '？')[:2]]))

def gap_verdict(con, area_a, area_b, gap):
    """同一位夥伴同天兩場（不重疊）的間隔：ok＝不用問；ask＝問店長趕不趕得上；no＝她說過這兩地這麼短趕不上。
    負責人：間隔多久要看距離，問店長「這樣趕不趕得上」→ 她每次的回答記在 gap_learn（兩地＋間隔），越問越少。"""
    try:
        L = json.loads(ix.setting(con, 'gap_learn', '{}') or '{}').get(_pair(area_a, area_b), {})
    except ValueError:
        L = {}
    if L.get('no') is not None and gap <= L['no']:
        return 'no'
    if L.get('ok') is not None and gap >= L['ok']:
        return 'ok'
    g = gap_rule(con) or GAP_DEFAULT
    return 'ok' if gap >= (g['same'] if area_a and area_b and area_a[:2] == area_b[:2] else g['cross']) else 'ask'

def gap_learn(con, area_a, area_b, gap, ok):
    d = json.loads(ix.setting(con, 'gap_learn', '{}') or '{}'); k = _pair(area_a, area_b); L = d.setdefault(k, {})
    if ok:
        L['ok'] = gap if L.get('ok') is None else min(L['ok'], gap)
        if L.get('no') is not None and L['no'] >= gap:
            L['no'] = gap - 1
    else:
        L['no'] = gap if L.get('no') is None else max(L['no'], gap)
        if L.get('ok') is not None and L['ok'] <= gap:
            L['ok'] = gap + 1
    ix.set_setting(con, 'gap_learn', json.dumps(d, ensure_ascii=False))
    return k

def gap_need(con, area_a, area_b):
    g = gap_rule(con)
    if not g:
        return 0
    return g['same'] if area_a and area_b and area_a[:2] == area_b[:2] else g['cross']

# ───────────── 日曆隱私 ─────────────
def calendar_title(con, site, order, names):
    import board
    if (ix.setting(con, 'calendar_privacy') or 'nameless') != 'full':      # 資安：預設不放姓名
        return f"{board.area_of(site) or '臺北'}・{site.get('service_type') or order['title']}｜{'、'.join(names) or '待派'}"
    return f"{site.get('name') or order['title']}｜{'、'.join(names) or '待派'}"

def calendar_location(con, site):
    import board
    if (ix.setting(con, 'calendar_privacy') or 'nameless') != 'full':      # 資安：預設不放姓名
        return c.place(board.area_of(site))
    return site.get('address', '')

def rotate_reminder(con):
    """選了「每季換連結」：每季第一天提醒負責人換 Google 日曆同步網址（換了舊網址就失效，Apps Script 要一起改）。"""
    import line_bot
    if not ix.setting(con, 'calendar_rotate'):
        return
    t = dt.datetime.now(c.TZ).date(); q = f'{t.year}Q{(t.month - 1) // 3 + 1}'
    if ix.setting(con, 'calendar_rotate_notice') == q or t.day > 7:
        return
    ix.set_setting(con, 'calendar_rotate_notice', q)
    line_bot.queue(con, line_bot.WORK_ACCOUNT, line_bot.OWNER_UID, 'admin', '該換日曆同步網址了',
                   line_bot._txt('📅 依店長的決定，每季要換一次 Google 日曆同步網址：到中控台「設定 › Google 日曆」按「重新產生金鑰」，再把新金鑰貼到 Apps Script。'))

# ───────────── 現場估價費 ─────────────
def visit_fee_on_send(con, q):
    """報價單要傳給客戶時：現場估價過、規則是成交折抵 → 自動加一行折抵（只加一次）。"""
    rule = ix.setting(con, 'visit_fee_rule')
    if q['mode'] != 'site_visit' or not q['visit_fee'] or rule not in ('deduct_charge', 'deduct_free'):
        return False
    items = json.loads(q['items'] or '[]')
    if any(i.get('title') == '現場估價費折抵' for i in items):
        return False
    items.append({'title': '現場估價費折抵', 'sub': '已付現場估價費', 'amount': -int(q['visit_fee']), 'unit': '', 'per_unit': False, 'separate': False, 'scope': []})
    con.execute('UPDATE quotes SET items=? WHERE id=?', (json.dumps(items, ensure_ascii=False), q['id']))
    return True

def visit_fee_on_stage(con, q):
    """排現場估價時：免費 → 不記費用；要收 → 提醒管理群現場記得收。"""
    import line_bot
    rule = ix.setting(con, 'visit_fee_rule')
    if rule == 'free':
        con.execute('UPDATE quotes SET visit_fee=0 WHERE id=?', (q['id'],))
    elif rule in ('deduct_charge', 'charge', 'deduct_free') and q['visit_fee']:
        f = json.loads(q['form'] or '{}')
        line_bot.admin_notify(con, '📍 現場估價：記得說明估價費', [('客戶', f.get('name') or ''), ('估價費', f"NT$ {int(q['visit_fee']):,}"),
                              ('規則', {'deduct_charge': '成交折抵，沒成交照收', 'deduct_free': '成交折抵，沒成交免收', 'charge': '一律照收不折抵'}[rule])], f"/#q/{q['id']}", '#C47F00')

# ───────────── 取消車馬費 ─────────────
def cancel_subsidy(con, before, after):
    """客戶取消：依店長的規則，給「取消時已接單」的夥伴補貼（寫成核定金額 → 月結、薪資明細、我的收入都會算到）。"""
    import line_bot
    rule = ix.setting(con, 'cancel_subsidy')
    if rule in ('', 'none', None) or not before.get('date') or not before.get('start'):
        return 0
    start = dt.datetime.fromisoformat(f"{before['date']}T{before['start']}:00").replace(tzinfo=c.TZ)
    now = dt.datetime.now(c.TZ)
    hit = (rule == '24h_200' and start - now <= dt.timedelta(hours=24)) or (rule in ('day_200', 'day_half') and now.date().isoformat() == before['date'])
    if not hit or start < now - dt.timedelta(hours=12):
        return 0
    n = 0
    for a in c.rows(con, "SELECT * FROM assignments WHERE order_id=? AND notes LIKE '%（工單取消時已接）%' AND amount IS NULL", (after['id'],)):
        if rule == 'day_half':
            m = c.minutes(before['start'], before['end']) or 240
            amt = c.cents(c.number(a['rate'] or 0, '時薪') * m / 60 / 2) if a['rate'] else 0
        else:
            amt = 20000
        if amt:
            con.execute("UPDATE assignments SET amount=?,notes=COALESCE(notes,'')||'（取消補貼）',version=version+1 WHERE id=?", (amt, a['id']))
            c.audit(con, 'cancel_subsidy', 'assignments', a['id'], after={'amount': amt, 'rule': rule})
            line_bot.to_worker(con, a['worker_id'], 'subsidy', '取消補貼', line_bot._txt(f"{line_bot.when(before)} 這場客戶臨時取消，依規定補貼你 NT$ {amt // 100:,}，會併入月底結算 🙏"), after['id'])
            n += 1
    return n
