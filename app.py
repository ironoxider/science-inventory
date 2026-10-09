"""Science Inventory: lab equipment, chemicals and textbooks (with lending) for a science department.

A small Flask + SQLite web app. Run with:  python launcher.py
"""

import ipaddress
import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for

import app_settings
import lending
import photo_extract
from collections_engine import NEW_OPTION, make_blueprint
from db import (MULTI_SEP, OPTION_TITLES, all_locations, clean, close_db, ensure_option,
                find_option, get_db, init_db, list_options, location_usage, option_usage,
                rename_option_values)
from science import COLLECTIONS, DEFAULT_CHEMICAL_WARNING_DAYS, chemical_warning_days, soon_date


def create_app(test_config=None):
    app = Flask(__name__,
                template_folder=os.path.join(app_settings.RESOURCE_DIR, "templates"),
                static_folder=os.path.join(app_settings.RESOURCE_DIR, "static"))
    app.config.update(DATABASE=app_settings.default_db_path(), MAX_CONTENT_LENGTH=25 * 1024 * 1024)
    if test_config:
        app.config.update(test_config)
    os.makedirs(os.path.dirname(os.path.abspath(app.config["DATABASE"])), exist_ok=True)
    app.config.setdefault("SETTINGS_PATH", app_settings.settings_path(app.config["DATABASE"]))
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = app_settings.secret_key(app.config["SETTINGS_PATH"])

    app.teardown_appcontext(close_db)
    app.before_request(check_remote_access)
    with app.app_context():
        init_db()

    for coll in COLLECTIONS:
        app.register_blueprint(make_blueprint(coll))
    app.register_blueprint(lending.bp)
    register_routes(app)
    app.jinja_env.globals.update(
        APP_NAME=app_settings.APP_NAME,
        APP_VERSION=app_settings.APP_VERSION,
        NEW_OPTION=NEW_OPTION,
        MULTI_SEP=MULTI_SEP,
        ai_enabled=lambda: photo_extract.is_configured(saved_api_key()),
        today=lambda: date.today().isoformat(),
        soon_date=soon_date,
    )
    return app


# ---------------------------------------------------------------- access & settings helpers


def is_this_computer(addr):
    try:
        if ipaddress.ip_address(addr or "").is_loopback:
            return True
    except ValueError:
        return False
    return addr in app_settings.lan_addresses()


def check_remote_access():
    """Turn away other devices unless phone access is on; Settings stay local-only."""
    from flask import current_app

    if is_this_computer(request.remote_addr):
        return None
    if not app_settings.load(current_app.config["SETTINGS_PATH"]).get("allow_network", False):
        return render_template("remote_blocked.html", reason="off"), 403
    if request.endpoint == "settings":
        return render_template("remote_blocked.html", reason="settings"), 403
    return None


def phone_access_info(app):
    port = app.config.get("LISTEN_PORT")
    urls = [f"http://{a}:{port}/" for a in app_settings.lan_addresses()] if port else []
    return {"listening": bool(app.config.get("LISTENS_ON_NETWORK")), "urls": urls}


def saved_api_key():
    from flask import current_app

    return app_settings.load(current_app.config["SETTINGS_PATH"]).get("anthropic_api_key")


def normalize_isbn(value):
    isbn = re.sub(r"[^0-9Xx]", "", value or "").upper()
    return isbn if len(isbn) in (10, 13) else None


