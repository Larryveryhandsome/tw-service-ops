"""子行程執行：重新應徵冒用情境。用法：python _reapply_case.py <已 seed 的資料夾>；結果印成一行 JSON。"""
import json
import os
import sys
import time
from pathlib import Path

os.environ['OPS_DATA'] = sys.argv[1]
os.environ['OPS_NO_PUSH'] = '1'
os.environ['OPS_QUIET_TEST'] = '1'
APP = Path(__file__).resolve().parent.parent / 'app'
sys.path.insert(0, str(APP))
import demo_server  # noqa: F401,E402  （載入網路防護）
import core as c  # noqa: E402
import integrations as ix  # noqa: E402
import recruit  # noqa: E402
import interviews  # noqa: E402
import line_bot  # noqa: E402
import worker_portal  # noqa: E402

WID, ACC, UID, T = 'wor-demo02', 'demoshop', 'U-test-stranger', 'reapplytoken01'
con = c.connect()
c.CTX.actor = 'test'
recruit.ensure(con)
interviews.ensure(con)
victim = c.get(con, 'workers', WID)
con.execute("UPDATE workers SET status='paused' WHERE id=?", (WID,))
con.execute("INSERT INTO interviews(id,worker_id,status,created_at,decided_at,note) VALUES('iv-old',?,'rejected',?,?,'測試')", (WID, c.now(), c.now()))
ix.set_setting(con, 'reapply_mode', 'screen')
con.execute("INSERT OR REPLACE INTO line_users(account,user_id,display_name) VALUES(?,?,'陌生帳號')", (ACC, UID))
con.execute("INSERT INTO line_bind(code,kind,ref_id,expires,account) VALUES(?,?,?,?,?)", ('A' + T, 'apply', UID, int(time.time()) + 3600, ACC))
con.execute("INSERT INTO worker_files(id,worker_id,ref,kind,data,note,created_at,by) VALUES('wf-test',NULL,?,'portfolio',x'00','',?,'應徵者')", ('apply:' + T, c.now()))
con.commit()

def screening():
    return con.execute("SELECT COUNT(*) FROM interviews WHERE worker_id=? AND status='screening'", (WID,)).fetchone()[0]

res = worker_portal.apply(con, T, {'name': '冒用者', 'phone': victim['phone'], 'regions': '臺北市', 'skills': '基本服務', 'consent': True})
con.commit()
out = {'result': res, 'screening_before_bind': screening(),
       'file_owner_before_bind': con.execute("SELECT worker_id FROM worker_files WHERE id='wf-test'").fetchone()[0],
       'line_worker_before_bind': con.execute('SELECT worker_id FROM line_users WHERE account=? AND user_id=?', (ACC, UID)).fetchone()[0],
       'pending_saved': bool(ix.setting(con, f'rescreen_wait:{ACC}:{UID}'))}
req = con.execute("SELECT id FROM bind_requests WHERE user_id=? AND ref_id=? AND status='open'", (UID, WID)).fetchone()
out['bind_request'] = bool(req)
if req:                                         # 管理層確認「是本人」之後才做
    line_bot.decide_bind(con, req[0], True, '測試管理員')
    con.commit()
out['screening_after_bind'] = screening()
out['file_owner_after_bind'] = con.execute("SELECT worker_id FROM worker_files WHERE id='wf-test'").fetchone()[0]
print(json.dumps(out, ensure_ascii=False))
