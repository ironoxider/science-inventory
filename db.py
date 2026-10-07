"""Database access and small helpers shared by every part of the app."""

import os
import sqlite3
from datetime import date

from flask import current_app, g

import app_settings

# Starting choices for the managed drop-downs (edited later on the Lists page).
DEFAULT_OPTIONS = {
    "equipment_category": [
        "Glassware", "Microscopes & Optics", "Measurement", "Heating", "Electronics & Probes",
        "Physics Kits", "Models & Specimens", "Safety Equipment", "Tools", "Consumables", "Other",
    ],
    "condition": ["New", "Good", "Fair", "Poor", "Needs Repair", "Damaged"],
    "hazard_class": [
        "Flammable", "Oxidizer", "Corrosive", "Toxic", "Health Hazard", "Irritant",
        "Compressed Gas", "Explosive", "Environmental Hazard", "Non-hazardous",
    ],
    "storage_group": [
        "Inorganic: Acids", "Inorganic: Bases", "Inorganic: Oxidizers", "Inorganic: General",
        "Organic: Acids", "Organic: Flammables", "Organic: General", "Toxics", "Reactives",
    ],
    "chemical_unit": ["g", "kg", "mg", "mL", "L", "each"],
    "subject": ["Biology", "Chemistry", "Physics", "Earth Science", "Environmental Science",
                "Anatomy & Physiology", "General Science"],
}
OPTION_TITLES = {
    "equipment_category": "Equipment categories",
    "condition": "Conditions (equipment and textbooks)",
    "hazard_class": "Chemical hazard classes",
    "storage_group": "Chemical storage groups",
    "chemical_unit": "Chemical units",
    "subject": "Textbook subjects",
}
# Where each list is used: (table, column, multi-valued?)
OPTION_USES = {
    "equipment_category": [("equipment", "category", False)],
    "condition": [("equipment", "condition", False), ("copies", "condition", False)],
    "hazard_class": [("chemicals", "hazards", True)],
    "storage_group": [("chemicals", "storage_group", False)],
    "chemical_unit": [("chemicals", "unit", False)],
    "subject": [("textbooks", "subject", False)],
}
MULTI_SEP = "; "


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    with open(os.path.join(app_settings.RESOURCE_DIR, "schema.sql")) as f:
        db.executescript(f.read())
    for kind, names in DEFAULT_OPTIONS.items():
        if not db.execute("SELECT 1 FROM options WHERE kind = ?", (kind,)).fetchone():
            for name in names:
                ensure_option(db, kind, name)
    db.commit()


def app_setting(key, default):
    value = app_settings.load(current_app.config["SETTINGS_PATH"]).get(key)
    return default if value is None else value


def clean(value):
    """Strip whitespace and turn empty strings into NULL."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def today():
    return date.today().isoformat()


# ---------------------------------------------------------------- managed lists


def list_options(db, kind):
    return [r["name"] for r in db.execute(
        "SELECT name FROM options WHERE kind = ? ORDER BY sort_order, id", (kind,))]


def find_option(db, kind, name):
    """The stored spelling of `name` (matched ignoring case), or None."""
    row = db.execute("SELECT name FROM options WHERE kind = ? AND name = ?", (kind, name)).fetchone()
    return row["name"] if row else None


def ensure_option(db, kind, name):
    """Return the stored spelling of `name`, adding it to the end of the list if new."""
    name = clean(name)
    if not name:
        return None
    existing = find_option(db, kind, name)
    if existing:
        return existing
    db.execute(
        "INSERT INTO options (kind, name, sort_order) VALUES (?, ?, "
        "(SELECT COALESCE(MAX(sort_order), 0) + 1 FROM options WHERE kind = ?))",
        (kind, name, kind))
    return name


def option_usage(db, kind, name):
    """How many records use this list entry."""
    total = 0
    for table, column, multi in OPTION_USES.get(kind, []):
        if multi:
            rows = db.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (f"%{name}%",))
            total += sum(1 for r in rows
                         if name.lower() in [v.strip().lower() for v in r[0].split(MULTI_SEP.strip())])
        else:
            total += db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (name,)).fetchone()[0]
    return total


def rename_option_values(db, kind, old, new):
    """Replace `old` with `new` wherever the list entry is used; returns changed (collection, id)s."""
    changed = []
    for table, column, multi in OPTION_USES.get(kind, []):
        rows = db.execute(f"SELECT id, {column} AS v FROM {table} WHERE {column} LIKE ?", (f"%{old}%",))
        for r in rows.fetchall():
            if multi:
                parts = [p.strip() for p in r["v"].split(MULTI_SEP.strip())]
                if old.lower() not in [p.lower() for p in parts]:
                    continue
                value = MULTI_SEP.join(new if p.lower() == old.lower() else p for p in parts)
            elif r["v"].lower() == old.lower():
                value = new
            else:
                continue
            db.execute(f"UPDATE {table} SET {column} = ?, updated_at = datetime('now') WHERE id = ?",
                       (value, r["id"]))
            record_history(db, table, r["id"], [(column, r["v"], value)])
            changed.append((table, r["id"]))
    return changed


# ---------------------------------------------------------------- locations


def get_or_create_location(db, name):
    name = clean(name)
    if not name:
        return None
    row = db.execute("SELECT id FROM locations WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    return db.execute("INSERT INTO locations (name) VALUES (?)", (name,)).lastrowid


def location_name(db, location_id):
    if location_id is None:
        return None
    row = db.execute("SELECT name FROM locations WHERE id = ?", (location_id,)).fetchone()
    return row["name"] if row else None


def all_locations(db):
    return db.execute("SELECT * FROM locations ORDER BY name COLLATE NOCASE").fetchall()


LOCATION_TABLES = [("equipment", "equipment item"), ("chemicals", "chemical"),
                   ("copies", "textbook copy")]


def location_usage(db, location_id):
    return {table: db.execute(f"SELECT COUNT(*) FROM {table} WHERE location_id = ?",
                              (location_id,)).fetchone()[0]
            for table, _ in LOCATION_TABLES}


# ---------------------------------------------------------------- history


def record_history(db, collection, record_id, changes):
    db.executemany(
        "INSERT INTO history (collection, record_id, field, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
        [(collection, record_id, f, None if o is None else str(o), None if n is None else str(n))
         for f, o, n in changes])


def get_history(db, collection, record_id):
    return db.execute("SELECT * FROM history WHERE collection = ? AND record_id = ? ORDER BY id DESC",
                      (collection, record_id)).fetchall()
