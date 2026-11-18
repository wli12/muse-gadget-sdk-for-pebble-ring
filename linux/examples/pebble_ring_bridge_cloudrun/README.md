# Pebble ring → Muse on Cloud Run

This variant of [`../pebble_ring_bridge.py`](../pebble_ring_bridge.py) forwards
Pebble's signed webhooks to the same Muse main chat through the official Gadget
SDK. **Both** submits the Pebble transcription and recording in one message.
Recording-only and text-only payloads also work; no wake word, content filter or
routing is applied. A small shared parser is extracted inside the original
example; its local text behavior and the SDK protocols remain unchanged.

The complete setup and operations guide is available in
[English](../../../README.md) and [Simplified Chinese](../../../README.zh-CN.md),
including prerequisites, Google Cloud signup, initial pairing, deployment,
phone settings, verification, privacy, costs and recovery. A Linux host with BLE
or a Mac is used once for initial Muse Bluetooth pairing and can then be turned
off. Daily ring-to-phone transfers still use Bluetooth. Cloud Run needs no
Bluetooth or always-on Mac.

| Runtime file | Responsibility |
| --- | --- |
| `../pebble_ring_bridge.py` | Shared MIME parsing, transcription and token extraction. |
| `app.py` | Authenticate webhooks; serialize credential access and deduplicate delivery. |
| `muse_sender.py` | Preserve refreshed credentials and submit once using the official SDK. |
| `muse_transport.py` | Restrict SDK connections to official Muse origins; reject redirects and environment proxies. |
| `audio.py` | Convert bounded M4A/WAV input locally to PCM16 mono 16 kHz WAV. |

`deploy.sh` builds from an explicit source allowlist. `mac_pairing.py` is an
optional experimental adapter; `verify_webhook.py` performs signed connectivity
checks and an explicitly selected test message. These tools are outside the
runtime path. No credentials, generated configuration or recordings belong in Git.
The [privacy section](../../../README.md#privacy-and-data-flow) lists all intended
data recipients and the limits of these restrictions.

Run tests from the repository root with Python 3.12:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  -r linux/examples/pebble_ring_bridge_cloudrun/requirements.txt pytest==9.2.0
PYTHONPATH=linux/src:linux/examples:linux/examples/pebble_ring_bridge_cloudrun \
  .venv/bin/python -m pytest -q linux/examples/pebble_ring_bridge_test.py \
  linux/tests/test_pebble_ring_bridge.py \
  linux/examples/pebble_ring_bridge_cloudrun
bash -n linux/examples/pebble_ring_bridge_cloudrun/deploy.sh
```

Tests use synthetic data. Real codec tests run when FFmpeg is present and always
run in the Docker build's test stage; pytest and test files stay out of the final
image. The SDK is pinned to `08f89ed329918c27d53b11192065ad6b2cb0e868`.
Cloud Run uses request-based CPU with min 0/max 1; low usage may fit free quotas,
but zero cost and immediate iPhone background delivery are not guaranteed.
