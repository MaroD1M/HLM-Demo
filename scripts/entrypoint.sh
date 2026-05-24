#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/app"
DEV_ROOT="/app-devsrc"
REQ_MARK_FILE="/tmp/hlm_dev_requirements.sha256"
RESULT_FILE="/app/instance/dev_apply_result.json"
RUNTIME_ENV_FILE="/app/instance/dev_runtime.env"
PIP_SYNC_NOTE=""

# Development mode is controlled only by settings page generated runtime file.
unset HLM_DEV_MODE HLM_DEV_AUTO_PULL HLM_DEV_GIT_REPO HLM_DEV_GIT_BRANCH HLM_DEV_AUTO_PIP_SYNC
unset HLM_DEV_PIP_SYNC_TIMEOUT HLM_DEV_GIT_TOKEN HLM_DEV_PROXY_URL HLM_DEV_NO_PROXY

log() {
  printf '[entrypoint] %s\n' "$*"
}

bool_true() {
  local v="${1:-}"
  [[ "${v,,}" == "1" || "${v,,}" == "true" || "${v,,}" == "yes" || "${v,,}" == "on" ]]
}

load_runtime_env() {
  if [[ -f "$RUNTIME_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$RUNTIME_ENV_FILE"
    set +a
    log "已加载页面开发配置: $RUNTIME_ENV_FILE"
  fi
}


log_dev_effective_config() {
  local mode="${HLM_DEV_MODE:-false}"
  local auto_pull="${HLM_DEV_AUTO_PULL:-false}"
  local repo="${HLM_DEV_GIT_REPO:-}"
  local branch="${HLM_DEV_GIT_BRANCH:-master}"
  local pip_sync="${HLM_DEV_AUTO_PIP_SYNC:-false}"
  local timeout_s="${HLM_DEV_PIP_SYNC_TIMEOUT:-120}"
  local proxy="${HLM_DEV_PROXY_URL:-}"
  if [[ -z "$repo" ]]; then
    repo='(empty)'
  fi
  if [[ -z "$proxy" ]]; then
    proxy='(none)'
  fi
  log "开发模式生效配置: mode=${mode}, auto_pull=${auto_pull}, repo=${repo}, branch=${branch}, auto_pip_sync=${pip_sync}, pip_timeout=${timeout_s}, proxy=${proxy}"
}

write_result() {
  local st="$1"
  local msg="$2"
  mkdir -p "$(dirname "$RESULT_FILE")" || true
  msg="${msg//$"\n"/ }"
  printf "{\"status\":\"%s\",\"message\":\"%s\",\"at\":\"%s\"}\n" "$st" "$msg" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RESULT_FILE" || true
}

ensure_repo() {
  local repo="${HLM_DEV_GIT_REPO:-}"
  local branch="${HLM_DEV_GIT_BRANCH:-master}"

  if [[ -z "$repo" ]]; then
    log "HLM_DEV_GIT_REPO 未设置，跳过自动拉取。"
    write_result "skipped" "dev_auto_pull=true but repo is empty"
    return 1
  fi

  if [[ ! -d "$DEV_ROOT/.git" ]]; then
    rm -rf "$DEV_ROOT"
    mkdir -p "$DEV_ROOT"
    log "初始化开发仓库: $repo ($branch)"
    if [[ -n "${HLM_DEV_GIT_TOKEN:-}" && "$repo" =~ ^https:// ]]; then
      git -c "http.extraHeader=Authorization: Bearer ${HLM_DEV_GIT_TOKEN}" clone --depth 1 --branch "$branch" "$repo" "$DEV_ROOT"
    else
      git clone --depth 1 --branch "$branch" "$repo" "$DEV_ROOT"
    fi
    return 0
  fi

  log "更新开发仓库到最新: $branch"
  git -C "$DEV_ROOT" remote set-url origin "$repo" >/dev/null 2>&1 || true
  if [[ -n "${HLM_DEV_GIT_TOKEN:-}" && "$repo" =~ ^https:// ]]; then
    git -C "$DEV_ROOT" -c "http.extraHeader=Authorization: Bearer ${HLM_DEV_GIT_TOKEN}" fetch --depth 1 origin "$branch"
  else
    git -C "$DEV_ROOT" fetch --depth 1 origin "$branch"
  fi
  git -C "$DEV_ROOT" checkout -q "$branch"
  git -C "$DEV_ROOT" reset --hard "origin/$branch"
  # Keep devsrc identical to remote branch to avoid stale untracked files surviving restarts.
  git -C "$DEV_ROOT" clean -fd
  return 0
}

apply_dev_proxy() {
  local proxy="${HLM_DEV_PROXY_URL:-}"
  local no_proxy="${HLM_DEV_NO_PROXY:-}"
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
  if ! bool_true "${HLM_DEV_AUTO_PIP_SYNC:-false}"; then
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
    PIP_SYNC_NOTE="pip sync skipped(requirements unchanged)"
    return 0
  fi

  local timeout_s="${HLM_DEV_PIP_SYNC_TIMEOUT:-120}"
  log "检测到 requirements 变化，开始同步依赖（超时 ${timeout_s}s）..."
  if timeout "${timeout_s}" pip install --no-cache-dir -r "$req_file"; then
    echo "$new_sha" > "$REQ_MARK_FILE"
    log "依赖同步完成。"
    PIP_SYNC_NOTE="pip sync ok"
  else
    log "依赖同步失败或超时，继续启动当前代码（请检查网络或依赖源）。"
    PIP_SYNC_NOTE="pip sync failed or timeout"
  fi
}

load_runtime_env
log_dev_effective_config

if bool_true "${HLM_DEV_MODE:-false}"; then
  log "开发模式已启用。"
  apply_dev_proxy
  if bool_true "${HLM_DEV_AUTO_PULL:-false}"; then
    if ensure_repo; then
      maybe_sync_requirements
      write_result "success" "git pull/reset ok; ${PIP_SYNC_NOTE:-pip sync skipped} "
      log "启动开发仓库代码：$DEV_ROOT/app.py"
      exec python -u "$DEV_ROOT/app.py"
    fi
    write_result "failed" "git pull failed, fallback to image code"
  else
    write_result "skipped" "dev mode on but auto pull disabled"
  fi
  log "开发模式未启用自动拉取或拉取失败，回退启动镜像内代码。"
else
  write_result "skipped" "dev mode disabled"
fi

exec python -u "$APP_ROOT/app.py"
