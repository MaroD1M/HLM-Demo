#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
VERSION="$(cat VERSION)"
IMAGE_REPO="${IMAGE_REPO:-ghcr.io/marod1m/hlm-demo}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required to build the image"
  exit 1
fi

docker build -t "${IMAGE_REPO}:${VERSION}" -t "${IMAGE_REPO}:latest" .
echo "Built ${IMAGE_REPO}:${VERSION} and ${IMAGE_REPO}:latest"

if [[ "${PUSH:-false}" == "true" ]]; then
  docker push "${IMAGE_REPO}:${VERSION}"
  docker push "${IMAGE_REPO}:latest"
  echo "Pushed ${IMAGE_REPO}:${VERSION} and ${IMAGE_REPO}:latest"
fi
