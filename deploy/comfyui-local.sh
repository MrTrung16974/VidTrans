#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${ROOT}/comfyui"
APP="${RUNTIME}/app"
VENV="${APP}/.venv"
PID_FILE="${RUNTIME}/comfyui.pid"
LOG_FILE="${RUNTIME}/comfyui.log"
VERSION="${VIDTRANS_COMFYUI_VERSION:-v0.42.5}"
PORT="${VIDTRANS_COMFYUI_PORT:-8188}"
LISTEN="${VIDTRANS_COMFYUI_LISTEN:-0.0.0.0}"
# VAE fp32: LTX-Video/SDXL VAE ra NaN (khung đen/trắng) ở bf16/fp16 trên MPS.
read -r -a EXTRA_ARGS <<<"${VIDTRANS_COMFYUI_EXTRA_ARGS:---fp32-vae}"
CA_FILE="${VIDTRANS_CA_FILE:-${ROOT}/secrets/mcredit-root-ca.crt}"
TORCH_VERSION="${VIDTRANS_TORCH_VERSION:-2.12.1}"
IPADAPTER_COMMIT="${VIDTRANS_IPADAPTER_COMMIT:-a0f451a5113cf9becb0847b92884cb10cbdec0ef}"
WORKER_PID_FILE="${RUNTIME}/animation-worker.pid"
WORKER_LOG_FILE="${RUNTIME}/animation-worker.log"
WORKER_PORT="${VIDTRANS_WORKER_PORT:-9000}"
HF="https://huggingface.co"
# <folder>|<file>|<url>: Juggernaut XL (cinematic, xianxia/cổ trang người thật), IP-Adapter SDXL giữ nhân vật,
# LTX-Video 2B distilled + T5 cho image-to-video.
MODELS=(
  "checkpoints|Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors|${HF}/RunDiffusion/Juggernaut-XL-v9/resolve/main/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"
  "ipadapter|ip-adapter_sdxl_vit-h.safetensors|${HF}/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter_sdxl_vit-h.safetensors"
  "clip_vision|CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors|${HF}/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors"
  "checkpoints|ltxv-2b-0.9.8-distilled.safetensors|${HF}/Lightricks/LTX-Video/resolve/main/ltxv-2b-0.9.8-distilled.safetensors"
  "text_encoders|t5xxl_fp16.safetensors|${HF}/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors"
)

usage() {
  cat <<'EOF'
Usage: ./deploy/comfyui-local.sh <command>

  install    Tải ComfyUI, custom node IP-Adapter và cài Python environment cô lập
  models     Tải model ảnh (Juggernaut XL + IP-Adapter) và video (LTX-Video 2B)
  start      Chạy ComfyUI + animation worker nền, ghi PID và log
  stop       Dừng ComfyUI + animation worker do script này khởi động
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
  # torch 2.14.x sinh nhiễu/khung vỡ với LTX-Video trên MPS; 2.12.1 đã kiểm chứng ổn định.
  "${pip_env[@]}" "${VENV}/bin/python" -m pip install "torch==${TORCH_VERSION}" torchvision torchaudio
  "${pip_env[@]}" "${VENV}/bin/python" -m pip install -r "${APP}/requirements.txt"
  local ipadapter="${APP}/custom_nodes/ComfyUI_IPAdapter_plus"
  if [[ ! -d "${ipadapter}/.git" ]]; then
    git clone https://github.com/cubiq/ComfyUI_IPAdapter_plus.git "${ipadapter}"
  fi
  git -C "${ipadapter}" fetch --depth 1 origin "${IPADAPTER_COMMIT}"
  git -C "${ipadapter}" checkout --detach "${IPADAPTER_COMMIT}"
  echo "Cài xong ComfyUI ${VERSION}. Model đặt tại: ${RUNTIME}/models/checkpoints"
}

download_models() {
  local entry folder file url
  for entry in "${MODELS[@]}"; do
    IFS='|' read -r folder file url <<<"${entry}"
    mkdir -p "${RUNTIME}/models/${folder}"
    echo "==> ${folder}/${file}"
    curl -fL --retry 5 -C - -o "${RUNTIME}/models/${folder}/${file}" "${url}"
  done
}

worker_running() {
  [[ -f "${WORKER_PID_FILE}" ]] || return 1
  local pid
  pid="$(cat "${WORKER_PID_FILE}")"
  [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

start_worker() {
  if worker_running; then
    echo "Animation worker đang chạy (PID $(cat "${WORKER_PID_FILE}"))"
    return
  fi
  VIDTRANS_COMFYUI_URL="http://127.0.0.1:${PORT}" VIDTRANS_WORKER_LISTEN="${LISTEN}" \
    nohup "${VENV}/bin/python" "${ROOT}/deploy/comfyui-integration/animation_worker.py" \
    >>"${WORKER_LOG_FILE}" 2>&1 &
  echo $! >"${WORKER_PID_FILE}"
  echo "Animation worker: http://127.0.0.1:${WORKER_PORT} (log: ${WORKER_LOG_FILE})"
}

stop_worker() {
  if worker_running; then
    kill "$(cat "${WORKER_PID_FILE}")"
    echo "Đã dừng animation worker"
  fi
  rm -f "${WORKER_PID_FILE}"
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
      --listen "${LISTEN}" --port "${PORT}" --disable-api-nodes "${EXTRA_ARGS[@]}" \
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
    start_worker
  else
    echo "ComfyUI không khởi động được. Log cuối:" >&2
    tail -50 "${LOG_FILE}" >&2 || true
    exit 1
  fi
}

stop_comfyui() {
  stop_worker
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
  if worker_running; then
    echo "Animation worker: running (PID $(cat "${WORKER_PID_FILE}")) http://127.0.0.1:${WORKER_PORT}"
  else
    echo "Animation worker: stopped"
  fi
  echo "Models: ${RUNTIME}/models"
  find "${RUNTIME}/models" -mindepth 2 -maxdepth 2 -type f -name '*.safetensors' -print 2>/dev/null || true
  du -sh "${RUNTIME}" 2>/dev/null || true
}

uninstall_comfyui() {
  stop_comfyui
  rm -rf "${APP}" "${RUNTIME}/uv-cache" "${RUNTIME}/input" "${RUNTIME}/output" "${RUNTIME}/user"
  rm -f "${LOG_FILE}" "${WORKER_LOG_FILE}"
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
  models) download_models ;;
  start) start_comfyui ;;
  stop) stop_comfyui ;;
  restart) stop_comfyui; start_comfyui ;;
  status) status_comfyui ;;
  logs) touch "${LOG_FILE}"; tail -f "${LOG_FILE}" ;;
  uninstall) uninstall_comfyui ;;
  purge) purge_comfyui ;;
  *) usage; exit 2 ;;
esac
