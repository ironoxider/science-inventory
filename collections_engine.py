"""A small engine for "a list of things with fields": equipment, chemicals, textbook titles.

Each record type is described by a Collection (its fields, filters and import rules); this
module turns that description into list / view / add / edit / delete / CSV pages.
"""

import csv
import io
from dataclasses import dataclass, field as dc_field
from datetime import date
from typing import Callable, Optional

from flask import (Blueprint, Response, abort, flash, redirect, render_template, request,
                   url_for)

from db import (MULTI_SEP, all_locations, clean, ensure_option, find_option, get_db,
                get_history, get_or_create_location, list_options, location_name, record_history)

NEW_OPTION = "__new__"  # value of the "+ Add new…" entry in drop-downs


@dataclass
class Field:
    name: str
    label: str
    type: str = "text"  # text textarea int number date select multi url location person
    group: str = "Details"
    required: bool = False
    kind: Optional[str] = None  # managed list for select / multi
    default: object = None
    unique: bool = False
    minimum: Optional[float] = None
    placeholder: str = ""
    help: str = ""
    list: bool = False  # column on the list page
    search: bool = False  # included in the search box
    filter: bool = False  # drop-down filter on the list page
    sort: bool = False
    mono: bool = False


@dataclass
class Collection:
    key: str  # URL prefix and blueprint name, e.g. "equipment"
    table: str
    title: str  # "Equipment"
    singular: str  # "item"
    fields: list
    display: Callable  # row -> short name shown in headings and messages
    natural_keys: list = dc_field(default_factory=list)  # tuples of fields that identify a record
    default_sort: str = "name"
    extra_select: str = ""  # extra SQL columns for the list (computed counts)
    extra_columns: list = dc_field(default_factory=list)  # (sql alias, label)
    custom_filters: dict = dc_field(default_factory=dict)  # name -> {value: (label, sql, params_fn)}
    cell_class: Optional[Callable] = None  # (field name, row) -> css class
    photo: Optional[dict] = None  # photo panel options for the form
    view_extra: Optional[str] = None  # template included at the bottom of the view page
    form_script: Optional[str] = None  # extra static JS for the form
    intro: str = ""

    def field(self, name):
        return next((f for f in self.fields if f.name == name), None)

    @property
    def has_location(self):
        return any(f.type == "location" for f in self.fields)

    def from_sql(self):
        loc = " LEFT JOIN locations l ON l.id = t.location_id" if self.has_location else ""
        name = "l.name" if self.has_location else "NULL"
        extra = f", {self.extra_select}" if self.extra_select else ""
        return f"SELECT t.*, {name} AS location{extra} FROM {self.table} t{loc}"

    @property
    def groups(self):
        out = []
        for f in self.fields:
            if f.group not in out:
                out.append(f.group)
        return out

    @property
    def columns(self):
        """CSV columns: id, then every field (location as its name)."""
        return ["id"] + [("location" if f.type == "location" else f.name) for f in self.fields]

    @property
    def db_columns(self):
        return [("location_id" if f.type == "location" else f.name) for f in self.fields]


# ---------------------------------------------------------------- parsing & validation


def parse_value(f, raw, db, errors, allow_new=True):
    """Convert one submitted value to its stored form; appends a message to `errors` if invalid."""
    if f.type == "multi":
        values = raw if isinstance(raw, list) else [v for v in str(raw or "").replace(",", ";").split(";")]
        values = [clean(v) for v in values if clean(v)]
        if f.kind:
            values = [ensure_option(db, f.kind, v) if allow_new else (find_option(db, f.kind, v) or v)
                      for v in values]
        return MULTI_SEP.join(dict.fromkeys(values)) or None
    value = clean(raw)
    if value is None:
        return None
    if f.type == "select":
        if value == NEW_OPTION:
            return None
        return ensure_option(db, f.kind, value) if allow_new else (find_option(db, f.kind, value) or value)
    if f.type in ("int", "number"):
        try:
            number = int(value) if f.type == "int" else float(value.replace("$", "").replace(",", ""))
        except ValueError:
            errors.append(f"{f.label} must be a {'whole ' if f.type == 'int' else ''}number.")
            return value
        if f.minimum is not None and number < f.minimum:
            errors.append(f"{f.label} can't be less than {f.minimum:g}.")
        return number
    if f.type == "date":
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            errors.append(f"{f.label} must be a date in YYYY-MM-DD format.")
            return value
    if f.type == "url" and not value.lower().startswith(("http://", "https://")):
        errors.append(f"{f.label} must be a web address starting with http:// or https://.")
    return value


