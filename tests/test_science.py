import io
import os
import re
import sqlite3
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402
from app import create_app  # noqa: E402

TODAY = date.today()


def days(n):
    return (TODAY + timedelta(days=n)).isoformat()


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db")})


@pytest.fixture
def client(app):
    return app.test_client()


def one(app, sql, *params):
    con = sqlite3.connect(app.config["DATABASE"])
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, params).fetchone()
    finally:
        con.close()


def upload(client, url, text):
    return client.post(url, data={"file": (io.BytesIO(text.encode()), "x.csv")},
                       content_type="multipart/form-data", follow_redirects=True)


def add_equipment(client, **fields):
    data = {"name": "Beaker", "size": "250 mL", "quantity": "24", "category": "Glassware",
            "new_location": "Room 210", "condition": "Good"}
    data.update(fields)
    return client.post("/equipment/new", data=data, follow_redirects=True)


def add_chemical(client, **fields):
    data = {"name": "Hydrochloric acid", "concentration": "1 M", "cas_number": "7647-01-0",
            "amount": "2.5", "unit": "L", "containers": "2", "new_location": "Acid cabinet",
            "storage_group": "Inorganic: Acids", "hazards": ["Corrosive", "Toxic"]}
    data.update(fields)
    return client.post("/chemicals/new", data=data, follow_redirects=True)


def add_textbook(client, **fields):
    data = {"title": "Biology 2e", "author": "Clark et al.", "isbn": "9781947172517",
            "edition": "2nd", "subject": "Biology"}
    data.update(fields)
    return client.post("/textbooks/new", data=data, follow_redirects=True)


def add_copies(client, textbook_id=1, **fields):
    data = {"prefix": "BIO-", "start": "1", "count": "3", "condition": "Good", "new_location": "Room 212"}
    data.update(fields)
    return client.post(f"/textbooks/{textbook_id}/copies/add", data=data, follow_redirects=True)


# ---------------------------------------------------------------- home & search


def test_home_page_counts_and_alerts(client):
    add_equipment(client, min_quantity="30")  # 24 < 30 -> low stock
    add_chemical(client, expiration_date=days(-1))
    add_textbook(client)
    add_copies(client)
    client.post("/copies/1/checkout", data={"borrower": "Ada Lovelace", "due_date": days(-2)})
    page = client.get("/").data.decode()
    assert "What do you want to do?" in page
    assert "1 chemical(s) past their expiration date" in page
    assert "1 textbook(s) overdue" in page
    assert "1 equipment item(s) below their reorder level" in page
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    assert "1 item, 24 pieces in total" in text and "3 copies of 1 title" in text


def test_search_everything_and_copy_number_shortcut(client):
    add_equipment(client, name="Compound microscope", size="", quantity="1", asset_tag="SCI-0042")
    add_chemical(client)
    add_textbook(client)
    add_copies(client)
    client.post("/copies/2/checkout", data={"borrower": "Grace Hopper"})
    assert "Compound microscope" in client.get("/search?q=SCI-0042").data.decode()
    assert "Hydrochloric acid" in client.get("/search?q=7647-01-0").data.decode()
    assert "Grace Hopper" in client.get("/search?q=hopper").data.decode()
    resp = client.get("/search?q=bio-002")
    assert resp.status_code == 302 and resp.location.endswith("/copies/2")


# ---------------------------------------------------------------- equipment


def test_equipment_add_view_edit_history(app, client):
    page = add_equipment(client).data.decode()
    assert "Added Beaker (250 mL)" in page and "Room 210" in page
    client.post("/equipment/1/edit", data={"name": "Beaker", "size": "250 mL", "quantity": "20",
                                           "category": "Glassware", "location_id": "1"})
    view = client.get("/equipment/1").data.decode()
    assert "Quantity" in view and "24 → 20" in view


def test_equipment_validation(client):
    page = add_equipment(client, name="", quantity="lots").data.decode()
    assert "Item is required" in page and "Quantity must be a whole number" in page
    add_equipment(client, name="Balance", quantity="1", asset_tag="SCI-1")
    assert "already used" in add_equipment(client, name="Other", asset_tag="sci-1").data.decode()


def test_equipment_low_stock_filter_and_new_category(app, client):
    add_equipment(client, name="Test tube", quantity="5", min_quantity="20")
    add_equipment(client, name="Petri dish", quantity="50", min_quantity="20", category="Plasticware")
    low = client.get("/equipment/?stock=low").data.decode()
    assert "Test tube" in low and "Petri dish" not in low
    assert "Plasticware" in client.get("/lists").data.decode()  # typed-in category added to the list


