#!/usr/bin/env bash
# Reviewable stages: no command here deploys/publishes as an incidental side effect.
set -euo pipefail
umask 077

ACTION=${1:-plan}
PROJECT_ID=${2:-}
REGION=us-central1
SERVICE=muse-ring-bridge
REPOSITORY=muse-ring
RUNTIME_NAME=muse-ring-runtime
BUILDER_NAME=muse-ring-builder
SDK_COMMIT=08f89ed329918c27d53b11192065ad6b2cb0e868
SOURCE_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
PYTHON=${BRIDGE_PYTHON:-python3}

if [[ ! $PROJECT_ID =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  echo 'Usage: deploy.sh plan|bootstrap|build|upload-pairing|upload-bundle|deploy-private|publish|status PROJECT_ID [PRIVATE_DIRECTORY]'
  exit 2
fi

STATE_BUCKET=${PROJECT_ID}-muse-ring-state
BUILD_BUCKET=${PROJECT_ID}-muse-ring-build
RUNTIME_SA=${RUNTIME_NAME}@${PROJECT_ID}.iam.gserviceaccount.com
BUILDER_SA=${BUILDER_NAME}@${PROJECT_ID}.iam.gserviceaccount.com
DEPLOY_DATA=${BRIDGE_DEPLOY_DATA:-$SOURCE_DIR/.deployment/$PROJECT_ID}
IMAGE_BASE=${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${SERVICE}

plan() {
  cat <<EOF
Project: $PROJECT_ID
Region: $REGION
Cloud Run: $SERVICE
Runtime: request-based CPU billing; 1 vCPU / 1 GiB; min 0; max 1; concurrency 1; timeout 120s
Private credential/state bucket: gs://$STATE_BUCKET
Build-only staging bucket: gs://$BUILD_BUCKET
Container repository: $REGION/$REPOSITORY
Runtime identity: $RUNTIME_SA (object access only to the state bucket)
Builder identity: $BUILDER_SA (cannot access the state bucket)
Meta SDK commit: $SDK_COMMIT
No cloud command has run. No credentials have been read or uploaded.

Stages:
  bootstrap       Enable APIs and create the named private resources and identities.
  build           Upload only allowlisted source files; build the image, without credentials.
  upload-pairing  Upload the 3 local Muse pairing files and new webhook keys, only with explicit approval.
  upload-bundle   Upload preassembled muse.json + webhook.json from a private directory, only with explicit approval.
  deploy-private  Create/update Cloud Run with IAM-required access; does not publish the webhook.
  publish         Grant public invocation, only with separate explicit approval; signed requests remain required by the app.
  status          Read deployment metadata only, without reading secret objects.
EOF
}

gcloud_cmd() { gcloud "$@" --project "$PROJECT_ID"; }
require_cli() { command -v gcloud >/dev/null || { echo 'Google Cloud CLI is required; sign in with gcloud auth login first.'; exit 1; }; }

create_sa() {
  local short=$1 email=$2 description=$3
  if ! gcloud_cmd iam service-accounts describe "$email" >/dev/null 2>&1; then
    gcloud_cmd iam service-accounts create "$short" --display-name "$description" --quiet
  fi
}

create_bucket() {
  local bucket=$1
  if ! gcloud_cmd storage buckets describe "gs://$bucket" >/dev/null 2>&1; then
    gcloud_cmd storage buckets create "gs://$bucket" --location "$REGION" --uniform-bucket-level-access --public-access-prevention --quiet
  else
    gcloud_cmd storage buckets update "gs://$bucket" --uniform-bucket-level-access --public-access-prevention --quiet
  fi
  # Avoid retaining obsolete versions of rotating credentials. This dedicated
  # bucket is created solely for this bridge; no existing general-purpose bucket is used.
  gcloud_cmd storage buckets update "gs://$bucket" --no-versioning --clear-soft-delete --quiet
}

case "$ACTION" in
  plan) plan ;;
  bootstrap)
    require_cli
    gcloud_cmd services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com storage.googleapis.com iam.googleapis.com --quiet
    create_sa "$RUNTIME_NAME" "$RUNTIME_SA" 'Muse ring runtime'
    create_sa "$BUILDER_NAME" "$BUILDER_SA" 'Muse ring container builder'
    create_bucket "$STATE_BUCKET"
    create_bucket "$BUILD_BUCKET"
    gcloud_cmd storage buckets add-iam-policy-binding "gs://$STATE_BUCKET" --member="serviceAccount:$RUNTIME_SA" --role=roles/storage.objectUser --quiet
    # Builder can read its source tarball, but has no access to pairing state.
    gcloud_cmd storage buckets add-iam-policy-binding "gs://$BUILD_BUCKET" --member="serviceAccount:$BUILDER_SA" --role=roles/storage.objectViewer --quiet
    gcloud_cmd projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$BUILDER_SA" --role=roles/logging.logWriter --condition=None --quiet >/dev/null
    if ! gcloud_cmd artifacts repositories describe "$REPOSITORY" --location "$REGION" >/dev/null 2>&1; then
      gcloud_cmd artifacts repositories create "$REPOSITORY" --location "$REGION" --repository-format=docker --description='Muse ring bridge images; no credentials' --quiet
    fi
    gcloud_cmd artifacts repositories add-iam-policy-binding "$REPOSITORY" --location "$REGION" --member="serviceAccount:$BUILDER_SA" --role=roles/artifactregistry.writer --quiet
    mkdir -p "$DEPLOY_DATA"
    cat > "$DEPLOY_DATA/cleanup-policy.json" <<'JSON'
[
  {"name":"delete-old-versions","action":{"type":"Delete"},"condition":{"tagState":"any","olderThan":"604800s"}},
  {"name":"keep-current","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":1}}
]
JSON
    gcloud_cmd artifacts repositories set-cleanup-policies "$REPOSITORY" --location "$REGION" --policy="$DEPLOY_DATA/cleanup-policy.json" --no-dry-run --quiet
    cat > "$DEPLOY_DATA/build-lifecycle.json" <<'JSON'
{"rule":[{"action":{"type":"Delete"},"condition":{"age":1}}]}
JSON
    gcloud_cmd storage buckets update "gs://$BUILD_BUCKET" --lifecycle-file="$DEPLOY_DATA/build-lifecycle.json" --quiet
    echo 'Bootstrap complete. Nothing has been deployed or published; pairing state has not been read.'
    ;;
  build)
    require_cli
    mkdir -p "$DEPLOY_DATA"
    BUILD_CONTEXT=$(mktemp -d "${TMPDIR:-/tmp}/muse-ring-build.XXXXXX")
    trap 'rm -rf -- "$BUILD_CONTEXT"' EXIT
    # Never use the chat workspace or a pairing directory as a build context.
    for file in app.py muse_sender.py audio.py codec_smoke_test.py requirements.txt Dockerfile; do
      [[ -f "$SOURCE_DIR/$file" ]] || { echo "Missing reviewed source file: $file"; exit 1; }
      cp -- "$SOURCE_DIR/$file" "$BUILD_CONTEXT/$file"
    done
    TAG=$(date -u +%Y%m%dT%H%M%SZ)
    IMAGE=$IMAGE_BASE:$TAG
    cat > "$BUILD_CONTEXT/cloudbuild.yaml" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ['build', '--build-arg', 'MUSE_SDK_COMMIT=$SDK_COMMIT', '-t', '$IMAGE', '.']
