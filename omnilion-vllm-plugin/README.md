# OmniLion vLLM plugin

Native vLLM 0.29 support for the OmniLion composite model:

```text
SEA-LION / Qwen3.6 decoder + visual tower
Qwen3-ASR audio encoder
trained P21 projector
```

The plugin registers `OmniLionForConditionalGeneration` and preserves text,
image, video, audio, and joint video-plus-audio inference in one vLLM process.
It is not a second gateway and does not run a separate ASR server.

## Pinned runtime

The release was exercised on Linux ARM64 / NVIDIA GB10 with:

- Python 3.12
- vLLM 0.29.0
- PyTorch 2.13.0+cu129
- Transformers 5.17.0
- soundfile 0.13.1
- Ninja 1.13.0

Other platforms may work but are not part of the verified release receipt.
Install the platform-appropriate vLLM/PyTorch build before installing this
small plugin wheel.

## Build and install

From the repository root:

```sh
scripts/build-omnilion-plugin.sh
python -m pip install --no-deps artifacts/omnilion-vllm-plugin/omnilion_vllm_plugin-0.1.0-py3-none-any.whl
```

For development:

```sh
python -m pip install --no-deps -e ./omnilion-vllm-plugin
```

Verify discovery:

```sh
VLLM_PLUGINS=omnilion python - <<'PY'
from vllm import ModelRegistry
from vllm.plugins import load_general_plugins
load_general_plugins()
assert "OmniLionForConditionalGeneration" in ModelRegistry.get_supported_archs()
print("OmniLion plugin registered")
PY
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

OpenAI-compatible chat requests use `image_url`, `video_url`, and
`input_audio` content parts. The repository's `litellm-config.yaml` exposes the
same backend as `model="OmniLion"`.

For the hackathon host service, copy `.env.example`, set a strong
`OMNILION_API_KEY`, and use:

```sh
scripts/omnilion-service.sh start
scripts/omnilion-service.sh status
scripts/omnilion-service.sh logs
```

Bind the raw vLLM service only to loopback or Docker's host-gateway address.
vLLM's API key does not protect every non-`/v1` endpoint.

## Model contract

The matching model tree must contain:

- `architectures: ["OmniLionForConditionalGeneration"]`;
- the SEA-LION/Qwen3.6 decoder and visual weights;
- `model.audio_tower.*` Qwen3-ASR encoder weights;
- `model.multi_modal_projector.*` trained P21 projector weights;
- combined Qwen3-VL and Qwen3-ASR processor metadata.

Do not use this plugin with an audio-only tree or a tree that discarded
`model.visual.*`.

## License

Plugin source is Apache-2.0. Model weights retain their component licenses; see
`huggingface/LICENSES.md` before distributing a composite model repository.
