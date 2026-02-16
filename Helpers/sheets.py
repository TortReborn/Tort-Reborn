import os
import requests
from Helpers.variables import test


def _get_url():
    if test:
        return os.getenv("TEST_SHEETS_SCRIPT_URL", "")
    return os.getenv("SHEETS_SCRIPT_URL", "")


def _post(payload: dict) -> dict:
    url = _get_url()
    if not url:
        return {"success": False, "error": "SHEETS_SCRIPT_URL not configured"}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def add_row(
    ticket: str,
    ign: str,
    recruiter: str,
    type_: str = "Member",
    paid: str = "NYP",
    recruiter_format: dict | None = None,
) -> dict:
    payload = {
        "action": "addRow",
        "ticket": ticket,
        "type": type_,
        "ign": ign,
        "recruiter": recruiter,
        "paid": paid,
    }
    if recruiter_format is not None:
        payload["recruiterFormat"] = recruiter_format
    return _post(payload)


def update_type(ign: str, type_: str) -> dict:
    return _post({
        "action": "updateType",
        "ign": ign,
        "type": type_,
    })


def update_paid(ign: str, paid: str) -> dict:
    return _post({
        "action": "updatePaid",
        "ign": ign,
        "paid": paid,
    })


def update_promo(ign: str, promo: str) -> dict:
    return _post({
        "action": "updatePromo",
        "ign": ign,
        "promo": promo,
    })


def find_by_ign(ign: str) -> dict:
    return _post({
        "action": "findByIGN",
        "ign": ign,
    })
