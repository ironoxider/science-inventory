CREATE TABLE IF NOT EXISTS locations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    building TEXT,
    room     TEXT,
    notes    TEXT
);

-- Choices for drop-downs (equipment categories, conditions, hazard classes, ...),
-- managed on the Lists page.
CREATE TABLE IF NOT EXISTS options (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL COLLATE NOCASE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (kind, name)
);

CREATE TABLE IF NOT EXISTS equipment (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    category       TEXT,
    size           TEXT,
    quantity       INTEGER NOT NULL DEFAULT 1,
    min_quantity   INTEGER,
    location_id    INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    condition      TEXT,
    asset_tag      TEXT UNIQUE COLLATE NOCASE,
    manufacturer   TEXT,
    model          TEXT,
    model_number   TEXT,
    serial_number  TEXT,
    supplier       TEXT,
    catalog_number TEXT,
    unit_cost      REAL,
    purchase_date  TEXT,
    notes          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chemicals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    cas_number      TEXT,
    formula         TEXT,
    concentration   TEXT,
    amount          REAL,
    unit            TEXT,
    containers      INTEGER NOT NULL DEFAULT 1,
    location_id     INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    storage_group   TEXT,
    hazards         TEXT,
    received_date   TEXT,
    expiration_date TEXT,
    supplier        TEXT,
    catalog_number  TEXT,
    sds_url         TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS textbooks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    author     TEXT,
    isbn       TEXT,
    edition    TEXT,
    publisher  TEXT,
    year       TEXT,
    subject    TEXT,
    course     TEXT,
    unit_cost  REAL,
    notes      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per physical copy of a textbook.
CREATE TABLE IF NOT EXISTS copies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    copy_number TEXT NOT NULL UNIQUE COLLATE NOCASE,
    textbook_id INTEGER NOT NULL REFERENCES textbooks(id) ON DELETE CASCADE,
    condition   TEXT,
    status      TEXT NOT NULL DEFAULT 'Available',
    location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_copies_textbook ON copies(textbook_id);

-- Textbook loans. returned_on IS NULL means the copy is still out.
CREATE TABLE IF NOT EXISTS checkouts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    copy_id        INTEGER NOT NULL REFERENCES copies(id) ON DELETE CASCADE,
    borrower       TEXT NOT NULL,
    checked_out_on TEXT NOT NULL,
    due_date       TEXT,
    returned_on    TEXT,
    condition_out  TEXT,
    condition_in   TEXT,
    notes          TEXT
);
CREATE INDEX IF NOT EXISTS idx_checkouts_copy ON checkouts(copy_id);
CREATE INDEX IF NOT EXISTS idx_checkouts_open ON checkouts(returned_on);

-- Change log for every record type.
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    collection TEXT NOT NULL,
    record_id  INTEGER NOT NULL,
    changed_at TEXT NOT NULL DEFAULT (datetime('now')),
    field      TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_record ON history(collection, record_id);
