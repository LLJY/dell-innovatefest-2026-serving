#!/usr/bin/env python3
"""Finalize and fail-closed seal an OmniLion BF16 Hugging Face model tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

from safetensors import safe_open

ARCHITECTURE = "OmniLionForConditionalGeneration"
PLUGIN_WHEEL = "omnilion_vllm_plugin-0.1.0-py3-none-any.whl"
METADATA_FILES = ("README.md", "PROVENANCE.md", "LICENSES.md", "runtime-requirements.txt")
MODEL_SHARDS = tuple(
    f"model-{number:05d}-of-00016.safetensors" for number in range(1, 17)
)
ALLOWED_RELEASE_FILES = frozenset(
    {
        ".gitattributes",
        "LICENSES.md",
        "PROVENANCE.md",
        "README.md",
        "chat_template.jinja",
        "config.json",
        "configuration.json",
        "generation_config.json",
        "merges.txt",
        "model.safetensors.index.json",
        PLUGIN_WHEEL,
        "preprocessor_config.json",
        "release-manifest.json",
        "release-policy.json",
        "runtime-requirements.txt",
        "standardization_receipt.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
        *MODEL_SHARDS,
    }
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_exact(source: Path, destination: Path) -> None:
    if destination.is_symlink():
        raise RuntimeError(f"release destination must not be a symlink: {destination}")
    if destination.exists():
        if not destination.is_file():
            raise RuntimeError(f"release destination must be a regular file: {destination}")
        if sha256(source) != sha256(destination):
            raise RuntimeError(f"refusing to replace differing release file: {destination}")
        return
    shutil.copy2(source, destination)


def validate_tree(model_dir: Path) -> dict[str, object]:
    actual_files: set[str] = set()
    invalid_entries: list[str] = []
    for path in model_dir.iterdir():
        if path.is_symlink() or not path.is_file():
            invalid_entries.append(path.name)
        else:
            actual_files.add(path.name)
    if invalid_entries:
        raise RuntimeError(
            f"release tree contains symlinks, directories, or special files: {sorted(invalid_entries)}"
        )
    unexpected = actual_files - ALLOWED_RELEASE_FILES
    missing = (ALLOWED_RELEASE_FILES - {"release-manifest.json"}) - actual_files
    if unexpected or missing:
        raise RuntimeError(
            f"release payload differs from exact allowlist: missing={sorted(missing)}, "
            f"unexpected={sorted(unexpected)}"
        )

    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    if config.get("architectures") != [ARCHITECTURE]:
        raise RuntimeError("OmniLion architecture metadata differs")
    if config.get("omnilion_artifact") != "BF16":
        raise RuntimeError("OmniLion artifact must be BF16")
    if config.get("omnilion_plugin_version") != "0.1.0":
        raise RuntimeError("OmniLion plugin version metadata differs")
    if not isinstance(config.get("omnilion_audio_config"), dict):
        raise RuntimeError("OmniLion audio configuration is missing")

    processor = json.loads((model_dir / "preprocessor_config.json").read_text(encoding="utf-8"))
    if processor.get("processor_class") != "Qwen3VLProcessor":
        raise RuntimeError("SEA-LION vision processor metadata is missing")
    if processor.get("feature_extractor_type") != "Qwen3ASRFeatureExtractor":
        raise RuntimeError("Qwen3-ASR feature extractor metadata is missing")
    if processor.get("sampling_rate") != 16000:
        raise RuntimeError("OmniLion audio sampling rate differs")

    index = json.loads((model_dir / "model.safetensors.index.json").read_text(encoding="utf-8"))
    weight_map = index.get("weight_map")
    if not isinstance(weight_map, dict) or not weight_map:
        raise RuntimeError("SafeTensors weight map is missing")
    shard_names = sorted(set(weight_map.values()))
    expected_shards = [f"model-{number:05d}-of-00016.safetensors" for number in range(1, 17)]
    if shard_names != expected_shards:
        raise RuntimeError(f"expected the canonical 16 shards, found: {shard_names}")
    disk_shards = sorted(path.name for path in model_dir.glob("*.safetensors"))
    if disk_shards != expected_shards:
        raise RuntimeError("unreferenced or missing SafeTensors shards found")

    actual_keys: set[str] = set()
    dtype_counts: dict[str, int] = {}
    prefix_counts = {"visual": 0, "audio_tower": 0, "projector": 0}
    for shard_name in shard_names:
        with safe_open(model_dir / shard_name, framework="pt", device="cpu") as stream:
            for name in stream.keys():
                if name in actual_keys:
                    raise RuntimeError(f"duplicate tensor across shards: {name}")
                actual_keys.add(name)
                dtype = stream.get_slice(name).get_dtype()
                dtype_counts[dtype] = dtype_counts.get(dtype, 0) + 1
                if name.startswith("model.visual."):
                    prefix_counts["visual"] += 1
                elif name.startswith("model.audio_tower."):
                    prefix_counts["audio_tower"] += 1
                elif name.startswith("model.multi_modal_projector."):
                    prefix_counts["projector"] += 1
                if "lora_" in name.lower():
                    raise RuntimeError(f"explicit runtime adapter tensor found: {name}")
    if set(weight_map) != actual_keys:
        missing = sorted(set(weight_map) - actual_keys)[:5]
        extra = sorted(actual_keys - set(weight_map))[:5]
        raise RuntimeError(f"weight index differs: missing={missing}, extra={extra}")
    if len(actual_keys) != 1595:
        raise RuntimeError(f"expected 1595 tensors, found {len(actual_keys)}")
    if prefix_counts != {"visual": 333, "audio_tower": 393, "projector": 3}:
        raise RuntimeError(f"component tensor counts differ: {prefix_counts}")
    if set(dtype_counts) != {"BF16"}:
        raise RuntimeError(f"non-BF16 tensors found: {dtype_counts}")

    wheel = model_dir / PLUGIN_WHEEL
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if any(
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or "__pycache__" in Path(name).parts
            or name.endswith((".pyc", ".pyo"))
            for name in names
        ):
            raise RuntimeError("plugin wheel contains unsafe paths or bytecode/cache files")
        required_modules = {
            "omnilion_vllm_plugin/__init__.py",
            "omnilion_vllm_plugin/model.py",
        }
        if not required_modules <= set(names):
            raise RuntimeError("plugin wheel is missing OmniLion runtime modules")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        entry_point_names = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        if len(metadata_names) != 1 or len(entry_point_names) != 1:
            raise RuntimeError("plugin wheel distribution metadata is incomplete")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
        entry_points = archive.read(entry_point_names[0]).decode("utf-8")
    for expected in (
        "Name: omnilion-vllm-plugin",
        "Version: 0.1.0",
        "License-Expression: Apache-2.0",
        "Requires-Dist: vllm==0.29.0",
        "Requires-Dist: transformers==5.17.0",
        "Requires-Dist: soundfile==0.13.1",
    ):
        if expected not in metadata:
            raise RuntimeError(f"plugin wheel metadata is missing: {expected}")
    if "omnilion = omnilion_vllm_plugin:register" not in entry_points:
        raise RuntimeError("plugin wheel is missing the vllm.general_plugins entry point")

    policy = json.loads((model_dir / "release-policy.json").read_text(encoding="utf-8"))
    if policy != {
        "hub_repository": "LLJYY/OmniLion",
        "visibility": "public",
        "public_release_authorized": True,
        "training_data_included": False,
        "evaluation_media_included": False,
    }:
        raise RuntimeError("release policy differs from the public release contract")

    return {
        "architecture": ARCHITECTURE,
        "artifact": "BF16",
        "shards": len(shard_names),
        "tensors": len(actual_keys),
        "component_tensors": prefix_counts,
        "tensor_dtypes": dtype_counts,
        "plugin_wheel": {"file": PLUGIN_WHEEL, "sha256": sha256(wheel)},
    }


def build_manifest(model_dir: Path, summary: dict[str, object]) -> dict[str, object]:
    manifest_path = model_dir / "release-manifest.json"
    files = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(model_dir.iterdir())
        if path.is_file() and path != manifest_path
    }
    return {
        "schema_version": 1,
        "status": "PASS_OMNILION_BF16_RELEASE_SEAL",
        "hub_repository": "LLJYY/OmniLion",
        "visibility": "private",
        "summary": summary,
        "payload_files": files,
        "payload_bytes": sum(entry["bytes"] for entry in files.values()),
    }


def print_receipt(model_dir: Path, manifest: dict[str, object]) -> None:
    summary = manifest["summary"]
    assert isinstance(summary, dict)
    files = manifest["payload_files"]
    assert isinstance(files, dict)
    print(json.dumps({
        "status": manifest["status"],
        "payload_files": len(files),
        "payload_bytes": manifest["payload_bytes"],
        "manifest_sha256": sha256(model_dir / "release-manifest.json"),
        **summary,
    }, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("plugin_wheel", type=Path)
    parser.add_argument("--verify-existing-manifest", action="store_true")
    args = parser.parse_args()

    model_dir = args.model_dir.expanduser().resolve(strict=True)
    plugin_wheel = args.plugin_wheel.expanduser().resolve(strict=True)
    if plugin_wheel.name != PLUGIN_WHEEL:
        raise SystemExit(f"plugin wheel must be named {PLUGIN_WHEEL}")

    manifest_path = model_dir / "release-manifest.json"
    if args.verify_existing_manifest:
        summary = validate_tree(model_dir)
        manifest = build_manifest(model_dir, summary)
        if not manifest_path.is_file():
            raise RuntimeError("release-manifest.json is missing")
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise RuntimeError("release manifest does not match current bytes")
        print_receipt(model_dir, manifest)
        return

    template_dir = (
        Path(__file__).resolve().parents[1]
        / "omnilion-vllm-plugin"
        / "huggingface"
    )
    for name in METADATA_FILES:
        copy_exact(template_dir / name, model_dir / name)
    copy_exact(plugin_wheel, model_dir / PLUGIN_WHEEL)

    attributes = (
        "*.safetensors filter=lfs diff=lfs merge=lfs -text\n"
        "*.whl filter=lfs diff=lfs merge=lfs -text\n"
        "tokenizer.json filter=lfs diff=lfs merge=lfs -text\n"
    )
    attributes_path = model_dir / ".gitattributes"
    if attributes_path.is_symlink():
        raise RuntimeError(".gitattributes must not be a symlink")
    if attributes_path.exists() and attributes_path.read_text(encoding="utf-8") != attributes:
        raise RuntimeError("refusing to replace differing .gitattributes")
    attributes_path.write_text(attributes, encoding="utf-8")

    policy = {
        "hub_repository": "LLJYY/OmniLion",
        "visibility": "public",
        "public_release_authorized": True,
        "training_data_included": False,
        "evaluation_media_included": False,
    }
    policy_path = model_dir / "release-policy.json"
    policy_text = json.dumps(policy, indent=2, sort_keys=True) + "\n"
    if policy_path.is_symlink():
        raise RuntimeError("release-policy.json must not be a symlink")
    if policy_path.exists() and policy_path.read_text(encoding="utf-8") != policy_text:
        raise RuntimeError("refusing to replace differing release-policy.json")
    policy_path.write_text(policy_text, encoding="utf-8")

    summary = validate_tree(model_dir)
    manifest = build_manifest(model_dir, summary)
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if manifest_path.is_symlink():
        raise RuntimeError("release-manifest.json must not be a symlink")
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != manifest_text:
        raise RuntimeError("refusing to overwrite a differing release manifest")
    manifest_path.write_text(manifest_text, encoding="utf-8")
    os.chmod(manifest_path, 0o644)
    print_receipt(model_dir, manifest)


if __name__ == "__main__":
    main()
