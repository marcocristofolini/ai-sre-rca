#!/bin/sh
# Run the optional Kafka signal preflight in the existing backend test image.
# Requires an operator-created disposable Kind cluster and its kubeconfig.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
context=${1:?Usage: sh validation/run-kafka-docker.sh kind-<test-cluster> <kubeconfig>}
config=${2:?Pass the exact kubeconfig path for this disposable Kind cluster}
case "$context" in
  kind-*) ;;
  *) echo "Refusing non-Kind context" >&2; exit 2 ;;
esac
test -r "$config" || { echo "Missing readable kubeconfig" >&2; exit 2; }
config=$(realpath -- "$config")
image=${VALIDATION_TEST_IMAGE:-ai-sre-rca-test}
kubectl=$(readlink -f -- "$(command -v kubectl)")
docker run --rm --network host \
  -v "$root:$root" \
  -v "$config:/tmp/kafka-kubeconfig:ro" \
  -v "$kubectl:/usr/local/bin/kubectl:ro" \
  -e KUBECONFIG=/tmp/kafka-kubeconfig \
  -w "$root" "$image" \
  python -m validation.kafka_live --context "$context"