def form_to_record(coll, form, db):
    """Read the add/edit form. Returns (data keyed by db column, errors)."""
    data, errors = {}, []
    for f in coll.fields:
        if f.type == "location":
            new_loc = clean(form.get("new_location"))
            loc = clean(form.get("location_id"))
            data["location_id"] = (get_or_create_location(db, new_loc) if new_loc
                                   else int(loc) if loc else None)
        elif f.type == "multi":
            data[f.name] = parse_value(f, form.getlist(f.name), db, errors)
        else:
            data[f.name] = parse_value(f, form.get(f.name), db, errors)
    return data, errors


def validate(coll, data, db, record_id=None):
    errors = []
    for f in coll.fields:
        key = "location_id" if f.type == "location" else f.name
        if f.required and data.get(key) in (None, ""):
            errors.append(f"{f.label} is required.")
        if f.unique and data.get(key):
            clash = db.execute(f"SELECT id FROM {coll.table} WHERE {f.name} = ? AND id IS NOT ?",
                               (data[key], record_id)).fetchone()
            if clash:
                errors.append(f"{f.label} '{data[key]}' is already used by another {coll.singular}.")
    return errors


def save_record(coll, db, data, record_id=None):
    """Insert or update; records each changed field in history. Returns the id."""
    cols = coll.db_columns
    for f in coll.fields:  # fill defaults for new records
        if record_id is None and f.default is not None and data.get(f.name) in (None, ""):
            data[f.name] = f.default
    if record_id is None:
        cur = db.execute(f"INSERT INTO {coll.table} ({', '.join(cols)}) "
                         f"VALUES ({', '.join('?' * len(cols))})", [data.get(c) for c in cols])
        record_id = cur.lastrowid
        record_history(db, coll.table, record_id,
                       [("created", None, location_name(db, data.get("location_id")))])  # None if no location
        return record_id
    old = db.execute(f"SELECT * FROM {coll.table} WHERE id = ?", (record_id,)).fetchone()
    changes = []
    for c in cols:
        if old[c] != data.get(c):
            if c == "location_id":
                changes.append(("location", location_name(db, old[c]), location_name(db, data.get(c))))
            else:
                changes.append((c, old[c], data.get(c)))
    if changes:
        db.execute(f"UPDATE {coll.table} SET {', '.join(c + ' = ?' for c in cols)}, "
                   "updated_at = datetime('now') WHERE id = ?", [data.get(c) for c in cols] + [record_id])
        record_history(db, coll.table, record_id, changes)
    return record_id


# ---------------------------------------------------------------- queries


