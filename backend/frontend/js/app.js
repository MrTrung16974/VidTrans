const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  files: [], links: [], jobs: [], pendingUploads: [], selectedJobIds: new Set(), pollTimer: null, searchTimer: null,
  tiktokAuth: null, appStarted: false,
  douyinBrowser: null, douyinBrowserPollTimer: null, douyinBrowserLoaded: false,
  auth: { enabled: false, configured: false, authenticated: false, username: null },
};
const stepLabels = {
  queued: "Đang xếp hàng", starting: "Đang khởi động", "downloading-source": "Đang tải video nguồn",
  "source-ready": "Nguồn đã sẵn sàng",
  "extracting-subtitles": "Đang đọc chữ Trung",
  transcribing: "Đang nhận diện giọng nói", translating: "Đang dịch sang tiếng Việt",
  summarizing: "Đang viết nội dung TikTok", "routing-voices": "Đang chọn giọng",
  tts: "Đang tạo giọng đọc", "mixing-audio": "Đang trộn âm thanh", rendering: "Đang dựng video", "publishing-tiktok": "Đang đăng lên TikTok",
  "waiting-tiktok-publish": "Đang chờ lịch đăng TikTok",
  completed: "Đã hoàn tất", failed: "Xử lý thất bại", cancelled: "Đã hủy", cancelling: "Đang dừng an toàn",
};
const statusLabels = { queued: "Đang chờ", scheduled: "Đã đặt lịch", processing: "Đang chạy", completed: "Hoàn tất", failed: "Thất bại", cancelled: "Đã hủy", cancelling: "Đang hủy" };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function toast(message, isError = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 3500);
}

function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-VidTrans-Request", "1");
  return fetch(url, { ...options, headers, credentials: "same-origin" });
}

function lockApplication(message = "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.", isError = true) {
  clearInterval(state.pollTimer);
  clearInterval(state.douyinBrowserPollTimer);
  state.pollTimer = null;
  state.douyinBrowserPollTimer = null;
  state.appStarted = false;
  state.auth.authenticated = false;
  $("#appLogoutButton").classList.add("is-hidden");
  $("#authGate").classList.remove("is-hidden");
  const authMessage = $("#authMessage");
  authMessage.textContent = message;
  authMessage.classList.toggle("error", isError);
  setTimeout(() => $("#authUsername").focus(), 0);
}

async function requestJson(url, options = {}) {
  const response = await apiFetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401 && !url.startsWith("/api/v1/auth/")) lockApplication();
  if (!response.ok) throw new Error(payload.detail || "Yêu cầu thất bại");
  return payload;
}

async function refreshApplicationAuth() {
  const status = await requestJson("/api/v1/auth/status");
  state.auth = status;
  if (!status.enabled) {
    $("#authGate").classList.add("is-hidden");
    $("#appLogoutButton").classList.add("is-hidden");
    return true;
  }
  if (!status.configured) {
    const detail = (status.configuration_errors || []).join(" · ") || status.message;
    lockApplication(detail);
    $("#authSubmit").disabled = true;
    return false;
  }
  $("#authSubmit").disabled = false;
  if (!status.authenticated) {
    lockApplication("Nhập tài khoản quản trị để tiếp tục.", false);
    return false;
  }
  $("#authGate").classList.add("is-hidden");
  const logout = $("#appLogoutButton");
  logout.textContent = `${status.username} · Đăng xuất`;
  logout.classList.remove("is-hidden");
  return true;
}

async function startProtectedApplication() {
  if (state.appStarted) return;
  state.appStarted = true;
  await Promise.all([loadJobs(), refreshDouyinAuthStatus(), refreshTikTokAuthStatus()]);
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(loadJobs, 2500);
  if (location.hash === "#douyin") startDouyinBrowserPolling();
}

async function bootstrapApplication() {
  try {
    if (await refreshApplicationAuth()) await startProtectedApplication();
  } catch (error) {
    lockApplication(`Không kiểm tra được cấu hình xác thực: ${error.message}`);
  }
}

async function refreshDouyinAuthStatus() {
  try {
    const status = await requestJson("/api/v1/douyin-auth");
    const label = $("#douyinAuthStatus");
    label.textContent = status.message;
    label.classList.toggle("authenticated", Boolean(status.authenticated));
    $("#douyinLogoutButton").classList.toggle("is-hidden", !status.authenticated);
  } catch (error) {
    $("#douyinAuthStatus").textContent = "Không kiểm tra được phiên Douyin";
  }
}

