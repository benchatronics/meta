import os
import hmac
import hashlib
import base64
import json
import requests

# === CONFIG ===
SECRET = os.environ.get("DEPOSIT_WEBHOOK_SECRET", "Vs6gP-XBbOK6H1cuIP6WUfuIynyqU-KEi-RlKeL5ImM")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "http://127.0.0.1:8000/deposit/webhook/confirm/")

# This reference must match a real DepositRequest.reference in your DB
body = {
    "reference": "1TBACVPUQXJD",  # e.g. dep.reference from your DB
    "network": "ETH",                           # optional but useful
    "txid": "0xDEADBEEF...",                    # optional but useful (store it in view)
    "amount": "100.00"                          # optional; you can also verify it in the view
}

payload = json.dumps(body, separators=(",", ":")).encode("utf-8")

# 1) Create HMAC-SHA256
digest = hmac.new(SECRET.encode("utf-8"), payload, hashlib.sha256).digest()

# 2) Base64 encode
sig = base64.b64encode(digest).decode("ascii")

# 3) Send
resp = requests.post(
    WEBHOOK_URL,
    headers={
        "Content-Type": "application/json",
        "X-DEP-SIGN": sig
    },
    data=payload,
    timeout=10,
)

print(resp.status_code, resp.text)
