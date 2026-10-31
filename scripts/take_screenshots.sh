#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
GUNICORN_BIN="${GUNICORN_BIN:-$ROOT_DIR/.venv/bin/gunicorn}"
CHROME_BIN="${CHROME_BIN:-chromium}"
PORT="${SCREENSHOT_PORT:-5055}"
BASE_URL="http://127.0.0.1:${PORT}"
OUT_DIR="${ROOT_DIR}/static/images/screenshots"
TMP_DIR="$(mktemp -d /tmp/hlm-shots.XXXXXX)"
mkdir -p "$OUT_DIR"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

export HLM_INSTANCE_PATH="$TMP_DIR/instance"
export SECRET_KEY="screenshot-only-secret"
export HLM_ALLOW_NO_AUTH=true
export TZ="Asia/Shanghai"
export APP_LOG_DIR="$TMP_DIR/logs"

"$PYTHON_BIN" - <<'PY'
from datetime import UTC, datetime, timedelta
from pathlib import Path
from app import app, db, init_app
from core.models import (
    AdminUser, AppConfig, CronJob, DeleteMonitorTask, DeletePendingAction,
    Downloader, FileLinkMap, HardlinkCache, HardlinkTask, JobExecutionLog,
    JobQueue, Notifier, OperationLog, RecycleItem,
)

