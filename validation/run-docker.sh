#!/bin/sh
# Reuse the backend test image and host tools; never use host Python/kubeconfig.
set -eu
validation_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
validation_kind=${KIND_BIN:-$(command -v kind)}
validation_kubectl=${KUBECTL_BIN:-$(command -v kubectl)}
validation_docker=$(command -v docker)
mkdir -p "$validation_root/validation-artifacts/runtime"
umask 077
exec docker run --rm --network host \
  -e "VALIDATION_COMMIT=$(git -C "$validation_root" rev-parse HEAD)" \
  -e "VALIDATION_WORKLOAD_PORT=${VALIDATION_WORKLOAD_PORT:-18090}" \
  -e "KUBECONFIG=$validation_root/validation-artifacts/runtime/kubeconfig" \
  -e "PYTHONPATH=$validation_root:$validation_root/backend" \
  -v "$validation_root:$validation_root" \
  -v "$validation_kind:/usr/local/bin/kind:ro" \
  -v "$validation_kubectl:/usr/local/bin/kubectl:ro" \
  -v "$validation_docker:/usr/local/bin/docker:ro" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -w "$validation_root" "${VALIDATION_TEST_IMAGE:-ai-sre-rca-test}" \
  python -c 'from validation.run import main; raise SystemExit(main())' "$@"
