"""Textbook copies and lending: add numbered copies, check out, check in, overdue list."""

import csv
import io
from datetime import date

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

from collections_engine import people
from db import (all_locations, app_setting, clean, ensure_option, get_db, get_history,
                get_or_create_location, list_options, location_name, record_history, today)

bp = Blueprint("lending", __name__)

COPY_STATUSES = ["Available", "Checked out", "Damaged", "Lost", "Retired"]
COPY_CSV = ["copy_number", "isbn", "title", "edition", "condition", "location", "status",
            "borrower", "checked_out_on", "due_date", "notes"]


def default_due_date():
    saved = app_setting("textbook_due_date", None)
    if saved and saved >= today():
        return saved
    t = date.today()  # end of the current school year (June 30)
    return date(t.year if t.month <= 6 else t.year + 1, 6, 30).isoformat()


def get_copy(db, copy_id):
    row = db.execute(
        "SELECT c.*, t.title, t.edition, t.isbn, t.author, l.name AS location FROM copies c "
        "JOIN textbooks t ON t.id = c.textbook_id LEFT JOIN locations l ON l.id = c.location_id "
        "WHERE c.id = ?", (copy_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def open_checkout(db, copy_id):
    return db.execute("SELECT * FROM checkouts WHERE copy_id = ? AND returned_on IS NULL "
                      "ORDER BY id DESC", (copy_id,)).fetchone()


def find_copy(db, number):
    return db.execute("SELECT id FROM copies WHERE copy_number = ?", (clean(number),)).fetchone()


def copies_for(db, textbook_id):
    return db.execute(
        "SELECT c.*, l.name AS location, k.borrower, k.due_date FROM copies c "
        "LEFT JOIN locations l ON l.id = c.location_id "
        "LEFT JOIN checkouts k ON k.copy_id = c.id AND k.returned_on IS NULL "
        "WHERE c.textbook_id = ? ORDER BY c.copy_number COLLATE NOCASE", (textbook_id,)).fetchall()


def do_checkout(db, copy, borrower, due, condition, notes):
    db.execute("INSERT INTO checkouts (copy_id, borrower, checked_out_on, due_date, condition_out, notes) "
               "VALUES (?, ?, ?, ?, ?, ?)", (copy["id"], borrower, today(), due, condition, notes))
    db.execute("UPDATE copies SET status = 'Checked out', condition = COALESCE(?, condition), "
               "updated_at = datetime('now') WHERE id = ?", (condition, copy["id"]))
    record_history(db, "copies", copy["id"], [("checked out", None, f"{borrower} (due {due or '—'})")])


@bp.app_template_global()
def copy_statuses():
    return COPY_STATUSES


@bp.app_template_global()
def textbook_copies(textbook_id):
    return copies_for(get_db(), textbook_id)


@bp.app_template_global()
def condition_options():
    return list_options(get_db(), "condition")


@bp.app_template_global()
def location_options():
    return all_locations(get_db())


# ---------------------------------------------------------------- copies


@bp.route("/textbooks/<int:textbook_id>/copies/add", methods=["POST"])
def add_copies(textbook_id):
    db = get_db()
    if not db.execute("SELECT 1 FROM textbooks WHERE id = ?", (textbook_id,)).fetchone():
        abort(404)
    prefix = clean(request.form.get("prefix")) or ""
    try:
        start = int(request.form.get("start") or 1)
        count = int(request.form.get("count") or 1)
    except ValueError:
        flash("Start number and how many must be whole numbers.", "error")
        return redirect(url_for("textbooks.view", record_id=textbook_id) + "#copies")
    if not 1 <= count <= 500:
        flash("Add between 1 and 500 copies at a time.", "error")
        return redirect(url_for("textbooks.view", record_id=textbook_id) + "#copies")
    digits = max(3, len(str(start + count - 1)))
    condition = ensure_option(db, "condition", request.form.get("condition"))
    location_id = get_or_create_location(db, request.form.get("new_location")) or \
        (int(request.form["location_id"]) if clean(request.form.get("location_id")) else None)
    added, skipped = 0, []
    for n in range(start, start + count):
        number = f"{prefix}{n:0{digits}d}"
        if find_copy(db, number):
            skipped.append(number)
            continue
        cid = db.execute("INSERT INTO copies (copy_number, textbook_id, condition, location_id) "
                         "VALUES (?, ?, ?, ?)", (number, textbook_id, condition, location_id)).lastrowid
        record_history(db, "copies", cid, [("created", None, location_name(db, location_id))])
        added += 1
    db.commit()
    msg = f"Added {added} cop{'y' if added == 1 else 'ies'}."
    if skipped:
        msg += f" Skipped {len(skipped)} number(s) already in use: {', '.join(skipped[:10])}" + \
               ("…" if len(skipped) > 10 else "")
    flash(msg, "success" if added else "error")
    return redirect(url_for("textbooks.view", record_id=textbook_id) + "#copies")


@bp.route("/copies/<int:copy_id>")
def copy_view(copy_id):
    db = get_db()
    copy = get_copy(db, copy_id)
    loans = db.execute("SELECT * FROM checkouts WHERE copy_id = ? ORDER BY id DESC", (copy_id,)).fetchall()
    return render_template("copy_view.html", copy=copy, current=open_checkout(db, copy_id), loans=loans,
                           history=get_history(db, "copies", copy_id), locations=all_locations(db),
                           conditions=list_options(db, "condition"), people=people(db),
                           due=default_due_date())


@bp.route("/copies/<int:copy_id>/edit", methods=["POST"])
def copy_edit(copy_id):
    db = get_db()
    copy = get_copy(db, copy_id)
    number = clean(request.form.get("copy_number"))
    status = request.form.get("status")
    if not number:
        flash("Copy number is required.", "error")
        return redirect(url_for("lending.copy_view", copy_id=copy_id))
    clash = find_copy(db, number)
    if clash and clash["id"] != copy_id:
        flash(f"Copy number {number} is already used.", "error")
        return redirect(url_for("lending.copy_view", copy_id=copy_id))
    if status not in COPY_STATUSES or (status == "Checked out") != (copy["status"] == "Checked out"):
        status = copy["status"]  # checking out/in happens with the buttons, not here
    new = {"copy_number": number, "status": status,
           "condition": ensure_option(db, "condition", request.form.get("condition")),
           "location_id": get_or_create_location(db, request.form.get("new_location"))
           or (int(request.form["location_id"]) if clean(request.form.get("location_id")) else None),
           "notes": clean(request.form.get("notes"))}
    changes = []
    for k, v in new.items():
        if copy[k] != v:
            changes.append(("location", copy["location"], location_name(db, v)) if k == "location_id"
                           else (k, copy[k], v))
    if changes:
        db.execute("UPDATE copies SET copy_number = ?, status = ?, condition = ?, location_id = ?, "
                   "notes = ?, updated_at = datetime('now') WHERE id = ?",
                   (*new.values(), copy_id))
        record_history(db, "copies", copy_id, changes)
        db.commit()
        flash("Copy saved.", "success")
    return redirect(url_for("lending.copy_view", copy_id=copy_id))


@bp.route("/copies/<int:copy_id>/delete", methods=["POST"])
def copy_delete(copy_id):
    db = get_db()
    copy = get_copy(db, copy_id)
    db.execute("DELETE FROM copies WHERE id = ?", (copy_id,))
    db.execute("DELETE FROM history WHERE collection = 'copies' AND record_id = ?", (copy_id,))
    db.commit()
    flash(f"Deleted copy {copy['copy_number']}.", "success")
    return redirect(url_for("textbooks.view", record_id=copy["textbook_id"]) + "#copies")


@bp.route("/copies/<int:copy_id>/checkout", methods=["POST"])
def checkout(copy_id):
    db = get_db()
    copy = get_copy(db, copy_id)
    borrower = clean(request.form.get("borrower"))
    due = clean(request.form.get("due_date"))
    if copy["status"] in ("Checked out", "Lost", "Retired"):
        flash(f"Copy {copy['copy_number']} is {copy['status'].lower()}, so it can't be checked out.", "error")
    elif not borrower:
        flash("Enter who is borrowing it.", "error")
    elif due and not _is_date(due):
        flash("Due date must be a date in YYYY-MM-DD format.", "error")
    else:
        do_checkout(db, copy, borrower, due, ensure_option(db, "condition", request.form.get("condition")),
                    clean(request.form.get("notes")))
        db.commit()
        flash(f"Checked out {copy['copy_number']} to {borrower}.", "success")
        if request.form.get("next") == "desk":
            return redirect(url_for("lending.desk"))
    return redirect(url_for("lending.copy_view", copy_id=copy_id))


@bp.route("/copies/<int:copy_id>/return", methods=["POST"])
def checkin(copy_id):
    db = get_db()
    copy = get_copy(db, copy_id)
    loan = open_checkout(db, copy_id)
    if not loan:
        flash(f"Copy {copy['copy_number']} isn't checked out.", "error")
        return redirect(url_for("lending.copy_view", copy_id=copy_id))
    condition = ensure_option(db, "condition", request.form.get("condition")) or copy["condition"]
    notes = clean(request.form.get("notes"))
    db.execute("UPDATE checkouts SET returned_on = ?, condition_in = ?, "
               "notes = COALESCE(notes || ' / ', '') || COALESCE(?, '') WHERE id = ?",
               (today(), condition, notes, loan["id"]))
    status = "Damaged" if condition == "Damaged" else "Available"
    db.execute("UPDATE copies SET status = ?, condition = ?, updated_at = datetime('now') WHERE id = ?",
               (status, condition, copy_id))
    record_history(db, "copies", copy_id, [("returned", loan["borrower"], f"condition: {condition or '—'}")])
    db.commit()
    flash(f"Checked in {copy['copy_number']} from {loan['borrower']}.", "success")
    if request.form.get("next") == "desk":
        return redirect(url_for("lending.desk"))
    return redirect(url_for("lending.copy_view", copy_id=copy_id))


def _is_date(value):
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- lending desk


@bp.route("/lending")
def desk():
    db = get_db()
    q = clean(request.args.get("q"))
    show = request.args.get("show", "out")
    where, params = ["k.returned_on IS NULL"], []
    if show == "overdue":
        where.append("k.due_date IS NOT NULL AND k.due_date < date('now')")
    elif show == "all":
        where = []
    if q:
        where.append("(k.borrower LIKE ? OR c.copy_number LIKE ? OR t.title LIKE ?)")
        params += [f"%{q}%"] * 3
    loans = db.execute(
        "SELECT k.*, c.copy_number, c.id AS copy_id, t.title, t.id AS textbook_id FROM checkouts k "
        "JOIN copies c ON c.id = k.copy_id JOIN textbooks t ON t.id = c.textbook_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY k.returned_on IS NOT NULL, k.due_date IS NULL, k.due_date, k.borrower COLLATE NOCASE",
        params).fetchall()
    counts = db.execute(
        "SELECT SUM(returned_on IS NULL) AS out, "
        "SUM(returned_on IS NULL AND due_date IS NOT NULL AND due_date < date('now')) AS overdue "
        "FROM checkouts").fetchone()
    return render_template("lending.html", loans=loans, q=q or "", show=show, counts=counts,
                           conditions=list_options(db, "condition"))


@bp.route("/lending/find")
def find():
    db = get_db()
    number = clean(request.args.get("copy"))
    row = find_copy(db, number) if number else None
    if row:
        return redirect(url_for("lending.copy_view", copy_id=row["id"]))
    flash(f"No copy numbered '{number}'." if number else "Type or scan a copy number.", "error")
    return redirect(url_for("lending.desk"))


@bp.route("/lending/export.csv")
def export_loans():
    db = get_db()
    rows = db.execute(
        "SELECT c.copy_number, t.title, k.borrower, k.checked_out_on, k.due_date, k.returned_on, "
        "k.condition_out, k.condition_in, k.notes FROM checkouts k JOIN copies c ON c.id = k.copy_id "
        "JOIN textbooks t ON t.id = c.textbook_id ORDER BY k.id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(rows[0].keys() if rows else ["copy_number", "title", "borrower"])
    w.writerows([["" if v is None else v for v in r] for r in rows])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=textbook-loans-{today()}.csv"})


# ---------------------------------------------------------------- copies CSV


@bp.route("/copies/export.csv")
def export_copies():
    db = get_db()
    rows = db.execute(
        "SELECT c.copy_number, t.isbn, t.title, t.edition, c.condition, l.name AS location, c.status, "
        "k.borrower, k.checked_out_on, k.due_date, c.notes FROM copies c "
        "JOIN textbooks t ON t.id = c.textbook_id LEFT JOIN locations l ON l.id = c.location_id "
        "LEFT JOIN checkouts k ON k.copy_id = c.id AND k.returned_on IS NULL "
        "ORDER BY t.title, c.copy_number").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COPY_CSV)
    w.writerows([["" if r[k] is None else r[k] for k in COPY_CSV] for r in rows])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=textbook-copies-{today()}.csv"})


@bp.route("/copies/import", methods=["GET", "POST"])
def import_copies():
    result = None
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            flash("Choose a CSV file to import.", "error")
            return redirect(url_for("lending.import_copies"))
        text = upload.read().decode("utf-8-sig", errors="replace")
        result = import_copy_rows(get_db(), csv.DictReader(io.StringIO(text)))
    return render_template("copies_import.html", result=result, columns=COPY_CSV)


def import_copy_rows(db, reader):
    """Adds/updates copies; creates the textbook title if needed; opens a loan if a borrower is given."""
    cols = {h: h.strip().lower().replace(" ", "_") for h in reader.fieldnames or []}
    result = {"added": 0, "updated": 0, "loans": 0, "errors": []}
    if "copy_number" not in cols.values():
        result["errors"].append("The CSV needs a 'copy_number' column.")
        return result
    for line, raw in enumerate(reader, start=2):
        row = {cols[h]: clean(v) for h, v in raw.items() if h in cols}
        if not any(row.values()):
            continue
        number = row.get("copy_number")
        if not number:
            result["errors"].append(f"Row {line}: copy_number is empty.")
            continue
        existing = db.execute("SELECT * FROM copies WHERE copy_number = ?", (number,)).fetchone()
        book = None
        if row.get("isbn"):
            book = db.execute("SELECT id FROM textbooks WHERE isbn = ?", (row["isbn"],)).fetchone()
        if not book and row.get("title"):
            book = db.execute("SELECT id FROM textbooks WHERE title = ? COLLATE NOCASE AND "
                              "COALESCE(edition, '') = COALESCE(?, '') COLLATE NOCASE",
                              (row["title"], row.get("edition"))).fetchone()
            if not book:
                book = {"id": db.execute("INSERT INTO textbooks (title, isbn, edition) VALUES (?, ?, ?)",
                                         (row["title"], row.get("isbn"), row.get("edition"))).lastrowid}
                record_history(db, "textbooks", book["id"], [("created", None, "CSV import")])
        if not book and not existing:
            result["errors"].append(f"Row {line}: give a title or an ISBN that's already in Textbooks.")
            continue
        status = row.get("status")
        if status and status.title() not in [s.title() for s in COPY_STATUSES]:
            result["errors"].append(f"Row {line}: status must be one of {', '.join(COPY_STATUSES)}.")
            continue
        status = next((s for s in COPY_STATUSES if status and s.lower() == status.lower()), None)
        values = {
            "textbook_id": book["id"] if book else existing["textbook_id"],
            "condition": ensure_option(db, "condition", row.get("condition")) if row.get("condition") else None,
            "location_id": get_or_create_location(db, row.get("location")) if row.get("location") else None,
            "notes": row.get("notes"),
        }
        if existing:
            sets = {k: v for k, v in values.items() if v is not None}
            if status and status != "Checked out":
                sets["status"] = status
            if sets:
                db.execute(f"UPDATE copies SET {', '.join(k + ' = ?' for k in sets)}, "
                           "updated_at = datetime('now') WHERE id = ?", (*sets.values(), existing["id"]))
                record_history(db, "copies", existing["id"], [("CSV import", None, ", ".join(sets))])
            copy_id = existing["id"]
            result["updated"] += 1
        else:
            copy_id = db.execute(
                "INSERT INTO copies (copy_number, textbook_id, condition, location_id, notes, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (number, values["textbook_id"], values["condition"], values["location_id"], values["notes"],
                 status if status and status != "Checked out" else "Available")).lastrowid
            record_history(db, "copies", copy_id, [("created", None, "CSV import")])
            result["added"] += 1
        borrower = row.get("borrower")
        if borrower and not open_checkout(db, copy_id):
            due = row.get("due_date") if row.get("due_date") and _is_date(row["due_date"]) else default_due_date()
            copy = db.execute("SELECT * FROM copies WHERE id = ?", (copy_id,)).fetchone()
            do_checkout(db, copy, borrower, due, None, None)
            if row.get("checked_out_on") and _is_date(row["checked_out_on"]):
                db.execute("UPDATE checkouts SET checked_out_on = ? WHERE copy_id = ? AND returned_on IS NULL",
                           (row["checked_out_on"], copy_id))
            result["loans"] += 1
    db.commit()
    return result
