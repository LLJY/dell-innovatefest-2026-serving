# OmniLion container profile

The default deployment keeps OmniLion vLLM in the verified host virtual
environment. This optional profile packages the same pinned NVFP4 release into
a private GPU container. Merely adding or validating the profile does not pull
an image, download the model, stop the host service, or reserve GPU memory.

## Boundary

The container exposes port 8002 only to the Compose network; it publishes no
host port. The private adapter uses `http://omnilion-vllm:8002/v1`, and LiteLLM
remains the only public model gateway.

The image is pinned to the ARM64-capable NVIDIA vLLM base
`nvcr.io/nvidia/vllm:26.05.post1-py3`. Its build fails unless the base provides
vLLM 0.29.0. The image installs pinned audio dependencies and verifies the
bundled OmniLion plugin wheel. At startup it anonymously downloads the exact
model revision, verifies the release-manifest SHA-256, and keeps the service key
out of process arguments.

## Validate without running

```sh
scripts/omnilion-container.sh config
```

This is the only command required to check the dormant profile. It renders the
combined Compose configuration quietly and does not build or start anything.

## Deliberate cutover

Do not run host-native and containerized OmniLion together. They compete for
GPU memory and compute. The wrapper refuses `up` while the host user service is
active, so cutover requires an explicit stop:

```sh
scripts/omnilion-runtime.sh down
scripts/omnilion-container.sh up
scripts/omnilion-container.sh status
```

Starting the profile may pull/build the NVIDIA image and download approximately
27 GB into the persistent `omnilion-hf-cache` volume. It then recreates the
adapter with the container service URL.

To leave the profile and restore the normal host-native route:

```sh
scripts/omnilion-container.sh down
scripts/luna-up.sh
```

The first command stops the containerized model and adapter without deleting
the model-cache or PostgreSQL volumes. The second command starts/verifies the
host runtime and recreates the normal adapter route.