init_app()
with app.app_context():
    now = datetime.now(UTC)
    downloader = Downloader(name='qBittorrent Main', type='qbittorrent', host='http://127.0.0.1', port=8080, username='admin', category='movies', enabled=True)
    downloader.set_password('demo-password')
    notifier = Notifier(name='Telegram Alerts', type='telegram', chat_id='-100123456', enabled=True)
    notifier.set_api_key('123456:demo-token')
    db.session.add_all([downloader, notifier])
    db.session.commit()

    movie = HardlinkTask(name='Movies / 4K', source_dir='/media/downloads/movie', dest_dir='/media/library/movie', extensions='.mkv,.mp4', exclude_dirs='sample,subs', monitor_source_delete=True, monitor_dest_delete=True, delete_downloader_id=downloader.id, delete_notifier_id=notifier.id, delete_dry_run=False, enabled=True)
    tv = HardlinkTask(name='TV Shows', source_dir='/media/downloads/tv', dest_dir='/media/library/tv', extensions='.mkv,.mp4', exclude_dirs='sample', monitor_source_delete=True, monitor_dest_delete=True, delete_dry_run=True, enabled=True)
    anime = HardlinkTask(name='Anime', source_dir='/media/downloads/anime', dest_dir='/media/library/anime', extensions='.mkv', exclude_dirs='', monitor_source_delete=False, monitor_dest_delete=False, enabled=False)
    db.session.add_all([movie, tv, anime])
    db.session.commit()

    legacy = DeleteMonitorTask(name=f'[兼容]硬链接任务#{movie.id}', directory=movie.source_dir, downloader_id=downloader.id, notifier_id=notifier.id, enabled=False)
    db.session.add(legacy)
    db.session.commit()

    sources = [
        ('/media/downloads/movie/Dune.Part.Two.2024.2160p.mkv', '/media/library/movie/Dune.Part.Two.2024/Dune.Part.Two.2024.2160p.mkv', 'downloader', 'hash-dune'),
        ('/media/downloads/movie/Oppenheimer.2023.1080p.mkv', '/media/library/movie/Oppenheimer.2023/Oppenheimer.2023.1080p.mkv', 'downloader', 'hash-opp'),
        ('/media/downloads/movie/Unknown.Title.mkv', '/media/library/movie/Unknown.Title/Unknown.Title.mkv', 'pending', None),
        ('/media/downloads/tv/Show.S01E01.mkv', '/media/library/tv/Show/Show.S01E01.mkv', 'downloader', 'hash-show'),
    ]
    for idx, (src, dst, stype, thash) in enumerate(sources, start=1):
        db.session.add(FileLinkMap(task_id=movie.id if idx <= 3 else tv.id, source_path=src, dest_path=dst, source_type=stype, torrent_hash=thash, downloader_id=downloader.id if stype == 'downloader' else None, file_size=123456789 + idx * 1000, last_seen_at=now - timedelta(minutes=idx * 10)))
        db.session.add(HardlinkCache(source_path=src, dest_path=dst))
    db.session.add(DeletePendingAction(task_id=legacy.id, file_map_id=1, deleted_path=sources[2][0], torrent_hash='hash-unknown', match_by='name_match', status='pending', reason='疑似误删风险，需人工确认'))
    db.session.add(RecycleItem(original_path='/media/library/movie/Old.Movie/Old.Movie.mkv', recycled_path=str(Path('/app/data/recycle/20261031-120000/Old.Movie.mkv')), size=987654321))
    for i in range(6):
        db.session.add(JobExecutionLog(job_name=['Movies / 4K', 'TV Shows', '映射回填'][i % 3], job_type=['batch_hardlink', 'delete_scan', 'backfill_mapping'][i % 3], source='cron' if i % 2 else 'manual', target_id=movie.id, status='success' if i != 4 else 'failed', message='扫描 128，成功 126，失败 2' if i != 4 else 'qBittorrent 连接失败', started_at=now - timedelta(minutes=i * 17), finished_at=now - timedelta(minutes=i * 17) + timedelta(seconds=23), duration_ms=23000 - i * 500))
    for i, (op, msg) in enumerate([('hardlink_scan', '扫描 128，成功 126，失败 2'), ('torrent_deleted', '删除种子 hash-dune'), ('torrent_delete_pending', 'cross-seed 保护，已加入待确认'), ('db_backup', '备份完成')]):
        db.session.add(OperationLog(operation_type=op, target_type='System', target_name='演示任务', message=msg, success=True, created_at=now - timedelta(minutes=i * 8)))
    db.session.add_all([
        CronJob(name='电影库扫描', task_type='batch_hardlink', target_id=movie.id, cron_expression='0 */2 * * *', description='每 2 小时扫描电影目录', enabled=True),
        CronJob(name='系统映射回填', task_type='backfill_mapping', cron_expression='0 * * * *', description='每 60 分钟回填映射', enabled=True),
        CronJob(name='系统日志清理', task_type='clean_logs', cron_expression='0 3 * * *', description='每天 03:00 清理日志', enabled=True),
    ])
    db.session.add_all([
        JobQueue(run_key=f'hardlink:{movie.id}', job_name='Movies / 4K', job_type='hardlink', target_id=movie.id, source='web', status='queued', priority=10, message='等待 worker'),
        JobQueue(run_key='backfill:0', job_name='映射回填', job_type='backfill', source='cron', status='success', message='回填成功 12', finished_at=now - timedelta(minutes=5)),
    ])
    AppConfig.query.filter_by(key='github_version_check_enabled').update({'value': 'false'})
    AppConfig.query.filter_by(key='proxy_url').update({'value': ''})
    db.session.commit()
PY

"$GUNICORN_BIN" --workers 1 --threads 2 --bind "127.0.0.1:${PORT}" --error-logfile "$TMP_DIR/gunicorn.log" wsgi:application >"$TMP_DIR/gunicorn.out" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 40); do
  if curl -fsS "$BASE_URL/api/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done

shoot() {
  local name="$1" path="$2" height="${3:-1100}"
  "$CHROME_BIN" --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
    --user-data-dir="$TMP_DIR/chrome-$name" --window-size="1440,${height}" \
    --virtual-time-budget=5000 --screenshot="$OUT_DIR/$name.png" "$BASE_URL$path" >/dev/null 2>&1
  echo "screenshot: $OUT_DIR/$name.png"
}

shoot dashboard /
shoot hardlink /hardlink 1000
shoot delete-monitor /delete-monitor 900
shoot mapping /mapping 1000
shoot recycle /recycle 900
shoot diagnostics /diagnostics 1200
shoot settings /settings 1200
