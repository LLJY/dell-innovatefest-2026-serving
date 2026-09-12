from __future__ import annotations

import importlib.util
from pathlib import Path

import torch
from safetensors.torch import save_file


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare-omnilion-bf16-layout.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("prepare_omnilion_bf16_layout", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_combined_preprocessor_config_preserves_vision_and_audio() -> None:
    module = load_script_module()
    vision = {
        "size": {"longest_edge": 16_777_216, "shortest_edge": 65_536},
        "patch_size": 16,
        "temporal_patch_size": 2,
        "merge_size": 2,
        "image_mean": [0.5, 0.5, 0.5],
        "image_std": [0.5, 0.5, 0.5],
        "processor_class": "Qwen3VLProcessor",
        "image_processor_type": "Qwen2VLImageProcessorFast",
    }
    audio = {
        "feature_extractor_type": "Qwen3ASRFeatureExtractor",
        "feature_size": 128,
        "hop_length": 160,
        "n_samples": 480_000,
        "sampling_rate": 16_000,
    }

    combined = module.combine_preprocessor_configs(vision, audio)

    assert combined["processor_class"] == "Qwen3VLProcessor"
    assert combined["image_processor_type"] == "Qwen2VLImageProcessorFast"
    assert combined["size"] == vision["size"]
    assert combined["feature_extractor_type"] == "Qwen3ASRFeatureExtractor"
    assert combined["sampling_rate"] == 16_000
    assert combined["n_samples"] == 480_000


def test_safetensors_total_size_counts_tensor_storage_not_headers(tmp_path: Path) -> None:
    module = load_script_module()
    shard = tmp_path / "model.safetensors"
    save_file(
        {
            "bf16": torch.zeros((2, 3), dtype=torch.bfloat16),
            "f32": torch.zeros((5,), dtype=torch.float32),
        },
        shard,
    )

    assert module.safetensors_tensor_bytes(shard) == 32
    assert shard.stat().st_size > 32
