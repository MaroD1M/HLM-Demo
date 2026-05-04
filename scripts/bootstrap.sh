#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已基于 .env.example 创建 .env，请至少修改 SECRET_KEY 和 MEDIA_ROOT（可选再配置账号密码）。"
fi

mkdir -p data/instance data/backups

echo "Checking compose config..."
docker compose config >/dev/null

echo "Bootstrap done. Next: docker compose up -d"
