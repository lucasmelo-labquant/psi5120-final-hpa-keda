#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REGION="us-east-1"
STACK="psi5120-final-foundation"
PILOT_ROOT="${PILOT_ROOT:-/tmp/psi5120-final-pilot-$(date -u +%Y%m%d%H%M%S)}"
PILOT_POLICIES="${PILOT_POLICIES:-hpa keda}"

stack_output() {
  aws cloudformation describe-stacks --region "${REGION}" --stack-name "${STACK}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

load_sdk_credentials() {
  local credential_exports
  credential_exports="$(aws configure export-credentials --profile default \
    --format env --region "${REGION}")"
  eval "${credential_exports}"
  unset credential_exports
  if [[ -n "${AWS_CREDENTIAL_EXPIRATION:-}" ]]; then
    remaining="$(( $(date -d "${AWS_CREDENTIAL_EXPIRATION}" +%s) - $(date +%s) ))"
    if (( remaining < 600 )); then
      echo "AWS credentials have less than 10 minutes remaining; renew aws login." >&2
      return 4
    fi
  fi
}

if [[ ! -x "${PROJECT_DIR}/.venv-linux/bin/python" ]] || \
  ! "${PROJECT_DIR}/.venv-linux/bin/python" -c 'import boto3,numpy,pandas' 2>/dev/null; then
  python3 -m venv --clear "${PROJECT_DIR}/.venv-linux"
  "${PROJECT_DIR}/.venv-linux/bin/pip" install \
    -r "${PROJECT_DIR}/experiment/requirements.txt"
fi
python_cmd="${PROJECT_DIR}/.venv-linux/bin/python"
mkdir -p "${PILOT_ROOT}/traces"
"${python_cmd}" "${PROJECT_DIR}/experiment/generate_traces.py" \
  --output "${PILOT_ROOT}/traces" --seeds 999 --duration 60 \
  --mean-rate 1.6 --service-rate 5.0 --low-rate 0.4 --high-rate 4.0

input_url="$(stack_output InputQueueUrl)"
result_url="$(stack_output ResultQueueUrl)"
position=1
for policy in ${PILOT_POLICIES}; do
  bash "${SCRIPT_DIR}/13-select-policy.sh" "${policy}"
  load_sdk_credentials
  if ! "${python_cmd}" "${PROJECT_DIR}/experiment/run_once.py" \
    --trace "${PILOT_ROOT}/traces/mmpp_seed_999.json" \
    --policy "${policy}" --sequence-position "${position}" \
    --campaign-phase pilot \
    --output "${PILOT_ROOT}/${policy}" \
    --input-queue-url "${input_url}" --result-queue-url "${result_url}"; then
    echo "Pilot ${policy} was invalid; continuing operational validation." >&2
    aws sqs purge-queue --region "${REGION}" --queue-url "${input_url}"
    aws sqs purge-queue --region "${REGION}" --queue-url "${result_url}"
    sleep 60
  fi
  position="$((position + 1))"
done

echo "Pilot retained at ${PILOT_ROOT}"
