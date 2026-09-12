ARG BASE_IMAGE=nvcr.io/nvidia/vllm:26.05.post1-py3
FROM ${BASE_IMAGE}

USER root

ARG OMNILION_MODEL=LLJYY/OmniLion-NVFP4-W4A16
ARG OMNILION_REVISION=cc2628352cf7eb76c93b992c4f3079f4b3bc9498
ARG OMNILION_PLUGIN_SHA256=989711f66d16fb7f1e6e943c7b73babf0ab53018abf45276889ab89bd2a1df07

COPY install-plugin.py /tmp/install-omnilion-plugin.py
RUN python3 -m pip install --no-cache-dir \
      av==18.1.0 \
      scipy==1.18.1 \
      soundfile==0.13.1 \
      soxr==1.1.0 \
    && python3 -c 'import vllm; assert vllm.__version__ == "0.29.0", vllm.__version__' \
    && python3 /tmp/install-omnilion-plugin.py \
      "$OMNILION_MODEL" "$OMNILION_REVISION" "$OMNILION_PLUGIN_SHA256"

COPY --chmod=0755 entrypoint.py /usr/local/bin/omnilion-vllm-entrypoint

ENV HF_HOME=/var/cache/huggingface \
    HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    PYTHONNOUSERSITE=1 \
    VLLM_PLUGINS=omnilion

EXPOSE 8002
ENTRYPOINT ["python3", "/usr/local/bin/omnilion-vllm-entrypoint"]