def lookup_isbn(isbn):
    """Book details from Open Library, or None if not found."""
    url = ("https://openlibrary.org/api/books?" +
           urllib.parse.urlencode({"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"}))
    req = urllib.request.Request(url, headers={"User-Agent": f"{app_settings.APP_NAME} (school inventory)"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.load(resp).get(f"ISBN:{isbn}")
    if not data:
        return None
    title = data.get("title", "")
    if data.get("subtitle"):
        title += f": {data['subtitle']}"
    year = re.search(r"\d{4}", data.get("publish_date") or "")
    return {
        "title": title or None,
        "author": ", ".join(a["name"] for a in data.get("authors", []) if a.get("name")) or None,
        "publisher": ", ".join(p["name"] for p in data.get("publishers", []) if p.get("name")) or None,
        "year": year.group(0) if year else None,
        "edition": data.get("edition_name") or None,
    }


# ---------------------------------------------------------------- routes


def register_routes(app):
    @app.route("/")
    def home():
        db = get_db()
        eq = db.execute(
            "SELECT COUNT(*) AS items, COALESCE(SUM(quantity), 0) AS pieces, "
            "SUM(min_quantity IS NOT NULL AND quantity < min_quantity) AS low, "
            "SUM(condition IN ('Needs Repair', 'Damaged')) AS repair FROM equipment").fetchone()
        ch = db.execute(
            "SELECT COUNT(*) AS items, COALESCE(SUM(containers), 0) AS containers, "
            "SUM(expiration_date < date('now')) AS expired, "
            "SUM(expiration_date >= date('now') AND expiration_date <= ?) AS soon FROM chemicals",
            (soon_date(),)).fetchone()
        tb = db.execute(
            "SELECT (SELECT COUNT(*) FROM textbooks) AS titles, (SELECT COUNT(*) FROM copies) AS copies, "
            "(SELECT COUNT(*) FROM copies WHERE status = 'Available') AS available, "
            "(SELECT COUNT(*) FROM checkouts WHERE returned_on IS NULL) AS out, "
            "(SELECT COUNT(*) FROM checkouts WHERE returned_on IS NULL AND due_date < date('now')) "
            "AS overdue").fetchone()
        return render_template("home.html", eq=eq, ch=ch, tb=tb, warn_days=chemical_warning_days())

    @app.route("/search")
    def search():
        q = clean(request.args.get("q"))
        if not q:
            return redirect(url_for("home"))
        db = get_db()
        copy = lending.find_copy(db, q)
        if copy:
            return redirect(url_for("lending.copy_view", copy_id=copy["id"]))
        from collections_engine import query
        results = [(coll, query(coll, db, {"q": q}, limit=50)) for coll in COLLECTIONS]
        loans = db.execute(
            "SELECT k.*, c.copy_number, c.id AS copy_id, t.title FROM checkouts k "
            "JOIN copies c ON c.id = k.copy_id JOIN textbooks t ON t.id = c.textbook_id "
            "WHERE k.returned_on IS NULL AND k.borrower LIKE ? ORDER BY k.borrower", (f"%{q}%",)).fetchall()
        return render_template("search.html", q=q, results=results, loans=loans)

    # ---- locations
    @app.route("/locations", methods=["GET", "POST"])
    def locations():
        db = get_db()
        if request.method == "POST":
            name = clean(request.form.get("name"))
            if not name:
                flash("Location name is required.", "error")
            else:
                try:
                    db.execute("INSERT INTO locations (name, building, room, notes) VALUES (?, ?, ?, ?)",
                               (name, clean(request.form.get("building")), clean(request.form.get("room")),
                                clean(request.form.get("notes"))))
                    db.commit()
                    flash(f"Added location {name}.", "success")
                except sqlite3.IntegrityError:
                    flash(f"A location named '{name}' already exists.", "error")
            return redirect(url_for("locations"))
        rows = [(loc, location_usage(db, loc["id"])) for loc in all_locations(db)]
        return render_template("locations.html", rows=rows)

    @app.route("/locations/<int:location_id>/edit", methods=["GET", "POST"])
    def location_edit(location_id):
        db = get_db()
        loc = db.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
        if loc is None:
            abort(404)
        if request.method == "POST":
            name = clean(request.form.get("name"))
            if not name:
                flash("Location name is required.", "error")
            else:
                try:
                    db.execute("UPDATE locations SET name = ?, building = ?, room = ?, notes = ? WHERE id = ?",
                               (name, clean(request.form.get("building")), clean(request.form.get("room")),
                                clean(request.form.get("notes")), location_id))
                    db.commit()
                    flash("Location saved.", "success")
                    return redirect(url_for("locations"))
                except sqlite3.IntegrityError:
                    flash(f"A location named '{name}' already exists.", "error")
        return render_template("location_form.html", location=loc)

    @app.route("/locations/<int:location_id>/delete", methods=["POST"])
    def location_delete(location_id):
        db = get_db()
        used = sum(location_usage(db, location_id).values())
        if used:
            flash(f"Can't delete: {used} record(s) are still at this location. Move them first.", "error")
        else:
            db.execute("DELETE FROM locations WHERE id = ?", (location_id,))
            db.commit()
            flash("Location deleted.", "success")
        return redirect(url_for("locations"))

    # ---- managed lists
    @app.route("/lists")
    def lists():
        db = get_db()
        sections = []
        for kind, title in OPTION_TITLES.items():
            rows = [dict(r, used=option_usage(db, kind, r["name"])) for r in db.execute(
                "SELECT id, name FROM options WHERE kind = ? ORDER BY sort_order, id", (kind,))]
            sections.append({"kind": kind, "title": title, "rows": rows})
        return render_template("lists.html", sections=sections)

    def get_option(option_id):
        row = get_db().execute("SELECT * FROM options WHERE id = ?", (option_id,)).fetchone()
        if row is None:
            abort(404)
        return row

    @app.route("/lists/<kind>/add", methods=["POST"])
    def option_add(kind):
        if kind not in OPTION_TITLES:
            abort(404)
        db = get_db()
        name = clean(request.form.get("name"))
        if not name:
            flash("Type a name to add.", "error")
        elif find_option(db, kind, name):
            flash(f"'{name}' is already in the list.", "error")
        else:
            ensure_option(db, kind, name)
            db.commit()
            flash(f"Added '{name}'.", "success")
        return redirect(url_for("lists") + f"#{kind}")

    @app.route("/lists/option/<int:option_id>/rename", methods=["POST"])
    def option_rename(option_id):
        db = get_db()
        opt = get_option(option_id)
        kind, old, new = opt["kind"], opt["name"], clean(request.form.get("name"))
        clash = new and db.execute("SELECT 1 FROM options WHERE kind = ? AND name = ? AND id != ?",
                                   (kind, new, option_id)).fetchone()
        if not new:
            flash("The name can't be blank.", "error")
        elif clash:
            flash(f"'{new}' is already in the list.", "error")
        elif new != old:
            db.execute("UPDATE options SET name = ? WHERE id = ?", (new, option_id))
            changed = rename_option_values(db, kind, old, new)
            db.commit()
            flash(f"Renamed '{old}' to '{new}'" + (f" on {len(changed)} record(s)." if changed else "."),
                  "success")
        return redirect(url_for("lists") + f"#{kind}")

    @app.route("/lists/option/<int:option_id>/move", methods=["POST"])
    def option_move(option_id):
        db = get_db()
        opt = get_option(option_id)
        ids = [r["id"] for r in db.execute("SELECT id FROM options WHERE kind = ? ORDER BY sort_order, id",
                                           (opt["kind"],))]
        i = ids.index(option_id)
        j = i - 1 if request.form.get("direction") == "up" else i + 1
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
        db.executemany("UPDATE options SET sort_order = ? WHERE id = ?", list(enumerate(ids)))
        db.commit()
        return redirect(url_for("lists") + f"#{opt['kind']}")

    @app.route("/lists/option/<int:option_id>/delete", methods=["POST"])
    def option_delete(option_id):
        db = get_db()
        opt = get_option(option_id)
        used = option_usage(db, opt["kind"], opt["name"])
        if used:
            flash(f"Can't remove '{opt['name']}': {used} record(s) use it. Change them first, "
                  "or rename it instead.", "error")
        else:
            db.execute("DELETE FROM options WHERE id = ?", (option_id,))
            db.commit()
            flash(f"Removed '{opt['name']}'.", "success")
        return redirect(url_for("lists") + f"#{opt['kind']}")

    # ---- AI label reading and ISBN lookup
    @app.route("/api/extract", methods=["POST"])
    def api_extract():
        api_key = saved_api_key()
        if not photo_extract.is_configured(api_key):
            return jsonify(error="AI photo reading isn't set up. Add an Anthropic API key on the "
                                 "Settings page."), 503
        images = [(f.read(), f.mimetype) for f in request.files.getlist("photos") if f and f.filename]
        db = get_db()
        try:
            if request.args.get("kind") == "chemicals":
                result = photo_extract.extract_chemical_info(
                    images, list_options(db, "chemical_unit"), list_options(db, "storage_group"),
                    list_options(db, "hazard_class"), api_key=api_key)
            else:
                result = photo_extract.extract_device_info(
                    images, list_options(db, "equipment_category"), api_key=api_key)
        except photo_extract.ExtractionError as e:
            return jsonify(error=str(e)), 400
        return jsonify(result)

    @app.route("/api/isbn")
    def api_isbn():
        isbn = normalize_isbn(request.args.get("isbn"))
        if not isbn:
            return jsonify(error="An ISBN has 10 or 13 digits."), 400
        try:
            book = lookup_isbn(isbn)
        except (urllib.error.URLError, TimeoutError, ValueError):
            return jsonify(error="Couldn't reach the book lookup service (Open Library). "
                                 "Check the internet connection, or fill in the details by hand."), 502
        if not book:
            return jsonify(error=f"No book found for ISBN {isbn}. Fill in the details by hand."), 404
        return jsonify(isbn=isbn, fields={k: v for k, v in book.items() if v})

    # ---- settings
    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        path = app.config["SETTINGS_PATH"]
        if request.method == "POST":
            changes = {"allow_network": bool(request.form.get("allow_network"))}
            try:
                days = int(request.form.get("chemical_warning_days", ""))
                if 0 <= days <= 730:
                    changes["chemical_warning_days"] = days
            except ValueError:
                pass
            due = clean(request.form.get("textbook_due_date"))
            if due:
                try:
                    changes["textbook_due_date"] = date.fromisoformat(due).isoformat()
                except ValueError:
                    flash("The default due date must be a date.", "error")
            new_key = clean(request.form.get("anthropic_api_key"))
            if request.form.get("remove_key"):
                changes["anthropic_api_key"] = None
            elif new_key:
                if photo_extract.is_admin_key(new_key):
                    flash(photo_extract.WRONG_KEY_MESSAGE, "error")
                    return redirect(url_for("settings"))
                elif not new_key.startswith("sk-ant-"):
                    flash("That doesn't look like an Anthropic API key (they start with sk-ant-).", "error")
                    return redirect(url_for("settings"))
                changes["anthropic_api_key"] = new_key
            before = app_settings.load(path).get("allow_network", False)
            app_settings.update(path, **changes)
            msg = "Settings saved."
            if changes["allow_network"] != before:
                msg += " Phone access is now on." if changes["allow_network"] else " Phone access is now off."
            flash(msg, "success")
            return redirect(url_for("settings"))
        data = app_settings.load(path)
        key = data.get("anthropic_api_key")
        return render_template(
            "settings.html",
            key_hint=f"…{key[-4:]}" if key else None,
            env_key=bool(os.environ.get("ANTHROPIC_API_KEY")),
            allow_network=data.get("allow_network", False),
            chemical_warning_days=data.get("chemical_warning_days", DEFAULT_CHEMICAL_WARNING_DAYS),
            textbook_due_date=lending.default_due_date(),
            db_path=os.path.abspath(app.config["DATABASE"]),
            phone=phone_access_info(app),
        )


if __name__ == "__main__":
    application = create_app()
    application.run(host=os.environ.get("INVENTORY_HOST", "127.0.0.1"),
                    port=int(os.environ.get("INVENTORY_PORT", "5050")),
                    debug=os.environ.get("INVENTORY_DEBUG") == "1")
