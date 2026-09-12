from __future__ import annotations


def register() -> None:
    from vllm import ModelRegistry

    architecture = "OmniLionForConditionalGeneration"
    if architecture not in ModelRegistry.get_supported_archs():
        ModelRegistry.register_model(
            architecture,
            "omnilion_vllm_plugin.model:OmniLionForConditionalGeneration",
        )


__all__ = ["register"]
