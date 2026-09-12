#!/usr/bin/env python3
"""Add OmniLion to an existing LiteLLM key without exposing either key."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def read_secrets() -> tuple[str, str]:
    master_key = sys.stdin.readline().rstrip("\r\n")
    deployment_key = sys.stdin.readline().rstrip("\r\n")
    if not master_key or not deployment_key:
        raise SystemExit("expected master key and deployment key as two stdin lines")
    return master_key, deployment_key


def open_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read()).get("error", {}).get("message")
        except Exception:
            detail = "non-JSON response"
        raise SystemExit(f"LiteLLM returned HTTP {error.code}: {str(detail)[:300]}") from None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:4000")
    args = parser.parse_args()
    master_key, deployment_key = read_secrets()
    base_url = args.base_url.rstrip("/")

    info_request = urllib.request.Request(
        f"{base_url}/key/info",
        headers={"Authorization": f"Bearer {deployment_key}"},
    )
    info = open_json(info_request)
    key_info = info.get("info", info)
    existing = key_info.get("models")
    if not isinstance(existing, list):
        raise SystemExit("LiteLLM key metadata did not contain a model allowlist")
    models = list(dict.fromkeys([*existing, "gpt-5.6-luna", "omnilion"]))

    body = json.dumps({"key": deployment_key, "models": models}).encode()
    update_request = urllib.request.Request(
        f"{base_url}/key/update",
        data=body,
        headers={
            "Authorization": f"Bearer {master_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    open_json(update_request)
    print(f"PASS: deployment key allowlist includes {', '.join(models)}")


if __name__ == "__main__":
    main()
