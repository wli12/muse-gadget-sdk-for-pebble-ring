# Pebble ring → Muse on Cloud Run

Forward each authenticated, genuine Pebble Index recording to the same Muse
chat using the official Gadget SDK. Choose **Both** in Pebble to send its
transcription and recording together, after phone transcription completes.
Cloud Run converts M4A locally to PCM16 mono 16 kHz WAV and calls
`send_voice(wav, message=transcription, output_modality='text')` once, submitting
both in one message without adding instruction text. Recording-only payloads
send voice without Pebble text. Text-only payloads remain verbatim,
apart from UTF-8 decoding and outer whitespace trimming. There is no wake word,
keyword filter, classification or semantic routing. Invalid audio is rejected
instead of silently falling back to its accompanying text.

```text
Index ring → Pebble phone app → signed HTTPS webhook → Cloud Run → Muse chat
                                                        ↕
                                               private GCS state
```

This optional variant of [`../pebble_ring_bridge.py`](../pebble_ring_bridge.py)
opens a bounded SDK connection per request, rather than using a local daemon's
Unix socket. It changes no SDK pairing, TLS, Noise or chat protocol. The default
target is Muse's main chat. The phone still needs the ring and Internet access;
the cloud host needs no Bluetooth, local Mac, VPN or tunnel after pairing.

**[G3.md](../../../G3.md) is the canonical setup and operations guide** (Chinese
with English commands): prerequisites, official Linux pairing, optional
experimental [Mac adapter](mac_pairing.py), staged deployment, private state,
phone navigation, recovery, rotation, rollback, teardown and review findings.

## Test and deploy

Use Python 3.12 from the repository root. Offline tests use synthetic state;
the Docker build separately runs real FFmpeg codec checks with synthetic audio.
No local Docker or FFmpeg is required for these unit tests and Cloud Build.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  -r linux/examples/pebble_ring_bridge_cloudrun/requirements.txt pytest
PYTHONPATH=linux/src:linux/examples/pebble_ring_bridge_cloudrun \
  .venv/bin/python -m pytest -q linux/examples/pebble_ring_bridge_cloudrun
bash -n linux/examples/pebble_ring_bridge_cloudrun/deploy.sh
```

You need an authenticated Google Cloud CLI, a billing-enabled project with
resource-creation permissions, and a gadget paired to your Muse account.
Follow [G3](../../../G3.md) before transferring its owner-only `identity.json`,
`pairing.json` and `sdk_token`. Stop the same gadget identity's local service.

```sh
cd linux/examples/pebble_ring_bridge_cloudrun
PROJECT_ID='your-project-id'
PAIRING_DIR='/absolute/path/to/private/paired-state'
export BRIDGE_DEPLOY_DATA="$HOME/.local/state/muse-ring-deploy/$PROJECT_ID"
bash deploy.sh plan "$PROJECT_ID"
bash deploy.sh bootstrap "$PROJECT_ID"
bash deploy.sh build "$PROJECT_ID"
APPROVED_CREDENTIAL_UPLOAD=yes \
  bash deploy.sh upload-pairing "$PROJECT_ID" "$PAIRING_DIR"
bash deploy.sh deploy-private "$PROJECT_ID"
APPROVED_PUBLIC_WEBHOOK=yes bash deploy.sh publish "$PROJECT_ID"
bash deploy.sh status "$PROJECT_ID"
```

The upload guard acknowledges transfer of device credentials to the named
private Google bucket; the public guard allows phone access to HTTPS `/index`.
Application Bearer and Pebble v1 HMAC checks remain mandatory. If only the second
credential upload fails, use G3's missing-object recovery; never delete or
overwrite live `state/muse.json` to rerun setup.

## Phone settings and verification

In Pebble 1.18.0 on iPhone: **Index → gear → Settings → Ring Button**
(or **More → Index** first). Open the selected gesture's **Webhook settings**,
for example **Double click & hold → Webhook only**. Other gestures may keep
Index Agent. [Navigation illustration](docs/pebble-webhook-navigation.png).
Set the HTTPS `/index` URL, `Authorization: Bearer <bearer>`, **Sign requests**
with `signing_secret`, and payload **Both**. This waits for Pebble transcription
and includes both inputs in one SDK submission. **Recording** sends voice before
phone ASR; **Transcription** sends text only.
Keys are in `$BRIDGE_DEPLOY_DATA/webhook.json`. The optional
`make_phone_setup.py --url ... --keys ... --output ...` creates a private,
offline HTML sheet; it contains credentials and must never be shared or committed.

**Send test event** checks connectivity without sending a Muse message.
`smoke_test.py --url ... --keys ...` performs synthetic checks only; adding
`--send` explicitly submits one benign **text** message and repeats its delivery
ID to verify deduplication. Neither proves voice delivery. Use the configured
ring gesture and confirm the message and response in Muse. As of 2026-11-16,
81 offline tests and real Linux-image codec checks passed.
Physical text and Recording-mode voice/replies were user-confirmed; the user
preferred Pebble's Chinese transcription and chose Both. A signed synthetic M4A
also received cloud voice ACK and duplicate confirmation. A combined text/M4A
submission received ACK and duplicate confirmation on the latest deployment;
the official SDK serializer test verifies both items in one request. Both ring
behavior and Watch sync/push still need physical verification. A text-only
display cannot establish whether Muse retained or exposed audio, and the bridge
does not control how Muse weighs text against voice. Phone background scheduling
can delay upload before Cloud Run receives anything; Both also waits for ASR.

## Runtime boundaries

Audio: ≤8 MiB M4A/WAV input, ≤60 seconds, converted WAV below the SDK's 2 MiB
limit; oversize/invalid audio is refused, not truncated. Synthetic tests never
reach the SDK. GCS generation-based state writes, a 300-second lease and a
delivery ledger protect rotating tokens and repeated requests. Ambiguous sends
return `202 needs_review` and are not retried automatically. No shell or file
commands are registered. Recording bodies, text and chat contents are not logged
or stored in the ledger; Google processes them, and Meta receives the submission.

Deployment uses us-central1, 1 vCPU/1 GiB, request-based CPU, min 0, max 1,
concurrency 1 and a 120-second request timeout. Low volume may fit free allowances;
billing, storage/build/image/log costs and budget limits still require review.
The SDK is pinned to `08f89ed329918c27d53b11192065ad6b2cb0e868`; only direct
Python dependencies are pinned. Keep credentials and generated HTML outside Git
and the build context. See G3 for current pricing links and remaining failure limits.
