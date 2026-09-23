#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
IP By samarth hacker — IP info proxy with Primary, Backup, and Security providers.
"""

import base64
import json
import os
import time
import ipaddress

import requests
import urllib3
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------
_K = 0x5A

def _decode(s: str) -> str:
    return "".join(chr(b ^ _K) for b in base64.b64decode(s))

_PRIMARY_ENC = "LC0mOTo4MTM2LzowNTB0MiwzLjowNT0sLy86MTMsOi8wNTYyNT06MDQw"
PRIMARY_URL = os.environ.get("PRIMARY_URL") or _decode(_PRIMARY_ENC)
BACKUP_URL  = os.environ.get("BACKUP_URL")  or "https://ipwho.is/"
IPLOCATE_KEY = os.environ.get("IPLOCATE_KEY", "")

# ------------------------------------------------------------------
# Branding fields to strip
# ------------------------------------------------------------------
STRIP_FIELDS = {
    "channel", "developer", "promo", "advertisement", "ad",
    "sponsor", "telegram", "contact", "email", "website",
    "social", "credits", "powered_by", "poweredby", "source",
}

# ------------------------------------------------------------------
# Flask
# ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))

app = Flask(__name__, static_folder=ROOT_DIR, static_url_path="")
CORS(app)

CACHE = {}
CACHE_TTL = 600

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

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
    if isinstance(obj, dict):
        return {k: strip_branding(v) for k, v in obj.items() if k.lower() not in STRIP_FIELDS}
    if isinstance(obj, list):
        return [strip_branding(v) for v in obj]
    return obj

def _normalize(d: dict) -> dict:
    keys = [
        "ip", "type", "city", "region", "country", "country_code",
        "continent", "postal", "latitude", "longitude", "timezone",
        "utc_offset", "isp", "org", "domain", "asn",
        "currency_code", "currency_symbol", "calling_code",
        "is_vpn", "is_proxy", "is_tor", "is_datacenter", "is_mobile",
        "is_abuser", "is_icloud_relay", "risk_score",
        "company_name", "company_domain", "company_type",
        "abuse_email", "abuse_phone",
    ]
    return {k: d.get(k) for k in keys}

# ------------------------------------------------------------------
# Primary — YOUR API
# ------------------------------------------------------------------
def fetch_primary(ip: str) -> dict:
    url = PRIMARY_URL.rstrip("/") + "/" + ip
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=15, verify=False)
    r.raise_for_status()
    raw = r.json()
    if not raw.get("success") or not raw.get("data"):
        raise ValueError("Primary returned no data")
    return _normalize(strip_branding(raw)["data"])

# ------------------------------------------------------------------
# Backup — ipwho.is [citation:18]
# ------------------------------------------------------------------
def fetch_backup(ip: str) -> dict:
    url = BACKUP_URL.rstrip("/") + "/" + ip
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=15, verify=False)
    r.raise_for_status()
    raw = strip_branding(r.json())
    if not raw.get("success"):
        raise ValueError("Backup returned no data")
    
    conn = raw.get("connection") or {}
    tz   = raw.get("timezone") or {}
    curr = raw.get("currency") or {}

    return _normalize({
        "ip": raw.get("ip"), "type": raw.get("type"),
        "city": raw.get("city"), "region": raw.get("region"),
        "country": raw.get("country"), "country_code": raw.get("country_code"),
        "continent": raw.get("continent"), "postal": raw.get("postal"),
        "latitude": raw.get("latitude"), "longitude": raw.get("longitude"),
        "timezone": tz.get("id"), "utc_offset": tz.get("utc"),
        "isp": conn.get("isp"), "org": conn.get("org"),
        "domain": conn.get("domain"), "asn": conn.get("asn"),
        "currency_code": curr.get("code"),
        "currency_symbol": curr.get("symbol"),
    })

# ------------------------------------------------------------------
# Security — IPLocate.io (VPN/Proxy/Tor + Currency/Company/Abuse) [citation:1]
# ------------------------------------------------------------------
def fetch_security(ip: str) -> dict:
    if not IPLOCATE_KEY:
        raise ValueError("IPLOCATE_KEY not set")
    url = f"https://iplocate.io/api/lookup/{ip}?apikey={IPLOCATE_KEY}"
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=15, verify=False)
    r.raise_for_status()
    raw = r.json()
    
    privacy = raw.get("privacy") or {}
    company = raw.get("company") or {}
    abuse   = raw.get("abuse") or {}
    asn_obj = raw.get("asn") or {}

    return _normalize({
        "ip": raw.get("ip"), "type": "IPv4" if "." in ip else "IPv6",
        "city": raw.get("city"), "region": raw.get("subdivision"),
        "country": raw.get("country"), "country_code": raw.get("country_code"),
        "continent": raw.get("continent"), "postal": raw.get("postal_code"),
        "latitude": raw.get("latitude"), "longitude": raw.get("longitude"),
        "timezone": raw.get("time_zone"),
        "isp": asn_obj.get("name"), "org": asn_obj.get("name"),
        "domain": asn_obj.get("domain"), "asn": asn_obj.get("asn"),
        "currency_code": raw.get("currency_code"),
        "calling_code": raw.get("calling_code"),
        "is_vpn": privacy.get("is_vpn"),
        "is_proxy": privacy.get("is_proxy"),
        "is_tor": privacy.get("is_tor"),
        "is_datacenter": privacy.get("is_hosting"),
        "is_abuser": privacy.get("is_abuser"),
        "is_icloud_relay": privacy.get("is_icloud_relay"),
        "is_mobile": None,
        "risk_score": None,
        "company_name": company.get("name"),
        "company_domain": company.get("domain"),
        "company_type": company.get("type"),
        "abuse_email": abuse.get("email"),
        "abuse_phone": abuse.get("phone"),
    })

# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------
@app.route("/ip/<ip>", methods=["GET"])
def ip_lookup(ip: str):
    if not is_valid_ip(ip):
        return jsonify({"success": False, "error": "Invalid IP address"}), 400

    provider = request.args.get("provider", "auto").lower()
    if provider not in ("auto", "primary", "backup", "security"):
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
            errors.append(f"primary: {type(e).__name__}: {e}")

    if data is None and provider in ("auto", "backup"):
        try:
            data = fetch_backup(ip)
            used = "backup"
        except Exception as e:
            errors.append(f"backup: {type(e).__name__}: {e}")

    if data is None and provider in ("auto", "security") and IPLOCATE_KEY:
        try:
            data = fetch_security(ip)
            used = "security"
        except Exception as e:
            errors.append(f"security: {type(e).__name__}: {e}")

    if data is None:
        return jsonify({
            "success": False,
            "error": "All providers failed",
            "details": errors,
        }), 502

    data = strip_branding(data)
    result = {"success": True, "provider": used, "data": data}
    cache_set(cache_key, result)
    return jsonify(result)

@app.route("/providers", methods=["GET"])
def providers():
    return jsonify({
        "success": True,
        "providers": [
            {"id": "auto",     "name": "Auto (primary → backup → security)"},
            {"id": "primary",  "name": "Your API"},
            {"id": "backup",   "name": "ipwho.is"},
            {"id": "security", "name": "IPLocate.io (VPN/Proxy/Tor/Currency)"},
        ],
    })

@app.route("/cache/clear", methods=["GET"])
def clear_cache():
    n = len(CACHE)
    CACHE.clear()
    return jsonify({"success": True, "cleared": n})

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
    print(f"→ Primary: {PRIMARY_URL}")
    print(f"→ Backup:  {BACKUP_URL}")
    print(f"→ Security: {'enabled' if IPLOCATE_KEY else 'disabled (no IPLOCATE_KEY)'}")
    app.run(host="0.0.0.0", port=port, debug=False)