def test_equipment_form_has_photo_panel(client):
    page = client.get("/equipment/new").data.decode()
    assert 'id="photo-fill"' in page and "catalog_number" in page


def test_equipment_csv_round_trip_without_duplicates(app, client):
    add_equipment(client)
    csv_text = client.get("/equipment/export.csv").data.decode()
    assert csv_text.splitlines()[0].startswith("id,name,category,size,quantity")
    resp = upload(client, "/equipment/import",
                  "name,size,quantity,location,category\nBeaker,250 mL,30,Room 210,Glassware\n"
                  "Graduated cylinder,100 mL,12,Room 210,Glassware\n,,,,\nBad,,many,,\n")
    page = resp.data.decode()
    assert "Added 1, updated 1" in page and "Quantity must be a whole number" in page
    assert one(app, "SELECT quantity FROM equipment WHERE name = 'Beaker'")[0] == 30


# ---------------------------------------------------------------- chemicals


def test_chemical_hazards_and_expiry_filters(app, client):
    add_chemical(client, expiration_date=days(-10))
    add_chemical(client, name="Ethanol", concentration="95%", hazards=["Flammable"], expiration_date=days(30))
    add_chemical(client, name="Sodium chloride", concentration="", hazards=[], expiration_date=days(900))
    assert one(app, "SELECT hazards FROM chemicals WHERE id = 1")[0] == "Corrosive; Toxic"
    expired = client.get("/chemicals/?expiry=expired").data.decode()
    soon = client.get("/chemicals/?expiry=soon").data.decode()
    assert "Hydrochloric" in expired and "Ethanol" not in expired
    assert "Ethanol" in soon and "Sodium chloride" not in soon
    assert "Hydrochloric" in client.get("/chemicals/?hazards=Toxic").data.decode()


def test_chemical_sds_must_be_a_link(client):
    assert "must be a web address" in add_chemical(client, sds_url="sds.pdf").data.decode()
    page = add_chemical(client, sds_url="https://example.com/hcl-sds.pdf").data.decode()
    assert 'href="https://example.com/hcl-sds.pdf"' in page


def test_chemical_import_splits_hazards(app, client):
    page = upload(client, "/chemicals/import",
                  "name,concentration,amount,unit,containers,location,hazards,expiration_date\n"
                  "Acetone,ACS,4,L,1,Flammables cabinet,Flammable; Irritant,2027-05-01\n").data.decode()
    assert "Added 1, updated 0" in page
    row = one(app, "SELECT * FROM chemicals")
    assert row["hazards"] == "Flammable; Irritant" and row["amount"] == 4


def test_renaming_a_hazard_updates_chemicals_and_blocks_delete(app, client):
    add_chemical(client)
    opt = one(app, "SELECT id FROM options WHERE kind = 'hazard_class' AND name = 'Toxic'")[0]
    assert "1 record(s) use it" in client.post(f"/lists/option/{opt}/delete", follow_redirects=True).data.decode()
    client.post(f"/lists/option/{opt}/rename", data={"name": "Acute Toxicity"})
    assert one(app, "SELECT hazards FROM chemicals")[0] == "Corrosive; Acute Toxicity"


# ---------------------------------------------------------------- textbooks & lending


def test_isbn_lookup_api(client, monkeypatch):
    monkeypatch.setattr(app_module, "lookup_isbn",
                        lambda isbn: {"title": "Biology 2e", "author": "Mary Ann Clark", "year": "2018"})
    resp = client.get("/api/isbn?isbn=978-1-947172-51-7")
    assert resp.status_code == 200
    assert resp.get_json() == {"isbn": "9781947172517",
                               "fields": {"title": "Biology 2e", "author": "Mary Ann Clark", "year": "2018"}}
    assert client.get("/api/isbn?isbn=123").status_code == 400
    monkeypatch.setattr(app_module, "lookup_isbn", lambda isbn: None)
    assert client.get("/api/isbn?isbn=9780000000002").status_code == 404


def test_add_copies_numbering_and_skips_existing(app, client):
    add_textbook(client)
    page = add_copies(client, count="12").data.decode()
    assert "Added 12 copies" in page and "BIO-001" in page and "BIO-012" in page
    page = add_copies(client, start="11", count="3").data.decode()
    assert "Added 1 copy" in page and "Skipped 2" in page
    textbooks = client.get("/textbooks/").data.decode()
    assert re.search(r"<td>13</td>\s*<td>13</td>\s*<td>0</td>", textbooks)  # copies / available / out


