#!/usr/bin/env bash
set -euo pipefail

INSTALL_BIN="${HOME}/.local/bin"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TEMP_DIR}"' EXIT
mkdir -p "${INSTALL_BIN}"

case "$(uname -m)" in
  x86_64) AWS_ARCH=x86_64; TOOL_ARCH=amd64 ;;
  aarch64|arm64) AWS_ARCH=aarch64; TOOL_ARCH=arm64 ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

for command_name in curl unzip tar; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "Missing prerequisite: ${command_name}" >&2
    exit 1
  }
done

if ! command -v aws >/dev/null 2>&1; then
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip" \
    -o "${TEMP_DIR}/awscliv2.zip"
  unzip -q "${TEMP_DIR}/awscliv2.zip" -d "${TEMP_DIR}"
  "${TEMP_DIR}/aws/install" -i "${HOME}/.local/aws-cli" -b "${INSTALL_BIN}"
fi

if ! command -v eksctl >/dev/null 2>&1; then
  curl -fsSL \
    "https://github.com/eksctl-io/eksctl/releases/download/v0.229.0/eksctl_Linux_${TOOL_ARCH}.tar.gz" \
    -o "${TEMP_DIR}/eksctl.tar.gz"
  tar -xzf "${TEMP_DIR}/eksctl.tar.gz" -C "${TEMP_DIR}" eksctl
  install -m 0755 "${TEMP_DIR}/eksctl" "${INSTALL_BIN}/eksctl"
fi

if ! command -v helm >/dev/null 2>&1; then
  curl -fsSL "https://get.helm.sh/helm-v3.18.6-linux-${TOOL_ARCH}.tar.gz" \
    -o "${TEMP_DIR}/helm.tar.gz"
  tar -xzf "${TEMP_DIR}/helm.tar.gz" -C "${TEMP_DIR}"
  install -m 0755 "${TEMP_DIR}/linux-${TOOL_ARCH}/helm" "${INSTALL_BIN}/helm"
fi

export PATH="${INSTALL_BIN}:${PATH}"
aws --version
eksctl version
helm version --short
