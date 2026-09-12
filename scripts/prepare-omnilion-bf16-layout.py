#!/usr/bin/env python3
"""Assemble a standard OmniLion BF16 Hugging Face tree without copying decoder shards."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
from pathlib import Path

from safetensors import safe_open
from safetensors.torch import load_file, save_file
from transformers import AutoProcessor

MODEL_METADATA_FILES = (
    "chat_template.jinja",
    "configuration.json",
    "generation_config.json",
    "merges.txt",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safetensors_tensor_bytes(path: Path) -> int:
    dtype_bytes = {
        "BOOL": 1,
        "U8": 1,
        "I8": 1,
        "I16": 2,
        "F16": 2,
        "BF16": 2,
        "I32": 4,
        "F32": 4,
        "I64": 8,
        "F64": 8,
        "F8_E4M3": 1,
        "F8_E5M2": 1,
    }
    total = 0
    with safe_open(path, framework="pt", device="cpu") as stream:
        for name in stream.keys():
            view = stream.get_slice(name)
            dtype = view.get_dtype()
            if dtype not in dtype_bytes:
                raise ValueError(f"unsupported SafeTensors dtype {dtype} in {path}")
            total += math.prod(view.get_shape()) * dtype_bytes[dtype]
    return total


def link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def combine_preprocessor_configs(
    vision: dict[str, object],
    audio: dict[str, object],
) -> dict[str, object]:
    """Combine Qwen3VL and Qwen3-ASR preprocessing metadata in one HF file."""
    combined = dict(vision)
    combined.update(audio)
    if "processor_class" in vision:
        combined["processor_class"] = vision["processor_class"]
    return combined


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    source = args.source.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    model_source = source / "model"
    audio_weights = source / "audio" / "weights"
    audio_processor = source / "audio" / "processor"
    for required in (model_source, audio_weights, audio_processor):
        if not required.is_dir():
            raise SystemExit(f"required source directory missing: {required}")

    output.mkdir(parents=True)
    for name in MODEL_METADATA_FILES:
        shutil.copy2(model_source / name, output / name)
    vision_preprocessor_path = model_source / "preprocessor_config.json"
    if not vision_preprocessor_path.is_file():
        raise SystemExit(
            f"required SEA-LION vision processor config missing: {vision_preprocessor_path}"
        )
    vision_preprocessor = json.loads(
        vision_preprocessor_path.read_text(encoding="utf-8")
    )
    processor = AutoProcessor.from_pretrained(audio_processor, local_files_only=True)
    combined_preprocessor = combine_preprocessor_configs(
        vision_preprocessor,
        processor.feature_extractor.to_dict(),
    )
    (output / "preprocessor_config.json").write_text(
        json.dumps(combined_preprocessor, indent=2) + "\n",
        encoding="utf-8",
    )

    source_index = json.loads(
        (model_source / "model.safetensors.index.json").read_text(encoding="utf-8")
    )
    source_shards = sorted(set(source_index["weight_map"].values()))
    if len(source_shards) != 15:
        raise SystemExit(f"expected 15 BF16 decoder shards, found {len(source_shards)}")

    shard_renames: dict[str, str] = {}
    decoder_hashes: dict[str, str] = {}
    for number, source_name in enumerate(source_shards, start=1):
        destination_name = f"model-{number:05d}-of-00016.safetensors"
        shard_renames[source_name] = destination_name
        source_path = model_source / source_name
        destination_path = output / destination_name
        link_or_copy(source_path, destination_path)
        decoder_hashes[destination_name] = sha256(destination_path)

    tower_path = audio_weights / "audio_tower.safetensors"
    projector_path = audio_weights / "target_projector.safetensors"
    tower = load_file(tower_path, device="cpu")
    projector = load_file(projector_path, device="cpu")
    integrated_weights = {
        **{f"model.audio_tower.{name}": value for name, value in tower.items()},
        **{
            f"model.multi_modal_projector.{name}": value
            for name, value in projector.items()
        },
    }
    if len(integrated_weights) != 396:
        raise SystemExit(
            f"expected 396 audio/projector tensors, found {len(integrated_weights)}"
        )
    integrated_name = "model-00016-of-00016.safetensors"
    integrated_path = output / integrated_name
    save_file(
        integrated_weights,
        integrated_path,
        metadata={"format": "pt", "component": "OmniLion audio tower and projector"},
    )
    del tower, projector, integrated_weights

    weight_map = {
        name: shard_renames[filename]
        for name, filename in source_index["weight_map"].items()
    }
    with safe_open(integrated_path, framework="pt", device="cpu") as stream:
        for name in stream.keys():
            if name in weight_map:
                raise SystemExit(f"duplicate integrated tensor: {name}")
            weight_map[name] = integrated_name

    total_size = sum(
        safetensors_tensor_bytes(output / name) for name in set(weight_map.values())
    )
    index = {
        "metadata": {"total_size": total_size},
        "weight_map": dict(sorted(weight_map.items())),
    }
    (output / "model.safetensors.index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )

    config = json.loads((model_source / "config.json").read_text(encoding="utf-8"))
    asr_config = json.loads((audio_processor / "config.json").read_text(encoding="utf-8"))
    config.update(
        {
            "architectures": ["OmniLionForConditionalGeneration"],
            "omnilion_artifact": "BF16",
            "omnilion_audio_config": asr_config["audio_config"],
            "omnilion_audio_token": "<|audio_pad|>",
            "omnilion_audio_token_id": 248076,
            "omnilion_plugin_version": "0.1.0",
            "omnilion_schema_version": 1,
        }
    )
    (output / "config.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )

    receipt = {
        "artifact": "OmniLion-BF16",
        "architecture": "OmniLionForConditionalGeneration",
        "audio_tensors": 393,
        "projector_tensors": 3,
        "decoder_shards_reused_byte_for_byte": True,
        "decoder_shards": decoder_hashes,
        "integrated_audio_projector_shard": {
            "file": integrated_name,
            "sha256": sha256(integrated_path),
            "source_audio_tower_sha256": sha256(tower_path),
            "source_projector_sha256": sha256(projector_path),
        },
        "status": "OMNILION_BF16_NATIVE_LAYOUT_ASSEMBLED",
    }
    (output / "standardization_receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
