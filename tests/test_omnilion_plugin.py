from __future__ import annotations

import pytest
import torch


def test_plugin_registers_native_omnilion_architecture() -> None:
    from omnilion_vllm_plugin import register
    from vllm import ModelRegistry

    register()
    assert "OmniLionForConditionalGeneration" in ModelRegistry.get_supported_archs()


def test_audio_placeholder_preserves_accepted_boundary() -> None:
    from omnilion_vllm_plugin.model import OmniLionForConditionalGeneration

    assert (
        OmniLionForConditionalGeneration.get_placeholder_str("audio", 0)
        == "\n<|audio_start|><|audio_pad|><|audio_end|>"
    )


def test_qwen3_asr_output_lengths_match_accepted_frontend() -> None:
    from omnilion_vllm_plugin.model import audio_output_lengths

    masks = torch.zeros((2, 3000), dtype=torch.int32)
    masks[0, :3000] = 1
    masks[1, :1000] = 1
    assert audio_output_lengths(masks, chunk_len=100).tolist() == [390, 130]


def test_projector_is_exact_1024_to_4096_to_5120_squared_relu() -> None:
    from omnilion_vllm_plugin.model import SquaredReLUProjector

    projector = SquaredReLUProjector()
    assert projector.norm.weight.shape == (1024,)
    assert projector.up.weight.shape == (4096, 1024)
    assert projector.down.weight.shape == (5120, 4096)
    assert projector.up.bias is None
    assert projector.down.bias is None

    values = torch.randn(2, 1024)
    with torch.no_grad():
        expected = projector.down(torch.relu(projector.up(projector.norm(values))).square())
        actual = projector(values)
    torch.testing.assert_close(actual, expected)


def test_plugin_targets_the_pinned_vllm_runtime() -> None:
    from pathlib import Path

    project = Path(__file__).resolve().parents[1] / "omnilion-vllm-plugin"
    pyproject = (project / "pyproject.toml").read_text()
    assert 'vllm==0.29.0' in pyproject
    assert 'vllm.general_plugins' in pyproject


def test_weight_mapper_preserves_visual_and_audio_towers() -> None:
    from omnilion_vllm_plugin.model import OmniLionForConditionalGeneration

    mapper = OmniLionForConditionalGeneration.hf_to_vllm_mapper
    assert (
        mapper._map_name("model.visual.blocks.0.attn.qkv.weight")
        == "visual.blocks.0.attn.qkv.weight"
    )
    assert (
        mapper._map_name("model.audio_tower.conv2d1.weight")
        == "audio_tower.conv2d1.weight"
    )
    assert (
        mapper._map_name("model.multi_modal_projector.up.weight")
        == "multi_modal_projector.up.weight"
    )


def test_model_extends_native_qwen35_vision_model() -> None:
    from omnilion_vllm_plugin.model import OmniLionForConditionalGeneration
    from vllm.model_executor.models.qwen3_5 import Qwen3_5ForConditionalGeneration

    assert issubclass(
        OmniLionForConditionalGeneration,
        Qwen3_5ForConditionalGeneration,
    )


def test_processor_extends_native_qwen35_vision_processor() -> None:
    from omnilion_vllm_plugin.model import (
        OmniLionMultiModalProcessor,
        OmniLionProcessingInfo,
    )
    from vllm.model_executor.models.qwen3_5 import Qwen3_5ProcessingInfo
    from vllm.model_executor.models.qwen3_vl import Qwen3VLMultiModalProcessor

    assert issubclass(OmniLionProcessingInfo, Qwen3_5ProcessingInfo)
    assert issubclass(OmniLionMultiModalProcessor, Qwen3VLMultiModalProcessor)


def test_multimodal_parser_accepts_audio_at_16khz() -> None:
    import numpy as np

    from omnilion_vllm_plugin.model import OmniLionMultiModalDataParser

    parser = OmniLionMultiModalDataParser(
        2,
        expected_hidden_size=5120,
    )
    items = parser.parse_mm_data(
        {"audio": (np.zeros(16_000, dtype=np.float32), 16_000)}
    )
    assert items.get_all_counts()["audio"] == 1


def test_multimodal_parser_preserves_audio_first_order() -> None:
    import numpy as np
    from PIL import Image

    from omnilion_vllm_plugin.model import OmniLionMultiModalDataParser

    parser = OmniLionMultiModalDataParser(2, expected_hidden_size=5120)
    items = parser.parse_mm_data(
        {
            "audio": (np.zeros(16_000, dtype=np.float32), 16_000),
            "image": Image.new("RGB", (16, 16), "red"),
        }
    )
    assert list(items) == ["audio", "image"]


def test_audio_sample_limit_accepts_30_seconds_and_rejects_longer() -> None:
    import numpy as np

    from omnilion_vllm_plugin.model import validate_audio_sample_limit

    validate_audio_sample_limit(np.zeros(30 * 16_000, dtype=np.float32), 16_000)
    validate_audio_sample_limit(
        [np.zeros(16_000, dtype=np.float32), np.zeros(30 * 16_000, dtype=np.float32)],
        16_000,
    )
    validate_audio_sample_limit(np.zeros(30 * 16_000 + 256, dtype=np.float32), 16_000)
    with pytest.raises(ValueError, match="at most 30 seconds"):
        validate_audio_sample_limit(np.zeros(30 * 16_000 + 1_601, dtype=np.float32), 16_000)


def test_model_declares_both_native_towers() -> None:
    from omnilion_vllm_plugin.model import OmniLionForConditionalGeneration

    mapping = OmniLionForConditionalGeneration.get_mm_mapping(None)
    assert mapping.language_model == ["language_model"]
    assert mapping.tower_model == ["visual.", "audio_tower."]
    assert mapping.connector == ["visual.merger", "multi_modal_projector"]
