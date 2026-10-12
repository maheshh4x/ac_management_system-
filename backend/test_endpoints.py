"""Test all Flask API endpoints and report status."""
import urllib.request
import json
import time

time.sleep(2)

base = "http://localhost:5000"
endpoints = [
    "/api/health",
    "/api/db-test",
    "/api/blocks",
    "/api/acs",
    "/api/dashboard",
    "/api/dashboard/ac-status",
    "/api/dashboard/maintenance-summary",
]

for ep in endpoints:
    try:
        resp = urllib.request.urlopen(base + ep, timeout=25)
        body = json.loads(resp.read())
        stat = body.get("status", "?")
        print(f"GET {ep}: {resp.status} -> status={stat}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        print(f"GET {ep}: {e.code} ERROR -> {raw[:400]}")
    except Exception as e:
        print(f"GET {ep}: EXCEPTION -> {e}")
