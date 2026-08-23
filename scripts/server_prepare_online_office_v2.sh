#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: $0 <40-char-source-commit> <sha256:source-snapshot> [work-root]" >&2
  exit 2
fi

SOURCE_COMMIT="$1"
SOURCE_SNAPSHOT_SHA256="$2"
WORK_ROOT="${3:-$PWD/.trace-g-server-build}"
SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE="$SOURCE_ROOT/config/releases/v0.2.0-rc.2.json"
MODEL_STORE="$WORK_ROOT/ollama-models"
WHEELHOUSE="$WORK_ROOT/python-wheelhouse"
EVIDENCE="$WORK_ROOT/evidence"
MODEL_EVIDENCE="$EVIDENCE/online-model-verification.json"
BUILD_RECEIPT="$EVIDENCE/online-build-receipt.json"

OLLAMA_IMAGE="ollama/ollama:0.32.1"
PYTHON_IMAGE="python:3.11.9-slim-bookworm@sha256:8fb099199b9f2d70342674bd9dbccd3ed03a258f26bbd1d556822c6dfc60c317"
NODE_IMAGE="node:24.14.0-bookworm-slim"
LANGGRAPH_IMAGE="trace-g/langgraph-agent:v0.2.0-rc.2"
HARNESS_IMAGE="trace-g/deepseek-harness-agent:v0.2.0-rc.2"
MUTATOR_IMAGE="trace-g/qwen-mutator:v0.2.0-rc.2"
CONTROLLER_IMAGE="trace-g/controller:v0.2.0-rc.2"
DOWNLOAD_CONTAINER="trace-g-model-download-$$"

for command in docker nvidia-smi; do
  command -v "$command" >/dev/null || {
    echo "required host command is missing: $command" >&2
    exit 2
  }
