#!/usr/bin/env python3
"""Install the pinned OmniLion plugin during the container image build."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: install-plugin.py MODEL REVISION PLUGIN_SHA256")
    model, revision, expected_hash = sys.argv[1:]
    wheel = Path(
        hf_hub_download(
            repo_id=model,
            filename="omnilion_vllm_plugin-0.1.0-py3-none-any.whl",
            revision=revision,
            token=False,
        )
    )
    actual_hash = sha256(wheel)
    if actual_hash != expected_hash:
        raise SystemExit("OmniLion plugin wheel checksum mismatch")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", str(wheel)],
        check=True,
    )


if __name__ == "__main__":
    main()
