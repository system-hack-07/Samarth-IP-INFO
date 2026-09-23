#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
IP By samarth hacker — IP info proxy with primary + backup providers.
Serves frontend from parent directory + JSON API.
Strips upstream branding (channel, developer, promo fields).
"""

import base64
import json
import os
import time
import ipaddress

import requests
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS

# ------------------------------------------------------------------
# Obfuscated primary endpoint (XOR + base64)
# ------------------------------------------------------------------
_K = 0x5A

def _decode(s: str) -> str:
    return "".join(chr(b ^ _K) for b in base64.b64decode(s))

_PRIMARY_ENC = "LC0mOTo4MTM2LzowNTB0MiwzLjowNT0sLy86MTMsOi8wNTYyNT06MDQw"

PRIMARY_URL = os.environ.get("PRIMARY_URL") or _decode(_PRIMARY_ENC)
BACKUP_URL  = os.environ.get("BACKUP_URL")  or "https://ipwho.is/"

# ------------------------------------------------------------------
# Fields to strip from upstream responses
# ------------------------------------------------------------------
STRIP_FIELDS = {
    "channel", "developer", "promo", "advertisement", "ad",
    "sponsor", "telegram", "contact", "email", "website",
    "social", "credits", "powered_by", "poweredby", "source",
}

# ------------------------------------------------------------------
# Flask — serves index.html from PARENT directory
# ------------------------------------------------------------------
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))   # api/
ROOT_DIR     = os.path.abspath(os.path.join(BASE_DIR, "..")) # my-project/

app = Flask(__name__, static_folder=ROOT_DIR, static_url_path="")
CORS(app)

CACHE = {}
CACHE_TTL = 600


def cache_get(key):
    item = CACHE.get(key)
    if not item:
        return None
    if time.time() - item["ts"] > CACHE_TTL:
        CACHE.pop(key, None)
        return None
    return item["value"]


def cache_set(key, value):
    CACHE[key] = {"ts": time.time(), "value": value}


def is_valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def strip_branding(obj):
    """Recursively remove branded/promo keys from nested dicts/lists."""
    if isinstance(obj, dict):
        return {
            k: strip_branding(v)
            for k, v in obj.items()
            if k.lower() not in STRIP_FIELDS
        }
    if isinstance(obj, list):
        return [strip_branding(v) for v in obj]
    return obj


# ------------------------------------------------------------------
# Providers
# ------------------------------------------------------------------

def _normalize(d: dict) -> dict:
    keys = [
        "ip", "type", "city", "region", "country", "country_code",
        "continent", "postal", "latitude", "longitude", "timezone",
        "utc_offset", "isp", "org", "domain", "asn",
    ]
    return {k: d.get(k) for k in keys}


def fetch_primary(ip: str) -> dict:
    r = requests.get(PRIMARY_URL + ip, headers={"User-Agent": "ipsh/1.0"}, timeout=8)
    r.raise_for_status()
    raw = r.json()
    if not raw.get("success") or not raw.get("data"):
        raise ValueError("Primary returned no data")

    cleaned = strip_branding(raw)
    return _normalize(cleaned["data"])


def fetch_backup(ip: str) -> dict:
    r = requests.get(BACKUP_URL + ip, headers={"User-Agent": "ipsh/1.0"}, timeout=8)
    r.raise_for_status()
    raw = strip_branding(r.json())
    if not raw.get("success"):
        raise ValueError("Backup returned no data")

    conn = raw.get("connection") or {}
    tz   = raw.get("timezone") or {}

    return _normalize({
        "ip":           raw.get("ip"),
        "type":         raw.get("type"),
        "city":         raw.get("city"),
        "region":       raw.get("region"),
        "country":      raw.get("country"),
        "country_code": raw.get("country_code"),
        "continent":    raw.get("continent"),
        "postal":       raw.get("postal"),
        "latitude":     raw.get("latitude"),
        "longitude":    raw.get("longitude"),
        "timezone":     tz.get("id"),
        "utc_offset":   tz.get("utc"),
        "isp":          conn.get("isp"),
        "org":          conn.get("org"),
        "domain":       conn.get("domain"),
        "asn":          conn.get("asn"),
    })


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.route("/ip/<ip>", methods=["GET"])
def ip_lookup(ip: str):
    if not is_valid_ip(ip):
        return jsonify({"success": False, "error": "Invalid IP address"}), 400

    provider = request.args.get("provider", "auto").lower()
    if provider not in ("auto", "primary", "backup"):
        provider = "auto"

    cache_key = f"{provider}:{ip}"
    cached = cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    data = None
    used = None
    errors = []

    if provider in ("auto", "primary"):
        try:
            data = fetch_primary(ip)
            used = "primary"
        except Exception as e:
            errors.append(f"primary: {e}")

    if data is None and provider in ("auto", "backup"):
        try:
            data = fetch_backup(ip)
            used = "backup"
        except Exception as e:
            errors.append(f"backup: {e}")

    if data is None:
        return jsonify({
            "success": False,
            "error": "All providers failed",
            "details": errors,
        }), 502

    data = strip_branding(data)

    result = {
        "success": True,
        "provider": used,
        "data": data,
    }
    cache_set(cache_key, result)
    return jsonify(result)


@app.route("/providers", methods=["GET"])
def providers():
    return jsonify({
        "success": True,
        "providers": [
            {"id": "auto",    "name": "Auto (fallback chain)"},
            {"id": "primary", "name": "Primary API"},
            {"id": "backup",  "name": "Backup API"},
        ],
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "ipsh"})


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(ROOT_DIR, "index.html")


@app.errorhandler(404)
def not_found(_):
    return jsonify({"success": False, "error": "Not found"}), 404


@app.errorhandler(500)
def server_error(_):
    return jsonify({"success": False, "error": "Server error"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
