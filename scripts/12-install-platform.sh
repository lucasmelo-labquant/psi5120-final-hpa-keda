#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
CLUSTER="psi5120-final-eks"
STACK="psi5120-final-foundation"

cost_reminder() {
  status=$?
  if [[ "${status}" != "0" ]]; then
    echo "Platform setup failed while the cluster may still be billing." >&2
  fi
}
trap cost_reminder EXIT

if [[ "${ALLOW_AWS_CHARGES:-}" != "YES" ]]; then
  echo "Blocked. This step uses the running cluster and ECR." >&2
  exit 1
fi

bash "${SCRIPT_DIR}/00-preflight.sh"

stack_output() {
  aws cloudformation describe-stacks --region "${REGION}" --stack-name "${STACK}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

input_url="$(stack_output InputQueueUrl)"
result_url="$(stack_output ResultQueueUrl)"
repository="$(stack_output WorkerRepositoryUri)"
worker_policy="$(stack_output WorkerPolicyArn)"
keda_policy="$(stack_output KedaPolicyArn)"
account="$(aws sts get-caller-identity --query Account --output text)"

kubectl apply -f "${PROJECT_DIR}/manifests/00-namespace.yaml"
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
if ! aws eks describe-addon --cluster-name "${CLUSTER}" --addon-name metrics-server \
  --region "${REGION}" >/dev/null 2>&1; then
  aws eks create-addon --cluster-name "${CLUSTER}" --addon-name metrics-server \
    --region "${REGION}"
fi
aws eks wait addon-active --cluster-name "${CLUSTER}" --addon-name metrics-server \
  --region "${REGION}"
helm upgrade --install keda kedacore/keda \
  --namespace keda --create-namespace --version 2.17.2 --wait --timeout 5m

eksctl create iamserviceaccount --cluster "${CLUSTER}" --region "${REGION}" \
  --namespace scaling-study --name sqs-worker --attach-policy-arn "${worker_policy}" \
  --approve --override-existing-serviceaccounts
eksctl create iamserviceaccount --cluster "${CLUSTER}" --region "${REGION}" \
  --namespace keda --name keda-operator --attach-policy-arn "${keda_policy}" \
  --approve --override-existing-serviceaccounts
kubectl rollout restart deployment keda-operator -n keda
kubectl rollout status deployment keda-operator -n keda --timeout=3m

aws ecr get-login-password --region "${REGION}" | \
  docker login --username AWS --password-stdin "${account}.dkr.ecr.${REGION}.amazonaws.com"
source_revision="$(git -C "${PROJECT_DIR}" rev-parse --short HEAD 2>/dev/null || echo uncommitted)"
source_hash="$(sha256sum "${PROJECT_DIR}/worker/Dockerfile" \
  "${PROJECT_DIR}/worker/requirements.txt" "${PROJECT_DIR}/worker/worker.py" | \
  sha256sum | cut -d ' ' -f 1)"
image_tag="${source_revision}-${source_hash:0:12}-$(date -u +%Y%m%d%H%M%S)"
image_uri="${repository}:${image_tag}"
docker build --platform linux/amd64 -t "${image_uri}" "${PROJECT_DIR}/worker"
docker push "${image_uri}"

mkdir -p "${PROJECT_DIR}/rendered"
sed -e "s|REPLACE_IMAGE_URI|${image_uri}|g" \
  -e "s|REPLACE_INPUT_QUEUE_URL|${input_url}|g" \
  -e "s|REPLACE_RESULT_QUEUE_URL|${result_url}|g" \
  -e "s|REPLACE_SOURCE_HASH|${source_hash}|g" \
  "${PROJECT_DIR}/manifests/01-worker.yaml.template" \
  > "${PROJECT_DIR}/rendered/01-worker.yaml"
kubectl apply -f "${PROJECT_DIR}/rendered/01-worker.yaml"
kubectl rollout status deployment sqs-worker -n scaling-study --timeout=3m
bash "${SCRIPT_DIR}/12-smoke-platform.sh"
