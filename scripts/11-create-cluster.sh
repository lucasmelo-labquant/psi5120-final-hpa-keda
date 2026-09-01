#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
CLUSTER="psi5120-final-eks"

cost_reminder() {
  status=$?
  if [[ "${status}" != "0" ]]; then
    echo "Cluster creation failed. Run guarded cleanup to avoid residual charges." >&2
  fi
}
trap cost_reminder EXIT

if [[ "${ALLOW_AWS_CHARGES:-}" != "YES" ]]; then
  echo "Blocked. Review costs, then set ALLOW_AWS_CHARGES=YES." >&2
  exit 1
fi

bash "${SCRIPT_DIR}/00-preflight.sh"
aws cloudformation describe-stacks --region "${REGION}" \
  --stack-name psi5120-final-foundation >/dev/null
eksctl create cluster -f "${PROJECT_DIR}/aws/cluster.yaml"
aws eks update-kubeconfig --name "${CLUSTER}" --region "${REGION}"
kubectl get nodes -o wide