images: ['$IMAGE']
options:
  logging: CLOUD_LOGGING_ONLY
timeout: 900s
EOF
    gcloud_cmd builds submit "$BUILD_CONTEXT" --region "$REGION" --config "$BUILD_CONTEXT/cloudbuild.yaml" --service-account="projects/$PROJECT_ID/serviceAccounts/$BUILDER_SA" --gcs-source-staging-dir="gs://$BUILD_BUCKET/sources" --quiet
    printf '%s\n' "$IMAGE" > "$DEPLOY_DATA/image.txt"
    echo 'Source-only container build complete. No pairing credentials were included.'
    ;;
  upload-pairing)
    require_cli
    if [[ ${APPROVED_CREDENTIAL_UPLOAD:-} != yes ]]; then
      echo "Stopped: explicit approval is required to upload Muse device access/refresh tokens, identity, SDK token, and webhook keys to gs://$STATE_BUCKET."
      echo 'After approval, set APPROVED_CREDENTIAL_UPLOAD=yes for this one invocation.'
      exit 3
    fi
    PAIRING_DIR=${3:-}
    [[ -n "$PAIRING_DIR" && -d "$PAIRING_DIR" ]] || { echo 'Pass the local pairing directory as the third argument.'; exit 2; }
    mkdir -p "$DEPLOY_DATA"
    BUNDLE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/muse-ring-pairing.XXXXXX")
    trap 'rm -rf -- "$BUNDLE_DIR"' EXIT
    "$PYTHON" - "$PAIRING_DIR" "$BUNDLE_DIR" "$DEPLOY_DATA" <<'PY'
from pathlib import Path
import json, os, secrets, sys
source, dest, local = map(Path, sys.argv[1:])
identity = json.loads((source/'identity.json').read_text())
pairing = json.loads((source/'pairing.json').read_text())
token = (source/'sdk_token').read_text().strip()
if not token.startswith('mgst_') or not all(pairing.get(k) for k in ('access_token','refresh_token')):
    raise SystemExit('Pairing files are missing expected credentials; nothing was uploaded.')
