"""Read equipment and chemical labels from photos using Claude.

Requires an Anthropic API key, saved on the Settings page or set in the
ANTHROPIC_API_KEY environment variable.
"""

import base64
import json
import os
from datetime import date

import anthropic

DEFAULT_MODEL = "claude-opus-5"
ALLOWED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024

EXTRACT_FIELDS = ["manufacturer", "model", "model_number", "serial_number", "manufacture_date",
                  "category"]

PROMPT = """These photos show a piece of lab equipment or a device and/or its identification \
label (the sticker or plate on the bottom, back, or inside the battery bay). Read the label and identify the device \
so an inventory record can be filled in.

Fields:
- manufacturer: brand, e.g. "Dell", "HP", "Lenovo", "Apple", "Epson".
- model: marketing model name, e.g. "Latitude 5440", "MacBook Air 13-inch M2", "PowerLite 1781W".
- model_number: the regulatory/model/type number printed on the label, e.g. "P169G", \
"A2681", "20XW-00AB". For Lenovo, use the MTM ("Type"). For Apple, use the "Model Axxxx" number.
- serial_number: the serial number ("S/N", "Serial No.", Dell "Service Tag", Apple "Serial"). \
Copy it exactly as printed.
- manufacture_date: the manufacture date ("Mfg. Date", "Date of Manufacture", "MFD") as \
YYYY-MM-DD. If only a month and year are printed, use the 1st of that month and say so in notes. \
Use null if no manufacture date is printed; don't guess it from the model.
- category: one of the allowed values that best fits the device.

Rules:
- Only report text you can actually read. Use null for anything you can't read or aren't \
sure of - a blank field is much better than a wrong serial number.
- Don't confuse the serial with part numbers (P/N), UPC/EAN, MAC addresses, IMEI, FCC ID, \
or regulatory numbers.
- If a character is ambiguous (0/O, 1/I, 5/S, 8/B), say which in notes.
- notes: one short sentence about anything uncertain, or null if everything was clear."""


def _nullable_string():
    return {"type": ["string", "null"]}


def output_schema(categories):
    return {
        "type": "object",
        "properties": {
            "manufacturer": _nullable_string(),
            "model": _nullable_string(),
            "model_number": _nullable_string(),
            "serial_number": _nullable_string(),
            "manufacture_date": _nullable_string(),
            "category": {"anyOf": [{"type": "string", "enum": list(categories)}, {"type": "null"}]},
            "notes": _nullable_string(),
        },
        "required": EXTRACT_FIELDS + ["notes"],
        "additionalProperties": False,
    }


class ExtractionError(Exception):
    """A problem to show the user (bad input, API failure, refusal)."""


def is_configured(api_key=None):
    return bool(api_key or os.environ.get("ANTHROPIC_API_KEY"))