def test_checkout_and_checkin_flow(app, client):
    add_textbook(client)
    add_copies(client)
    page = client.post("/copies/1/checkout", data={"borrower": "Ada Lovelace", "due_date": days(20),
                                                   "condition": "Good"}, follow_redirects=True).data.decode()
    assert "Checked out BIO-001 to Ada Lovelace" in page and "Check in" in page
    assert one(app, "SELECT status FROM copies WHERE id = 1")[0] == "Checked out"
    again = client.post("/copies/1/checkout", data={"borrower": "Someone"}, follow_redirects=True).data.decode()
    assert "be checked out" in again
    page = client.post("/copies/1/return", data={"condition": "Fair"}, follow_redirects=True).data.decode()
    assert "Checked in BIO-001 from Ada Lovelace" in page
    copy = one(app, "SELECT status, condition FROM copies WHERE id = 1")
    assert (copy["status"], copy["condition"]) == ("Available", "Fair")
    loan = one(app, "SELECT * FROM checkouts")
    assert loan["returned_on"] == TODAY.isoformat() and loan["condition_in"] == "Fair"
    assert "Ada Lovelace" in client.get("/copies/1").data.decode()  # loan history


def test_returned_damaged_and_lost_copies(app, client):
    add_textbook(client)
    add_copies(client)
    client.post("/copies/1/checkout", data={"borrower": "A"})
    client.post("/copies/1/return", data={"condition": "Damaged"})
    assert one(app, "SELECT status FROM copies WHERE id = 1")[0] == "Damaged"
    client.post("/copies/2/edit", data={"copy_number": "BIO-002", "status": "Lost"})
    assert "be checked out" in client.post("/copies/2/checkout", data={"borrower": "B"},
                                                 follow_redirects=True).data.decode()


def test_lending_desk_overdue_find_and_quick_checkin(app, client):
    add_textbook(client)
    add_copies(client)
    client.post("/copies/1/checkout", data={"borrower": "Late Larry", "due_date": days(-3)},
                follow_redirects=True)
    client.post("/copies/2/checkout", data={"borrower": "On-time Olivia", "due_date": days(30)},
                follow_redirects=True)
    overdue = client.get("/lending?show=overdue").data.decode()
    assert "Late Larry" in overdue and "On-time Olivia" not in overdue
    assert client.get("/lending/find?copy=bio-002").location.endswith("/copies/2")
    assert "No copy numbered" in client.get("/lending/find?copy=XYZ", follow_redirects=True).data.decode()
    client.post("/copies/1/return", data={"next": "desk"}, follow_redirects=True)
    assert "Late Larry" not in client.get("/lending").data.decode()
    assert "Late Larry" in client.get("/lending?show=all").data.decode()


def test_default_due_date_setting(client):
    add_textbook(client)
    add_copies(client)
    client.post("/settings", data={"textbook_due_date": days(100)})
    assert f'name="due_date" value="{days(100)}"' in client.get("/copies/1").data.decode()


def test_copies_import_creates_titles_and_loans(app, client):
    page = upload(client, "/copies/import",
                  "copy_number,isbn,title,edition,condition,location,borrower,due_date\n"
                  "CHEM-001,9780000000001,Chemistry: The Central Science,14th,Good,Room 214,Marie Curie,"
                  + days(50) + "\nCHEM-002,9780000000001,,,,Room 214,,\n"
                  "PHYS-001,,Conceptual Physics,,Fair,,,\n,,,,,,,\nX-1,,,,,,,\n").data.decode()
    assert "Added 3 copies" in page and "recorded 1 current loan" in page and "Row 6" in page
    assert one(app, "SELECT COUNT(*) FROM textbooks")[0] == 2
    assert one(app, "SELECT status FROM copies WHERE copy_number = 'CHEM-001'")[0] == "Checked out"
    page = upload(client, "/copies/import", "copy_number,condition\nCHEM-002,Poor\n").data.decode()
    assert "Added 0 copies, updated 1" in page


def test_textbook_delete_removes_copies(app, client):
    add_textbook(client)
    add_copies(client)
    client.post("/copies/1/checkout", data={"borrower": "A"})
    client.post("/textbooks/1/delete")
    assert one(app, "SELECT COUNT(*) FROM copies")[0] == 0
    assert one(app, "SELECT COUNT(*) FROM checkouts")[0] == 0


# ---------------------------------------------------------------- locations


def test_location_delete_blocked_while_in_use(app, client):
    add_textbook(client)
    add_copies(client)  # copies in Room 212
    loc = one(app, "SELECT id FROM locations WHERE name = 'Room 212'")[0]
    page = client.post(f"/locations/{loc}/delete", follow_redirects=True).data.decode()
    assert "3 record(s) are still at this location" in page
    page = client.get("/locations").data.decode()
    assert "Textbook copies" in page
