#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/app"
DEV_ROOT="/app-devsrc"
REQ_MARK_FILE="/tmp/hlm_dev_requirements.sha256"

log() {
  printf '[entrypoint] %s\n' "$*"
}

bool_true() {
  local v="${1:-}"
  [[ "${v,,}" == "1" || "${v,,}" == "true" || "${v,,}" == "yes" || "${v,,}" == "on" ]]
}

ensure_repo() {
  local repo="${APP_DEV_GIT_REPO:-}"
  local branch="${APP_DEV_GIT_BRANCH:-master}"

  if [[ -z "$repo" ]]; then
    log "APP_DEV_GIT_REPO 未设置，跳过自动拉取。"
    return 1
  fi

  if [[ ! -d "$DEV_ROOT/.git" ]]; then
    rm -rf "$DEV_ROOT"
    mkdir -p "$DEV_ROOT"
    log "初始化开发仓库: $repo ($branch)"
    if [[ -n "${APP_DEV_GIT_TOKEN:-}" && "$repo" =~ ^https:// ]]; then
      local auth_repo
      auth_repo="https://${APP_DEV_GIT_TOKEN}@${repo#https://}"
      git clone --depth 1 --branch "$branch" "$auth_repo" "$DEV_ROOT"
    else
      git clone --depth 1 --branch "$branch" "$repo" "$DEV_ROOT"
    fi
    return 0
  fi

  log "更新开发仓库到最新: $branch"
  git -C "$DEV_ROOT" remote set-url origin "$repo" >/dev/null 2>&1 || true
  git -C "$DEV_ROOT" fetch --depth 1 origin "$branch"
  git -C "$DEV_ROOT" checkout -q "$branch"
  git -C "$DEV_ROOT" reset --hard "origin/$branch"
  return 0
}

apply_dev_proxy() {
  local proxy="${APP_DEV_PROXY_URL:-}"
  local no_proxy="${APP_DEV_NO_PROXY:-}"
  if [[ -z "$proxy" ]]; then
    return 0
  fi

  export HTTP_PROXY="$proxy"
  export HTTPS_PROXY="$proxy"
  export http_proxy="$proxy"
  export https_proxy="$proxy"
  if [[ -n "$no_proxy" ]]; then
    export NO_PROXY="$no_proxy"
    export no_proxy="$no_proxy"
  fi
  git config --global http.proxy "$proxy" || true
  git config --global https.proxy "$proxy" || true
  log "开发模式代理已启用: $proxy"
}

maybe_sync_requirements() {
  if ! bool_true "${APP_DEV_AUTO_PIP_SYNC:-false}"; then
    return 0
  fi

  local req_file="$DEV_ROOT/requirements.txt"
  if [[ ! -f "$req_file" ]]; then
    log "未找到 requirements.txt，跳过依赖同步。"
    return 0
  fi

  local old_sha=""
  local new_sha
  [[ -f "$REQ_MARK_FILE" ]] && old_sha="$(cat "$REQ_MARK_FILE" 2>/dev/null || true)"
  new_sha="$(sha256sum "$req_file" | awk '{print $1}')"

  if [[ "$new_sha" == "$old_sha" ]]; then
    log "requirements 未变化，跳过依赖同步。"
    return 0
  fi

  local timeout_s="${APP_DEV_PIP_SYNC_TIMEOUT:-120}"
  log "检测到 requirements 变化，开始同步依赖（超时 ${timeout_s}s）..."
  if timeout "${timeout_s}" pip install --no-cache-dir -r "$req_file"; then
    echo "$new_sha" > "$REQ_MARK_FILE"
    log "依赖同步完成。"
  else
    log "依赖同步失败或超时，继续启动当前代码（请检查网络或依赖源）。"
  fi
}

if bool_true "${APP_DEV_MODE:-false}"; then
  log "开发模式已启用。"
  apply_dev_proxy
  if bool_true "${APP_DEV_AUTO_PULL:-false}"; then
    if ensure_repo; then
      maybe_sync_requirements
      log "启动开发仓库代码：$DEV_ROOT/app.py"
      exec python -u "$DEV_ROOT/app.py"
    fi
  fi
  log "开发模式未启用自动拉取或拉取失败，回退启动镜像内代码。"
fi

exec python -u "$APP_ROOT/app.py"