done
docker buildx version >/dev/null
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
  echo "source commit must be a full lowercase Git object ID" >&2
  exit 2
}
[[ "$SOURCE_SNAPSHOT_SHA256" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "source snapshot identity must be a canonical SHA-256 digest" >&2
  exit 2
}
[[ -f "$RELEASE" ]] || {
  echo "release identity is missing: $RELEASE" >&2
  exit 2
}
[[ ! -e "$BUILD_RECEIPT" ]] || {
  echo "refusing to overwrite an existing build receipt: $BUILD_RECEIPT" >&2
  exit 2
}
mkdir -p "$MODEL_STORE" "$WHEELHOUSE" "$EVIDENCE"

cleanup() {
  docker stop "$DOWNLOAD_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker pull "$PYTHON_IMAGE"
docker pull "$NODE_IMAGE"
docker pull "$OLLAMA_IMAGE"

mapfile -t RELEASE_VALUES < <(
  docker run --rm --volume "$SOURCE_ROOT:/src:ro" "$PYTHON_IMAGE" \
    python -c 'import json,sys; p=json.load(open(sys.argv[1], encoding="utf-8")); print(p["model"]["name"]); print(p["model"]["manifest_digest"]); print(p["model"]["ollama_local_image_id"]); print(next(x["composition_digest"] for x in p["agent_runtimes"] if x["runtime_kind"] == "deepseek_harness"))' \
    /src/config/releases/v0.2.0-rc.2.json
)
MODEL_NAME="${RELEASE_VALUES[0]}"
MODEL_DIGEST="${RELEASE_VALUES[1]}"
EXPECTED_OLLAMA_IMAGE_ID="${RELEASE_VALUES[2]}"
HARNESS_COMPOSITION="${RELEASE_VALUES[3]#sha256:}"

OLLAMA_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$OLLAMA_IMAGE")"
[[ "$OLLAMA_IMAGE_ID" == "$EXPECTED_OLLAMA_IMAGE_ID" ]] || {
  echo "pulled Ollama image differs from the release identity" >&2
  exit 1
}

docker run --detach --rm \
  --name "$DOWNLOAD_CONTAINER" \
  --volume "$MODEL_STORE:/root/.ollama/models" \
  "$OLLAMA_IMAGE" serve >/dev/null
for _ in $(seq 1 120); do
  if docker exec "$DOWNLOAD_CONTAINER" ollama list >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$DOWNLOAD_CONTAINER" ollama list >/dev/null
docker exec "$DOWNLOAD_CONTAINER" ollama pull "$MODEL_NAME"
docker stop "$DOWNLOAD_CONTAINER" >/dev/null

docker run --rm \
  --volume "$SOURCE_ROOT:/src:ro" \
  --volume "$MODEL_STORE:/models:ro" \
  --volume "$EVIDENCE:/evidence" \
  "$PYTHON_IMAGE" \
  python /src/scripts/verify_online_ollama_store.py \
    --store /models \
    --release /src/config/releases/v0.2.0-rc.2.json \
    --output /evidence/online-model-verification.json

docker run --rm \
  --volume "$SOURCE_ROOT:/src:ro" \
  --volume "$WHEELHOUSE:/wheelhouse" \
  "$PYTHON_IMAGE" \
  python -m pip download \
    --dest /wheelhouse \
    --platform manylinux_2_17_x86_64 \
    --python-version 311 \
    --implementation cp \
    --abi cp311 \
    --only-binary=:all: \
    --require-hashes \
    --requirement /src/agent_image/requirements.agent-qwen.lock

mapfile -t PROMPT_VALUES < <(
  docker run --rm --volume "$SOURCE_ROOT:/src:ro" "$PYTHON_IMAGE" \
    python /src/scripts/print_agent_prompt_identity.py --format lines
)
SYSTEM_PROMPT_VERSION="${PROMPT_VALUES[0]}"
SYSTEM_PROMPT_DIGEST="${PROMPT_VALUES[1]}"
MUTATOR_PROMPT_VERSION="${PROMPT_VALUES[2]}"
MUTATOR_PROMPT_DIGEST="${PROMPT_VALUES[3]}"
MODEL_FAMILY="${MODEL_NAME%%:*}"
MODEL_TAG="${MODEL_NAME#*:}"

docker buildx build --load \
  --file "$SOURCE_ROOT/agent_image/Dockerfile.qwen" \
  --tag "$LANGGRAPH_IMAGE" \
  --build-context "wheelhouse=$WHEELHOUSE" \
  --build-context "ollama-models=$MODEL_STORE" \
  --build-arg "OLLAMA_IMAGE=$OLLAMA_IMAGE" \
  --build-arg "MODEL_NAME=$MODEL_NAME" \
  --build-arg "MODEL_DIGEST=$MODEL_DIGEST" \
  --build-arg "MODEL_MANIFEST_PATH=registry.ollama.ai/library/$MODEL_FAMILY/$MODEL_TAG" \
  --build-arg "SYSTEM_PROMPT_VERSION=$SYSTEM_PROMPT_VERSION" \
  --build-arg "SYSTEM_PROMPT_DIGEST=$SYSTEM_PROMPT_DIGEST" \
  --build-arg "MUTATOR_PROMPT_VERSION=$MUTATOR_PROMPT_VERSION" \
  --build-arg "MUTATOR_PROMPT_DIGEST=$MUTATOR_PROMPT_DIGEST" \
  "$SOURCE_ROOT"

docker buildx build --load \
  --file "$SOURCE_ROOT/agent_image/Dockerfile.qwen-mutator" \
  --tag "$MUTATOR_IMAGE" \
  --build-arg "AGENT_BASE_IMAGE=$LANGGRAPH_IMAGE" \
  "$SOURCE_ROOT"

docker buildx build --load \
  --file "$SOURCE_ROOT/agent_variants/deepseek_harness/Dockerfile.qwen" \
  --tag "$HARNESS_IMAGE" \
  --build-arg "AGENT_BASE_IMAGE=$LANGGRAPH_IMAGE" \
  --build-arg "HARNESS_COMPOSITION_SHA256=$HARNESS_COMPOSITION" \
  "$SOURCE_ROOT"

docker buildx build --load \
  --file "$SOURCE_ROOT/controller_image/Dockerfile" \
  --tag "$CONTROLLER_IMAGE" \
  "$SOURCE_ROOT"

require_label() {
  local image="$1"
  local label="$2"
  local expected="$3"
  local observed
  observed="$(docker image inspect --format "{{ index .Config.Labels \"$label\" }}" "$image")"
  [[ "$observed" == "$expected" ]] || {
    echo "image label differs: $image $label" >&2
    exit 1
  }
}
require_label "$LANGGRAPH_IMAGE" "org.trace-g.agent-framework" "langgraph"
require_label "$LANGGRAPH_IMAGE" "org.trace-g.model.digest" "$MODEL_DIGEST"
require_label "$HARNESS_IMAGE" "org.trace-g.agent-runtime" "deepseek_harness"
require_label "$HARNESS_IMAGE" "org.trace-g.runtime" "deepseek-harness-h4-v1"
require_label "$HARNESS_IMAGE" "org.trace-g.composition-sha256" "$HARNESS_COMPOSITION"
require_label "$HARNESS_IMAGE" "org.trace-g.model.digest" "$MODEL_DIGEST"
require_label "$MUTATOR_IMAGE" "org.trace-g.role" "mutator"
for image in "$LANGGRAPH_IMAGE" "$HARNESS_IMAGE" "$MUTATOR_IMAGE"; do
  [[ "$(docker image inspect --format '{{.Config.User}}' "$image")" == "10001:10001" ]] || {
    echo "image does not use the locked non-root identity: $image" >&2
    exit 1
  }
done

NODE_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$NODE_IMAGE")"
PYTHON_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$PYTHON_IMAGE")"
LANGGRAPH_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$LANGGRAPH_IMAGE")"
HARNESS_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$HARNESS_IMAGE")"
MUTATOR_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$MUTATOR_IMAGE")"
CONTROLLER_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$CONTROLLER_IMAGE")"

docker run --rm \
  --volume "$SOURCE_ROOT:/src:ro" \
  --volume "$EVIDENCE:/evidence" \
  "$PYTHON_IMAGE" \
  python /src/scripts/write_online_build_receipt.py \
    --release /src/config/releases/v0.2.0-rc.2.json \
    --model-verification /evidence/online-model-verification.json \
    --output /evidence/online-build-receipt.json \
    --source-commit "$SOURCE_COMMIT" \
    --source-snapshot-sha256 "$SOURCE_SNAPSHOT_SHA256" \
    --ollama-image-id "$OLLAMA_IMAGE_ID" \
    --node-image-id "$NODE_IMAGE_ID" \
    --python-image-id "$PYTHON_IMAGE_ID" \
    --langgraph-image-id "$LANGGRAPH_IMAGE_ID" \
    --harness-image-id "$HARNESS_IMAGE_ID" \
    --mutator-image-id "$MUTATOR_IMAGE_ID" \
    --controller-image-id "$CONTROLLER_IMAGE_ID"

echo "online server preparation completed"
echo "build receipt: $BUILD_RECEIPT"