def query(coll, db, args, limit=None):
    where, params = [], []
    q = clean(args.get("q"))
    if q:
        cols = [f"t.{f.name}" for f in coll.fields if f.search] + (["l.name"] if coll.has_location else [])
        where.append("(" + " OR ".join(f"{c} LIKE ?" for c in cols) + ")")
        params += [f"%{q}%"] * len(cols)
    loc = clean(args.get("location")) if coll.has_location else None
    if loc:
        if loc == "none":
            where.append("t.location_id IS NULL")
        else:
            where.append("t.location_id = ?")
            params.append(loc)
    for f in coll.fields:
        value = clean(args.get(f.name))
        if f.filter and value:
            if f.type == "multi":
                where.append(f"(t.{f.name} LIKE ?)")
                params.append(f"%{value}%")
            else:
                where.append(f"t.{f.name} = ?")
                params.append(value)
    for name, choices in coll.custom_filters.items():
        value = args.get(name)
        if value in choices:
            _, sql, params_fn = choices[value]
            where.append(sql)
            params += params_fn() if params_fn else []
    sortable = {f.name: f"t.{f.name}" for f in coll.fields if f.sort}
    if coll.has_location:
        sortable["location"] = "l.name"
    sortable.update({alias: alias for alias, _ in coll.extra_columns})
    sort = sortable.get(args.get("sort"), f"t.{coll.default_sort}")
    direction = "DESC" if args.get("dir") == "desc" else "ASC"
    collate = "" if sort in [a for a, _ in coll.extra_columns] else " COLLATE NOCASE"
    sql = (coll.from_sql()
           + (" WHERE " + " AND ".join(where) if where else "")
           + f" ORDER BY {sort} IS NULL, {sort}{collate} {direction}, t.id"
           + (f" LIMIT {int(limit)}" if limit else ""))
    return db.execute(sql, params).fetchall()


