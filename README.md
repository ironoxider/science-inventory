# Science Inventory

A small web app for a science department to keep track of **lab equipment**, **textbooks** (with
checkout to students and teachers) and **chemicals**. It runs on Flask with a single SQLite file, so there
is no database server to set up. It started as a copy of the school's Device Inventory app and shares its
installer, photo/barcode reading and settings.

## Features

- **Start page** with the three areas, counts, and a "Needs attention" list: expired or soon-to-expire
  chemicals, overdue textbooks, and equipment below its reorder level.
- **Lab equipment**: item, category, size/description, **quantity** (e.g. 24 beakers, 250 mL), reorder level,
  location, condition. Individual instruments (microscopes, balances, probes) can also have an asset tag,
  manufacturer, model and serial number. Supplier, catalog number, cost and purchase date are optional.
- **Textbooks**: add each title once (type, scan or photograph the ISBN barcode and click **Look up** to fill in
  the title, author, publisher and year from Open Library), then **add numbered copies** in one go
  (e.g. BIO-001 … BIO-030).
  - **Check out / check in** each copy to a student or teacher, with a due date and the copy's condition going
    out and coming back. Every copy keeps its full loan history.
  - The **Lending** page lists what's out, what's overdue, and who has what. Scan or type a copy number there
    (a USB barcode scanner works) to jump straight to that copy.
- **Chemicals**: name, concentration/grade, CAS number, formula, amount and unit, number of containers,
  storage location/cabinet, **storage group** (to keep incompatibles apart), **hazard classes**, received and
  **expiration dates**, supplier and a link to the **Safety Data Sheet (SDS)**. Expired chemicals and those
  expiring within 90 days (adjustable) are flagged.
- **Search everything** from the box at the top: items, chemicals (including CAS numbers), titles, ISBNs,
  copy numbers and borrowers' names.
- **Drop-down lists you control** on the **Lists** page: equipment categories, conditions, hazard classes,
  storage groups, units and subjects. Rename (updates every record using it), reorder, add or remove.
  Forms also offer "+ Add new…".
- **History** of every change on every record.
- **CSV export and import** for equipment, chemicals and textbook titles, plus a **copies import** that loads
  existing textbook copies (and who currently has them) from a spreadsheet.
- **Fill from photos** on the equipment and chemical forms: barcodes are read in the browser; with an
  Anthropic API key, **Read label with AI** also reads the printed label.
  - Equipment: maker, model, model number and serial number.
  - Chemicals (tuned for **Flinn Scientific** labels, works with other suppliers too): name, concentration,
    CAS number, formula, amount and unit, catalog number, supplier, expiration date, hazard classes from the
    GHS pictograms, and a storage group. The lot number and Flinn's storage code (e.g. "Inorganic #4") go in
    the notes. If you rename your storage groups to Flinn's codes on the Lists page, the code on the label
    picks the group directly.
- **Phones**: turn on phone access in Settings and scan the QR code to use it from a phone on the same Wi-Fi.

## Download and run (no programming needed)

1. On GitHub, open this repository's **Actions** tab and click the latest successful **Build app** run.
   Under **Artifacts**, download the file for your computer:
   - **Windows:** `ScienceInventory-windows`
   - **Mac (Apple chip, 2020 or newer):** `ScienceInventory-mac-apple-silicon`
   - **Linux:** `ScienceInventory-linux`
