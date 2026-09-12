# Component licenses

OmniLion combines independently licensed components. No single relicensing
statement in this repository overrides their original terms.

## Decoder and visual tower

`aisingapore/Qwen-SEA-LION-v4.5-27B-IT` identifies its license as **MIT** and
links to the Qwen3.6 license:

- Model card: https://huggingface.co/aisingapore/Qwen-SEA-LION-v4.5-27B-IT
- License link: https://huggingface.co/Qwen/Qwen3.6-27B/blob/main/LICENSE
- Pinned source revision: `544e416f68a5700c7577547ad48e351e7d294116`

## Audio tower and processor

`Qwen/Qwen3-ASR-1.7B-hf` is licensed under **Apache License 2.0**:

- Model card: https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf
- Pinned source revision: `bcd2b5b7f32b480ab5790554cfa8347f246a14f3`

## OmniLion plugin and trained additions

The `omnilion-vllm-plugin` source and wheel are distributed under Apache-2.0.
The trained P21 projector and materialized adapter-derived additions are
provided subject to the repository's component-license obligations and the
applicable training-data/use-rights review.

Training datasets and evaluation media are not included.

Users are responsible for reviewing the linked upstream licenses and ensuring
that their distribution and use comply with all applicable terms. This file is
a provenance aid, not legal advice.
