#!/usr/bin/env python3
"""Test private OmniLion audio inference with an API key read from stdin."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def secret_from_stdin() -> str:
    key = sys.stdin.readline().rstrip("\r\n")
    if not key:
        raise SystemExit("API key was not provided on stdin")
    return key


def request_json(url: str, key: str, body: dict | None, timeout: int) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            parsed = json.loads(payload)
            detail = parsed.get("error", {}).get("message") or parsed.get("detail")
        except Exception:
            detail = "non-JSON response"
        raise SystemExit(f"request failed with HTTP {error.code}: {str(detail)[:300]}") from None
    return json.loads(payload)


def audio_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in {"flac", "m4a", "mp3", "mp4", "mpeg", "mpga", "ogg", "wav", "webm"}:
        return suffix
    raise SystemExit(f"unsupported audio format: {suffix or 'unknown'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://172.17.0.1:8002/v1")
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not args.audio.is_file():
        raise SystemExit(f"audio file is not readable: {args.audio}")

    key = secret_from_stdin()
    base_url = args.base_url.rstrip("/")
    models = request_json(f"{base_url}/models", key, None, args.timeout)
    if "OmniLion" not in {entry.get("id") for entry in models.get("data", [])}:
        raise SystemExit("private backend does not advertise OmniLion")

    audio = base64.b64encode(args.audio.read_bytes()).decode("ascii")
    body = {
        "model": "OmniLion",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"data": audio, "format": audio_format(args.audio)}},
                {"type": "text", "text": "Transcribe this audio faithfully. Return only the transcription."},
            ],
        }],
        "max_tokens": 512,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    response = request_json(f"{base_url}/chat/completions", key, body, args.timeout)
    try:
        text = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise SystemExit("private backend response is missing assistant content") from None
    if not isinstance(text, str) or not text.strip():
        raise SystemExit("private backend returned an empty transcription")
    print(f"PASS: private OmniLion audio inference returned {len(text)} characters")


if __name__ == "__main__":
    main()
