#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"

aws configure set profile.psi5120.region us-east-1
aws configure set profile.psi5120.credential_process \
  "${HOME}/.local/bin/aws configure export-credentials --profile default --format process --region us-east-1"
account="$(AWS_PROFILE=psi5120 aws sts get-caller-identity --query Account --output text)"
if [[ "${account}" != "145043400604" ]]; then
  echo "Unexpected account through credential_process: ${account}" >&2
  exit 1
fi
echo "SDK profile psi5120 configured for account ${account}."
