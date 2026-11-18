<!--
Copyright (c) Meta Platforms, Inc. and affiliates.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Pebble Index 01 → Google Cloud Run → Muse

[简体中文](README.zh-CN.md)

Forward a Pebble Index 01 ring's transcription and recording to your main chat in **Muse from Meta**, using your own Google Cloud Run project. This fork adapts the [official Muse Gadget SDK example](linux/examples/pebble_ring_bridge.py), reuses its parsing helpers, and adds request-scoped cloud state and audio support.

This guide covers setup, file provenance, deployment, verification, privacy, costs, recovery, and teardown. Deployment projects, runtime accounts, and webhook addresses use placeholders. Public source links use real GitHub addresses. The repository contains no real device credentials, webhook secrets, personal chats, or recordings. For the original SDK overview, see the [upstream README at the pinned revision](https://github.com/facebookincubator/muse-gadget-sdk/blob/08f89ed329918c27d53b11192065ad6b2cb0e868/README.md).

[Requirements](#1-what-you-need) · [Privacy](#2-privacy-and-data-flow) · [Deployment steps](#3-step-by-step-deployment) · [FYI](#4-fyi-implementation-and-operations)

## 1. What you need

| Item | When it is used | What to prepare |
| --- | --- | --- |
| Ring | Every command | A [Pebble Index 01](https://repebble.com/products/pebble-index-01), paired to the phone using the [official guide](https://help.repebble.com/en/articles/15434751-index-01-getting-started-guide). A Pebble watch is not required. |
| Phone | Every command | **iPhone or Android**, with Bluetooth and internet access. iPhone needs **iOS 18+** for Muse. Android must support both apps below; Pebble's Android 8.0 requirement alone is not the whole setup's minimum. |
| Phone apps, Muse account and SDK token | Setup and daily use | Install [Pebble](https://repebble.com/app) and Muse from Meta ([iPhone](https://apps.apple.com/us/app/muse-from-meta/id6760173601), [Android](https://play.google.com/store/apps/details?id=com.facebook.aura)). Pebble must already receive/transcribe recordings and offer signed webhooks. Sign into Muse and obtain a [Gadget SDK token](https://gadgets.muse.ai/settings/sdk-tokens); review its [terms](https://gadgets.muse.ai/sdk-terms). |
| Your Google Cloud project | Daily cloud relay | This guide uses **Cloud Run to run the program** and **private Cloud Storage to keep its state**. No separately rented always-on VM is needed. Low use may fit free allowances. Prepare a Google account and billing-linked project: [signup](#44-create-a-google-cloud-project), [trial credits](#45-free-trial-credits), [actual costs](#46-view-your-actual-costs). |
| Bluetooth pairing computer | **Once, during initial setup** | A local **Linux host with BLE**, or a **Mac** using the experimental adapter. It pairs Muse with the gadget identity. After pairing, uploading that state and finishing cloud setup, it can be turned off. [Choose one pairing path](#31-for-you-ask-codex-and-complete-the-phoneaccount-steps). |
| Deployment terminal and tools | Setup, updates and checks | The simplest option is **Terminal on that same Mac/Linux computer**, using Git, Python 3.12 and `gcloud`. Alternatively use **Cloud Shell**, Google's browser-based cloud terminal. This is an alternative place to run deployment commands, not another computer to buy; Bluetooth pairing still stays local. [Prepare tools](#31-for-you-ask-codex-and-complete-the-phoneaccount-steps). |
| Apple Watch (optional) | Optional reply display | Install the official Muse Watch app if desired. Reply synchronization/notifications triggered by this bridge remain unverified. |

## 2. Privacy and data flow

**Daily data path:** Ring → **Bluetooth** → Pebble on your phone → **HTTPS** → your Google Cloud Run → **Muse Link** → Meta Muse.

| Participant | What it can see | What it cannot see through this bridge |
| --- | --- | --- |
| Pebble App on your phone | Ring audio/transcription and the webhook address, Bearer and signing keys configured in it. | The bridge does not return Muse replies/history or supply Muse device/SDK credentials to Pebble. |
| Pebble's selected cloud services (e.g. Wispr Flow / OpenRouter), if enabled | Audio/text sent by Pebble under its ASR, agent, backup and integration settings. | The bridge does not call these services or send Muse credentials/replies to them. Additional data sent by Pebble follows its own settings/policies. |
| Your Google Cloud project | Uploaded audio/text, device credentials, webhook keys and delivery/request metadata. Authorized administrators and Google's controlled operational/legal access apply. | The bridge does not upload unrelated phone/computer files or account passwords. It processes note content but does not retain recordings/note bodies in its storage or logs. |
| Meta Muse | Submitted audio/text and Muse device/authentication information; Meta processes/stores it. | The bridge does not supply its Google Cloud IAM credentials or webhook Bearer/signing keys to Muse. |
| Apps/tools/sites used by Muse | Task data shared under your instructions and their permissions. | The bridge does not automatically give them its stored credential bundle. Separate app authorizations may provide additional access. |
| One-time Mac/Linux pairing computer | Device identity, access/refresh credentials and SDK token in owner-only local files. People with access to that computer/account may read them. | After cloud migration, daily recordings/transcripts and replies are not routed through this computer. |
| GitHub, project author and review models | Public code/docs and public GitHub attribution; review input contains code/docs. | Normal bridge operation does not upload private credentials, recordings/transcripts or Muse chats to them or Codex/OpenAI. |

**Scope of “cannot see”:** the bridge does not transmit or expose the listed data by default. Anything you put in a note travels with it; separate permissions, provider backends or administrators can allow other access. This is not a guarantee that a party can never obtain the information.

**Settings to check:** Webhook only does not force local ASR. Muse's policy describes default training use of sanitized conversations/tool trajectories with a settings opt-out; the bridge does not change that preference.

Policies: [Pebble](https://repebble.com/privacy), [Pebble processing options](https://help.repebble.com/en/articles/15434751-index-01-getting-started-guide), [Google access controls](https://docs.cloud.google.com/docs/security/privileged-access-management), [Muse](https://muse.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse). Local ASR and local agent selection are [independent](https://developer.repebble.com/index-01/); review Muse's training and tool permissions too.

## 3. Step-by-step deployment

### 3.1 For you: ask Codex and complete the phone/account steps

Codex can prepare the code, dependencies, private cloud state, build, deployment and checks. You complete the following account and physical-device actions:

| Step | What you do | What Codex handles |
| --- | --- | --- |
| 1. Start | Send Codex the instruction below and say whether the phone is iPhone or Android, and the pairing computer is Mac or Linux. | Read this repository, inspect the environment and list the required manual actions together. |
| 2. Google Cloud | Sign in, choose your own billing-linked project and complete Google's authorization pages. [Signup](#44-create-a-google-cloud-project) / [trial credits](#45-free-trial-credits). | Prepare the dedicated cloud resources and deploy the bridge; keep min 0/max 1. |
| 3. Muse token | Sign into Muse, obtain an [SDK token](https://gadgets.muse.ai/settings/sdk-tokens) and enter it in the tool's hidden local prompt. Do not put its value in the README or public messages. | Start the pairing tool and store credentials privately. |
| 4. Pair once | On the phone: **Muse → Settings → Devices → Developer mode → Add Device**; select the device shown by the tool and confirm. | Run the local Bluetooth adapter, export/upload paired state and stop the local identity. |
| 5. Pebble settings | **Index → gear → Ring Button → Double click & hold → Webhook only → Webhook settings**. Enter the URL and signing key supplied privately by Codex; add header `Authorization` with value `Bearer <value>` (including the space); enable signing, choose **Both**, then Save. | Provide the private configuration values and verify the signed test event. |
| 6. Accept | Keep Pebble open for the first test. Press/release once, immediately press again and hold while speaking, then release. Check the note and reply in Muse. | Check logs and delivery/deduplication if needed, without blindly resending an uncertain action. |

The phone menu path above uses iPhone; if Index is missing, choose More → Index. Android menus may differ.

Copy this instruction to Codex:

```text
Read the README at https://github.com/wli12/muse-gadget-sdk-for-pebble-ring (branch pebble-cloudrun-pass-through) and deploy its Pebble Index 01 → Meta Muse bridge to my authorized Google Cloud project, following the machine reference. Use one-time local Bluetooth pairing, Cloud Run min 0/max 1 and the Both payload. Keep credentials in the required owner-only local files and dedicated private cloud storage; do not commit real credentials or my deployment addresses to GitHub. Complete the automated work, collect the login/authorization/phone steps I must do into one list, and verify the result.
```

After paired state is uploaded and cloud setup is complete, the pairing computer can be turned off. Daily use needs the phone's Bluetooth and internet connection. A successful webhook test is not a Muse reply; check the real ring message too.

### 3.2 For Codex: deployment and operations reference

<details>
<summary>Expand technical instructions — intended for Codex or a manual administrator</summary>

#### 3.2.1 Prepare the project, code and tools

Use your own billing-linked project; [create one first](#44-create-a-google-cloud-project) if needed. The simplest setup runs these commands in Terminal on the same Mac/Linux computer used for pairing. Cloud Shell is an optional deployment alternative; pairing still requires the local computer.

Clone the public fork containing this feature branch. To substitute your own fork, retain its `pebble-cloudrun-pass-through` branch first; copying only official `main` omits this bridge. Use your own Google Cloud project and deployment account:

```sh
GITHUB_REPO='wli12/muse-gadget-sdk-for-pebble-ring'
git clone "https://github.com/${GITHUB_REPO}.git" muse-ring-sdk
cd muse-ring-sdk
git switch pebble-cloudrun-pass-through
REPO_DIR="$PWD"
BRIDGE_DIR="$REPO_DIR/linux/examples/pebble_ring_bridge_cloudrun"

python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r "$BRIDGE_DIR/requirements.txt" pytest
python -m pip install ./linux

PYTHONPATH="$REPO_DIR/linux/src:$REPO_DIR/linux/examples:$BRIDGE_DIR" \
  python -m pytest -q \
  "$REPO_DIR/linux/examples/pebble_ring_bridge_test.py" \
  "$REPO_DIR/linux/tests/test_pebble_ring_bridge.py" "$BRIDGE_DIR"
bash -n "$BRIDGE_DIR/deploy.sh"
```

Docker need not be installed locally: Cloud Build builds the image. Install the CLI using [Google's instructions](https://cloud.google.com/sdk/docs/install), then run:

```sh
gcloud auth login
PROJECT_ID='your-project-id'
gcloud config set project "$PROJECT_ID"

PRIVATE_WORK_DIR="$HOME/.local/state/muse-ring-bridge"
mkdir -p "$PRIVATE_WORK_DIR"
chmod 700 "$PRIVATE_WORK_DIR"
export BRIDGE_DEPLOY_DATA="$PRIVATE_WORK_DIR/deploy/$PROJECT_ID"
```

`PRIVATE_WORK_DIR` is outside Git. Pairing directories use `0700` and secret files use `0600`. Later commands reuse these variables in the same terminal; in a new terminal, restore the variables and activate the virtual environment again.

With Cloud Shell, the code, tests, and deployment commands are the same; verify the project and Google's authorization prompt. BLE pairing remains on the local Mac/Linux host. Uploading pairing files into Cloud Shell also transfers credentials to Google, so do that only after choosing this architecture. Do not transfer them through public file links or chat attachments.

#### 3.2.2 Pair Muse once

**Choose Linux OR Mac, not both.** Run pairing on the local computer. If you used Cloud Shell above, clone the same repository and prepare Python/the private working directory on the local pairing computer too; transfer the completed pairing files securely to the chosen deployment terminal afterward.

##### Option A: Linux with BLE

See the [Linux SDK README](linux/README.md#set-it-up-with-muse) for the official flow. Use a Debian, Ubuntu, or Raspberry Pi system with BlueZ/BLE; an ordinary cloud host has no local Bluetooth for pairing with your phone. Run these commands from the same repository on **that Linux host**.

Create an [SDK token](https://gadgets.muse.ai/settings/sdk-tokens) and review its terms first. A separate account without sudo access is recommended for the temporary pairing service. The following name is a convention for this guide; substitute a suitable existing account if appropriate:

```sh
sudo useradd --create-home --shell /usr/sbin/nologin musepair
bash linux/install.sh --from "$PWD/linux" --run-as musepair
```

The installer prompts for the SDK token with hidden input. Keep real tokens out of command-line arguments and Git files. It changes system dependencies, Bluetooth configuration, and systemd services on that Linux machine; review its displayed actions before proceeding. When the pairing window opens, use the phone's Muse app:

1. Open **Settings → Devices → Developer mode**.
2. Choose **Add Device / +**, then the installer's `MuseGadgetXXXXXX` name.
3. Confirm that this is your own community device.
4. If asked for Wi-Fi, choose the network the host already uses and follow the official flow; no additional network password is required.

The pairing window lasts about ten minutes; `sudo musegadget pair` opens it again after timeout. That is a window length, not a permanent-connection guarantee. Use a trusted pairing environment. After success, stop the local service and export the three files:

```sh
sudo systemctl disable --now musegadget
PAIRING_DIR="$PRIVATE_WORK_DIR/muse-state"
mkdir -p "$PAIRING_DIR"
chmod 700 "$PAIRING_DIR"
for filename in identity.json pairing.json sdk_token; do
  sudo install -m 600 -o "$(id -u)" -g "$(id -g)" \
    "/var/lib/musegadget/$filename" "$PAIRING_DIR/$filename"
done
```

If deploying elsewhere, transfer the directory securely, preserve permissions, and set its absolute path on the deployment machine. All three files belong to the same device identity and must not be mixed. Do not restart that local identity after cloud token rotation begins; the local copy may then be stale.

##### Option B: Mac

The Mac adapter used here is an experimental community tool, **not a statement of official Meta macOS support**. It replaces only the Bluetooth platform adapter and reuses official pairing. It does not start the generic host-command service. It needs Mac Bluetooth, Python 3.12, and PyObjC wheels.

Run inside the previously created virtual environment:

```sh
python -m pip install --only-binary=:all: \
  -r "$BRIDGE_DIR/requirements-macos.txt"

PAIRING_DIR="$PRIVATE_WORK_DIR/muse-state"
python "$BRIDGE_DIR/mac_pairing.py" --state-dir "$PAIRING_DIR"
```

A hidden prompt obtains the SDK token; the program accepts no token command-line option. If macOS asks for Bluetooth permission, allow the Terminal/Python process running the tool, then add the displayed device in Muse using the Linux option above. The tool refuses to overwrite an existing completed pairing. Do not delete a valid identity merely to investigate a temporary cloud error.

Exit the pairing tool and confirm that no local SDK service uses the same identity before upload. **An iPhone-to-Mac USB cable is not the relay path.** A system “Trust This Computer” prompt concerns USB device access, separately from Muse BLE pairing and ring webhooks. Normal use requires neither USB nor an online Mac.

If pairing and deployment use different terminals, securely transfer the pairing files, then set `PAIRING_DIR='/absolute/path/to/muse-state'` **in the deployment terminal**, using the actual destination directory. If you use the same computer/terminal, keep the `PAIRING_DIR` already set above.

#### 3.2.3 Deploy to Google Cloud

Use a dedicated new project, or confirm that the dedicated resource names are unused. The script modifies bucket versioning, soft delete, and IAM. Do not substitute an existing general-purpose bucket and execute it without reviewing those changes.

```sh
bash "$BRIDGE_DIR/deploy.sh" plan "$PROJECT_ID"
```

`plan` shows the resources it will create under your project, without contacting Google or reading credentials. Check the project and resource names before continuing.

After reviewing the plan, create resources and build. The deploying account needs the relevant API, resource-creation, and service-account-use permissions. This does not mean granting project Owner to the daily runtime account:

```sh
bash "$BRIDGE_DIR/deploy.sh" bootstrap "$PROJECT_ID"
bash "$BRIDGE_DIR/deploy.sh" build "$PROJECT_ID"
```

Next, `upload-pairing` transfers device access/refresh tokens, identity, SDK token, and generated webhook secrets into your private Google bucket. Verify the destination project first; this command actually transfers credentials. Pairing files are excluded from the build:

```sh
bash "$BRIDGE_DIR/deploy.sh" upload-pairing "$PROJECT_ID" "$PAIRING_DIR"
bash "$BRIDGE_DIR/deploy.sh" deploy-private "$PROJECT_ID"
```

`upload-pairing` generates strong random Bearer and signing secrets. Their local copy is:

```sh
KEYS_FILE="$BRIDGE_DEPLOY_DATA/webhook.json"
```

The cloud objects are `state/muse.json` and `state/webhook.json`. Create-only generation preconditions prevent overwriting the live identity's latest rotated tokens. If an upload rerun reports an existing object, inspect state rather than deleting the valid object and trying again.

For a partial initial credential upload, use the [recovery procedure](#partial-initial-credential-upload); do not delete or replace valid device state.

`deploy-private` creates a service requiring Google IAM authentication. After verifying private deployment, publish its HTTPS endpoint for the phone:

```sh
bash "$BRIDGE_DIR/deploy.sh" publish "$PROJECT_ID"
bash "$BRIDGE_DIR/deploy.sh" status "$PROJECT_ID"

SERVICE_URL=$(gcloud run services describe muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1 --format='value(status.url)')
curl --fail --show-error --silent "$SERVICE_URL/health"
```

`publish` grants `allUsers` permission to invoke this service. App-level Bearer and valid signatures are still required, so ordinary visitors cannot submit chat messages. `GET /health` returns static health only; it does not read device credentials or test Muse connectivity. If organization policy prohibits public Cloud Run, an administrator must choose an allowed architecture rather than disable organization protections.

`BRIDGE_BUCKET` is the only required runtime environment variable. Secrets load from private storage, not command-line arguments, the image, or Cloud Run environment variables. All messages target Muse's main chat; there is no chat-selection or semantic-routing option.

##### Cloud resource names

| Resource | Name |
| --- | --- |
| Region | `us-central1` |
| Cloud Run service | `muse-ring-bridge` |
| Private state bucket | `${PROJECT_ID}-muse-ring-state` |
| Build-source bucket | `${PROJECT_ID}-muse-ring-build` |
| Artifact Registry repository | `muse-ring` |
| Runtime service account | `muse-ring-runtime@${PROJECT_ID}.iam.gserviceaccount.com` |
| Builder service account | `muse-ring-builder@${PROJECT_ID}.iam.gserviceaccount.com` |

#### 3.2.4 Configure the phone and test the ring

Use `$SERVICE_URL/index` and the private `KEYS_FILE` values `bearer` and `signing_secret`. Open it in the chosen deployment environment’s editor (Cloud Shell Editor if using Cloud Shell) and copy the values into the phone fields. Keep the file, secrets, and secret-containing screenshots out of GitHub and chat. The instructions below need no extra setup-page generator.

These paths were checked on **Pebble 1.18.0 for iPhone**, against the [official Index settings source](https://github.com/coredevices/mobileapp/blob/1.18.0/experimental/src/commonMain/kotlin/coredevices/ring/ui/screens/settings/IndexSettings.kt) and [button/webhook settings source](https://github.com/coredevices/mobileapp/blob/1.18.0/experimental/src/commonMain/kotlin/coredevices/ring/ui/screens/settings/ButtonSwitchboard.kt):

1. Open **Index** at the bottom, or **More → Index** if it is not a bottom tab.
2. Use the top-right gear, then **Settings → Ring Button**.
3. Keep **Hold & talk → Index Agent**; assign **Double click & hold → Webhook only**.
4. Open that gesture's **Webhook settings**. The title should be **Webhook · Double click & hold**.
5. Set your HTTPS `/index` URL. Add a header named `Authorization`, with `Bearer ` followed by the local `bearer` value, including the separating space.
6. Enable **Sign requests** and enter the local `signing_secret`.
7. Select **Both** and save. Pebble waits for transcription and uploads text plus recording, which the bridge submits together. **Recording** uploads voice before Pebble ASR; **Transcription** uploads text only.
8. Use **Send test event**. Success establishes webhook connectivity/authentication and should not add a Muse chat message.
9. To use **Double click & hold**, press and release once, immediately press again and keep holding while speaking, then release. Say a benign instruction such as “Please reply only: ring connected.” Check its transcription and recording in Pebble, then the message and reply in Muse's main chat. The bridge cannot guarantee playback controls; text-only presentation does not establish lost audio.

Reopen the **same gesture's** webhook page after leaving settings. Android supports the same protocol, but its UI may differ; the iPhone tap sequence is not presented as a tested Android walkthrough.

A note appearing in Pebble does not establish that a webhook was sent. Confirm the configured gesture and actual payload in Recent runs.

iOS/Pebble background operation still governs collecting ring audio, queueing, and sending. Both includes the Pebble ASR wait; Recording avoids that wait but cannot guarantee immediate background delivery. If opening Pebble starts processing, compare phone collection/upload times with cloud receipt times. The Google service cannot repair a recording that the phone has not uploaded or force iOS background ASR to run immediately.

#### 3.2.5 Verify the cloud service

Start with a check that sends no chat message:

```sh
python "$BRIDGE_DIR/verify_webhook.py" --url "$SERVICE_URL" --keys "$KEYS_FILE"
```

It verifies that a signed synthetic event returns 200 and invalid authentication returns 401. The following explicitly submits **one** benign message, then replays its delivery ID to check deduplication:

```sh
python "$BRIDGE_DIR/verify_webhook.py" --url "$SERVICE_URL" --keys "$KEYS_FILE" \
  --send --report "$PRIVATE_WORK_DIR/smoke-report.json"
```

Each separate `--send` run creates a new ID and therefore a new message. The script does not retry network failures or follow redirects. Its report contains delivery IDs and check metadata, not note text or secrets.

`verify_webhook.py --send` checks text submission, not voice or Both. Build-time `audio_test.py` uses synthetic audio to verify real FFmpeg conversion without contacting Muse. The user confirmed real Recording and Both messages/replies, including original Pebble text and complete playable audio. Neither text-only display nor an SDK ACK establishes audio retention or presentation in the app.

Acceptance order is: offline tests → container codec and HTTP-startup checks → signed synthetic event → an explicitly selected real message plus deduplication → a physical ring using the selected payload. Earlier checks do not replace the physical test. Confirm the correct account and main chat in Muse.

#### 3.2.6 Delivery state and recovery

The state bucket uses these dedicated paths:

| Object | Contents and purpose |
| --- | --- |
| `state/muse.json` | `{identity, pairing, sdk_token}`, including rotating device credentials. |
| `state/webhook.json` | `{bearer, signing_secret}` for phone-ingress authentication. |
| `runtime/lease.json` | Global device-session lease, valid for 300 seconds. |
| `ledger/<SHA256(delivery ID)>.json` | Text/normalized-WAV digest, delivery state, time, and restricted diagnostics; no original text/audio. |

GCS generation preconditions implement compare-and-swap. Refreshed state is persisted before entering message submission. The lease prevents simultaneous use of the same identity by requests or old/new revisions. An in-container thread lock also protects process-global SDK state. Instance limits do not replace a cross-revision lease.

The send boundary is durable: write `sending` before calling the SDK's `send_chat` or `send_voice`, then mark `delivered` only on acknowledgement. Both paths share this boundary and deduplication. This reduces duplicate-action risk conservatively; it cannot add strict exactly-once semantics to a third-party chat protocol.

| Response/state | Handling |
| --- | --- |
| `200 delivered` | SDK acknowledged submission; the same ID/content returns `duplicate: true`. |
| `202 needs_review` | Submission began but acknowledgement is incomplete; retain the entry and do not automatically resend it. |
| `503 not_delivered_retry_later` | Submission has not begun; retry may be attempted later. |
| `503 busy_retry_later` | Device session is busy or a valid lease remains. |
| `409 delivery_id_reused` | Same ID has a different body digest; refuse replacement. |
| `401 unauthorized` | Invalid Bearer, signature, or timestamp. |
| `200 test: true` | Synthetic connectivity check; no Muse message. |

For `sending`/`uncertain`, check Muse for the message or completed action first. **Do not delete the ledger and replay blindly**: it could execute the instruction twice. Wait for a crash-left lease to expire; do not remove an active lease to force concurrency.

##### Partial initial credential upload

If the initial `muse.json` upload succeeds but `webhook.json` fails, a complete rerun stops at the already-existing first object. Confirm that this is **the same initial setup** and that valid device state was uploaded. Inspect object metadata only:

```sh
gcloud storage objects describe \
  "gs://${PROJECT_ID}-muse-ring-state/state/muse.json" \
  --project="$PROJECT_ID" --format='yaml(name,generation,timeCreated)'
gcloud storage objects describe \
  "gs://${PROJECT_ID}-muse-ring-state/state/webhook.json" \
  --project="$PROJECT_ID" --format='yaml(name,generation)'
```

Only when the second command explicitly returns **404 Not Found**, and the local `KEYS_FILE` is the original file generated for that setup, upload the missing object alone. A 403 or network failure does not mean absence:

```sh
gcloud storage cp "$KEYS_FILE" \
  "gs://${PROJECT_ID}-muse-ring-state/state/webhook.json" \
  --project="$PROJECT_ID" --if-generation-match=0
```

If both objects already exist, verify the setup and continue with private deployment. This recovery does **not delete or overwrite `state/muse.json`**, reupload stale tokens, or replace concurrently created secrets; `--if-generation-match=0` prevents that replacement.

#### 3.2.7 Updates, secret rotation and shutdown

##### Update code

Rerun offline tests, `build`, and `deploy-private`. Check IAM and the latest revision; use `publish` if reopening phone access. An application update **does not require uploading pairing files again**. Cloud `state/muse.json` contains the current rotated state; never replace it with an older Mac/Linux copy.

```sh
PYTHONPATH="$REPO_DIR/linux/src:$REPO_DIR/linux/examples:$BRIDGE_DIR" \
  python -m pytest -q \
  "$REPO_DIR/linux/examples/pebble_ring_bridge_test.py" \
  "$REPO_DIR/linux/tests/test_pebble_ring_bridge.py" "$BRIDGE_DIR"
bash "$BRIDGE_DIR/deploy.sh" build "$PROJECT_ID"
bash "$BRIDGE_DIR/deploy.sh" deploy-private "$PROJECT_ID"
bash "$BRIDGE_DIR/deploy.sh" status "$PROJECT_ID"
```

##### Rotate webhook secrets

Pause public invocation, create a new local file, replace `state/webhook.json` with a generation precondition, update the same phone gesture's two secrets, and reopen access. Secrets are cached for at most 60 seconds; allow expiry or deploy a new revision and verify it. These commands do not read or print the old secrets:

```sh
gcloud run services remove-iam-policy-binding muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1 \
  --member=allUsers --role=roles/run.invoker

ROTATED_KEYS="$PRIVATE_WORK_DIR/webhook-next.json"
python - "$ROTATED_KEYS" <<'PY'
import json, os, secrets, sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump({'bearer': secrets.token_urlsafe(32),
               'signing_secret': secrets.token_urlsafe(32)}, stream)
PY

CURRENT_GENERATION=$(gcloud storage objects describe \
  "gs://${PROJECT_ID}-muse-ring-state/state/webhook.json" \
  --project="$PROJECT_ID" --format='value(generation)')
gcloud storage cp "$ROTATED_KEYS" \
  "gs://${PROJECT_ID}-muse-ring-state/state/webhook.json" \
  --project="$PROJECT_ID" --if-generation-match="$CURRENT_GENERATION"
KEYS_FILE="$ROTATED_KEYS"
```

Use the new `KEYS_FILE` to configure the same phone gesture as in Section 3.2.4. Wait for the 60-second cache window, then `publish` and run the no-chat connectivity check. Keep using the new keys path, not old secrets or a stale private setup page. If device credentials leaked, revoke the affected device/SDK token in Muse and pair again; rotating webhook secrets does not revoke Muse device access.

##### Roll back the application

List revisions and choose one known to support the current state format and final pass-through behavior. Roll back the application, not token objects. Seven-day cleanup can remove older images, so confirm availability first.

```sh
gcloud run revisions list --service=muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1
PREVIOUS_REVISION='your-compatible-previous-revision'
gcloud run services update-traffic muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1 \
  --to-revisions="$PREVIOUS_REVISION=100"
```

Check `/health`, signatures, and an explicitly selected real message after rollback.

##### Pause or remove the deployment

To pause, use `remove-iam-policy-binding` above and disable that gesture's webhook in Pebble. Already-started requests may finish; pausing does not retract a task already submitted to Muse.

Before deletion, decide whether to retain this device identity. To resume locally, securely export the **current cloud** bundle and stop the cloud instance before migration; do not start a stale local copy. Otherwise remove the device in Muse, revoke the associated SDK token, and clean up local pairing files, private setup pages, and secrets.

These commands delete this bridge's resources and credentials. Confirm that `PROJECT_ID` is your dedicated project and the buckets contain no unrelated data:

```sh
gcloud run services delete muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1
gcloud artifacts repositories delete muse-ring \
  --project="$PROJECT_ID" --location=us-central1
gcloud storage rm --recursive "gs://${PROJECT_ID}-muse-ring-build" \
  --project="$PROJECT_ID"
gcloud storage rm --recursive "gs://${PROJECT_ID}-muse-ring-state" \
  --project="$PROJECT_ID"
gcloud projects remove-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:muse-ring-builder@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/logging.logWriter --condition=None
gcloud iam service-accounts delete \
  "muse-ring-runtime@${PROJECT_ID}.iam.gserviceaccount.com" --project="$PROJECT_ID"
gcloud iam service-accounts delete \
  "muse-ring-builder@${PROJECT_ID}.iam.gserviceaccount.com" --project="$PROJECT_ID"
```

Do not disable APIs shared by other applications or delete their logs/billing history. Public source may be shared; real credentials, private generated pages, recordings, chats, and personal deployment identity remain outside Git.

##### Current diagnostic rules

- Use real prebuilt Python/Git on Mac; avoid system shims that trigger unavailable Apple developer-tool downloads.
- For Cloud Build 403, check project, APIs, builder identity and IAM propagation; do not grant the runtime project Owner.
- For Muse handshake failures, keep valid pairing and TLS/signatures. Inspect safe diagnostics; do not reset identity or replay an uncertain send blindly.
- For text-only display, verify a new gesture's actual Recent runs payload; Muse controls audio presentation.


</details>

## 4. FYI: implementation and operations

### 4.1 Where each part runs

For your own resources, there are two places: **your Mac/Linux computer** and **your Google Cloud project**. Cloud Run is the managed place that runs this bridge; you do not need to manage a separate virtual machine. Cloud Storage is its file storage, not another program-running computer.

| Place | What it does / which files | When it is needed |
| --- | --- | --- |
| Your local Mac or Linux computer | Bluetooth pairing; Mac uses `mac_pairing.py`, Linux uses the official installer. It can also run deployment commands. | Initial pairing/state upload; optional later administration. It can be off during daily use. |
| Google Cloud → **Cloud Run** | Runs `app.py`, `muse_sender.py`, `muse_transport.py`, `audio.py` and the shared `pebble_ring_bridge.py`. Receives webhooks and submits to Muse. | Each incoming note; idle instances can scale to zero. |
| Google Cloud → **Cloud Storage** | Stores pairing credentials, webhook secrets, leases and delivery state. A bucket is a container for these files. No note bodies/recordings are retained by the bridge. | State persists between requests, even when Cloud Run has no running instance. |
| Deployment terminal: **your Mac/Linux Terminal OR Cloud Shell** | Runs `deploy.sh`, `verify_webhook.py` and checks. Cloud Shell is a terminal opened in your browser but running on Google infrastructure. | Setup, updates and checks; not part of daily forwarding. Choose one deployment terminal. |
| Google Cloud → **Cloud Build + Artifact Registry** | Build/test `Dockerfile` + dependencies, then store the image Cloud Run starts. | Build runs during releases; the image remains available afterward. No pairing credentials enter the build. |
| GitHub | Stores public source, guides and PR history. | Code distribution/review; does not receive ring webhooks. |
| Meta Muse | Processes the submitted instruction and produces the reply. | Each submitted command; separate from your Mac and Google project. |

Official introductions: [Cloud Run](https://docs.cloud.google.com/run/docs/overview/what-is-cloud-run), [Cloud Storage](https://docs.cloud.google.com/storage/docs/introduction), [Cloud Shell](https://docs.cloud.google.com/shell/docs), [Cloud Build](https://docs.cloud.google.com/build/docs/overview). The relay is not hosted on Codex/OpenAI machines. GitHub publication and Google deployment are separate actions; this repository has no automatic merge-to-deploy workflow.

### 4.2 Changes from the official Muse example

| Behavior | Official example | This bridge |
| --- | --- | --- |
| Host and connection | Permanent Linux service using a local SDK socket. | Cloud Run connects per request; no always-on Mac, VPN, or tunnel. |
| Chat destination | Required side chat; appends `[via Pebble Ring]`. | Fixed main chat; trim surrounding whitespace only, with no suffix, wake word, or routing. |
| Input | Extract text; do not submit recordings as voice. | Both submits text/audio together; text-only and recording-only remain supported. |
| Audio | No conversion. | Local FFmpeg WAV conversion; reject overlength input without truncating an instruction. |
| Webhook protocol | `/ingest`; multipart, JSON, or plain text with a shared token. | `/index`; Pebble v1 multipart, Bearer, HMAC, and timestamp window. |
| Test events | No special Pebble v1 handling. | After authentication, return connectivity status without asking Muse to perform a test task. |
| State | Managed by the permanent local SDK service. | Private GCS holds rotated state, a CAS lease, and a delivery ledger. |
| Duplicates and uncertain sends | No durable delivery ledger. | Deduplicate the same ID; do not automatically resend after an uncertain attempt. |
| Host-command access | Depends on the installed generic SDK service. | No shell, file-access, or other host commands are registered. |
| Network destinations | SDK permits configurable endpoints and redirects. | Fixed official Muse origins, no redirects/environment proxies; fixed Google Storage endpoint. |
| Deployment and reproduction | One local listener example. | Container, least-privilege deployment, matching module tests, and this guide. |

**Pinned SDK version:** [official Meta commit `08f89ed`](https://github.com/facebookincubator/muse-gadget-sdk/commit/08f89ed329918c27d53b11192065ad6b2cb0e868). This is a **public Git source-version identifier**, the same for everyone using that revision. It is not your SDK token, account, device identity or pairing code. [Runtime files and module tests](linux/examples/pebble_ring_bridge_cloudrun/README.md).

### 4.3 Final behavior and limits

| Final behavior / limit | What it means |
| --- | --- |
| Payload | **Both** submits Pebble's text and complete converted audio in one Muse message. Recording-only and text-only are also supported. Muse decides how to use/display them and may transcribe audio again. |
| Audio | At most **8 MiB input and 60 seconds**, converted locally to PCM16 mono 16 kHz WAV (SDK limit 2 MiB). Invalid/overlong audio is rejected, not cut short and executed. |
| Delivery | `delivered` is a submission acknowledgement, not task completion. If acknowledgement is uncertain, check Muse before resending; duplicates are not automatically re-executed. |
| Latency | Pebble/iOS background collection and ASR can delay upload. The relay can only act after the phone sends the note. |
| Storage and access | Private storage keeps rotating credentials, webhook keys and delivery state, not note bodies/audio. Request metadata can remain in Google logs; authorized administrators/provider access still apply. |
| Connections | Cloud forwarding uses fixed official Muse/Google destinations; Muse redirects/environment proxies and Storage endpoint overrides are rejected. This is an application restriction, not an OS sandbox or a restriction on other apps/provider backends. |
| Availability | The controlled 2026-11-18 recheck encountered Muse Link HTTP 502 before submission; that submission remains unconfirmed. This does not establish a global Meta outage; verify a new real message before accepting a deployment. |
| Watch | Official Muse Watch support exists, but synchronization/notifications for this bridge's replies remain unverified. |

The service scales to zero when idle; low use may fit free allowances, but budgets are alerts rather than a hard spending cap. [Current prices](https://cloud.google.com/run/pricing) and [your actual costs](#46-view-your-actual-costs) should be checked. File/test details and review history are available in the [code directory](linux/examples/pebble_ring_bridge_cloudrun/README.md) and [PR](https://github.com/wli12/muse-gadget-sdk-for-pebble-ring/pull/1).

### 4.4 Create a Google Cloud project

1. Open [Google Cloud Free](https://cloud.google.com/free) and sign in with an existing Google account, or create one. Eligible new users can choose the official free trial; its allowances, duration, and eligibility are controlled by that page.
2. Supply your actual region and required signup information, and complete Google's identity/payment-method verification. **A valid billing account is required even when using free allowances.** See [Create a billing account](https://docs.cloud.google.com/billing/docs/how-to/create-billing-account) and the [Free Trial FAQ](https://cloud.google.com/signup-faqs).
3. Open the [Google Cloud console](https://console.cloud.google.com/), use the project selector, and choose **New project** to create a dedicated project. See [Create and manage projects](https://docs.cloud.google.com/resource-manager/docs/creating-managing-projects). Record the **Project ID** for `PROJECT_ID` in the Codex reference; it is not the display name or numeric project number.
4. In the project's **Billing** page, confirm that a valid billing account is linked. Otherwise follow [Link a billing account](https://docs.cloud.google.com/billing/docs/how-to/modify-project). A project and a billing account are distinct objects.
5. Launch **Cloud Shell** from the console's top-right corner, where `gcloud` is preinstalled, or install Google Cloud CLI locally. The Codex reference runs `gcloud auth login` and selects the project. BLE pairing still happens on local Mac/Linux hardware.
6. Optionally configure [budget alerts](https://docs.cloud.google.com/billing/docs/how-to/budgets). Alert-only budgets do not stop spending automatically. Small workloads may fit free allowances, but there is no permanent zero-bill guarantee. A trial does not upgrade itself to a paid account; resources can stop when it expires, and the account owner decides whether to upgrade. Section 4.3 covers costs and resource limits.

### 4.5 Free Trial credits

**Checked 2026-11-18:** Google advertises **US$300 of Free Trial credits for eligible new customers, valid for 90 days**. Check the current amount and eligibility on [Google Cloud Free](https://cloud.google.com/free) and the [official program page](https://docs.cloud.google.com/free/docs/free-cloud-features).

Trial credits are separate from monthly Free Tier allowances. **Valid remaining credits can cover eligible usage beyond those free allowances**, so a small overage need not be paid out of pocket. The [official FAQ](https://cloud.google.com/signup-faqs) explains coverage and billing. Without a manual upgrade, a Free Trial account closes and resources stop when credits run out or the trial expires; it does not automatically start charging. After upgrading to a Paid billing account, uncovered usage can be billed. Check your own account's status, balance and expiry in Billing; trial credits do not guarantee permanent free operation.

### 4.6 View your actual costs

1. Open [Google Cloud Billing Reports](https://console.cloud.google.com/billing/reports) and select the billing account linked to your project.
2. Set the date range to this month or deployment date through today. In **Projects**, select your `PROJECT_ID`; use **Group by → Service** to inspect Cloud Run, Cloud Storage, Cloud Build, and Artifact Registry separately.
3. Review usage cost, **Savings** (credits/discounts), and the after-credit **Subtotal**. Compare pre-credit cost as well, so trial credit is not mistaken for zero resource usage. For finalized invoice charges, use the billing-period view or [Cost table](https://docs.cloud.google.com/billing/docs/how-to/cost-table).
4. Billing data arrives with a delay: a fresh zero is not a final zero bill. See the [official Reports guide](https://docs.cloud.google.com/billing/docs/how-to/reports).

Cloud Run free allowances are shared across projects on the billing account. Storage, builds, image storage, and network usage have separate prices/allowances. This is a low-cost option for light use, not a guaranteed zero-cost service; the pricing links above provide details.

### 4.7 License

Muse Gadgets is licensed under the Apache License, Version 2.0, found in
[`LICENSE`](LICENSE), except for these third-party files, which keep their
upstream licenses. This fork retains those files and notices; the table corresponds to the [upstream SDK license inventory](https://github.com/facebookincubator/muse-gadget-sdk/blob/08f89ed329918c27d53b11192065ad6b2cb0e868/README.md#license):

| Path | Upstream | License |
|---|---|---|
| [`esp32/components/minimp3/include/minimp3.h`](esp32/components/minimp3) | [lieff/minimp3](https://github.com/lieff/minimp3) | CC0-1.0, see [`LICENSE`](esp32/components/minimp3/LICENSE) |
| [`esp32/components/muse/boards/ostb_echoear_display_init.h`](esp32/components/muse/boards/ostb_echoear_display_init.h) | [78/xiaozhi-esp32 ESP-VoCat panel sequence](https://github.com/78/xiaozhi-esp32/blob/0d576d3d4c049c6f55eaf879725dc23e516511b4/main/boards/espressif/esp-vocat/esp_vocat.cc) | MIT, retained in the file header |
| ST7789 tuning table in [`board_atk_dnesp32s3_box2.c`](esp32/components/muse/boards/board_atk_dnesp32s3_box2.c) | [78/xiaozhi-esp32 ATK-DNESP32S3-BOX2 board](https://github.com/78/xiaozhi-esp32/blob/a78ca19d5bc9a12ee0853b9f509530276d36aa03/main/boards/alientek/atk-dnesp32s3-box2-wifi/atk_dnesp32s3_box2.cc) | MIT, see [`xiaozhi-esp32.txt`](esp32/devices/licenses/xiaozhi-esp32.txt) |
| JD9853A initialization sequence in [`board_seeed_xiao_s3_147.c`](esp32/components/muse/boards/board_seeed_xiao_s3_147.c) | [Seeed-Studio/Seeed_GFX2](https://github.com/Seeed-Studio/Seeed_GFX2) | MIT, see [`Seeed_GFX2.txt`](esp32/devices/licenses/Seeed_GFX2.txt) |
| [`esp32/components/muse/boards/viewe_smartring_display_init.h`](esp32/components/muse/boards/viewe_smartring_display_init.h) | [VIEWESMART/smartring_plus](https://github.com/VIEWESMART/smartring_plus) panel sequence | Apache-2.0 under VIEWE's copyright, in the file header |
| [`esp32/components/muse/boards/waveshare_s3_185b_init.h`](esp32/components/muse/boards/waveshare_s3_185b_init.h) | [waveshareteam/ESP32-S3-Touch-LCD-1.85B](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.85B/tree/7232af4c501c64d34215b73e1d8319376dfd9c19/examples/esp-idf/05_bq27220/components/esp32_s3_touch_lcd_1_85B) panel sequences | Apache-2.0, in the file header |
| [`esp32/main/pixel_font.c`](esp32/main/pixel_font.c) | Adafruit GFX `glcdfont.c` | BSD-2-Clause, in the file header |
| [`esp32/components/muse/fonts/`](esp32/components/muse/fonts) | GNU Unifont 16.0.04, Noto Sans SC, and Montserrat Medium via LVGL, subsets (see its README) | SIL Open Font License 1.1 (Unifont also GPL-2.0+ with the font embedding exception) |
| [`esp32/components/muse/eink/fonts/montserrat.ttf`](esp32/components/muse/eink/fonts) | Montserrat Medium, via LVGL v9.5.0 | SIL Open Font License 1.1 |
| [`esp32/components/muse/eink/fonts/unscii.ttf`](esp32/components/muse/eink/fonts) | unscii-8 | Public domain |
| [`esp32/components/muse/eink/fonts/symbols.ttf`](esp32/components/muse/eink/fonts) | Font Awesome 5 Free, via LVGL v9.5.0 | SIL Open Font License 1.1 (icons CC BY 4.0) |
| [`esp32/main/epd_ssd1677.c`](esp32/main/epd_ssd1677.c) | Seeed-reTerminal-E10xx-Firmware's SSD1677 refresh sequence | MIT, retained in the file header |
| [`esp32/components/tinyh264/src`](esp32/components/tinyh264) | the Android Open Source Project's H.264 decoder, via [udevbe/tinyh264](https://github.com/udevbe/tinyh264) | Apache-2.0, AOSP's copyright, see [`LICENSE`](esp32/components/tinyh264/LICENSE) |
| [`esp32/components/libvpx_vp8/src`](esp32/components/libvpx_vp8) | [libvpx](https://chromium.googlesource.com/webm/libvpx) v1.16.0's VP8 decoder, trimmed | BSD-3-Clause, the WebM Project authors' copyright, see [`LICENSE`](esp32/components/libvpx_vp8/LICENSE), with Google's patent grant in [`PATENTS`](esp32/components/libvpx_vp8/PATENTS) |

Dependencies fetched at build time are under their own licenses: ESP-IDF
components (into `esp32/managed_components/`), and the simulator's LVGL and
SDL (listed in [`esp32/simulator/THIRD_PARTY.md`](esp32/simulator/THIRD_PARTY.md)).

The Apache License does not cover the [Jollybot avatar](esp32/avatar).

Muse Gadgets Test's sample clips aren't in this repository: the app fetches a few
seconds of the Blender Foundation's open movies (CC BY 3.0) when one is
played, and shows their credits.

Muse Gadgets Test can use x264 (GPL-2.0) on the phone; this repository carries none
of its code. An APK or iPhone app you build with it is under the GPL
([`android/musegadgetstest/README.md`](android/musegadgetstest/README.md),
[`ios/musegadgetstest/README.md`](ios/musegadgetstest/README.md)).
