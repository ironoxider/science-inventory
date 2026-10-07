"""The science department's record types: lab equipment, chemicals and textbook titles."""

from datetime import date, timedelta

from collections_engine import Collection, Field
from db import app_setting

DEFAULT_CHEMICAL_WARNING_DAYS = 90


def chemical_warning_days():
    return int(app_setting("chemical_warning_days", DEFAULT_CHEMICAL_WARNING_DAYS))


def soon_date():
    return (date.today() + timedelta(days=chemical_warning_days())).isoformat()


# ---------------------------------------------------------------- equipment


def equipment_cell(name, row):
    if name == "quantity" and row["min_quantity"] is not None and row["quantity"] < row["min_quantity"]:
        return "expired"
    if name == "condition" and row["condition"] in ("Needs Repair", "Damaged"):
        return "due-soon"
    return ""


EQUIPMENT = Collection(
    key="equipment", table="equipment", title="Lab Equipment", singular="item",
    display=lambda r: r["name"] + (f" ({r['size']})" if r["size"] else ""),
    intro="Count items like beakers by quantity. Give individual instruments (microscopes, "
          "balances, probes) their own asset tag and serial number.",
    natural_keys=[("asset_tag",), ("name", "size", "location")],
    fields=[
        Field("name", "Item", required=True, list=True, search=True, sort=True,
              placeholder="e.g. Beaker, Compound microscope"),
        Field("category", "Category", type="select", kind="equipment_category", list=True,
              filter=True, sort=True),
        Field("size", "Size / description", list=True, search=True,
              placeholder="e.g. 250 mL, 40x–1000x"),
        Field("quantity", "Quantity", type="int", required=True, default=1, minimum=0, list=True,
              sort=True),
        Field("min_quantity", "Reorder when below", type="int", minimum=0,
              help="Optional. The Equipment page flags the item when the quantity drops below this."),
        Field("location_id", "Location", type="location", group="Where", list=True),
        Field("condition", "Condition", type="select", kind="condition", group="Where", list=True,
              filter=True, sort=True),
        Field("asset_tag", "Asset tag", group="Identification", unique=True, list=True, search=True,
              sort=True, mono=True, help="Only for items tracked individually."),
        Field("manufacturer", "Manufacturer", group="Identification", search=True),
        Field("model", "Model", group="Identification", search=True),
        Field("model_number", "Model number", group="Identification", search=True, mono=True),
        Field("serial_number", "Serial number", group="Identification", search=True, mono=True),
        Field("supplier", "Supplier", group="Purchase", search=True,
              placeholder="e.g. Carolina, Flinn, Ward's"),
        Field("catalog_number", "Catalog number", group="Purchase", search=True, mono=True),
        Field("unit_cost", "Cost each ($)", type="number", group="Purchase", minimum=0),
        Field("purchase_date", "Purchase date", type="date", group="Purchase"),
        Field("notes", "Notes", type="textarea", group="Notes", search=True),
    ],
    custom_filters={
        "stock": {"low": ("Below reorder level",
                          "t.min_quantity IS NOT NULL AND t.quantity < t.min_quantity", None)},
    },
    cell_class=equipment_cell,
    photo={"ai": True, "targets": [["serial_number", "Serial #"], ["model_number", "Model #"],
                                   ["asset_tag", "Asset tag"], ["catalog_number", "Catalog #"]]},
)

# ---------------------------------------------------------------- chemicals


def chemical_cell(name, row):
    if name == "expiration_date" and row["expiration_date"]:
        if row["expiration_date"] < date.today().isoformat():
            return "expired"
        if row["expiration_date"] <= soon_date():
            return "due-soon"
    return ""


