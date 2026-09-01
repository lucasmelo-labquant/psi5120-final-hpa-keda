#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
STACK="psi5120-final-foundation"

if [[ "${ALLOW_AWS_CHARGES:-}" != "YES" ]]; then
  echo "Blocked. Review costs, then set ALLOW_AWS_CHARGES=YES." >&2
  exit 1
fi

bash "${SCRIPT_DIR}/00-preflight.sh"
aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "${STACK}" \
  --template-file "${PROJECT_DIR}/aws/foundation.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --tags Project=PSI5120-FINAL Student=Lucas-Melo-Rocha
aws cloudformation describe-stacks --region "${REGION}" --stack-name "${STACK}" \
  --query 'Stacks[0].Outputs' --output table