def _ask(images, prompt, schema, client=None, api_key=None):
    """Send the photos and prompt to Claude and return the parsed JSON answer.

    `images` is a list of (bytes, media_type) tuples.
    """
    if not images:
        raise ExtractionError("No photos were uploaded.")
    if len(images) > MAX_IMAGES:
        raise ExtractionError(f"Upload at most {MAX_IMAGES} photos at a time.")
    content = []
    for data, media_type in images:
        if media_type not in ALLOWED_MEDIA_TYPES:
            raise ExtractionError("Photos must be JPEG, PNG, WebP or GIF images.")
        if len(data) > MAX_IMAGE_BYTES:
            raise ExtractionError("A photo is too large (max 5 MB each).")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        })
    content.append({"type": "text", "text": prompt})

    # With no saved key, the SDK falls back to the ANTHROPIC_API_KEY environment variable.
    client = client or anthropic.Anthropic(api_key=api_key or None, timeout=90.0)
    try:
        response = client.beta.messages.create(
            model=os.environ.get("INVENTORY_AI_MODEL", DEFAULT_MODEL),
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError:
        raise ExtractionError("The Anthropic API key is invalid. Check it on the Settings page.")
    except anthropic.PermissionDeniedError:
        raise ExtractionError("The Anthropic API key doesn't have access to this model.")
    except anthropic.RateLimitError:
        raise ExtractionError("Too many requests right now. Wait a minute and try again.")
    except anthropic.BadRequestError as e:
        raise ExtractionError(f"The photo couldn't be processed: {e.message}")
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"The AI service returned an error ({e.status_code}). Try again.")
    except anthropic.APIConnectionError:
        raise ExtractionError("Couldn't reach the AI service. Check the internet connection.")

    if response.stop_reason == "refusal":
        raise ExtractionError("The AI declined to read this photo. Enter the details manually.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("The AI response was cut off. Try again with fewer photos.")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise ExtractionError("The AI didn't return any details. Try a clearer photo.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ExtractionError("The AI returned an unreadable answer. Try again.")


def _strings(result, keys):
    fields = {}
    for key in keys:
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            fields[key] = value.strip()
    return fields


def _notes(result):
    return result.get("notes") if isinstance(result.get("notes"), str) else None


def extract_device_info(images, categories, client=None, api_key=None):
    """Return a dict of equipment fields read from `images` (see _ask)."""
    result = _ask(images, PROMPT, output_schema(categories), client, api_key)
    fields = _strings(result, EXTRACT_FIELDS)
    if fields.get("category") not in categories:
        fields.pop("category", None)
    if "manufacture_date" in fields:
        try:
            parsed = date.fromisoformat(fields["manufacture_date"])
            if not (1980 <= parsed.year <= date.today().year):
                raise ValueError
        except ValueError:
            fields.pop("manufacture_date")
    return {"fields": fields, "notes": _notes(result)}


# ---------------------------------------------------------------- chemical labels

CHEMICAL_FIELDS = ["name", "concentration", "cas_number", "formula", "unit", "storage_group",
                   "expiration_date", "supplier", "catalog_number"]

CHEMICAL_PROMPT = """These photos show the label on a chemical bottle or jar in a school science \
lab. Most come from Flinn Scientific, but some are from other suppliers (Carolina, Ward's, \
Fisher, Sigma-Aldrich, etc.). Read the label so an inventory record can be filled in.

Flinn labels usually show the chemical name, a catalog number made of one or more letters and \
digits (e.g. "S0071", "H0034", "AP7146"), the amount in the bottle (e.g. "500 g", "100 mL"), \
the formula, the CAS number, a lot number, GHS hazard pictograms and/or a "Hazard Alert", and \
a "Flinn Suggested Chemical Storage Pattern" code such as "Inorganic #4" or "Organic #2". \
Other suppliers show similar information in different places.

Fields:
- name: the chemical name as printed, without the concentration, e.g. "Hydrochloric Acid", \
"Sodium Chloride", "Copper(II) Sulfate Pentahydrate". For a mixture or kit solution use its \
product name.
- concentration: concentration or grade, e.g. "6 M", "0.1 M", "3%", "95%", "Reagent", \
"Laboratory Grade", "ACS". null if none is printed.
- cas_number: CAS registry number, e.g. "7647-01-0". Copy it exactly.
- formula: chemical formula as printed, e.g. "HCl", "CuSO4·5H2O".
- amount: the quantity in this container as a number, e.g. 500 for "500 g". null if not printed.
- unit: one of the allowed units that matches the amount (convert nothing; pick the unit printed).
- storage_code: the supplier's storage code exactly as printed (e.g. "Inorganic #4", \
"Organic #2", "Inorganic #1 - Flammable"). null if none is printed.
- storage_group: the allowed storage group that best fits. Use the printed storage code and \
hazards if there are any, otherwise the chemistry (acids, bases, oxidizers, flammable organics, \
etc.). null if you aren't sure.
- hazards: every allowed hazard class shown by the label's GHS pictograms or hazard \
statements, e.g. flame -> Flammable, flame over circle -> Oxidizer, corrosion -> Corrosive, \
skull and crossbones -> Toxic, health hazard (silhouette) -> Health Hazard, exclamation mark -> \
Irritant, gas cylinder -> Compressed Gas, exploding bomb -> Explosive, environment -> \
Environmental Hazard. Use ["Non-hazardous"] only if the label says it isn't hazardous; \
[] if you can't tell.
- expiration_date: expiration or "use by" date as YYYY-MM-DD (end of month if only month and \
year are printed). null if none is printed; don't estimate a shelf life.
- supplier: the company that sold it, e.g. "Flinn Scientific".
- catalog_number: the supplier's catalog/product number (not the lot number, CAS number or UPC).
- lot_number: the lot or batch number. null if none.

Rules:
- Only report text you can actually read. Use null for anything you can't read or aren't sure \
of; a blank field is much better than a wrong CAS or catalog number.
- If a character is ambiguous (0/O, 1/I, 5/S, 8/B), say which in notes.
- notes: one short sentence about anything uncertain, or null if everything was clear."""


def _enum_or_null(values):
    if not values:
        return {"type": "null"}
    return {"anyOf": [{"type": "string", "enum": list(values)}, {"type": "null"}]}


def chemical_schema(units, storage_groups, hazard_classes):
    hazards = {"type": "array", "items": {"type": "string", "enum": list(hazard_classes)}} \
        if hazard_classes else {"type": "array", "items": {"type": "string"}}
    properties = {key: _nullable_string() for key in CHEMICAL_FIELDS + ["storage_code",
                                                                       "lot_number", "notes"]}
    properties.update({
        "amount": {"type": ["number", "null"]},
        "unit": _enum_or_null(units),
        "storage_group": _enum_or_null(storage_groups),
        "hazards": hazards,
    })
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _same(a, b):
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def extract_chemical_info(images, units, storage_groups, hazard_classes, client=None,
                          api_key=None):
    """Return a dict of chemical fields read from the label in `images` (see _ask).

    `hazards` comes back as a list. If the department names its storage groups after the
    supplier's codes (e.g. "Inorganic #4"), the printed code picks the group directly.
    """
    result = _ask(images, CHEMICAL_PROMPT,
                  chemical_schema(units, storage_groups, hazard_classes), client, api_key)
    fields = _strings(result, CHEMICAL_FIELDS)
    if fields.get("unit") not in units:
        fields.pop("unit", None)

    code = result.get("storage_code")
    code = code.strip() if isinstance(code, str) and code.strip() else None
    exact = next((g for g in storage_groups if code and _same(g, code)), None)
    if exact:
        fields["storage_group"] = exact
    elif fields.get("storage_group") not in storage_groups:
        fields.pop("storage_group", None)

    amount = result.get("amount")
    if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0:
        fields["amount"] = f"{amount:g}"

    hazards = result.get("hazards") if isinstance(result.get("hazards"), list) else []
    hazards = [h for h in hazard_classes if h in hazards]
    if hazards:
        fields["hazards"] = hazards

    if "expiration_date" in fields:
        try:
            parsed = date.fromisoformat(fields["expiration_date"])
            if not (1980 <= parsed.year <= date.today().year + 30):
                raise ValueError
        except ValueError:
            fields.pop("expiration_date")

    extra = []
    lot = result.get("lot_number")
    if isinstance(lot, str) and lot.strip():
        extra.append(f"Lot {lot.strip()}.")
    if code and not exact:
        extra.append(f"Storage code on label: {code}.")
    if extra:
        fields["notes"] = " ".join(extra)
    return {"fields": fields, "notes": _notes(result)}