function renderDouyinBrowserStatus(status) {
  state.douyinBrowser = status;
  const indicator = $("#douyinBrowserIndicator");
  const label = $("#douyinBrowserStatus");
  label.textContent = status.message;
  indicator.classList.toggle("available", Boolean(status.available));
  indicator.classList.toggle("authenticated", Boolean(status.authenticated));
  $("#douyinBrowserSync").disabled = !status.available;
  $("#douyinBrowserReload").disabled = !status.available;
  $("#douyinBrowserLogout").disabled = !status.available;

  const frame = $("#douyinBrowserFrame");
  const loading = $("#douyinBrowserLoading");
  if (status.available && status.browser_url && frame.dataset.source !== status.browser_url) {
    state.douyinBrowserLoaded = false;
    loading.classList.remove("is-hidden");
    frame.dataset.source = status.browser_url;
    frame.src = status.browser_url + "vnc.html?resize=scale&autoconnect=true";
  }
  if (!status.available) {
    loading.classList.remove("is-hidden");
    $("#douyinBrowserHelp").textContent = "Container douyin-browser chưa sẵn sàng; xem log để kiểm tra Chromium/noVNC.";
  } else if (status.authenticated) {
    $("#douyinBrowserHelp").textContent = "Cookie đã đồng bộ và sẵn sàng cho bộ tải video Douyin.";
  } else {
    $("#douyinBrowserHelp").textContent = "Đăng nhập trong khung bên dưới; OTP được nhập thẳng vào Douyin.";
  }
}

async function refreshDouyinBrowserStatus({ refresh = false, quiet = false } = {}) {
  try {
    const status = await requestJson(`/api/v1/douyin-browser/status${refresh ? "?refresh=true" : ""}`);
    renderDouyinBrowserStatus(status);
    if (status.authenticated) await refreshDouyinAuthStatus();
    return status;
  } catch (error) {
    renderDouyinBrowserStatus({ available: false, authenticated: false, message: error.message, browser_url: null });
    if (!quiet) toast(error.message, true);
    return null;
  }
}

function stopDouyinBrowserPolling() {
  clearInterval(state.douyinBrowserPollTimer);
  state.douyinBrowserPollTimer = null;
}

function startDouyinBrowserPolling() {
  stopDouyinBrowserPolling();
  refreshDouyinBrowserStatus();
  state.douyinBrowserPollTimer = setInterval(() => {
    refreshDouyinBrowserStatus({ refresh: true, quiet: true });
  }, 8000);
}

async function syncDouyinBrowserLogin() {
  const button = $("#douyinBrowserSync");
  button.disabled = true;
  button.textContent = "Đang đồng bộ…";
  try {
    const status = await requestJson("/api/v1/douyin-browser/sync", { method: "POST" });
    renderDouyinBrowserStatus(status);
    await refreshDouyinAuthStatus();
    toast(status.message, !status.authenticated);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.textContent = "Đã đăng nhập — Đồng bộ";
    button.disabled = !state.douyinBrowser?.available;
  }
}

async function reloadDouyinBrowserPage() {
  const button = $("#douyinBrowserReload");
  button.disabled = true;
  try {
    const status = await requestJson("/api/v1/douyin-browser/reload", { method: "POST" });
    renderDouyinBrowserStatus(status);
    toast("Đã tải lại trang Douyin");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = !state.douyinBrowser?.available;
  }
}

async function logoutDouyinBrowser() {
  if (!window.confirm("Đăng xuất Douyin và xóa cookie đã đồng bộ?")) return;
  try {
    const status = await requestJson("/api/v1/douyin-browser/session", { method: "DELETE" });
    renderDouyinBrowserStatus(status);
    await refreshDouyinAuthStatus();
    toast(status.message);
  } catch (error) {
    toast(error.message, true);
  }
}

async function refreshTikTokAuthStatus() {
  try {
    const status = await requestJson("/api/v1/tiktok-auth");
    state.tiktokAuth = status;
    const label = $("#tiktokAuthStatus");
    label.textContent = status.message;
    label.classList.toggle("authenticated", Boolean(status.connected));
    $("#tiktokConnectButton").classList.toggle("is-hidden", Boolean(status.connected));
    $("#tiktokDisconnectButton").classList.toggle("is-hidden", !status.connected);
    $("#tiktokConnectButton").disabled = !status.configured;
  } catch (error) {
    state.tiktokAuth = null;
    $("#tiktokAuthStatus").textContent = "Không kiểm tra được kết nối TikTok";
  }
}

async function connectTikTok() {
  try {
    const result = await requestJson("/api/v1/tiktok-auth/connect");
    location.href = result.authorization_url;
  } catch (error) {
    toast(error.message, true);
  }
}


function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function syncInputFiles() {
  const transfer = new DataTransfer();
  state.files.forEach(file => transfer.items.add(file));
  $("#files").files = transfer.files;
}

