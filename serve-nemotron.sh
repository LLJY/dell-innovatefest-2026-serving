#!/usr/bin/env bash
# Pull and serve Nemotron-3-Nano-Omni (NVFP4) with vLLM on one DGX Spark (GB10).
#
#   ./serve-nemotron.sh pull     # venv + vllm[audio] + model weights (~21 GB)
#   ./serve-nemotron.sh serve    # foreground vllm serve on :8000
#   ./serve-nemotron.sh check    # GET /v1/models, then a one-line text request
#   ./serve-nemotron.sh          # pull, then serve
#   ./serve-nemotron.sh docker   # same serve via the official aarch64 container
#
# With Docker + the NVIDIA container toolkit on the box, `docker compose up`
# in this directory is the equivalent (see docker-compose.yml).
#
# Recipe: uv pip install "vllm[audio]==0.20.0" + --moe-backend flashinfer_cutlass
# is what ran this exact checkpoint on a single Spark with audio input working
# (~57 tok/s). The container path with the Marlin env vars is the other verified
# route (~62 tok/s). Both are linked from docs/voice.md.
#
# Then, from the dell-innofest checkout (this box or a laptop on the tailnet):
#   MODEL_BASE_URL=http://<gb10>:8000/v1 MODEL_NAME=nemotron \
#     bun scripts/smoke-audio.ts       (in apps/server)
set -euo pipefail

MODEL="${MODEL:-nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4}"
SERVED_NAME="${SERVED_NAME:-nemotron}"
VLLM_VERSION="${VLLM_VERSION:-0.20.0}"
VENV="${VENV:-$HOME/.venvs/vllm-omni}"
PORT="${PORT:-8000}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"
# Fraction of the 128 GB unified pool vLLM may claim. 0.7 leaves room for the
# OS, the encoders' activations and the Bun agent server on the same box.
GPU_MEM="${GPU_MEM:-0.7}"
# flashinfer_cutlass is the verified pip-install backend on SM121. If it fails
# to load or produces gibberish, run with MOE_BACKEND=marlin, which sets the
# Marlin env vars from the forum benchmark below.
MOE_BACKEND="${MOE_BACKEND:-flashinfer_cutlass}"
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
# The card is gated behind the NVIDIA Open Model Agreement: `hf auth login`
# once, or export HF_TOKEN, before `pull`.

# Everything after the model id; shared by the pip and docker paths.
serve_args() {
  echo --served-model-name "$SERVED_NAME" --trust-remote-code \
    --host 0.0.0.0 --port "$PORT" \
    --max-model-len "$MAX_MODEL_LEN" --kv-cache-dtype fp8 \
    --gpu-memory-utilization "$GPU_MEM" \
    --mamba-ssm-cache-dtype float32 \
    --media-io-kwargs '{"video": {"fps": 2, "num_frames": 256}}' \
    --enable-auto-tool-choice --tool-call-parser qwen3_coder \
    --reasoning-parser nemotron_v3
}

# Marlin route from the single-Spark forum benchmark. Only exported when asked.
marlin_env() {
  export VLLM_NVFP4_GEMM_BACKEND=marlin
  export VLLM_USE_FLASHINFER_MOE_FP4=0
  export VLLM_MARLIN_USE_ATOMIC_ADD=1
}

backend_args() {
  if [[ "$MOE_BACKEND" == "marlin" ]]; then
    marlin_env
  else
    echo --moe-backend "$MOE_BACKEND"
  fi
}

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1 -- $2" >&2; exit 1; }; }

pull() {
  need uv "install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
  need nvidia-smi "no CUDA driver visible"
  [[ -d "$VENV" ]] || uv venv --python 3.12 "$VENV"
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  uv pip install "vllm[audio]==$VLLM_VERSION"
  # `hf` ships with huggingface_hub, which vllm depends on.
  hf download "$MODEL"
  python -c "import vllm; print('vllm', vllm.__version__)"
  echo "pulled $MODEL into $HF_HOME"
}

serve() {
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  # shellcheck disable=SC2046
  exec vllm serve "$MODEL" $(serve_args) $(backend_args)
}

docker_serve() {
  need docker "docker with the NVIDIA container toolkit"
  local envs=(-e HF_TOKEN="${HF_TOKEN:-}")
  if [[ "$MOE_BACKEND" == "marlin" ]]; then
    envs+=(-e VLLM_NVFP4_GEMM_BACKEND=marlin -e VLLM_USE_FLASHINFER_MOE_FP4=0 -e VLLM_MARLIN_USE_ATOMIC_ADD=1)
    backend=()
  else
    backend=(--moe-backend "$MOE_BACKEND")
  fi
  # shellcheck disable=SC2046
  exec docker run --rm --gpus all --ipc=host --network host \
    -v "$HF_HOME:/root/.cache/huggingface" "${envs[@]}" \
    "vllm/vllm-openai:v${VLLM_VERSION}-aarch64-cu130-ubuntu2404" \
    "$MODEL" $(serve_args) "${backend[@]}"
}

check() {
  local base="http://localhost:$PORT/v1"
  curl -sf "$base/models" | python3 -c 'import json,sys; m=json.load(sys.stdin)["data"][0]; print(m["id"], "max_model_len", m.get("max_model_len"))'
  curl -sf "$base/chat/completions" -H 'content-type: application/json' -d "{
    \"model\": \"$SERVED_NAME\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Reply with the single word: ready\"}],
    \"max_tokens\": 8,
    \"chat_template_kwargs\": {\"enable_thinking\": false}
  }" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(repr(r["choices"][0]["message"]["content"]), r["usage"])'
}

case "${1:-all}" in
  pull) pull ;;
  serve) serve ;;
  docker) docker_serve ;;
  check) check ;;
  all) pull; serve ;;
  *) echo "usage: $0 [pull|serve|docker|check]" >&2; exit 2 ;;
esac
