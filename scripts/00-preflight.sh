#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"
REGION="${AWS_REGION:-us-east-1}"
EXPECTED_ACCOUNT="145043400604"

if [[ "${REGION}" != "us-east-1" ]]; then
  echo "This frozen experiment only supports us-east-1." >&2
  exit 1
fi

for command_name in aws eksctl kubectl helm docker python3; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "Missing command: ${command_name}" >&2
    exit 1
  }
done

account="$(aws sts get-caller-identity --query Account --output text)"
if [[ "${account}" != "${EXPECTED_ACCOUNT}" ]]; then
  echo "Unexpected AWS account: ${account}; expected ${EXPECTED_ACCOUNT}" >&2
  exit 1
fi

echo "AWS account: ${account}"
echo "Region: ${REGION}"
aws eks list-clusters --region "${REGION}" --output table
aws cloudformation describe-stacks --region "${REGION}" \
  --query "Stacks[?contains(StackName, 'psi5120-final')].[StackName,StackStatus]" \
  --output table 2>/dev/null || true
echo "Cost guardrail: one EKS control plane, one c7i-flex.large node, no NAT or LB."
echo "Conservative three-hour estimate is below USD 1, excluding student credits."