(dest/'muse.json').write_text(json.dumps({'identity':identity,'pairing':pairing,'sdk_token':token}))
keys = local/'webhook.json'
if not keys.exists():
    fd = os.open(keys,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump({'bearer':secrets.token_urlsafe(32),'signing_secret':secrets.token_urlsafe(32)},f)
(dest/'webhook.json').write_bytes(keys.read_bytes())
for f in dest.iterdir(): f.chmod(0o600)
PY
    # Never overwrite live rotating credentials or webhook keys accidentally.
    gcloud_cmd storage cp "$BUNDLE_DIR/muse.json" "gs://$STATE_BUCKET/state/muse.json" --if-generation-match=0 --quiet
    gcloud_cmd storage cp "$BUNDLE_DIR/webhook.json" "gs://$STATE_BUCKET/state/webhook.json" --if-generation-match=0 --quiet
    echo 'Credential upload complete. Do not run the same gadget identity on the Mac while the cloud bridge is using it.'
    echo "Phone webhook keys are in the owner-only local file: $DEPLOY_DATA/webhook.json"
    ;;
  upload-bundle)
    require_cli
    if [[ ${APPROVED_CREDENTIAL_UPLOAD:-} != yes ]]; then
      echo "Stopped: explicit approval is required to upload device credentials and webhook keys to gs://$STATE_BUCKET."
      exit 3
    fi
    PRIVATE_DIR=${3:-}
    [[ -n "$PRIVATE_DIR" && -d "$PRIVATE_DIR" ]] || { echo 'Pass the private directory holding muse.json and webhook.json.'; exit 2; }
    "$PYTHON" - "$PRIVATE_DIR" <<'PY'
from pathlib import Path
import json, sys
p=Path(sys.argv[1])
m=json.loads((p/'muse.json').read_text())
w=json.loads((p/'webhook.json').read_text())
assert isinstance(m.get('identity'),dict)
assert all(m.get('pairing',{}).get(k) for k in ('access_token','refresh_token'))
assert isinstance(m.get('sdk_token'),str) and m['sdk_token'].startswith('mgst_')
assert all(isinstance(w.get(k),str) and len(w[k])>=32 for k in ('bearer','signing_secret'))
print('Private bundle structure checked; no credential values printed.')
PY
    gcloud_cmd storage cp "$PRIVATE_DIR/muse.json" "gs://$STATE_BUCKET/state/muse.json" --if-generation-match=0 --quiet
    gcloud_cmd storage cp "$PRIVATE_DIR/webhook.json" "gs://$STATE_BUCKET/state/webhook.json" --if-generation-match=0 --quiet
    echo 'Uploaded private state. Do not start the same gadget identity on the Mac while cloud requests use it.'
    ;;
  deploy-private)
    require_cli
    IMAGE=$(cat "$DEPLOY_DATA/image.txt")
    [[ $IMAGE == "$IMAGE_BASE:"* ]] || { echo 'Unexpected image reference; refusing deployment.'; exit 1; }
    gcloud_cmd storage objects describe "gs://$STATE_BUCKET/state/muse.json" >/dev/null
    gcloud_cmd storage objects describe "gs://$STATE_BUCKET/state/webhook.json" >/dev/null
    gcloud_cmd run deploy "$SERVICE" --region "$REGION" --image "$IMAGE" --service-account "$RUNTIME_SA" --cpu=1 --memory=1Gi --cpu-throttling --no-cpu-boost --min=0 --max=1 --min-instances=0 --max-instances=1 --concurrency=1 --timeout=120s --port=8080 --startup-probe=httpGet.path=/health,httpGet.port=8080,initialDelaySeconds=0,periodSeconds=5,timeoutSeconds=2,failureThreshold=12 --set-env-vars="BRIDGE_BUCKET=$STATE_BUCKET" --no-allow-unauthenticated --ingress=all --quiet
    echo 'Cloud Run deployed privately. Anonymous phone requests are not admitted yet.'
    ;;
  publish)
    require_cli
    if [[ ${APPROVED_PUBLIC_WEBHOOK:-} != yes ]]; then
      echo "Stopped: explicit approval is required to make the HTTPS endpoint for $PROJECT_ID/$SERVICE publicly reachable."
      echo 'The application will still require the private bearer and a valid Pebble HMAC signature.'
      exit 3
    fi
    gcloud_cmd run services add-iam-policy-binding "$SERVICE" --region "$REGION" --member=allUsers --role=roles/run.invoker --quiet
    URL=$(gcloud_cmd run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
    echo "Webhook: $URL/index"
    echo 'Phone settings: Authorization: Bearer <local bearer>; Sign requests: on; signing_secret; Recording (Muse processes the audio), or Transcription for text fallback.'
    ;;
  status)
    require_cli
    gcloud_cmd run services describe "$SERVICE" --region "$REGION" --format='yaml(status.url,status.conditions,spec.template.spec.containerConcurrency,spec.template.spec.timeoutSeconds,spec.template.metadata.annotations,spec.template.spec.serviceAccountName)'
    ;;
  *) echo 'Unknown stage. Run plan first.'; exit 2 ;;
esac
