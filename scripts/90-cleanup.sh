#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
CLUSTER="psi5120-final-eks"
EXPECTED_ACCOUNT="145043400604"

if [[ "${ALLOW_AWS_DELETE:-}" != "YES" ]]; then
  echo "Cleanup blocked. Set ALLOW_AWS_DELETE=YES after checking the target." >&2
  exit 1
fi

if [[ "${REGION}" != "us-east-1" ]]; then
  echo "Cleanup only supports us-east-1." >&2
  exit 1
fi
account="$(aws sts get-caller-identity --region "${REGION}" \
  --query Account --output text)"
if [[ "${account}" != "${EXPECTED_ACCOUNT}" ]]; then
  echo "Unexpected AWS account: ${account}; cleanup refused." >&2
  exit 1
fi

if aws eks describe-cluster --name "${CLUSTER}" --region "${REGION}" >/dev/null 2>&1; then
  aws eks update-kubeconfig --name "${CLUSTER}" --region "${REGION}"
  eksctl delete iamserviceaccount --cluster "${CLUSTER}" --region "${REGION}" \
    --namespace scaling-study --name sqs-worker --wait || true
  eksctl delete iamserviceaccount --cluster "${CLUSTER}" --region "${REGION}" \
    --namespace keda --name keda-operator --wait || true
  eksctl delete cluster -f "${PROJECT_DIR}/aws/cluster.yaml" --wait
fi

if aws cloudformation describe-stacks --stack-name psi5120-final-foundation \
  --region "${REGION}" >/dev/null 2>&1; then
  aws cloudformation delete-stack --stack-name psi5120-final-foundation --region "${REGION}"
  aws cloudformation wait stack-delete-complete \
    --stack-name psi5120-final-foundation --region "${REGION}"
fi
bash "${SCRIPT_DIR}/91-audit-cleanup.sh"
