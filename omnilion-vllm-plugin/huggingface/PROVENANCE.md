# OmniLion provenance

## Components

| Component | Source | Immutable revision | Role |
|---|---|---|---|
| Decoder and visual tower | `aisingapore/Qwen-SEA-LION-v4.5-27B-IT` | `544e416f68a5700c7577547ad48e351e7d294116` | Qwen3.6 language model, tokenizer, chat template, image/video tower, visual merger and MRoPE behavior |
| Audio tower and processor | `Qwen/Qwen3-ASR-1.7B-hf` | `bcd2b5b7f32b480ab5790554cfa8347f246a14f3` | 16 kHz mono feature extraction and Qwen3-ASR audio encoding |
| P21 projector | OmniLion trained checkpoint | accepted generation `step_049291_1789014139367389200` | Projects 1,024-dimensional audio states to the decoder's 5,120-dimensional embedding space |

## Derived-weight construction

The accepted Deep-rsLoRA decoder derivative was materialized into the BF16
SEA-LION decoder using FP32 adapter accumulation and BF16 output:

```text
adapter tensors       992
merged linear layers  496
decoder shards        15
source conversion     false
```

The original explicit-adapter source remains separate. Runtime LoRA is not
required and explicit adapter tensors are not included in this repository.

The Qwen3-ASR tower and trained projector are stored in shard 16 under
`model.audio_tower.*` and `model.multi_modal_projector.*`. Existing SEA-LION
visual tensors remain in the original decoder/vision shards; they were not
removed or replaced.

## Source payload hashes

```text
audio_tower.safetensors
  e221c86dc79bb40950cf569de223df0e69ec24c569c6097247930ee039d19236

decoder_deep_lora.safetensors
  eadb5cbec7c65b6393d28a7a770703bccddc5e8127ac3379597945c3f48c5551

target_projector.safetensors
  f8795f377dd00350b91e0c8a46691b3ce5002db6c07b8b70d37cc2b3e3b44f29

accepted checkpoint state
  9c5ac1acf5a9d59242a7de45df10f260597433d0f22d55ce61c634cc64aa0b31

integrated shard 16
  b94688df24a524f2a63b9518e9518dbeadb3eccdccc6abaa3401b34cd1d4a85e
```

The repository's `standardization_receipt.json` records the decoder shard
hashes and integrated-shard source hashes. `release-manifest.json` inventories
all payload files intended for publication.

## Runtime architecture

```text
audio
  → Qwen3-ASR feature extractor and encoder
  → trained RMSNorm / Linear / squared-ReLU / Linear projector
  → Qwen3.6 token embedding stream

image or video
  → SEA-LION visual tower and merger
  → Qwen3.6 visual MRoPE positions and embedding stream

joint video plus audio
  → both towers
  → deterministic visual then audio placeholder/embedding merge
  → Qwen3.6 decoder
```

Internal `model_type: qwen3_5` is the Transformers/vLLM compatibility
implementation used by the Qwen3.6 lineage. It is not a claim that the public
decoder lineage changed to Qwen3.5.
