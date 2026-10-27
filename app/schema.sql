PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS customers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, line_name TEXT DEFAULT '', phone TEXT DEFAULT '', status TEXT DEFAULT 'active',
 notes TEXT DEFAULT '', billing_rule TEXT DEFAULT '', line_id TEXT DEFAULT '', line_account TEXT DEFAULT '', source TEXT DEFAULT '', version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sites (
 id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), name TEXT NOT NULL, address TEXT DEFAULT '',
 service_type TEXT DEFAULT '', notes TEXT DEFAULT '', rate TEXT, pricing_note TEXT DEFAULT '', source TEXT DEFAULT '', version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS workers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT DEFAULT '', regions TEXT DEFAULT '', skills TEXT DEFAULT '',
 availability TEXT DEFAULT '', rate TEXT, status TEXT DEFAULT 'review', notes TEXT DEFAULT '', source TEXT DEFAULT '', version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS orders (
 id TEXT PRIMARY KEY, site_id TEXT REFERENCES sites(id), customer_id TEXT REFERENCES customers(id), title TEXT NOT NULL,
 date TEXT DEFAULT '', start TEXT DEFAULT '', end TEXT DEFAULT '', actual_start TEXT DEFAULT '', actual_end TEXT DEFAULT '',
 break_minutes INTEGER DEFAULT 0, required_workers INTEGER DEFAULT 1, status TEXT DEFAULT 'review', billing_mode TEXT DEFAULT 'fixed',
 rate TEXT, billable_hours TEXT, approved_amount INTEGER, expense INTEGER DEFAULT 0, adjustment INTEGER DEFAULT 0,
 adjustment_reason TEXT DEFAULT '', notes TEXT DEFAULT '', source TEXT DEFAULT '', original_json TEXT DEFAULT '{}',
 review_note TEXT DEFAULT '', confirmed_at TEXT DEFAULT '', completed_at TEXT DEFAULT '', version INTEGER DEFAULT 1,
 calendar_event_id TEXT DEFAULT '', calendar_revision INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS assignments (
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), worker_id TEXT NOT NULL REFERENCES workers(id),
 status TEXT DEFAULT 'invited', pay_mode TEXT DEFAULT 'hourly', rate TEXT, approved_minutes INTEGER, amount INTEGER,
 notes TEXT DEFAULT '', version INTEGER DEFAULT 1, UNIQUE(order_id,worker_id)
);
CREATE TABLE IF NOT EXISTS invoices (
 id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), period TEXT NOT NULL, due_date TEXT NOT NULL,
 status TEXT DEFAULT 'draft', notes TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP, issued_at TEXT DEFAULT '', version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS invoice_lines (
 id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES invoices(id), order_id TEXT REFERENCES orders(id), description TEXT NOT NULL,
 amount INTEGER NOT NULL, UNIQUE(order_id)
);
CREATE TABLE IF NOT EXISTS payments (
 id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES invoices(id), amount INTEGER NOT NULL CHECK(amount!=0),
 date TEXT NOT NULL, method TEXT DEFAULT '', reference TEXT DEFAULT '', reason TEXT DEFAULT '', request_key TEXT NOT NULL UNIQUE,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS settlements (
 id TEXT PRIMARY KEY, worker_id TEXT NOT NULL REFERENCES workers(id), period TEXT NOT NULL, status TEXT DEFAULT 'draft',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, paid_at TEXT DEFAULT '', reference TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settlement_lines (
 id TEXT PRIMARY KEY, settlement_id TEXT NOT NULL REFERENCES settlements(id), assignment_id TEXT NOT NULL UNIQUE REFERENCES assignments(id), amount INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS issues (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT DEFAULT '', detail TEXT NOT NULL, status TEXT DEFAULT 'open',
 resolution TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS source_rows (
 id TEXT PRIMARY KEY, file_hash TEXT NOT NULL, sheet TEXT NOT NULL, row_no INTEGER NOT NULL, payload TEXT NOT NULL,
 UNIQUE(file_hash,sheet,row_no)
);
CREATE TABLE IF NOT EXISTS import_runs (file_hash TEXT PRIMARY KEY, filename TEXT, imported_at TEXT DEFAULT CURRENT_TIMESTAMP, summary TEXT);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT DEFAULT CURRENT_TIMESTAMP, actor TEXT NOT NULL, action TEXT NOT NULL,
 entity TEXT NOT NULL, entity_id TEXT DEFAULT '', before_json TEXT, after_json TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS secrets (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires INTEGER NOT NULL, csrf TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_states (state TEXT PRIMARY KEY, verifier TEXT NOT NULL, expires INTEGER NOT NULL, session_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS service_reports (
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), notes TEXT NOT NULL, customer_confirmed INTEGER DEFAULT 0,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS line_accounts (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, basic_id TEXT DEFAULT '', verified_at TEXT DEFAULT '', webhook TEXT DEFAULT '',
 mode TEXT NOT NULL DEFAULT 'read_only' CHECK(mode='read_only')
);
CREATE TABLE IF NOT EXISTS conversation_notes (
 id TEXT PRIMARY KEY, customer_id TEXT REFERENCES customers(id), line_account_id TEXT REFERENCES line_accounts(id),
 source TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS orders_date ON orders(date,status);
CREATE INDEX IF NOT EXISTS orders_customer ON orders(customer_id);
