#!/usr/bin/env python3
import argparse
import json
import time
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(
        description="Fund, spend, and replay requests against the wallet demo."
    )
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()

    def call(method, path, body=None, key=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        payload = json.dumps(body).encode() if body is not None else None
        request = Request(args.api.rstrip("/") + path, data=payload, headers=headers, method=method)
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response), response.headers

    user_id = uuid4()
    path = f"/v1/wallets/{user_id}"
    _, wallet, _ = call("PUT", path)
    print(f"Wallet: {wallet['id']} (user {user_id})")
    key = str(uuid4())
    body = {"amount": "1000000", "source_account_id": "demo-customer-1"}
    status, receipt, _ = call("POST", path + "/top-ups", body, key)
    assert status == 202
    status_path = f"{path}/top-ups/{receipt['id']}"
    print(f"Funding intent: {receipt['id']}; waiting for provider confirmation...")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        _, payment, _ = call("GET", status_path)
        if payment["status"] != "PENDING":
            break
        time.sleep(1)
    assert payment["status"] == "SETTLED", payment
    _, replay, _ = call("POST", path + "/top-ups", body, key)
    assert replay == receipt

    debit_key = str(uuid4())
    _, entry, _ = call("POST", path + "/debits", {"amount": "250000"}, debit_key)
    _, replay, _ = call("POST", path + "/debits", {"amount": "250000"}, debit_key)
    assert replay == entry
    _, balance, _ = call("GET", path)
    _, history, _ = call("GET", path + "/entries")
    assert balance["balance"] == "750000"
    assert len(history["entries"]) == 2
    print(json.dumps({"wallet": balance, "history": history}, indent=2))
    print("Demo passed: provider funding, internal spending, and both idempotent replays.")


if __name__ == "__main__":
    main()