function addFiles(files) {
  const keys = new Set(state.files.map(file => `${file.name}:${file.size}:${file.lastModified}`));
  for (const file of files) {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (file.type.startsWith("video/") && !keys.has(key) && state.files.length + state.links.length < 50) {
      state.files.push(file);
      keys.add(key);
    }
  }
  syncInputFiles();
  renderFiles();
}

function renderFiles() {
  $("#fileCount").textContent = String(state.files.length + state.links.length);
  $("#fileList").innerHTML = state.files.map((file, index) => `
    <div class="file-item">
      <span class="file-item-icon">VID</span>
      <div><strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong><small>${formatBytes(file.size)}</small></div>
      <button class="file-remove" type="button" data-file-index="${index}" aria-label="Xóa ${escapeHtml(file.name)}">✕</button>
    </div>`).join("");
}

function extractSourceLinks(value) {
  const domains = ["tiktok.com", "douyin.com", "iesdouyin.com"];
  const matches = String(value || "").match(/https?:\/\/[^\s<>\[\]"']+/gi) || [];
  const unique = new Set();
  for (const raw of matches) {
    const candidate = raw.replace(/[.,;:!?\)\]\}，。；：！？）】》、]+$/u, "");
    try {
      const url = new URL(candidate);
      const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
      if (domains.some(domain => hostname === domain || hostname.endsWith(`.${domain}`))) {
        url.hash = "";
        unique.add(url.toString());
      }
    } catch (_) {
      // Backend returns the detailed validation error when the form is submitted.
    }
  }
  return [...unique];
}

function renderSourceLinks() {
  state.links = extractSourceLinks($("#sourceLinks").value);
  $("#linkList").innerHTML = state.links.map(link => {
    const platform = new URL(link).hostname.includes("douyin") ? "DY" : "TT";
    return `<div class="file-item link-item">
      <span class="file-item-icon">${platform}</span>
      <div><strong title="${escapeHtml(link)}">${escapeHtml(link)}</strong><small>Sẽ tải trong hàng đợi nền</small></div>
    </div>`;
  }).join("");
  renderFiles();
}

function subtitleStyle() {
  return {
    font_name: "Noto Sans CJK SC",
    font_size: 36,
    margin_v: 60,
    placement_mode: $("#placementMode").value,
    match_source_size: $("#matchSourceSize").checked,
    min_font_size: Number($("#minFontSize").value),
    max_font_size: Number($("#maxFontSize").value),
    position_gap: Number($("#positionGap").value),
    mask_original: $("#maskOriginal").checked,
  };
}

