-- v3：多人帳號、派工大廳、夥伴回報、客戶別名、月結
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL, pw TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','staff','finance','cleaner')),
 worker_id TEXT REFERENCES workers(id), active INTEGER NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 1,
 failed INTEGER NOT NULL DEFAULT 0, locked_until INTEGER NOT NULL DEFAULT 0,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, last_login TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS login_attempts (key TEXT NOT NULL, at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS login_attempts_key ON login_attempts(key,at);
CREATE TABLE IF NOT EXISTS customer_aliases (
 alias TEXT PRIMARY KEY COLLATE NOCASE, customer_id TEXT NOT NULL REFERENCES customers(id), site_id TEXT REFERENCES sites(id)
);
CREATE TABLE IF NOT EXISTS dispatch_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL REFERENCES orders(id), at TEXT DEFAULT CURRENT_TIMESTAMP,
 actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS checkins (
 assignment_id TEXT PRIMARY KEY REFERENCES assignments(id), arrived_at TEXT DEFAULT '', left_at TEXT DEFAULT '',
 arrive_lat REAL, arrive_lng REAL, leave_lat REAL, leave_lng REAL
);
-- 夥伴每月回報的可排時間（free）或不能排（busy）
CREATE TABLE IF NOT EXISTS availability (
 id TEXT PRIMARY KEY, worker_id TEXT NOT NULL REFERENCES workers(id), date TEXT NOT NULL,
 start TEXT NOT NULL DEFAULT '', end TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'free' CHECK(kind IN ('free','busy','unsure')),
 note TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(worker_id,date,start)
);
-- 客戶從官方 LINE 圖文選單點進來填的預約需求（尚未成交，不是工單）
CREATE TABLE IF NOT EXISTS inquiries (
 id TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL DEFAULT 'new'
   CHECK(status IN ('new','contacted','quoted','booked','lost','spam')),
 name TEXT NOT NULL, phone TEXT DEFAULT '', line_name TEXT DEFAULT '', area TEXT DEFAULT '', address TEXT DEFAULT '',
 service_type TEXT DEFAULT '', detail TEXT DEFAULT '{}', preferred TEXT DEFAULT '', notes TEXT DEFAULT '',
 customer_id TEXT REFERENCES customers(id), order_id TEXT REFERENCES orders(id), ip TEXT DEFAULT '', handled_note TEXT DEFAULT '', version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS push_subs (
 id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), endpoint TEXT UNIQUE NOT NULL, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
 ua TEXT DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP, last_ok TEXT, fails INTEGER DEFAULT 0, last_err TEXT
);
CREATE TABLE IF NOT EXISTS tours (user_id TEXT NOT NULL, key TEXT NOT NULL, at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_id,key));
-- 待整理區：系統或同事判斷不了的資料，交給負責人點選處理
CREATE TABLE IF NOT EXISTS triage (
 id TEXT PRIMARY KEY, entity TEXT NOT NULL CHECK(entity IN ('customers','workers','orders','inquiries')), ref_id TEXT NOT NULL,
 grp TEXT NOT NULL, reason TEXT NOT NULL, suggestion TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP, created_by TEXT DEFAULT '',
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done')), decided_at TEXT DEFAULT '', decided_by TEXT DEFAULT '', decision TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS triage_open ON triage(entity, ref_id) WHERE status='open';
-- 回收桶：刪除先放這裡（整筆含關聯資料的快照），可還原；在回收桶按「永久刪除」才真的消失
CREATE TABLE IF NOT EXISTS trash (
 id TEXT PRIMARY KEY, entity TEXT NOT NULL, ref_id TEXT NOT NULL, label TEXT NOT NULL, payload TEXT NOT NULL,
 deleted_at TEXT DEFAULT CURRENT_TIMESTAMP, deleted_by TEXT NOT NULL, reason TEXT DEFAULT ''
);
-- 異動通知：同一件事只通知一次
CREATE TABLE IF NOT EXISTS alerts_sent (key TEXT PRIMARY KEY, at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS alert_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT DEFAULT CURRENT_TIMESTAMP, key TEXT, title TEXT, body TEXT);
-- LINE 自動化：每個 LINE 帳號（客戶／夥伴／同事）的專屬編號與對應
CREATE TABLE IF NOT EXISTS line_users (
 account TEXT NOT NULL DEFAULT 'demoshop', user_id TEXT NOT NULL, display_name TEXT DEFAULT '', picture_url TEXT DEFAULT '', customer_id TEXT REFERENCES customers(id),
 worker_id TEXT REFERENCES workers(id), staff_user TEXT DEFAULT '', first_seen TEXT DEFAULT '', last_seen TEXT DEFAULT '', last_text TEXT DEFAULT '',
 is_friend INTEGER DEFAULT 1, is_staff INTEGER DEFAULT 0, PRIMARY KEY(account, user_id)
);
CREATE TABLE IF NOT EXISTS line_groups (group_id TEXT PRIMARY KEY, name TEXT DEFAULT '', joined_at TEXT DEFAULT '', active INTEGER DEFAULT 1, account TEXT DEFAULT 'demoshop', role TEXT DEFAULT '', worker_id TEXT);
CREATE TABLE IF NOT EXISTS line_bind (code TEXT PRIMARY KEY, kind TEXT NOT NULL, ref_id TEXT NOT NULL, expires INTEGER NOT NULL, account TEXT DEFAULT 'demoshop');
-- 發送佇列（交易成功才送）＋紀錄
CREATE TABLE IF NOT EXISTS line_outbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT DEFAULT '', to_id TEXT NOT NULL, kind TEXT NOT NULL, order_id TEXT DEFAULT '',
 alt TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', sent_at TEXT DEFAULT '', error TEXT DEFAULT '', account TEXT DEFAULT 'demoshop'
);
CREATE TABLE IF NOT EXISTS line_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT DEFAULT '', user_id TEXT, group_id TEXT, type TEXT, text TEXT, data TEXT, account TEXT DEFAULT 'demoshop');

-- 線上估價與報價單簽收
CREATE TABLE IF NOT EXISTS quotes (
 id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, created_at TEXT DEFAULT '', account TEXT DEFAULT 'demoshop', inquiry_id TEXT REFERENCES inquiries(id),
 customer_id TEXT REFERENCES customers(id), order_id TEXT REFERENCES orders(id), form TEXT NOT NULL, estimate TEXT NOT NULL, total INTEGER NOT NULL,
 date TEXT DEFAULT '', start TEXT DEFAULT '', end TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','signed','cancelled')),
 signer_name TEXT DEFAULT '', signature TEXT DEFAULT '', signed_at TEXT DEFAULT '', ip TEXT DEFAULT '', ua TEXT DEFAULT ''
);

-- 報價現況照片（客戶上傳或用 LINE 傳）：只有內部人員看得到
CREATE TABLE IF NOT EXISTS quote_photos (
 id TEXT PRIMARY KEY, quote_id TEXT NOT NULL REFERENCES quotes(id), created_at TEXT DEFAULT '', source TEXT DEFAULT 'upload',
 mime TEXT NOT NULL, bytes INTEGER NOT NULL, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS quote_photos_q ON quote_photos(quote_id);
