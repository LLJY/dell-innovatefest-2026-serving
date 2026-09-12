# SPDX-License-Identifier: Apache-2.0
"""Native vLLM 0.29 text, vision, audio, and joint-AV model for OmniLion."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from numbers import Number
from typing import Any

import torch
from torch import nn
from transformers import BatchFeature, ProcessorMixin
from transformers.models.qwen3_asr.configuration_qwen3_asr import (
    Qwen3ASREncoderConfig,
)
from transformers.models.qwen3_asr.feature_extraction_qwen3_asr import (
    Qwen3ASRFeatureExtractor,
)
from transformers.models.qwen3_asr.modeling_qwen3_asr import Qwen3ASREncoder

from vllm.config import VllmConfig
from vllm.config.multimodal import BaseDummyOptions
from vllm.inputs import MultiModalDataDict
from vllm.model_executor.models.interfaces import MultiModalEmbeddings
from vllm.model_executor.models.module_mapping import MultiModelKeys
from vllm.model_executor.models.qwen3_5 import (
    Qwen3_5ForConditionalGeneration,
    Qwen3_5ProcessingInfo,
)
from vllm.model_executor.models.qwen3_vl import (
    Qwen3VLDummyInputsBuilder,
    Qwen3VLMultiModalDataParser,
    Qwen3VLMultiModalProcessor,
)
from vllm.model_executor.models.utils import AutoWeightsLoader, WeightsMapper
from vllm.multimodal import MULTIMODAL_REGISTRY
from vllm.multimodal.inputs import (
    MultiModalFeatureSpec,
    MultiModalFieldConfig,
    MultiModalKwargsItems,
)
from vllm.multimodal.parse import MultiModalDataItems
from vllm.multimodal.processing import PromptReplacement, PromptUpdate
from vllm.transformers_utils.configs.qwen3_5 import Qwen3_5Config
from vllm.utils.gpu_sync_debug import gpu_sync_allowed

AUDIO_START = "<|audio_start|>"
AUDIO_TOKEN = "<|audio_pad|>"
AUDIO_END = "<|audio_end|>"
MAX_AUDIO_SECONDS = 30
AUDIO_DURATION_TOLERANCE_MS = 100
MAX_AUDIO_SAMPLES = MAX_AUDIO_SECONDS * 16_000


def validate_audio_sample_limit(audio: object, sampling_rate: int) -> None:
    """Reject audio beyond the fixed multimodal token budget before extraction."""
    if sampling_rate <= 0:
        raise ValueError("sampling_rate must be positive")

    def sample_count(item: object) -> int:
        shape = getattr(item, "shape", None)
        if shape is not None:
            if len(shape) == 0:
                raise TypeError("audio waveform must have at least one dimension")
            return int(shape[-1])
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return len(item)
        raise TypeError("audio waveform must be an array or numeric sequence")

    if hasattr(audio, "shape"):
        lengths = [sample_count(audio)]
    elif isinstance(audio, Sequence) and not isinstance(audio, (str, bytes, bytearray)):
        if not audio:
            lengths = []
        elif isinstance(audio[0], Number):
            lengths = [len(audio)]
        else:
            lengths = [sample_count(item) for item in audio]
    else:
        lengths = [sample_count(audio)]

    limit = (
        MAX_AUDIO_SECONDS * sampling_rate
        + (sampling_rate * AUDIO_DURATION_TOLERANCE_MS + 999) // 1_000
    )
    if any(length > limit for length in lengths):
        raise ValueError(
            f"OmniLion accepts at most {MAX_AUDIO_SECONDS} seconds of audio per item"
        )


class OmniLionAudioProcessor(ProcessorMixin):
    """Feature-only processor; prompt tokenization belongs to SEA-LION."""

    def __init__(self, feature_extractor: Qwen3ASRFeatureExtractor) -> None:
        super().__init__(feature_extractor)

    @classmethod
    def from_pretrained(cls, model: str, **kwargs: object) -> "OmniLionAudioProcessor":
        return cls(Qwen3ASRFeatureExtractor.from_pretrained(model, **kwargs))

    def __call__(
        self,
        *,
        audio: object,
        sampling_rate: int = 16_000,
        return_tensors: str = "pt",
        **kwargs: object,
    ) -> BatchFeature:
        kwargs.pop("truncation", None)
        kwargs.pop("padding", None)
        kwargs.pop("return_attention_mask", None)
        kwargs.pop("n_window", None)
        validate_audio_sample_limit(audio, sampling_rate)
        features = self.feature_extractor(
            raw_speech=audio,
            sampling_rate=sampling_rate,
            padding=True,
            truncation=False,
            return_attention_mask=True,
            n_window=50,
            return_tensors=return_tensors,
            **kwargs,
        )
        features["input_features_mask"] = features.pop("attention_mask")
        return features


def _post_cnn_length(lengths: torch.Tensor) -> torch.Tensor:
    for _ in range(3):
        lengths = torch.where(
            lengths > 0,
            (lengths - 1) // 2 + 1,
            torch.zeros_like(lengths),
        )
    return lengths


def audio_output_lengths(
    input_features_mask: torch.Tensor,
    *,
    chunk_len: int,
) -> torch.Tensor:
    if input_features_mask.ndim != 2:
        raise ValueError("input_features_mask must be rank 2")
    if chunk_len <= 0 or input_features_mask.shape[1] % chunk_len:
        raise ValueError("feature length must be divisible by the audio chunk length")
    batch_size = input_features_mask.shape[0]
    chunk_lengths = input_features_mask.reshape(batch_size, -1, chunk_len).sum(-1)
    return _post_cnn_length(chunk_lengths.to(torch.long)).sum(-1)


class RMSNorm(nn.Module):
    def __init__(self, width: int = 1024, eps: float = 1.0e-5) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.ones(width))
        self.eps = float(eps)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        dtype = values.dtype
        normalized = values.float()
        normalized = normalized * torch.rsqrt(
            normalized.square().mean(dim=-1, keepdim=True) + self.eps
        )
        return normalized.to(dtype=dtype) * self.weight.to(dtype=dtype)


class SquaredReLUProjector(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.norm = RMSNorm(1024, eps=1.0e-5)
        self.up = nn.Linear(1024, 4096, bias=False)
        self.down = nn.Linear(4096, 5120, bias=False)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        return self.down(torch.relu(self.up(self.norm(values))).square())


class OmniLionMultiModalDataParser(Qwen3VLMultiModalDataParser):
    def __init__(self, spatial_merge_size: int, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("target_sr", 16_000)
        kwargs.setdefault("target_channels", 1)
        super().__init__(spatial_merge_size, *args, **kwargs)


class OmniLionProcessingInfo(Qwen3_5ProcessingInfo):
    def get_hf_config(self) -> Qwen3_5Config:
        return self.ctx.get_hf_config(Qwen3_5Config)

    def get_audio_processor(self, **kwargs: object) -> OmniLionAudioProcessor:
        return self.ctx.get_hf_processor(OmniLionAudioProcessor, **kwargs)

    def get_data_parser(self) -> OmniLionMultiModalDataParser:
        config = self.get_hf_config()
        return OmniLionMultiModalDataParser(
            config.vision_config.spatial_merge_size,
            video_needs_metadata=True,
            expected_hidden_size=self._get_expected_hidden_size(),
            allow_missing_mm_embeddings=self.allow_missing_mm_embeddings,
        )

    def get_supported_mm_limits(self) -> Mapping[str, int | None]:
        return {"image": None, "video": None, "audio": None}

    def get_mm_max_tokens_per_item(
        self,
        seq_len: int,
        mm_counts: Mapping[str, int] | None = None,
    ) -> Mapping[str, int]:
        counts = mm_counts or {}
        limits = dict(
            super().get_mm_max_tokens_per_item(
                seq_len=seq_len,
                mm_counts=counts,
            )
        )
        if counts.get("audio", 0):
            limits["audio"] = 390
        else:
            limits.pop("audio", None)
        return limits


class OmniLionDummyInputsBuilder(Qwen3VLDummyInputsBuilder):
    def get_dummy_text(self, mm_counts: Mapping[str, int]) -> str:
        vision_text = super().get_dummy_text(mm_counts)
        audio_text = (AUDIO_START + AUDIO_TOKEN + AUDIO_END) * mm_counts.get(
            "audio", 0
        )
        return vision_text + audio_text

    def get_dummy_mm_data(
        self,
        seq_len: int,
        mm_counts: Mapping[str, int],
        mm_options: Mapping[str, BaseDummyOptions],
    ) -> MultiModalDataDict:
        data = super().get_dummy_mm_data(seq_len, mm_counts, mm_options)
        data["audio"] = self._get_dummy_audios(
            length=MAX_AUDIO_SAMPLES,
            num_audios=mm_counts.get("audio", 0),
            overrides=mm_options.get("audio"),
        )
        return data


class OmniLionMultiModalProcessor(Qwen3VLMultiModalProcessor):
    def _apply_hf_processor_main(
        self,
        mm_items: MultiModalDataItems,
        hf_processor_mm_kwargs: Mapping[str, object],
    ) -> BatchFeature:
        processed: dict[str, object] = {}
        # vLLM preserves mixed-modality embedding order through keyword insertion
        # order, so process each modality in the original prompt order.
        for modality in mm_items:
            selected_items = mm_items.select({modality})
            if modality in {"image", "video"}:
                processed.update(
                    dict(
                        super()._apply_hf_processor_main(
                            selected_items,
                            hf_processor_mm_kwargs,
                        )
                    )
                )
                continue
            if modality != "audio":
                raise ValueError(f"Unsupported OmniLion modality: {modality}")

            processor_data, passthrough_data = self._get_hf_mm_data(selected_items)
            audios = processor_data.get("audios", [])
            if audios:
                audio_kwargs = dict(hf_processor_mm_kwargs)
                audio_kwargs["sampling_rate"] = 16_000
                audio_output = self.info.ctx.call_hf_processor(
                    self.info.get_audio_processor(**audio_kwargs),
                    {"audio": audios},
                    audio_kwargs,
                )
                processed.update(dict(audio_output))
            processed.update(dict(passthrough_data))

        return BatchFeature(processed)

    def _get_mm_fields_config(
        self,
        hf_inputs: BatchFeature,
        hf_processor_mm_kwargs: Mapping[str, object],
    ) -> Mapping[str, MultiModalFieldConfig]:
        fields = dict(
            super()._get_mm_fields_config(
                hf_inputs,
                hf_processor_mm_kwargs,
            )
        )
        fields.update(
            {
                "input_features": MultiModalFieldConfig.batched("audio"),
                "input_features_mask": MultiModalFieldConfig.batched("audio"),
            }
        )
        return fields

    def _get_prompt_updates(
        self,
        mm_items: MultiModalDataItems,
        hf_processor_mm_kwargs: Mapping[str, object],
        out_mm_kwargs: MultiModalKwargsItems,
    ) -> Sequence[PromptUpdate]:
        updates = list(
            super()._get_prompt_updates(
                mm_items,
                hf_processor_mm_kwargs,
                out_mm_kwargs,
            )
        )
        tokenizer = self.info.get_tokenizer()
        audio_token_id = tokenizer.convert_tokens_to_ids(AUDIO_TOKEN)
        if not isinstance(audio_token_id, int) or audio_token_id < 0:
            raise ValueError("OmniLion audio placeholder is missing from the tokenizer")
        data = out_mm_kwargs.get_data()
        mask = data.get("input_features_mask")
        if mask is None:
            return updates
        if not isinstance(mask, torch.Tensor):
            raise TypeError("input_features_mask must be a tensor")
        lengths = audio_output_lengths(mask, chunk_len=100).tolist()

        def replacement(item_idx: int) -> list[int]:
            return [audio_token_id] * int(lengths[item_idx])

        updates.append(
            PromptReplacement(
                modality="audio",
                target=[audio_token_id],
                replacement=replacement,
            )
        )
        return updates


@MULTIMODAL_REGISTRY.register_processor(
    OmniLionMultiModalProcessor,
    info=OmniLionProcessingInfo,
    dummy_inputs=OmniLionDummyInputsBuilder,
)
class OmniLionForConditionalGeneration(Qwen3_5ForConditionalGeneration):
    supports_multimodal_pruning = False
    hf_to_vllm_mapper = Qwen3_5ForConditionalGeneration.hf_to_vllm_mapper | WeightsMapper(
        orig_to_new_prefix={
            "model.audio_tower.": "audio_tower.",
            "model.multi_modal_projector.": "multi_modal_projector.",
        }
    )

    @classmethod
    def get_placeholder_str(cls, modality: str, i: int) -> str | None:
        if modality.startswith("audio"):
            return "\n" + AUDIO_START + AUDIO_TOKEN + AUDIO_END
        return super().get_placeholder_str(modality, i)

    def __init__(self, *, vllm_config: VllmConfig, prefix: str = "model") -> None:
        super().__init__(vllm_config=vllm_config, prefix=prefix)
        config: Qwen3_5Config = vllm_config.model_config.hf_config
        self.multi_modal_config = self.multimodal_config

        audio_config = Qwen3ASREncoderConfig(**config.omnilion_audio_config)
        with self._mark_tower_model(vllm_config, {"audio"}):
            self.audio_tower = Qwen3ASREncoder(audio_config)
        self.multi_modal_projector = SquaredReLUProjector()

    def get_mm_mapping(self) -> MultiModelKeys:
        return MultiModelKeys.from_string_field(
            language_model="language_model",
            connector=["visual.merger", "multi_modal_projector"],
            tower_model=["visual.", "audio_tower."],
        )

    def get_mrope_input_positions(
        self,
        input_tokens: list[int],
        mm_features: list[MultiModalFeatureSpec],
    ) -> tuple[torch.Tensor, int]:
        vision_features = [
            f for f in mm_features if f.modality in {"image", "video"}
        ]
        return super().get_mrope_input_positions(
            input_tokens,
            vision_features,
        )

    def _parse_audio_input(
        self,
        **kwargs: object,
    ) -> tuple[torch.Tensor, torch.Tensor] | None:
        input_features = kwargs.get("input_features")
        input_features_mask = kwargs.get("input_features_mask")
        if input_features is None and input_features_mask is None:
            return None
        if not isinstance(input_features, torch.Tensor) or not isinstance(
            input_features_mask, torch.Tensor
        ):
            raise TypeError("OmniLion audio features and mask must be tensors")
        return input_features, input_features_mask

    def _process_audio_input(
        self,
        audio_input: tuple[torch.Tensor, torch.Tensor],
    ) -> tuple[torch.Tensor, ...]:
        input_features, input_features_mask = audio_input
        input_features = input_features.to(dtype=self.audio_tower.conv2d1.weight.dtype)
        input_features_mask = input_features_mask.to(device=input_features.device)
        lengths = audio_output_lengths(input_features_mask, chunk_len=100)
        states = self.audio_tower(
            input_features=input_features,
            input_features_mask=input_features_mask,
            return_dict=True,
        ).last_hidden_state
        projected = self.multi_modal_projector(states.to(dtype=input_features.dtype))
        with gpu_sync_allowed():
            return tuple(torch.split(projected, lengths.tolist()))

    def _parse_and_validate_multimodal_inputs(self, **kwargs: object) -> dict:
        mm_input_by_modality: dict[str, object] = {}
        for input_key in kwargs:
            if (
                input_key in ("pixel_values", "image_embeds")
                and "image" not in mm_input_by_modality
            ):
                mm_input_by_modality["image"] = self._parse_and_validate_image_input(
                    **kwargs
                )
            if (
                input_key in ("pixel_values_videos", "video_embeds")
                and "video" not in mm_input_by_modality
            ):
                mm_input_by_modality["video"] = self._parse_and_validate_video_input(
                    **kwargs
                )
            if input_key == "input_features" and "audio" not in mm_input_by_modality:
                mm_input_by_modality["audio"] = self._parse_audio_input(**kwargs)
        return mm_input_by_modality

    def embed_multimodal(self, **kwargs: object) -> MultiModalEmbeddings:
        mm_input_by_modality = self._parse_and_validate_multimodal_inputs(**kwargs)
        if not mm_input_by_modality:
            return []

        embeddings: list[torch.Tensor] = []
        for modality, multimodal_input in mm_input_by_modality.items():
            if modality == "image":
                image_embeddings = self._process_image_input(multimodal_input)
                image_embeddings = self._postprocess_image_embeds_evs(
                    image_embeddings,
                    multimodal_input,
                )
                embeddings.extend(image_embeddings)
            elif modality == "video":
                video_embeddings = self._process_video_input(multimodal_input)
                embeddings.extend(video_embeddings)
            elif modality == "audio":
                if multimodal_input is None:
                    raise ValueError("OmniLion audio input is missing")
                embeddings.extend(self._process_audio_input(multimodal_input))
            else:
                raise ValueError(f"Unsupported OmniLion modality: {modality}")
        return tuple(embeddings)

    def load_weights(
        self,
        weights: Iterable[tuple[str, torch.Tensor]],
    ) -> set[str]:
        loader = AutoWeightsLoader(self)
        return loader.load_weights(weights, mapper=self.hf_to_vllm_mapper)
