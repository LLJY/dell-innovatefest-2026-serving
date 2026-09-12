# API

## Base URL and authentication

Production clients use one LiteLLM gateway:

```text
https://gb10.hoshinoht.dev
```

Send the expiring deployment key as a bearer token. Never use the LiteLLM
master key, translator service key, or OmniLion service key from a client.
Store the deployment key in a mode-`0600` file or a platform secret store; do
not put it in source code, shell history, URLs, or command-line arguments.

The current deployment key permits these public model aliases:

| Alias | Endpoint | Purpose |
| --- | --- | --- |
| `gpt-5.6-luna` | `POST /v1/chat/completions` | Text, streaming, and tool-call relay |
| `omnilion` | `POST /v1/audio/transcriptions` | Local speech transcription |

## Safe curl smoke scripts

The repository includes curl-based checks that read the deployment key from its
protected file and feed the bearer header to curl through stdin. The key is not
placed in curl's arguments or inherited environment.

```sh
# Defaults to http://127.0.0.1:4000.
scripts/luna-models-curl.sh
scripts/omnilion-curl-smoke.sh /path/to/recording.wav

# Test the same authenticated routes through Cloudflare.
LUNA_URL=https://gb10.hoshinoht.dev scripts/luna-models-curl.sh
LUNA_URL=https://gb10.hoshinoht.dev \
  scripts/omnilion-curl-smoke.sh /path/to/recording.wav
```

Set `LUNA_KEY_FILE` if the deployment key is stored somewhere other than
`~/.config/gb10-serving/luna-deployment-key`. Both scripts validate the response;
the transcription smoke reports only the character count, not the transcript.

## Python client setup

These examples use the OpenAI Python SDK and read the key from a protected
file. Change `KEY_FILE` for the client environment.

```python
from pathlib import Path
from openai import OpenAI

BASE_URL = "https://gb10.hoshinoht.dev/v1"
KEY_FILE = Path.home() / ".config" / "gb10-serving" / "luna-deployment-key"

client = OpenAI(
    base_url=BASE_URL,
    api_key=KEY_FILE.read_text(encoding="utf-8").strip(),
    timeout=360.0,
)
```

## List available models

```python
for model in client.models.list().data:
    print(model.id)
```

The result is filtered by the deployment key. A correctly provisioned key sees
`gpt-5.6-luna` and `omnilion`.

## Luna chat completions

```python
response = client.chat.completions.create(
    model="gpt-5.6-luna",
    messages=[{"role": "user", "content": "Reply in one short sentence."}],
)
print(response.choices[0].message.content)
```

Streaming uses the same endpoint:

```python
stream = client.chat.completions.create(
    model="gpt-5.6-luna",
    stream=True,
    messages=[{"role": "user", "content": "Give a short greeting."}],
)
for event in stream:
    delta = event.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)
```

Tools are relayed but never executed by the server. The client or external
agent loop must validate and execute a returned tool call, then send its output
in a later Chat Completions request.

## OmniLion transcription

Upload audio as multipart form data through the OpenAI-compatible transcription
API:

```python
from pathlib import Path

audio_path = Path("recording.wav")
with audio_path.open("rb") as audio:
    transcription = client.audio.transcriptions.create(
        model="omnilion",
        file=(audio_path.name, audio),
        response_format="json",
    )
print(transcription.text)
```

Supported upload extensions are `wav`, `mp3`, `m4a`, `mp4`, `mpeg`, `mpga`,
`ogg`, `webm`, and `flac`. The current release accepts one audio item of up to
30 seconds; the gateway rejects files larger than 25 MiB. Supported response
formats are `json`, `text`, and `verbose_json`; `json` and `verbose_json`
currently return the same `text` field:

```json
{"text":"transcribed speech"}
```

OmniLion is local GPU inference and does not consume Luna subscription quota.
The raw vLLM and adapter services are private and are not client endpoints.

## Health and errors

`GET /health/liveliness` reports LiteLLM process health and does not require a
model request. Use authenticated `GET /v1/models` to verify the key and its
allowlist.

Errors use an OpenAI-compatible body where possible:

```json
{
  "error": {
    "message": "description",
    "type": "invalid_request_error",
    "code": "error_code"
  }
}
```

Common statuses are `400` for an invalid model or form field, `401` for a bad
or expired key, `413` for an oversized upload, `415` for an unsupported audio
format, `429` for a gateway limit, and `502` when the private model backend is
unavailable.
