from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "omnilion-vllm-plugin"
FINALIZER = ROOT / "scripts" / "finalize-omnilion-hf.py"


def load_finalizer_module():
    spec = importlib.util.spec_from_file_location("finalize_omnilion_hf", FINALIZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_distribution_metadata_is_complete() -> None:
    metadata = tomllib.loads((PLUGIN / "pyproject.toml").read_text())["project"]
    assert metadata["name"] == "omnilion-vllm-plugin"
    assert metadata["version"] == "0.1.0"
    assert metadata["readme"] == "README.md"
    assert metadata["license"] == "Apache-2.0"
    assert metadata["dependencies"] == [
        "vllm==0.29.0",
        "transformers==5.17.0",
        "soundfile==0.13.1",
    ]
    assert (PLUGIN / "README.md").is_file()
    assert (PLUGIN / "LICENSE").stat().st_size > 10_000


def test_huggingface_templates_are_public_and_omnimodal() -> None:
    card = (PLUGIN / "huggingface" / "README.md").read_text()
    for modality in ("text", "image", "video", "audio", "joint video + audio"):
        assert modality in card
    normalized_card = " ".join(card.lower().split())
    assert "matching vllm plugin is required" in normalized_card
    assert "omnilion_vllm_plugin-0.1.0-py3-none-any.whl" in card

    policy = json.loads(
        (PLUGIN / "huggingface" / "release-policy.example.json").read_text()
    )
    assert policy == {
        "hub_repository": "LLJYY/OmniLion",
        "visibility": "public",
        "public_release_authorized": True,
        "training_data_included": False,
        "evaluation_media_included": False,
    }


def test_release_scripts_are_executable_and_fail_closed() -> None:
    build = ROOT / "scripts" / "build-omnilion-plugin.sh"
    finalizer = ROOT / "scripts" / "finalize-omnilion-hf.py"
    assert os.access(build, os.X_OK)
    assert os.access(finalizer, os.X_OK)
    source = finalizer.read_text()
    for marker in (
        "ALLOWED_RELEASE_FILES",
        "release payload differs from exact allowlist",
        "expected the canonical 16 shards",
        "expected 1595 tensors",
        '"visual": 333',
        '"audio_tower": 393',
        '"projector": 3',
        "release manifest does not match current bytes",
        '"visibility": "public"',
        "tokenizer.json filter=lfs diff=lfs merge=lfs -text",
    ):
        assert marker in source


def test_release_allowlist_rejects_unexpected_files(tmp_path: Path) -> None:
    module = load_finalizer_module()
    (tmp_path / "secrets.txt").write_text("do not publish\n")
    with pytest.raises(RuntimeError, match="exact allowlist"):
        module.validate_tree(tmp_path)


def test_release_allowlist_rejects_symlinks(tmp_path: Path) -> None:
    module = load_finalizer_module()
    target = tmp_path.parent / "outside.txt"
    target.write_text("outside\n")
    (tmp_path / "README.md").symlink_to(target)
    with pytest.raises(RuntimeError, match="symlinks, directories, or special files"):
        module.validate_tree(tmp_path)


def test_public_examples_do_not_contain_private_dgx_paths() -> None:
    paths = [
        ROOT / ".env.example",
        PLUGIN / "README.md",
        *(PLUGIN / "huggingface").iterdir(),
    ]
    for path in paths:
        if path.is_file():
            text = path.read_text()
            assert "gb10-sit" not in text
            assert "p21-release-candidates" not in text
