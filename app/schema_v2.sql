CREATE TABLE IF NOT EXISTS contacts (
 id TEXT PRIMARY KEY, customer_id TEXT REFERENCES customers(id), name TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT '聯絡人', phone TEXT DEFAULT '', line_account TEXT DEFAULT '', alias TEXT DEFAULT '',
 notes TEXT DEFAULT '', version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS plans (
 id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), name TEXT NOT NULL,
 valid_from TEXT NOT NULL, valid_to TEXT NOT NULL, price INTEGER NOT NULL,
 units INTEGER NOT NULL, terms TEXT NOT NULL, status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS entitlements (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id), order_id TEXT REFERENCES orders(id),
 units INTEGER NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL, at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(order_id,kind)
);
CREATE TABLE IF NOT EXISTS receipts (
 id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), amount INTEGER NOT NULL CHECK(amount>0),
 kind TEXT NOT NULL, date TEXT NOT NULL, method TEXT DEFAULT '', reference TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', request_key TEXT NOT NULL UNIQUE, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS receipt_refunds (
 id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES receipts(id), amount INTEGER NOT NULL CHECK(amount>0),
 date TEXT NOT NULL, reference TEXT NOT NULL, reason TEXT NOT NULL, request_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS credits (
 id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES invoices(id), amount INTEGER NOT NULL CHECK(amount>0),
 reason TEXT NOT NULL, date TEXT NOT NULL, request_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS timesheets (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL UNIQUE REFERENCES assignments(id),
 start TEXT NOT NULL, end TEXT NOT NULL, pause INTEGER NOT NULL, minutes INTEGER NOT NULL,
 notes TEXT NOT NULL, version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS service_cases (
 id TEXT PRIMARY KEY, order_id TEXT REFERENCES orders(id), customer_id TEXT REFERENCES customers(id),
 title TEXT NOT NULL, detail TEXT NOT NULL, owner TEXT NOT NULL, due_date TEXT NOT NULL,
 status TEXT DEFAULT 'open', resolution TEXT DEFAULT '', version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, customer_id TEXT REFERENCES customers(id), title TEXT NOT NULL, detail TEXT DEFAULT '',
 owner TEXT NOT NULL, due_date TEXT NOT NULL, status TEXT DEFAULT 'open', resolution TEXT DEFAULT '', version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS attachments (
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), name TEXT NOT NULL,
 mime TEXT NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, label TEXT NOT NULL,
 service_date TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chat_threads (
 id TEXT PRIMARY KEY, account TEXT NOT NULL, source TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
 customer_id TEXT REFERENCES customers(id), classification TEXT DEFAULT 'review', notes TEXT DEFAULT '',
 count INTEGER NOT NULL, first_at TEXT NOT NULL, last_at TEXT NOT NULL, version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS chat_messages (
 id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES chat_threads(id), line INTEGER NOT NULL,
 record INTEGER NOT NULL, sender_type TEXT NOT NULL, sender TEXT NOT NULL, source_time TEXT NOT NULL,
 source_timezone TEXT NOT NULL, local_time TEXT NOT NULL, content TEXT NOT NULL, media INTEGER NOT NULL, auto INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_thread_time ON chat_messages(thread_id,local_time);
CREATE TABLE IF NOT EXISTS calendar_operations (
 order_id TEXT PRIMARY KEY REFERENCES orders(id), generation INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS attachment_blobs (
 id TEXT PRIMARY KEY REFERENCES attachments(id), content BLOB NOT NULL
);
