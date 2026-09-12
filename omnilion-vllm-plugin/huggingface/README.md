---
license: other
license_name: mixed-mit-apache-2.0
license_link: https://huggingface.co/LLJYY/OmniLion/blob/main/LICENSES.md
library_name: vllm
pipeline_tag: any-to-any
base_model:
- aisingapore/Qwen-SEA-LION-v4.5-27B-IT
- Qwen/Qwen3-ASR-1.7B-hf
tags:
- vllm
- multimodal
- qwen3.6
- sea-lion
- qwen3-asr
---

# OmniLion

OmniLion combines the SEA-LION/Qwen3.6 language and vision model with a
Qwen3-ASR audio encoder and a trained audio projector.

A single native vLLM process supports:

- text → text
- image → text
- video → text
- audio → text
- joint video + audio → text

This is a Hugging Face SafeTensors model, not GGUF. The matching vLLM plugin is
required.

## Install

The verified runtime is Linux ARM64 on NVIDIA GB10 with Python 3.12, vLLM
0.29.0, PyTorch 2.13.0+cu129, and Transformers 5.17.0. Install the appropriate
vLLM/PyTorch build for your platform first, then install the included plugin:

```sh
python -m pip install --no-deps ./omnilion_vllm_plugin-0.1.0-py3-none-any.whl
```

## Serve

```sh
export VLLM_PLUGINS=omnilion
export PYTHONNOUSERSITE=1

vllm serve LLJYY/OmniLion \
  --served-model-name OmniLion \
  --host 127.0.0.1 \
  --port 8002 \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --max-num-seqs 1 \
  --gpu-memory-utilization 0.65 \
  --limit-mm-per-prompt '{"image":1,"video":1,"audio":1}' \
  --media-io-kwargs '{"video":{"num_frames":30}}' \
  --chat-template-content-format string \
  --enforce-eager
```

Use `/v1/chat/completions` with `model: "OmniLion"`. OpenAI-compatible content
parts are `image_url`, `video_url`, and `input_audio`. For joint AV, place the
video and audio parts before the text instruction in the same user message.
Audio is limited to 30 seconds per item in this release.

## Thinking

OmniLion keeps Qwen3.6's thinking behavior enabled for normal requests. Clients
can pass `chat_template_kwargs: {"enable_thinking": false}` for strict-format,
low-latency, or deterministic health-check calls.

## Notes

- The release was verified with text, image, video, audio, and joint-AV requests
  through one vLLM process and through LiteLLM.
- Only BF16 on the pinned GB10 runtime is release-verified.
- The model can hallucinate and inherits the safety and bias limitations of its
  components and subsequent training.
- Training data and evaluation media are not included.
- Keep raw vLLM private; put an authenticated gateway such as LiteLLM in front
  for networked deployment.

Exact files and hashes are in [release-manifest.json](release-manifest.json).
Lineage is in [PROVENANCE.md](PROVENANCE.md), and component terms are in
[LICENSES.md](LICENSES.md).