def get_record(coll, db, record_id):
    row = db.execute(coll.from_sql() + " WHERE t.id = ?", (record_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def people(db):
    """Names used before, for person fields and textbook borrowers."""
    rows = db.execute("SELECT borrower FROM checkouts GROUP BY borrower COLLATE NOCASE "
                      "ORDER BY borrower COLLATE NOCASE")
    return [r[0] for r in rows]


# ---------------------------------------------------------------- CSV import


def import_rows(coll, db, reader):
    """Rows with an existing id (or matching a natural key) update that record; others are added."""
    header_map = {}
    for h in reader.fieldnames or []:
        key = h.strip().lower().replace(" ", "_").replace("#", "number")
        if key in coll.columns:
            header_map[h] = key
    result = {"added": 0, "updated": 0, "errors": []}
    required = [f.name for f in coll.fields if f.required]
    if not header_map:
        result["errors"].append("No recognised columns. Use the column names listed below.")
        return result
    for line_no, raw in enumerate(reader, start=2):
        row = {k: clean(raw.get(h)) for h, k in header_map.items()}
        if not any(row.values()):
            continue
        existing = find_existing(coll, db, row)
        data = dict(existing) if existing else {}
        errors = []
        for f in coll.fields:
            col = "location" if f.type == "location" else f.name
            if col not in row or (row[col] is None and existing):
                continue  # keep what's there
            if f.type == "location":
                data["location_id"] = get_or_create_location(db, row[col])
            else:
                data[f.name] = parse_value(f, row[col], db, errors)
        missing = [r for r in required if data.get(r) in (None, "")]
        errors += [f"{coll.field(r).label} is required." for r in missing]
        errors += validate(coll, data, db, existing["id"] if existing else None) if not errors else []
        if errors:
            result["errors"].append(f"Row {line_no}: " + " ".join(dict.fromkeys(errors)))
            continue
        save_record(coll, db, data, existing["id"] if existing else None)
        result["updated" if existing else "added"] += 1
    db.commit()
    return result


def find_existing(coll, db, row):
    if row.get("id") and str(row["id"]).isdigit():
        found = db.execute(f"SELECT * FROM {coll.table} WHERE id = ?", (int(row["id"]),)).fetchone()
        if found:
            return found
    for key in coll.natural_keys:
        if not all(row.get(k) for k in key if k != "location"):
            continue
        sql, params = [], []
        for k in key:
            if k == "location":
                if row.get("location"):
                    sql.append("location_id = (SELECT id FROM locations WHERE name = ?)")
                    params.append(row["location"])
                else:
                    sql.append("location_id IS NULL")
            else:
                sql.append(f"{k} = ? COLLATE NOCASE")
                params.append(row[k])
        found = db.execute(f"SELECT * FROM {coll.table} WHERE " + " AND ".join(sql), params).fetchone()
        if found:
            return found
    return None


# ---------------------------------------------------------------- pages


def make_blueprint(coll):
    bp = Blueprint(coll.key, __name__, url_prefix=f"/{coll.key}")

    def render_form(record, errors, new):
        db = get_db()
        return render_template("coll_form.html", coll=coll, record=record, errors=errors, new=new,
                               locations=all_locations(db),
                               options={f.name: list_options(db, f.kind) for f in coll.fields if f.kind})

    @bp.route("/")
    def index():
        db = get_db()
        rows = query(coll, db, request.args)
        filters = [(f, list_options(db, f.kind)) for f in coll.fields if f.filter and f.kind]
        return render_template("coll_list.html", coll=coll, rows=rows, args=request.args,
                               filters=filters, locations=all_locations(db))

    @bp.route("/new", methods=["GET", "POST"])
    def new():
        db = get_db()
        record = {f.name: f.default for f in coll.fields if f.default is not None}
        record.update({k: clean(v) for k, v in request.args.items() if coll.field(k)})  # prefill
        errors = []
        if request.method == "POST":
            record, errors = form_to_record(coll, request.form, db)
            errors += validate(coll, record, db)
            if not errors:
                record_id = save_record(coll, db, record)
                db.commit()
                flash(f"Added {coll.display(record)}.", "success")
                if request.form.get("add_another"):
                    return redirect(url_for(f"{coll.key}.new"))
                return redirect(url_for(f"{coll.key}.view", record_id=record_id))
            db.rollback()
        return render_form(record, errors, True)

    @bp.route("/<int:record_id>")
    def view(record_id):
        db = get_db()
        record = get_record(coll, db, record_id)
        return render_template("coll_view.html", coll=coll, record=record,
                               history=get_history(db, coll.table, record_id))

    @bp.route("/<int:record_id>/edit", methods=["GET", "POST"])
    def edit(record_id):
        db = get_db()
        record, errors = dict(get_record(coll, db, record_id)), []
        if request.method == "POST":
            record, errors = form_to_record(coll, request.form, db)
            record["id"] = record_id
            errors += validate(coll, record, db, record_id)
            if not errors:
                save_record(coll, db, record, record_id)
                db.commit()
                flash("Changes saved.", "success")
                return redirect(url_for(f"{coll.key}.view", record_id=record_id))
            db.rollback()
        return render_form(record, errors, False)

    @bp.route("/<int:record_id>/delete", methods=["POST"])
    def delete(record_id):
        db = get_db()
        record = get_record(coll, db, record_id)
        db.execute(f"DELETE FROM {coll.table} WHERE id = ?", (record_id,))
        db.execute("DELETE FROM history WHERE collection = ? AND record_id = ?", (coll.table, record_id))
        db.commit()
        flash(f"Deleted {coll.display(record)}.", "success")
        return redirect(url_for(f"{coll.key}.index"))

    @bp.route("/export.csv")
    def export():
        rows = query(coll, get_db(), request.args)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(coll.columns)
        for r in rows:
            writer.writerow(["" if r[c] is None else r[c] for c in coll.columns])
        name = f"{coll.key}-{date.today().isoformat()}.csv"
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @bp.route("/import", methods=["GET", "POST"])
    def import_csv():
        result = None
        if request.method == "POST":
            upload = request.files.get("file")
            if not upload or not upload.filename:
                flash("Choose a CSV file to import.", "error")
                return redirect(url_for(f"{coll.key}.import_csv"))
            text = upload.read().decode("utf-8-sig", errors="replace")
            result = import_rows(coll, get_db(), csv.DictReader(io.StringIO(text)))
        return render_template("coll_import.html", coll=coll, result=result)

    return bp
