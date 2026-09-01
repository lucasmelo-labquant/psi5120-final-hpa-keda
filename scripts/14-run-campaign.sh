#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
STACK="psi5120-final-foundation"
SEEDS="${SEEDS:-101,202,303,404,505}"
CAMPAIGN_RUN="${CAMPAIGN_RUN:-main-v2}"

cost_reminder() {
  status=$?
  if [[ "${status}" != "0" ]]; then
    echo "Campaign stopped. The EKS cluster is still billing; inspect or clean it up." >&2
  fi
}
trap cost_reminder EXIT

if [[ "${REGION}" != "us-east-1" ]]; then
  echo "This frozen experiment only supports us-east-1." >&2
  exit 1
fi

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

input_url="$(stack_output InputQueueUrl)"
result_url="$(stack_output ResultQueueUrl)"
mkdir -p "${PROJECT_DIR}/traces" "${PROJECT_DIR}/results"

if [[ ! -x "${PROJECT_DIR}/.venv-linux/bin/python" ]] || \
  ! "${PROJECT_DIR}/.venv-linux/bin/python" -c 'import boto3,numpy,pandas' 2>/dev/null; then
  python3 -m venv --clear "${PROJECT_DIR}/.venv-linux"
  "${PROJECT_DIR}/.venv-linux/bin/pip" install -r "${PROJECT_DIR}/experiment/requirements.txt"
fi
python_cmd="${PROJECT_DIR}/.venv-linux/bin/python"
"${python_cmd}" "${PROJECT_DIR}/experiment/generate_traces.py" \
  --output "${PROJECT_DIR}/traces" --seeds "${SEEDS}" \
  --mean-rate 1.6 --service-rate 5.0 --low-rate 0.4 --high-rate 4.0

IFS=',' read -r -a seed_values <<< "${SEEDS}"
for pattern in poisson mmpp; do
  for seed in "${seed_values[@]}"; do
    mapfile -t policies < <("${python_cmd}" -c \
      "import random; p=['hpa','keda']; random.Random('${pattern}-${seed}').shuffle(p); print(*p, sep='\\n')")
    for policy_index in 0 1; do
      policy="${policies[${policy_index}]}"
      sequence_position="$((policy_index + 1))"
      cell_dir="${PROJECT_DIR}/results/${CAMPAIGN_RUN}/${pattern}-seed${seed}-${policy}"
      if grep -q '"valid": true' "${cell_dir}"/attempt-*/summary.json 2>/dev/null; then
        echo "Skipping existing valid cell: ${pattern}, seed ${seed}, ${policy}"
        continue
      fi
      bash "${SCRIPT_DIR}/13-select-policy.sh" "${policy}"
      for attempt in 1 2; do
        run_dir="${cell_dir}/attempt-${attempt}"
        if [[ -e "${run_dir}" ]]; then
          echo "Skipping existing attempt directory: ${run_dir}"
          continue
        fi
        load_sdk_credentials
        if "${python_cmd}" "${PROJECT_DIR}/experiment/run_once.py" \
          --trace "${PROJECT_DIR}/traces/${pattern}_seed_${seed}.json" \
          --policy "${policy}" --sequence-position "${sequence_position}" \
          --campaign-phase main \
          --output "${run_dir}" \
          --input-queue-url "${input_url}" --result-queue-url "${result_url}"; then
          break
        fi
        if [[ "${attempt}" == "2" ]]; then
          echo "Two invalid attempts for ${pattern}, seed ${seed}, ${policy}." >&2
          exit 3
        fi
        aws sqs purge-queue --region "${REGION}" --queue-url "${input_url}"
        aws sqs purge-queue --region "${REGION}" --queue-url "${result_url}"
        sleep 60
      done
    done
  done
done

"${python_cmd}" "${PROJECT_DIR}/experiment/analyze.py" \
  --results "${PROJECT_DIR}/results" --output "${PROJECT_DIR}/results/analysis"