function localDateTimeValue(date) {
  const pad = value => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatScheduledTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

function syncTikTokSchedule({ setDefault = false } = {}) {
  const mode = $("#tiktokPublishMode").value;
  const scheduled = mode === "scheduled";
  $("#autoPublishTikTok").value = mode === "off" ? "false" : "true";
  $("#tiktokScheduleField").classList.toggle("is-hidden", !scheduled);
  if (mode !== "off") $("#generateTikTokPost").value = "true";
  const localInput = $("#tiktokPublishAtLocal");
  if (scheduled && setDefault && !localInput.value) {
    const defaultTime = new Date(Date.now() + 60 * 60 * 1000);
    defaultTime.setMinutes(Math.ceil(defaultTime.getMinutes() / 5) * 5, 0, 0);
    localInput.value = localDateTimeValue(defaultTime);
  }
  const selected = scheduled && localInput.value ? new Date(localInput.value) : null;
  $("#tiktokPublishAt").value = selected && !Number.isNaN(selected.getTime()) ? selected.toISOString() : "";
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "múi giờ thiết bị";
  $("#tiktokTimezoneLabel").textContent = timezone;
}

function updateFormSummary() {
  const modes = { "1": "Vietsub", "2": "Vietsub + voice", "3": "Voice + nhạc" };
  const placements = { replace_original: "Thay chữ gốc", above_original: "Trên chữ gốc", bottom_safe: "Vùng an toàn" };
  const mode = $("#mode").value;
  const placement = $("#placementMode").value;
  $("#modeSummary").textContent = modes[mode];
  $("#placementSummary").textContent = placements[placement];
  $("#musicField").classList.toggle("is-hidden", mode !== "3");
  const voiceEnabled = mode === "2" || mode === "3";
  const musicEnabled = mode === "3";
  ["#voiceModeField", "#voiceTypeField", "#speechRateField", "#originalVolumeField", "#originalAudioField"].forEach(selector => {
    const field = $(selector);
    field.classList.toggle("is-disabled", !voiceEnabled);
    $$('input, select', field).forEach(control => { control.disabled = !voiceEnabled; });
  });
  const musicVolumeField = $("#musicVolumeField");
  musicVolumeField.classList.toggle("is-disabled", !musicEnabled);
  $$('input, select', musicVolumeField).forEach(control => { control.disabled = !musicEnabled; });
  const hints = {
    "1": "Giữ nguyên âm thanh gốc, chỉ dịch và chèn phụ đề Việt. Nhanh nhất và không chạy TTS.",
    "2": "Tạo giọng Việt đồng bộ từng câu; tự co tốc độ câu dài và có thể giữ tiếng gốc ở mức nhỏ.",
    "3": "Giống chế độ lồng tiếng, đồng thời lặp nhạc nền vừa đủ thời lượng và tự cân bằng âm lượng.",
  };
  $("#modeHint").textContent = hints[mode];
  $("#subtitleStyle").value = JSON.stringify(subtitleStyle());
  const preview = $(".subtitle-preview");
  preview.classList.remove("mode-replace", "mode-above", "mode-bottom");
  preview.classList.add(placement === "replace_original" ? "mode-replace" : placement === "above_original" ? "mode-above" : "mode-bottom");
  $("#previewVietnamese").style.fontSize = $("#matchSourceSize").checked ? "17px" : "21px";
}

function showView() {
  const view = location.hash === "#jobs" ? "jobs" : location.hash === "#douyin" ? "douyin" : "create";
  $("#createView").classList.toggle("is-hidden", view !== "create");
  $("#jobsView").classList.toggle("is-hidden", view !== "jobs");
  $("#douyinView").classList.toggle("is-hidden", view !== "douyin");
  $$('[data-view-link]').forEach(link => link.classList.toggle("active", link.dataset.viewLink === view));
  if (view === "jobs" && state.appStarted) loadJobs();
  if (view === "douyin" && state.appStarted) startDouyinBrowserPolling();
  else stopDouyinBrowserPolling();
}

function uploadBatch(formData, { onProgress, onRequest } = {}) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    onRequest?.(request);
    request.open("POST", "/api/v1/batches");
    request.withCredentials = true;
    request.setRequestHeader("X-VidTrans-Request", "1");
    request.responseType = "json";
    request.upload.addEventListener("progress", event => {
      if (!event.lengthComputable) return;
      const percent = Math.round(event.loaded / event.total * 100);
      $("#uploadPercent").textContent = `${percent}%`;
      $("#uploadFill").style.width = `${percent}%`;
      onProgress?.(percent, "uploading");
    });
    request.upload.addEventListener("load", () => onProgress?.(100, "creating"));
    request.addEventListener("load", () => {
      const payload = request.response || {};
      if (request.status >= 200 && request.status < 300) resolve(payload);
      else {
        if (request.status === 401) lockApplication();
        reject(new Error(payload.detail || "Không thể tạo batch"));
      }
    });
    request.addEventListener("error", () => reject(new Error("Mất kết nối khi tải video")));
    request.addEventListener("abort", () => reject(new Error("Đã hủy tải batch")));
    request.send(formData);
  });
}