2. Unzip the download (on a Mac there's a second zip inside; double-click that too).
3. Double-click **ScienceInventory**. A small window shows the address and your browser opens the app.
   **Keep that window open while you use it; close it to stop the app.**

The first time, your computer may warn you because the program isn't signed by a registered developer:
**Windows:** click **More info**, then **Run anyway**. **Mac:** System Settings → Privacy & Security →
**Open Anyway**.

Your data is saved in **Documents/Science Inventory/inventory.db**. Copy that file somewhere safe to back it up.
This app uses its own folder and port (5050), so it can run on the same computer as Device Inventory.

## Run from source

Use this on a Chromebook or an Intel Mac. You need Python 3.9 or newer
([python.org/downloads](https://www.python.org/downloads/); on Windows tick **"Add python.exe to PATH"**).

1. On GitHub, click **Code → Download ZIP** and unzip it.
2. Start it with the script for your computer (the first run installs what it needs, about a minute):
   - **Windows:** double-click **Start Science Inventory.bat**
   - **Mac:** double-click **Start Science Inventory.command** (the first time, right-click → **Open**)
   - **Chromebook / Linux:** in the Terminal, go to the folder and run `./start.sh`
     (if it can't create the Python environment, run `sudo apt install python3-venv` first)

When run from source, the data is stored in `inventory.db` in the app's folder.

## Getting started

1. **Locations:** add your rooms, prep rooms and cabinets (or type new ones while adding things).
2. **Lists:** adjust the categories, hazard classes and storage groups to match how your department works.
3. **Textbooks:** add a title, then **Add copies** on its page. Write or label each copy number inside the cover.
   If you already track copies in a spreadsheet, use **Textbooks → Import copies** instead.
4. **Settings:** set the default textbook due date (it otherwise uses June 30, the end of the school year)
   and how many days ahead to warn about expiring chemicals.

## CSV formats

Each list page has **Export CSV** and **Import CSV**; export first to see the exact columns. Importing a row
that has an `id` from an export, or that matches an existing record (equipment: asset tag, or item + size +
location; chemicals: name + concentration + location; textbooks: ISBN, or title + edition) **updates** it
instead of adding a duplicate. Blank cells leave existing values unchanged. Separate several hazards with `;`.

**Textbook copies** (`Textbooks → Import copies`):
`copy_number, isbn, title, edition, condition, location, status, borrower, checked_out_on, due_date, notes`.
Titles are matched by ISBN or title + edition and created if missing; a `borrower` records the copy as
checked out.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `INVENTORY_DATA_DIR` | see above | Folder for the database and `settings.json` |
| `INVENTORY_DB` | `<data dir>/inventory.db` | Path to the SQLite database file |
| `INVENTORY_HOST` | `0.0.0.0` (other devices are turned away unless phone access is on) | Address to listen on |
| `INVENTORY_PORT` | `5050` | First port to try |
| `ANTHROPIC_API_KEY` | unset | API key for AI label reading, if not saved on the Settings page |
| `INVENTORY_AI_MODEL` | `claude-opus-5` | Claude model used to read labels |

> **Note:** there is no login. Use it on your own computer or a trusted school network, and only turn on phone
> access on a network you trust. Settings (including the API key) can only be changed on the computer running it.

## Building the program yourself

```bash
pip install -r requirements.txt pyinstaller
pyinstaller ScienceInventory.spec     # result is in dist/
```

The **Build app** GitHub Actions workflow builds Windows, macOS and Linux versions on every push and checks
that each one starts. Pushing a tag such as `v1.0.0` also publishes them on the Releases page.

## Tests

```bash
pip install pytest
python -m pytest
```

## Third-party code

- `static/vendor/zxing.min.js`: [ZXing for JS](https://github.com/zxing-js/library) 0.21.3 (Apache 2.0, see
  `static/vendor/zxing-LICENSE.txt`), reads barcodes.
- `static/vendor/heic2any.min.js`: [heic2any](https://github.com/alexcorvi/heic2any) 0.0.4 (MIT), which includes
  [libheif](https://github.com/strukturag/libheif) (LGPL-3.0); see `static/vendor/heic2any-LICENSE.txt`. Converts
  iPhone HEIC photos.
- `static/vendor/qrcode.js`: [QR Code Generator](https://github.com/kazuhikoarase/qrcode-generator) 2.0.4
  (MIT, license in the file header), draws the phone QR code.
- Book details come from [Open Library](https://openlibrary.org/developers/api).