CHEMICALS = Collection(
    key="chemicals", table="chemicals", title="Chemicals", singular="chemical",
    display=lambda r: r["name"] + (f" {r['concentration']}" if r["concentration"] else ""),
    intro="One row per chemical and storage location. Record the total amount on hand and how many "
          "containers it's in.",
    natural_keys=[("name", "concentration", "location")],
    fields=[
        Field("name", "Chemical", required=True, list=True, search=True, sort=True,
              placeholder="e.g. Sodium chloride"),
        Field("concentration", "Concentration / grade", list=True, search=True,
              placeholder="e.g. 1 M, 95%, ACS reagent"),
        Field("cas_number", "CAS number", list=True, search=True, mono=True, placeholder="e.g. 7647-14-5"),
        Field("formula", "Formula", search=True, placeholder="e.g. NaCl"),
        Field("amount", "Amount on hand", type="number", group="Amount", minimum=0, list=True, sort=True),
        Field("unit", "Unit", type="select", kind="chemical_unit", group="Amount", list=True),
        Field("containers", "Containers", type="int", group="Amount", default=1, minimum=0, required=True),
        Field("location_id", "Storage location / cabinet", type="location", group="Storage & safety",
              list=True),
        Field("storage_group", "Storage group", type="select", kind="storage_group",
              group="Storage & safety", filter=True, sort=True,
              help="Keep incompatible groups (e.g. acids and bases, oxidizers and flammables) apart."),
        Field("hazards", "Hazards", type="multi", kind="hazard_class", group="Storage & safety",
              list=True, filter=True),
        Field("sds_url", "Safety Data Sheet (SDS) link", type="url", group="Storage & safety",
              placeholder="https://…"),
        Field("received_date", "Received", type="date", group="Dates"),
        Field("expiration_date", "Expires", type="date", group="Dates", list=True, sort=True),
        Field("supplier", "Supplier", group="Purchase", search=True),
        Field("catalog_number", "Catalog number", group="Purchase", search=True, mono=True),
        Field("notes", "Notes", type="textarea", group="Notes", search=True),
    ],
    custom_filters={
        "expiry": {
            "expired": ("Expired", "t.expiration_date IS NOT NULL AND t.expiration_date < date('now')",
                        None),
            "soon": ("Expiring soon", "t.expiration_date IS NOT NULL AND t.expiration_date >= date('now')"
                                      " AND t.expiration_date <= ?", lambda: [soon_date()]),
        },
    },
    cell_class=chemical_cell,
    photo={"ai": False, "targets": [["catalog_number", "Catalog #"], ["cas_number", "CAS #"]]},
)

# ---------------------------------------------------------------- textbook titles

TEXTBOOKS = Collection(
    key="textbooks", table="textbooks", title="Textbooks", singular="textbook",
    display=lambda r: r["title"] + (f" ({r['edition']})" if r["edition"] else ""),
    intro="Add each title once, then add its numbered copies on the title's page. Copies are what "
          "you check out to students.",
    natural_keys=[("isbn",), ("title", "edition")],
    default_sort="title",
    extra_select=(
        "(SELECT COUNT(*) FROM copies c WHERE c.textbook_id = t.id) AS copies_total, "
        "(SELECT COUNT(*) FROM copies c WHERE c.textbook_id = t.id AND c.status = 'Available') "
        "AS copies_available, "
        "(SELECT COUNT(*) FROM copies c WHERE c.textbook_id = t.id AND c.status = 'Checked out') "
        "AS copies_out"),
    extra_columns=[("copies_total", "Copies"), ("copies_available", "Available"),
                   ("copies_out", "Checked out")],
    fields=[
        Field("title", "Title", required=True, list=True, search=True, sort=True),
        Field("author", "Author(s)", list=True, search=True, sort=True),
        Field("isbn", "ISBN", list=True, search=True, mono=True,
              help="Type it, scan the barcode with a USB scanner, or use a photo of the barcode, "
                   "then click Look up to fill in the rest."),
        Field("edition", "Edition", list=True),
        Field("publisher", "Publisher", search=True),
        Field("year", "Year"),
        Field("subject", "Subject", type="select", kind="subject", group="Use", list=True, filter=True,
              sort=True),
        Field("course", "Course(s)", group="Use", search=True, placeholder="e.g. Biology 9, AP Chemistry"),
        Field("unit_cost", "Replacement cost ($)", type="number", group="Use", minimum=0),
        Field("notes", "Notes", type="textarea", group="Notes", search=True),
    ],
    photo={"ai": False, "isbn": True, "targets": [["isbn", "ISBN"]]},
    view_extra="textbook_copies.html",
    form_script="isbn.js",
)

COLLECTIONS = [EQUIPMENT, CHEMICALS, TEXTBOOKS]