async function submitBatch(event) {
  event.preventDefault();
  syncTikTokSchedule();
  renderSourceLinks();
  const sourceText = $("#sourceLinks").value.trim();
  if (!state.files.length && !state.links.length) {
    return toast(sourceText ? "Không tìm thấy link TikTok/Douyin hợp lệ" : "Hãy chọn file hoặc dán ít nhất một link video", true);
  }
  if (state.files.length + state.links.length > 50) return toast("Mỗi batch chỉ nhận tối đa 50 video", true);
  if ($("#tiktokPublishMode").value !== "off" && !state.tiktokAuth?.connected) {
    return toast("Hãy kết nối tài khoản TikTok trước khi bật tự động đăng", true);
  }
  if ($("#tiktokPublishMode").value === "scheduled") {
    const publishAt = new Date($("#tiktokPublishAtLocal").value);
    if (Number.isNaN(publishAt.getTime())) return toast("Hãy chọn ngày giờ đăng TikTok", true);
    if (publishAt.getTime() <= Date.now() + 30_000) return toast("Lịch đăng phải cách hiện tại ít nhất 30 giây", true);
  }
  updateFormSummary();
  const form = event.currentTarget;
  const formData = new FormData(form);
  // An empty multiple-file input is otherwise serialized as a zero-byte file
  // by some browsers. Link-only batches must not be mistaken for bad uploads.
  if (!state.files.length) formData.delete("files");
  const keepOriginal = $('[name="keep_original_audio"]', form).checked;
  formData.set("keep_original_audio", String(keepOriginal));
  const music = formData.get("background_music");
  if (music instanceof File && !music.name) formData.delete("background_music");
  const sourceCookies = formData.get("source_cookies");
  if (sourceCookies instanceof File && !sourceCookies.name) formData.delete("source_cookies");
  if ($("#mode").value === "3" && !formData.has("background_music")) return toast("Mode 3 cần một file nhạc nền", true);

  const pendingUpload = {
    id: crypto.randomUUID?.() || `upload-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    batchName: String(formData.get("batch_name") || "Batch video mới"),
    sources: [...state.files.map(file => file.name), ...state.links],
    progress: 0,
    status: "uploading",
    detail: "Đang tải dữ liệu lên máy chủ. Giữ tab mở đến khi upload hoàn tất.",
    request: null,
  };
  state.pendingUploads.unshift(pendingUpload);
  $("#submitButton").disabled = true;
  $("#uploadProgress").classList.remove("is-hidden");
  location.hash = "#jobs";
  showView();
  renderJobDashboard();
  try {
    const result = await uploadBatch(formData, {
      onRequest: request => { pendingUpload.request = request; },
      onProgress: (percent, phase) => {
        pendingUpload.progress = percent;
        pendingUpload.detail = phase === "creating"
          ? "Upload hoàn tất. Đang tạo các tiến trình xử lý…"
          : `Đang tải dữ liệu lên máy chủ (${percent}%).`;
        renderJobDashboard();
      },
    });
    toast(`Đã tạo ${result.total_jobs} job trong ${result.name}`);
    state.pendingUploads = state.pendingUploads.filter(upload => upload.id !== pendingUpload.id);
    state.files = [];
    state.links = [];
    $("#sourceLinks").value = "";
    syncInputFiles();
    renderSourceLinks();
    await loadJobs();
  } catch (error) {
    pendingUpload.status = pendingUpload.status === "cancelled" ? "cancelled" : "failed";
    pendingUpload.detail = error.message;
    pendingUpload.request = null;
    renderJobDashboard();
    toast(error.message, true);
  } finally {
    pendingUpload.request = null;
    $("#submitButton").disabled = false;
    $("#uploadProgress").classList.add("is-hidden");
    $("#uploadFill").style.width = "0";
  }
}

function jobActions(job) {
  const terminal = ["completed", "failed", "cancelled"].includes(job.status);
  const downloads = job.download_all_url ? `<a href="${escapeHtml(job.download_all_url)}" title="Tải toàn bộ kết quả">ZIP</a>` : job.video_url ? `<a href="${escapeHtml(job.video_url)}" title="Tải video">Tải</a>` : "";
  const cancel = ["queued", "scheduled", "processing", "cancelling"].includes(job.status) ? `<button data-action="cancel" data-job="${job.job_id}" title="Hủy">Hủy</button>` : "";
  const retry = terminal ? `<button data-action="retry" data-job="${job.job_id}" title="Chạy lại">Chạy lại</button>` : "";
  const remove = terminal ? `<button data-action="delete" data-job="${job.job_id}" title="Xóa">Xóa</button>` : "";
  const refreshTikTok = job.tiktok_publish_id && !["PUBLISH_COMPLETE", "FAILED"].includes(job.tiktok_publish_status) ? `<button data-action="tiktok-status" data-job="${job.job_id}" title="Cập nhật trạng thái TikTok">TikTok</button>` : "";
  return downloads + refreshTikTok + cancel + retry + remove;
}

function eligibleJobs(action) {
  const selected = state.jobs.filter(job => state.selectedJobIds.has(job.job_id));
  if (action === "cancel") return selected.filter(job => ["queued", "scheduled", "processing"].includes(job.status));
  return selected.filter(job => ["completed", "failed", "cancelled"].includes(job.status));
}

function updateJobSelectionUI() {
  const visibleIds = state.jobs.map(job => job.job_id);
  const selectedVisible = visibleIds.filter(jobId => state.selectedJobIds.has(jobId));
  const selectAll = $("#selectAllJobs");
  selectAll.checked = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;
  selectAll.indeterminate = selectedVisible.length > 0 && selectedVisible.length < visibleIds.length;
  selectAll.disabled = visibleIds.length === 0;
  $("#selectedJobsCount").textContent = selectedVisible.length ? `Đã chọn ${selectedVisible.length} job` : "Chưa chọn job";
  const counts = {
    cancel: eligibleJobs("cancel").length,
    retry: eligibleJobs("retry").length,
    delete: eligibleJobs("delete").length,
  };
  const labels = { cancel: "Hủy", retry: "Chạy lại", delete: "Xóa" };
  for (const [action, count] of Object.entries(counts)) {
    const button = $(`[data-bulk-action="${action}"]`);
    button.disabled = count === 0;
    button.textContent = count ? `${labels[action]} (${count})` : labels[action];
  }
}

function renderPendingUpload(upload) {
  const progress = Math.max(0, Math.min(100, Math.round(Number(upload.progress || 0))));
  const sourceCount = upload.sources.length;
  const sourceSummary = sourceCount === 1
    ? upload.sources[0]
    : `${sourceCount} video · ${upload.sources.slice(0, 2).join(" · ")}`;
  const active = upload.status === "uploading";
  const statusClass = active ? "processing" : "failed";
  const statusText = active ? (progress >= 100 ? "Đang tạo job" : "Đang upload") : upload.status === "cancelled" ? "Đã hủy" : "Upload lỗi";
  const action = active
    ? `<button data-upload-cancel="${escapeHtml(upload.id)}" type="button">Hủy upload</button>`
    : `<button data-upload-dismiss="${escapeHtml(upload.id)}" type="button">Ẩn</button>`;
  return `<article class="job-row upload-row" data-upload-row="${escapeHtml(upload.id)}">
    <span class="job-select upload-indicator" aria-hidden="true">↑</span>
    <div class="job-file"><span class="job-file-icon">UP</span><div><strong title="${escapeHtml(sourceSummary)}">${escapeHtml(upload.batchName)}</strong><small>${escapeHtml(sourceSummary)}</small></div></div>
    <span class="status-pill status-${statusClass}">${statusText}</span>
    <div class="job-progress"><div class="progress-track"><span style="width:${progress}%"></span></div><small>${progress}%</small></div>
    <div class="job-step"><strong>${progress >= 100 && active ? "Đang tạo tiến trình" : "Đang tải batch"}</strong><small title="${escapeHtml(upload.detail)}">${escapeHtml(upload.detail)}</small></div>
    <div class="job-actions">${action}</div>
  </article>`;
}

function renderJobs(jobs) {
  const pendingHtml = state.pendingUploads.map(renderPendingUpload).join("");
  if (!jobs.length && !pendingHtml) {
    $("#jobsList").innerHTML = '<div class="empty-state"><div><strong>Chưa có tiến trình phù hợp</strong><br><small>Tạo batch mới hoặc thay đổi bộ lọc.</small></div></div>';
    updateJobSelectionUI();
    return;
  }
  const jobsHtml = jobs.map(job => {
    const progress = Math.max(0, Math.min(100, Math.round(Number(job.progress || 0) * 100)));
    const queue = job.queue_position ? ` • Hàng đợi #${job.queue_position}` : "";
    const selected = state.selectedJobIds.has(job.job_id);
    const detail = job.status === "scheduled" && job.tiktok_publish_at
      ? `Sẽ đăng TikTok lúc ${formatScheduledTime(job.tiktok_publish_at)}`
      : job.tiktok_publish_error
      ? `TikTok lỗi: ${job.tiktok_publish_error}`
      : job.tiktok_publish_status
        ? `TikTok: ${job.tiktok_publish_status}${job.tiktok_publish_title ? ` · ${job.tiktok_publish_title}` : ""}`
        : (job.error || job.step_detail || "");
    return `<article class="job-row${selected ? " selected" : ""}" data-job-row="${escapeHtml(job.job_id)}">
      <label class="job-select" title="Chọn job"><input type="checkbox" data-job-select="${escapeHtml(job.job_id)}" ${selected ? "checked" : ""} aria-label="Chọn ${escapeHtml(job.filename || job.job_id)}" /></label>
      <div class="job-file"><span class="job-file-icon">VID</span><div><strong title="${escapeHtml(job.filename || job.job_id)}">${escapeHtml(job.filename || job.job_id)}</strong><small>${escapeHtml(job.batch_id || "Video đơn")}${queue}</small></div></div>
      <span class="status-pill status-${escapeHtml(job.status)}">${statusLabels[job.status] || escapeHtml(job.status)}</span>
      <div class="job-progress"><div class="progress-track"><span style="width:${progress}%"></span></div><small>${progress}%</small></div>
      <div class="job-step"><strong>${escapeHtml(stepLabels[job.step] || job.step || "Đang cập nhật")}</strong><small title="${escapeHtml(detail)}">${escapeHtml(detail)}</small></div>
      <div class="job-actions">${jobActions(job)}</div>
    </article>`;
  }).join("");
  $("#jobsList").innerHTML = pendingHtml + jobsHtml;
  updateJobSelectionUI();
}

