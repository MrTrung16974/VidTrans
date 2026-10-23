#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${ROOT}/comfyui"
APP="${RUNTIME}/app"
VENV="${APP}/.venv"
PID_FILE="${RUNTIME}/comfyui.pid"
LOG_FILE="${RUNTIME}/comfyui.log"
VERSION="${VIDTRANS_COMFYUI_VERSION:-v0.36.0}"
PORT="${VIDTRANS_COMFYUI_PORT:-8188}"
LISTEN="${VIDTRANS_COMFYUI_LISTEN:-0.0.0.0}"
CA_FILE="${VIDTRANS_CA_FILE:-${ROOT}/secrets/mcredit-root-ca.crt}"

usage() {
  cat <<'EOF'
Usage: ./deploy/comfyui-local.sh <command>

  install    Tải ComfyUI và cài Python environment cô lập
  start      Chạy ComfyUI nền, ghi PID và log
  stop       Dừng ComfyUI do script này khởi động
  restart    Dừng rồi khởi động lại
  status     Xem trạng thái, URL, model và dung lượng
  logs       Theo dõi log
  uninstall  Xóa app + virtualenv, giữ nguyên models
  purge      Xóa toàn bộ runtime và models sau khi xác nhận
EOF
}

running_pid() {
  [[ -f "${PID_FILE}" ]] || return 1
  local pid
  pid="$(cat "${PID_FILE}")"
  [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

install_comfyui() {
  command -v git >/dev/null || { echo "Thiếu git" >&2; exit 1; }
  command -v uv >/dev/null || { echo "Thiếu uv: brew install uv" >&2; exit 1; }
  mkdir -p "${RUNTIME}/models/checkpoints" "${RUNTIME}/input" "${RUNTIME}/output" "${RUNTIME}/user"
  if [[ ! -d "${APP}/.git" ]]; then
    git clone --branch "${VERSION}" --depth 1 https://github.com/Comfy-Org/ComfyUI.git "${APP}"
  fi
  git -C "${APP}" fetch --depth 1 origin "refs/tags/${VERSION}:refs/tags/${VERSION}"
  git -C "${APP}" checkout --detach "${VERSION}"
  UV_SYSTEM_CERTS=true uv python install 3.12
  if [[ ! -x "${VENV}/bin/python" ]]; then
    UV_SYSTEM_CERTS=true uv venv --python 3.12 "${VENV}"
  fi
  "${VENV}/bin/python" -m ensurepip --upgrade
  local pip_env=(env PIP_DISABLE_PIP_VERSION_CHECK=1)
  if [[ -f "${CA_FILE}" ]]; then
    pip_env+=(PIP_CERT="${CA_FILE}")
  fi
  "${pip_env[@]}" "${VENV}/bin/python" -m pip install --upgrade pip setuptools wheel
  "${pip_env[@]}" "${VENV}/bin/python" -m pip install torch torchvision torchaudio
  "${pip_env[@]}" "${VENV}/bin/python" -m pip install -r "${APP}/requirements.txt"
  echo "Cài xong ComfyUI ${VERSION}. Model đặt tại: ${RUNTIME}/models/checkpoints"
}

start_comfyui() {
  [[ -x "${VENV}/bin/python" ]] || { echo "Chưa cài. Chạy: $0 install" >&2; exit 1; }
  if running_pid; then
    echo "ComfyUI đang chạy (PID $(cat "${PID_FILE}"))"
    return
  fi
  rm -f "${PID_FILE}"
  mkdir -p "${RUNTIME}/models/checkpoints" "${RUNTIME}/input" "${RUNTIME}/output" "${RUNTIME}/user"
  (
    cd "${APP}"
    PYTORCH_ENABLE_MPS_FALLBACK=1 nohup "${VENV}/bin/python" main.py \
      --listen "${LISTEN}" --port "${PORT}" --disable-api-nodes \
      --models-directory "${RUNTIME}/models" \
      --input-directory "${RUNTIME}/input" \
      --output-directory "${RUNTIME}/output" \
      --user-directory "${RUNTIME}/user" \
      >>"${LOG_FILE}" 2>&1 &
    echo $! >"${PID_FILE}"
  )
  sleep 2
  if running_pid; then
    echo "ComfyUI đã chạy: http://127.0.0.1:${PORT} (PID $(cat "${PID_FILE}"))"
    echo "Log: ${LOG_FILE}"
  else
    echo "ComfyUI không khởi động được. Log cuối:" >&2
    tail -50 "${LOG_FILE}" >&2 || true
    exit 1
  fi
}

stop_comfyui() {
  if ! running_pid; then
    rm -f "${PID_FILE}"
    echo "ComfyUI không chạy"
    return
  fi
  local pid
  pid="$(cat "${PID_FILE}")"
  kill "${pid}"
  for _ in {1..20}; do
    kill -0 "${pid}" 2>/dev/null || break
    sleep 0.25
  done
  kill -0 "${pid}" 2>/dev/null && kill -KILL "${pid}"
  rm -f "${PID_FILE}"
  echo "Đã dừng ComfyUI"
}

status_comfyui() {
  if running_pid; then
    echo "Trạng thái: running (PID $(cat "${PID_FILE}"))"
  else
    echo "Trạng thái: stopped"
  fi
  echo "URL: http://127.0.0.1:${PORT}"
  echo "Models: ${RUNTIME}/models/checkpoints"
  find "${RUNTIME}/models/checkpoints" -maxdepth 1 -type f -print 2>/dev/null || true
  du -sh "${RUNTIME}" 2>/dev/null || true
}

uninstall_comfyui() {
  stop_comfyui
  rm -rf "${APP}" "${RUNTIME}/uv-cache" "${RUNTIME}/input" "${RUNTIME}/output" "${RUNTIME}/user"
  rm -f "${LOG_FILE}"
  echo "Đã gỡ ComfyUI và virtualenv. Models vẫn còn tại ${RUNTIME}/models"
}

purge_comfyui() {
  printf 'Xóa cả ComfyUI và toàn bộ model trong %s? Nhập DELETE để xác nhận: ' "${RUNTIME}"
  read -r answer
  [[ "${answer}" == "DELETE" ]] || { echo "Đã hủy"; return; }
  stop_comfyui
  rm -rf "${RUNTIME}"
  echo "Đã xóa toàn bộ ComfyUI local"
}

case "${1:-}" in
  install) install_comfyui ;;
  start) start_comfyui ;;
  stop) stop_comfyui ;;
  restart) stop_comfyui; start_comfyui ;;
  status) status_comfyui ;;
  logs) touch "${LOG_FILE}"; tail -f "${LOG_FILE}" ;;
  uninstall) uninstall_comfyui ;;
  purge) purge_comfyui ;;
  *) usage; exit 2 ;;
esac
