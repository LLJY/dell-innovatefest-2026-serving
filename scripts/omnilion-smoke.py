#!/usr/bin/env python3
"""Test OmniLion through LiteLLM with a deployment key read from stdin."""

from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "Mozilla/5.0 OmniLionSmoke/1.0"


def secret_from_stdin() -> str:
    key = sys.stdin.readline().rstrip("\r\n")
    if not key:
        raise SystemExit("deployment key was not provided on stdin")
    return key


def open_request(request: urllib.request.Request, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            parsed = json.loads(payload)
            detail = parsed.get("error", {}).get("message") or parsed.get("detail")
        except Exception:
            detail = "non-JSON response"
        raise SystemExit(f"request failed with HTTP {error.code}: {str(detail)[:300]}") from None


def multipart(audio_path: Path) -> tuple[bytes, str]:
    boundary = f"omnilion-{secrets.token_hex(16)}"
    mime = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    filename = audio_path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nomnilion\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n".encode(),
        (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n"
        ).encode(),
        audio_path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), boundary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:4000")
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=360)
    args = parser.parse_args()
    if not args.audio.is_file():
        raise SystemExit(f"audio file is not readable: {args.audio}")

    key = secret_from_stdin()
    base_url = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT}
    models_request = urllib.request.Request(f"{base_url}/v1/models", headers=headers)
    models = json.loads(open_request(models_request, args.timeout))
    if "omnilion" not in {entry.get("id") for entry in models.get("data", [])}:
        raise SystemExit("omnilion is missing from the deployment key model list")
    print("authenticated_models=ok")

    body, boundary = multipart(args.audio)
    transcription_request = urllib.request.Request(
        f"{base_url}/v1/audio/transcriptions",
        data=body,
        headers={**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    response = json.loads(open_request(transcription_request, args.timeout))
    text = response.get("text")
    if not isinstance(text, str) or not text.strip():
        raise SystemExit("OmniLion returned an empty transcription")
    print(f"PASS: authenticated omnilion transcription returned {len(text)} characters")


if __name__ == "__main__":
    main()