function renderJobDashboard() {
  renderJobs(state.jobs);
  const counts = state.jobs.reduce((result, job) => ({ ...result, [job.status]: (result[job.status] || 0) + 1 }), {});
  const activeUploads = state.pendingUploads.filter(upload => upload.status === "uploading").length;
  const failedUploads = state.pendingUploads.filter(upload => upload.status !== "uploading").length;
  $("#queuedMetric").textContent = (counts.queued || 0) + (counts.scheduled || 0);
  $("#processingMetric").textContent = (counts.processing || 0) + (counts.cancelling || 0) + activeUploads;
  $("#completedMetric").textContent = counts.completed || 0;
  $("#failedMetric").textContent = (counts.failed || 0) + (counts.cancelled || 0) + failedUploads;
  $("#runningBadge").textContent = String((counts.queued || 0) + (counts.scheduled || 0) + (counts.processing || 0) + (counts.cancelling || 0) + activeUploads);
}

async function loadJobs() {
  if (!state.appStarted) return;
  const params = new URLSearchParams({ limit: "100" });
  const status = $("#statusFilter").value;
  const search = $("#jobSearch").value.trim();
  if (status) params.set("status", status);
  if (search) params.set("search", search);
  try {
    const response = await apiFetch(`/api/v1/jobs?${params}`);
    if (response.status === 401) lockApplication();
    if (!response.ok) throw new Error("Không tải được danh sách job");
    const data = await response.json();
    state.jobs = data.items;
    const visibleIds = new Set(state.jobs.map(job => job.job_id));
    state.selectedJobIds = new Set([...state.selectedJobIds].filter(jobId => visibleIds.has(jobId)));
    renderJobDashboard();
  } catch (error) {
    toast(error.message, true);
  }
}

