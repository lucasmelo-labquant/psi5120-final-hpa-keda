#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
policy="${1:-}"

kubectl delete scaledobject sqs-worker-keda -n scaling-study \
  --ignore-not-found --wait=true
kubectl delete triggerauthentication sqs-trigger-auth -n scaling-study \
  --ignore-not-found --wait=true
kubectl delete hpa sqs-worker-hpa keda-hpa-sqs-worker-keda -n scaling-study \
  --ignore-not-found --wait=true
kubectl scale deployment sqs-worker -n scaling-study --replicas=1
kubectl rollout status deployment sqs-worker -n scaling-study --timeout=3m

case "${policy}" in
  hpa)
    kubectl apply -f "${PROJECT_DIR}/manifests/10-hpa.yaml"
    ;;
  keda)
    kubectl apply -f "${PROJECT_DIR}/manifests/20-keda.yaml"
    ;;
  *)
    echo "Usage: $0 hpa|keda" >&2
    exit 2
    ;;
esac

deadline="$((SECONDS + 180))"
while (( SECONDS < deadline )); do
  desired="$(kubectl get deployment sqs-worker -n scaling-study \
    -o jsonpath='{.spec.replicas}')"
  ready="$(kubectl get deployment sqs-worker -n scaling-study \
    -o jsonpath='{.status.readyReplicas}')"
  if [[ "${desired}" == "1" && "${ready}" == "1" ]]; then
    break
  fi
  sleep 5
done
if [[ "${desired:-}" != "1" || "${ready:-}" != "1" ]]; then
  echo "Policy ${policy} did not stabilize at one ready replica." >&2
  exit 3
fi
sleep 30
kubectl get hpa -n scaling-study
