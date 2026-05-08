#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${REPO_ROOT}/logs/scheduler"
RUN_DIR="${REPO_ROOT}/run"
LOCK_DIR="${RUN_DIR}/paperfit-scheduler.lock"
TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
LOG_FILE="${LOG_DIR}/${TIMESTAMP}.log"
LATEST_LINK="${LOG_DIR}/latest.log"

mkdir -p "${LOG_DIR}" "${RUN_DIR}"

if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  echo "[paperfit-scheduler] another run is in progress, exiting"
  exit 0
fi

cleanup() {
  rmdir "${LOCK_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

ln -sfn "${LOG_FILE}" "${LATEST_LINK}"
exec >> "${LOG_FILE}" 2>&1

echo "[paperfit-scheduler] started at $(date '+%Y-%m-%d %H:%M:%S')"
echo "[paperfit-scheduler] repo: ${REPO_ROOT}"

cd "${REPO_ROOT}"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "[paperfit-scheduler] missing .venv, creating"
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

echo "[paperfit-scheduler] run verify"
npm run verify

if [[ -n "${PAPERFIT_AUTOMATION_HOOK:-}" ]]; then
  echo "[paperfit-scheduler] running automation hook: ${PAPERFIT_AUTOMATION_HOOK}"
  bash -lc "${PAPERFIT_AUTOMATION_HOOK}"
fi

echo "[paperfit-scheduler] completed at $(date '+%Y-%m-%d %H:%M:%S')"