async function jobAction(action, jobId) {
  if (action === "delete" && !confirm("Xóa job và toàn bộ file kết quả?")) return;
  if (action === "cancel" && !confirm("Yêu cầu dừng job này?")) return;
  const method = action === "delete" ? "DELETE" : "POST";
  const endpoint = action === "delete" ? `/api/v1/jobs/${jobId}` : action === "tiktok-status" ? `/api/v1/jobs/${jobId}/refresh-tiktok-status` : `/api/v1/jobs/${jobId}/${action}`;
  try {
    const response = await apiFetch(endpoint, { method });
    if (response.status === 401) lockApplication();
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Không thể ${action} job`);
    toast(action === "retry" ? `Đã tạo job chạy lại ${data.job_id}` : action === "cancel" ? "Đã gửi yêu cầu hủy" : action === "tiktok-status" ? `TikTok: ${data.tiktok_publish_status}` : "Đã xóa job");
    await loadJobs();
  } catch (error) { toast(error.message, true); }
}

async function bulkJobAction(action) {
  const jobs = eligibleJobs(action);
  if (!jobs.length) return;
  const verbs = { cancel: "hủy", retry: "chạy lại", delete: "xóa" };
  if (!confirm(`Bạn muốn ${verbs[action]} ${jobs.length} job đã chọn?`)) return;
  const results = await Promise.all(jobs.map(async job => {
    const method = action === "delete" ? "DELETE" : "POST";
    const endpoint = action === "delete" ? `/api/v1/jobs/${job.job_id}` : `/api/v1/jobs/${job.job_id}/${action}`;
    try {
      const response = await apiFetch(endpoint, { method });
      if (response.status === 401) lockApplication();
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Thao tác thất bại");
      return { ok: true, jobId: job.job_id };
    } catch (error) {
      return { ok: false, jobId: job.job_id, error };
    }
  }));
  const succeeded = results.filter(result => result.ok);
  const failed = results.length - succeeded.length;
  succeeded.forEach(result => state.selectedJobIds.delete(result.jobId));
  toast(`Đã ${verbs[action]} ${succeeded.length} job${failed ? `; ${failed} job lỗi` : ""}`, failed > 0);
  await loadJobs();
}

$("#authLoginForm").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("#authSubmit");
  const message = $("#authMessage");
  const body = new URLSearchParams({
    grant_type: "password",
    username: $("#authUsername").value.trim(),
    password: $("#authPassword").value,
    scope: "admin",
  });
  button.disabled = true;
  message.textContent = "Đang xác thực…";
  message.classList.remove("error");
  try {
    await requestJson("/api/v1/auth/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    $("#authPassword").value = "";
    if (await refreshApplicationAuth()) {
      await startProtectedApplication();
      toast("Đăng nhập VidTrans thành công");
    }
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  } finally {
    button.disabled = !state.auth.configured;
  }
});

$("#appLogoutButton").addEventListener("click", async () => {
  try {
    await requestJson("/api/v1/auth/session", { method: "DELETE" });
    $("#authPassword").value = "";
    lockApplication("Đã đăng xuất. Nhập tài khoản quản trị để tiếp tục.", false);
  } catch (error) {
    toast(error.message, true);
  }
});

$("#files").addEventListener("change", event => addFiles(event.target.files));
$("#sourceLinks").addEventListener("input", renderSourceLinks);
$("#fileList").addEventListener("click", event => {
  const button = event.target.closest("[data-file-index]");
  if (!button) return;
  state.files.splice(Number(button.dataset.fileIndex), 1);
  syncInputFiles(); renderFiles();
});
const dropZone = $("#dropZone");
["dragenter", "dragover"].forEach(type => dropZone.addEventListener(type, event => { event.preventDefault(); dropZone.classList.add("dragging"); }));
["dragleave", "drop"].forEach(type => dropZone.addEventListener(type, event => { event.preventDefault(); dropZone.classList.remove("dragging"); }));
dropZone.addEventListener("drop", event => addFiles(event.dataTransfer.files));
$("#batchForm").addEventListener("submit", submitBatch);
$("#douyinBrowserSync").addEventListener("click", syncDouyinBrowserLogin);
$("#douyinBrowserReload").addEventListener("click", reloadDouyinBrowserPage);
$("#douyinBrowserLogout").addEventListener("click", logoutDouyinBrowser);
$("#douyinBrowserFrame").addEventListener("load", () => {
  state.douyinBrowserLoaded = true;
  $("#douyinBrowserLoading").classList.add("is-hidden");
});
$("#douyinLogoutButton").addEventListener("click", async () => {
  try {
    await requestJson("/api/v1/douyin-auth", { method: "DELETE" });
    await refreshDouyinAuthStatus();
    toast("Đã xóa phiên đăng nhập Douyin");
  } catch (error) { toast(error.message, true); }
});
$("#tiktokConnectButton").addEventListener("click", connectTikTok);
$("#tiktokDisconnectButton").addEventListener("click", async () => {
  try {
    await requestJson("/api/v1/tiktok-auth", { method: "DELETE" });
    $("#tiktokPublishMode").value = "off";
    syncTikTokSchedule();
    await refreshTikTokAuthStatus();
    toast("Đã ngắt kết nối TikTok");
  } catch (error) { toast(error.message, true); }
});
$("#tiktokPublishMode").addEventListener("change", () => syncTikTokSchedule({ setDefault: true }));
$("#tiktokPublishAtLocal").addEventListener("input", () => syncTikTokSchedule());
$("#mode").addEventListener("change", updateFormSummary);
[$("#placementMode"), $("#matchSourceSize"), $("#minFontSize"), $("#maxFontSize"), $("#positionGap"), $("#maskOriginal")].forEach(element => element.addEventListener("change", updateFormSummary));
$("#refreshJobs").addEventListener("click", loadJobs);
$("#statusFilter").addEventListener("change", loadJobs);
$("#jobSearch").addEventListener("input", () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(loadJobs, 300); });
$("#jobsList").addEventListener("click", event => {
  const cancelUpload = event.target.closest("[data-upload-cancel]");
  if (cancelUpload) {
    const upload = state.pendingUploads.find(item => item.id === cancelUpload.dataset.uploadCancel);
    if (upload?.request) {
      upload.status = "cancelled";
      upload.detail = "Đã hủy tải batch";
      upload.request.abort();
      renderJobDashboard();
    }
    return;
  }
  const dismissUpload = event.target.closest("[data-upload-dismiss]");
  if (dismissUpload) {
    state.pendingUploads = state.pendingUploads.filter(item => item.id !== dismissUpload.dataset.uploadDismiss);
    renderJobDashboard();
    return;
  }
  const target = event.target.closest("[data-action]");
  if (target) jobAction(target.dataset.action, target.dataset.job);
});
$("#jobsList").addEventListener("change", event => {
  const checkbox = event.target.closest("[data-job-select]");
  if (!checkbox) return;
  const jobId = checkbox.dataset.jobSelect;
  if (checkbox.checked) state.selectedJobIds.add(jobId);
  else state.selectedJobIds.delete(jobId);
  checkbox.closest(".job-row")?.classList.toggle("selected", checkbox.checked);
  updateJobSelectionUI();
});
$("#selectAllJobs").addEventListener("change", event => {
  state.jobs.forEach(job => event.target.checked ? state.selectedJobIds.add(job.job_id) : state.selectedJobIds.delete(job.job_id));
  renderJobs(state.jobs);
});
$$('[data-bulk-action]').forEach(button => button.addEventListener("click", () => bulkJobAction(button.dataset.bulkAction)));
window.addEventListener("hashchange", showView);
window.addEventListener("beforeunload", event => {
  if (!state.pendingUploads.some(upload => upload.status === "uploading")) return;
  event.preventDefault();
  event.returnValue = "";
});
updateFormSummary();
syncTikTokSchedule();
renderFiles();
showView();
const oauthParams = new URLSearchParams(location.search);
if (oauthParams.get("tiktok") === "connected") toast("Đã kết nối tài khoản TikTok thành công");
if (oauthParams.get("tiktok_error")) toast(oauthParams.get("tiktok_error"), true);
if (oauthParams.has("tiktok") || oauthParams.has("tiktok_error")) {
  history.replaceState(null, "", `${location.pathname}${location.hash || "#create"}`);
}
bootstrapApplication();
