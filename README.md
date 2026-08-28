# gb10-serving

Model serving for the InnoFest voice agent on the DGX Spark (GB10). Kept out of
the app repo so the Spark clones only this.

Serves `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4` (~21 GB, audio in,
text out) with vLLM 0.20.0 on `:8000` as `nemotron`. The flags are the ones
verified on a single Spark with audio input working; sources at the bottom.

## Once

Accept the model licence on huggingface.co, then either `hf auth login` (pip
route) or put a read token in `.env` (Docker route, `cp .env.example .env`).

## Run

Without Docker:

```bash
./serve-nemotron.sh pull     # uv venv, vllm[audio]==0.20.0, weights
./serve-nemotron.sh serve    # foreground on :8000
./serve-nemotron.sh check    # /v1/models + a one-word request
```

`uv` must exist; the script says how to get it and does not install anything
itself. Knobs are env vars: `PORT`, `MAX_MODEL_LEN`, `GPU_MEM`, `VLLM_VERSION`,
`VENV`, `HF_HOME`.

With Docker and the NVIDIA container toolkit:

```bash
docker compose up
```

Same flags, official `vllm/vllm-openai` aarch64 image, weights cached in a named
volume. `docker compose ps` shows `healthy` once the model answers; first boot
downloads the weights, so that can take a while.

## If it misbehaves

- `flashinfer_cutlass` fails to load or the output is gibberish on SM121: run
  `MOE_BACKEND=marlin ./serve-nemotron.sh serve`, or in the compose file
  uncomment the three Marlin env vars and drop `--moe-backend`.
- The pip wheel does not resolve on the Spark's CUDA stack:
  `./serve-nemotron.sh docker` runs the same serve in the container.
- First boot complains about the cache dtype: drop `--kv-cache-dtype fp8`
  first; the pip recipe was verified without it.

## Then

From the `dell-innofest` checkout, gate the audio path before trusting it:

```bash
cd apps/server
MODEL_BASE_URL=http://promaxgb10-f4db.tailab1c77.ts.net:8000/v1 MODEL_NAME=nemotron \
  bun scripts/smoke-audio.ts
```

and run the agent server with the same two variables in its `.env`.

## Sources

- https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4
- https://dev.classmethod.jp/en/articles/dgx-spark-nemotron3-nano-omni-multimodal-launch-bench/ (pip recipe, audio verified, ~57 tok/s)
- https://forums.developer.nvidia.com/t/benchmark-nvidia-nemotron-3-nano-omni-30b-a3b-reasoning-nvfp4/368566 (container recipe, Marlin env, ~62 tok/s)
- https://vllm.ai/blog/2026-06-01-vllm-dgx-spark
