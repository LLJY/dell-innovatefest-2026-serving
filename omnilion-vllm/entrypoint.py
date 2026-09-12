#!/usr/bin/env python3
"""Verify the pinned public snapshot, then exec private native vLLM."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from huggingface_hub import snapshot_download


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or "replace-" in value:
        raise SystemExit(f"{name} is required")
    return value


def positive_integer(name: str, default: str) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError as error:
        raise SystemExit(f"{name} must be a positive integer") from error
    if value <= 0:
        raise SystemExit(f"{name} must be a positive integer")
    return value


def gpu_utilization() -> float:
    try:
        value = float(os.environ.get("OMNILION_GPU_MEMORY_UTILIZATION", "0.65"))
    except ValueError as error:
        raise SystemExit("OMNILION_GPU_MEMORY_UTILIZATION must be numeric") from error
    if not 0 < value < 1:
        raise SystemExit("OMNILION_GPU_MEMORY_UTILIZATION must be between 0 and 1")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    model = required("OMNILION_MODEL")
    revision = required("OMNILION_REVISION")
    manifest_hash = required("OMNILION_MANIFEST_SHA256")
    api_key = required("OMNILION_API_KEY")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise SystemExit("OMNILION_REVISION must be a full commit hash")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_hash):
        raise SystemExit("OMNILION_MANIFEST_SHA256 must be a SHA-256 digest")

    os.environ.pop("HF_TOKEN", None)
    os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["VLLM_API_KEY"] = api_key
    os.environ["VLLM_PLUGINS"] = "omnilion"
    os.environ["PYTHONNOUSERSITE"] = "1"

    snapshot = Path(snapshot_download(repo_id=model, revision=revision, token=False))
    manifest = snapshot / "release-manifest.json"
    if not manifest.is_file() or sha256(manifest) != manifest_hash:
        raise SystemExit("OmniLion release-manifest checksum mismatch")

    command = [
        "vllm",
        "serve",
        str(snapshot),
        "--served-model-name",
        "OmniLion",
        "--host",
        "0.0.0.0",
        "--port",
        "8002",
        "--dtype",
        "bfloat16",
        "--max-model-len",
        str(positive_integer("OMNILION_MAX_MODEL_LEN", "8192")),
        "--max-num-seqs",
        "1",
        "--gpu-memory-utilization",
        str(gpu_utilization()),
        "--limit-mm-per-prompt",
        '{"image":1,"video":1,"audio":1}',
        "--media-io-kwargs",
        '{"video":{"num_frames":%d}}'
        % positive_integer("OMNILION_VIDEO_NUM_FRAMES", "30"),
        "--chat-template-content-format",
        "string",
        "--enforce-eager",
    ]
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
