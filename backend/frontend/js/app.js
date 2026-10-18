import { createTikTokWorkspace } from "./tiktok.js?v=20261018-3";
import { resolveDouyinFrameUrl, resolveSourceBrowserFrameUrl } from "./browser-frame.js?v=20261015-1";
import { applyJobChanges, createJobEvents } from "./job-events.js?v=20261016-1";
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// Associate requests started synchronously by a user interaction with its
// button. Background polling deliberately has no trigger, so it stays quiet.
let apiActionTrigger = null;
const apiActionRequests = new WeakMap();

function rememberApiAction(element) {
  apiActionTrigger = element?.closest?.("button, [role=button]") || null;
  setTimeout(() => {
    if (apiActionTrigger === element || apiActionTrigger === element?.closest?.("button, [role=button]")) apiActionTrigger = null;
  }, 0);
}

document.addEventListener("click", event => rememberApiAction(event.target), true);
document.addEventListener("submit", event => {
  rememberApiAction(event.submitter || event.target.querySelector('button[type="submit"], input[type="submit"]'));
}, true);

function beginElementLoading(trigger) {
  if (!trigger) return () => {};
  const current = apiActionRequests.get(trigger);
  const action = current || { count: 0, startedAt: performance.now() };
  action.count += 1;
  apiActionRequests.set(trigger, action);
  trigger.classList.add("is-api-loading");
  trigger.setAttribute("aria-busy", "true");
  return () => {
    const active = apiActionRequests.get(trigger);
    if (!active) return;
    active.count = Math.max(0, active.count - 1);
    if (active.count) return;
    // Keep quick requests perceptible instead of flashing for only a few ms.
    const delay = Math.max(0, 450 - (performance.now() - active.startedAt));
    setTimeout(() => {
      if (apiActionRequests.get(trigger) !== active || active.count) return;
      apiActionRequests.delete(trigger);
      trigger.classList.remove("is-api-loading");
      trigger.removeAttribute("aria-busy");
    }, delay);
  };
}

function beginApiActionLoading() {
  return beginElementLoading(apiActionTrigger);
}

const state = {
  files: [], links: [], jobs: [], pendingUploads: [], selectedJobIds: new Set(), pollTimer: null, searchTimer: null,
  tiktokAuth: null, appStarted: false,
  douyinBrowser: null, douyinBrowserPollTimer: null, douyinBrowserLoaded: false,
  sourceBrowserPlatform: "douyin", sourceBrowsers: {},
  adminAccounts: [], adminAccountSummary: null, adminRetentionDefaultDays: null, adminSearchTimer: null,
  billing: null,
  auth: { enabled: false, configured: false, authenticated: false, username: null, role: "viewer", user_id: null },
};
const stepLabels = {
  queued: "Đang xếp hàng", starting: "Đang khởi động", "downloading-source": "Đang tải video nguồn",
  "source-ready": "Nguồn đã sẵn sàng",
  "extracting-subtitles": "Đang đọc phụ đề nguồn",
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
  const endLoading = beginApiActionLoading();
  return fetch(url, { ...options, headers, credentials: "same-origin" }).finally(endLoading);
}

function safeDownloadName(value, fallback = "vidtrans-download") {
  const filename = String(value || "").split(/[\\/]/).pop().trim();
  return (filename || fallback).replace(/[<>:"|?*\u0000-\u001f]/g, "_");
}

function startBrowserDownload(url, filename) {
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
}

async function saveDownload(url, suggestedName, trigger) {
  const filename = safeDownloadName(suggestedName);
  const endLoading = beginElementLoading(trigger);
  // Let the browser own the transfer so navigation does not interrupt a page
  // stream or leave a zero-byte Save File Picker destination and .crswap file.
  try {
    startBrowserDownload(url, filename);
    // Native downloads do not expose completion progress to page JavaScript.
    // Keep an acknowledgement spinner visible while the browser accepts it.
    await new Promise(resolve => setTimeout(resolve, 700));
    toast("Đã yêu cầu tải file. Xem tiến độ trong mục Tải xuống của trình duyệt; chờ tải xong trước khi mở.");
  } finally {
    endLoading();
  }
}

function lockApplication(message = "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.", isError = true) {
  tiktokWorkspace.lock();
  jobEvents.stop();
  clearInterval(state.pollTimer);
  clearInterval(state.douyinBrowserPollTimer);
  state.pollTimer = null;
  state.douyinBrowserPollTimer = null;
  state.appStarted = false;
  state.auth.authenticated = false;
  state.auth.role = "viewer";
  // Never leave the previous account's data rendered behind the login gate.
  // This also prevents a brief stale-data flash when another user signs in.
  state.jobs = [];
  state.pendingUploads = [];
  state.selectedJobIds.clear();
  state.files = [];
  state.links = [];
  applyPermissions();
  const jobsBody = $("#jobsBody");
  if (jobsBody) renderJobs([]);
  $("#userMenu").classList.add("is-hidden");
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
  if (!status.enabled) {
    const account = await requestJson("/api/v1/me");
    Object.assign(status, { authenticated: true, username: account.email, role: account.role, user_id: account.user_id });
  }
  state.auth = status;
  if (!status.enabled) {
    $("#authGate").classList.add("is-hidden");
    applyPermissions();
    return true;
  }
  if (!status.configured) {
    const detail = (status.configuration_errors || []).join(" · ") || status.message;
    lockApplication(detail);
    $("#authSubmit").disabled = true;
    $("#authRegisterSubmit").disabled = true;
    return false;
  }
  $("#authSubmit").disabled = false;
  $("#authRegisterSubmit").disabled = false;
  if (!status.authenticated) {
    lockApplication("Nhập tài khoản quản trị để tiếp tục.", false);
    return false;
  }
  $("#authGate").classList.add("is-hidden");
  applyPermissions();
  return true;
}

function roleLabel(role) {
  return ({ owner: "Chủ sở hữu", admin: "Quản trị viên", editor: "Biên tập viên", viewer: "Người xem" })[role] || "Thành viên";
}

function applyPermissions() {
  const role = state.auth.role || "viewer";
  const isAdmin = ["owner", "admin"].includes(role);
  $$('[data-admin-only]').forEach(element => {
    element.hidden = !isAdmin;
    element.classList.toggle("is-hidden", !isAdmin);
  });
  $$('[data-min-role="editor"]').forEach(element => element.classList.toggle("is-hidden", role === "viewer"));
  $("#userMenuName").textContent = state.auth.username || "Tài khoản";
  $("#userMenuRole").textContent = roleLabel(role);
  const popoverName = $("#userPopoverName");
  if (popoverName) popoverName.textContent = state.auth.username || "Tài khoản";
  const popoverRole = $("#userPopoverRole");
  if (popoverRole) popoverRole.textContent = roleLabel(role);
  $("#userAvatar").textContent = (state.auth.username || "U").charAt(0).toUpperCase();
  $("#appLogoutButton").classList.remove("is-hidden");
  $("#appLogoutButton").title = state.auth.enabled
    ? "Đăng xuất khỏi VidTrans"
    : "Xác thực đang tắt trong cấu hình môi trường local";
  $("#userMenu").classList.remove("is-hidden");
}

async function startProtectedApplication() {
  if (state.appStarted) return;
  state.appStarted = true;
  const canManageSources = ["owner", "admin"].includes(state.auth.role);
  await Promise.all([loadJobs(), canManageSources ? refreshDouyinAuthStatus() : Promise.resolve(), tiktokWorkspace.refresh()]);
  clearInterval(state.pollTimer);
  state.pollTimer = null;
  jobEvents.start();
  if (location.hash === "#douyin") startDouyinBrowserPolling();
  if (location.hash === "#tiktok") tiktokWorkspace.enter();
  if (location.hash === "#admin") loadAdminAccounts();
  if (location.hash === "#billing") loadBilling();
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
  const browserUrl = resolveDouyinFrameUrl(status.browser_url, location.href);
  if (status.available && browserUrl && frame.dataset.source !== browserUrl) {
    state.douyinBrowserLoaded = false;
    loading.classList.remove("is-hidden");
    frame.dataset.source = browserUrl;
    frame.src = browserUrl;
  }
  if (!status.available) {
    loading.classList.remove("is-hidden");
    $("#douyinBrowserHelp").textContent = "Container social-browser chưa sẵn sàng; xem log để kiểm tra Chromium/noVNC.";
  } else if (status.authenticated) {
    $("#douyinBrowserHelp").textContent = "Cookie đã đồng bộ và sẵn sàng cho bộ tải video Douyin.";
  } else {
    $("#douyinBrowserHelp").textContent = "Đăng nhập trong khung bên dưới; OTP được nhập thẳng vào Douyin.";
  }
}

const sourceBrowserLabels = { youtube: "YouTube", facebook: "Facebook" };

function renderSourceBrowserStatus(platform, status) {
  state.sourceBrowsers[platform] = status;
  const prefix = platform;
  const label = sourceBrowserLabels[platform];
  $(`#${prefix}BrowserStatus`).textContent = status.message;
  $(`#${prefix}BrowserIndicator`).classList.toggle("available", Boolean(status.available));
  $(`#${prefix}BrowserIndicator`).classList.toggle("authenticated", Boolean(status.authenticated));
  for (const action of ["Sync", "Reload", "Logout"]) $(`#${prefix}Browser${action}`).disabled = !status.available;
  const frame = $(`#${prefix}BrowserFrame`);
  const loading = $(`#${prefix}BrowserLoading`);
  const browserUrl = resolveSourceBrowserFrameUrl(status.browser_url, location.href, platform);
  if (status.available && browserUrl && frame.dataset.source !== browserUrl) {
    loading.classList.remove("is-hidden");
    frame.dataset.source = browserUrl;
    frame.src = browserUrl;
  }
  if (!status.available) $(`#${prefix}BrowserHelp`).textContent = "Container social-browser chưa sẵn sàng.";
  else if (status.authenticated) $(`#${prefix}BrowserHelp`).textContent = `Cookie đã đồng bộ và sẵn sàng cho bộ tải video ${label}.`;
  else $(`#${prefix}BrowserHelp`).textContent = `Đăng nhập ${label} trong khung bên dưới rồi bấm Đồng bộ.`;
}

async function refreshSourceBrowserStatus(platform, { refresh = false, quiet = false } = {}) {
  try {
    const status = await requestJson(`/api/v1/source-browser/${platform}/status${refresh ? "?refresh=true" : ""}`);
    renderSourceBrowserStatus(platform, status);
    return status;
  } catch (error) {
    renderSourceBrowserStatus(platform, { available: false, authenticated: false, message: error.message, browser_url: null });
    if (!quiet) toast(error.message, true);
    return null;
  }
}

async function selectSourceBrowserTab(platform) {
  state.sourceBrowserPlatform = platform;
  $$('[data-source-browser-tab]').forEach(button => {
    const active = button.dataset.sourceBrowserTab === platform;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $$('[data-source-browser-panel]').forEach(panel => panel.classList.toggle("is-hidden", panel.dataset.sourceBrowserPanel !== platform));
  try {
    const status = await requestJson(`/api/v1/source-browser/${platform}/activate`, { method: "POST" });
    if (platform === "douyin") renderDouyinBrowserStatus(status);
    else renderSourceBrowserStatus(platform, status);
  } catch (error) {
    toast(error.message, true);
    if (platform === "douyin") refreshDouyinBrowserStatus();
    else refreshSourceBrowserStatus(platform);
  }
}

async function syncSourceBrowser(platform) {
  const button = $(`#${platform}BrowserSync`);
  button.disabled = true;
  try {
    const status = await requestJson(`/api/v1/source-browser/${platform}/sync`, { method: "POST" });
    renderSourceBrowserStatus(platform, status);
    toast(status.message, !status.authenticated);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = !state.sourceBrowsers[platform]?.available; }
}

async function reloadSourceBrowser(platform) {
  try {
    const status = await requestJson(`/api/v1/source-browser/${platform}/reload`, { method: "POST" });
    renderSourceBrowserStatus(platform, status);
    toast(`Đã tải lại trang ${sourceBrowserLabels[platform]}`);
  } catch (error) { toast(error.message, true); }
}

async function logoutSourceBrowser(platform) {
  if (!window.confirm(`Đăng xuất ${sourceBrowserLabels[platform]} và xóa cookie đã đồng bộ?`)) return;
  try {
    const status = await requestJson(`/api/v1/source-browser/${platform}/session`, { method: "DELETE" });
    renderSourceBrowserStatus(platform, status);
    toast(status.message);
  } catch (error) { toast(error.message, true); }
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
  selectSourceBrowserTab(state.sourceBrowserPlatform);
  state.douyinBrowserPollTimer = setInterval(() => {
    if (state.sourceBrowserPlatform === "douyin") refreshDouyinBrowserStatus({ refresh: true, quiet: true });
    else refreshSourceBrowserStatus(state.sourceBrowserPlatform, { refresh: true, quiet: true });
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
  const domains = [
    "tiktok.com", "douyin.com", "iesdouyin.com",
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "facebook.com", "fb.com", "fb.watch",
  ];
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
    const hostname = new URL(link).hostname.toLowerCase();
    const platform = hostname.includes("douyin") ? "DY"
      : (hostname.includes("youtube") || hostname === "youtu.be") ? "YT"
      : (hostname.includes("facebook") || hostname === "fb.com" || hostname === "fb.watch") ? "FB"
      : "TT";
    return `<div class="file-item link-item">
      <span class="file-item-icon">${platform}</span>
      <div><strong title="${escapeHtml(link)}">${escapeHtml(link)}</strong><small>Sẽ tải trong hàng đợi nền</small></div>
    </div>`;
  }).join("");
  renderFiles();
}

function subtitleStyle() {
  return {
    font_name: "Noto Sans",
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


function updateFormSummary() {
  const modes = { "1": "Phụ đề", "2": "Phụ đề + voice", "3": "Voice + nhạc" };
  const languageLabels = { auto: "Tự nhận diện", vi: "Việt", en: "Anh", "zh-CN": "Trung", ja: "Nhật", ko: "Hàn", th: "Thái", id: "Indonesia", es: "Tây Ban Nha" };
  const previewTexts = { vi: "Tôi muốn trở thành phiên bản tốt nhất", en: "I want to become my best self", "zh-CN": "我想成为最好的自己", ja: "最高の自分になりたい", ko: "최고의 내가 되고 싶어요", th: "ฉันอยากเป็นตัวเองในแบบที่ดีที่สุด", id: "Saya ingin menjadi versi terbaik diri saya", es: "Quiero ser mi mejor versión" };
  const placements = { replace_original: "Thay chữ gốc", above_original: "Trên chữ gốc", bottom_safe: "Vùng an toàn" };
  const mode = $("#mode").value;
  const placement = $("#placementMode").value;
  $("#modeSummary").textContent = modes[mode];
  const sourceLanguage = $("#sourceLanguage").value;
  const targetLanguage = $("#targetLanguage").value;
  $("#languageSummary").textContent = `${languageLabels[sourceLanguage]} → ${languageLabels[targetLanguage]}`;
  $("#previewVietnamese").textContent = previewTexts[targetLanguage];
  const subtitleSource = $("#subtitleSource");
  const ocrAllowed = sourceLanguage === "zh-CN";
  [...subtitleSource.options].forEach(option => {
    if (option.value !== "speech") option.disabled = !ocrAllowed;
  });
  if (!ocrAllowed && subtitleSource.value !== "speech") subtitleSource.value = "speech";
  $("#placementSummary").textContent = placements[placement];
  $("#musicField").classList.toggle("is-hidden", mode !== "3");
  const voiceEnabled = mode === "2" || mode === "3";
  const musicEnabled = mode === "3";
  const voiceMode = document.querySelector('[name="voice_mode"]')?.value || "auto";
  const voiceTypeLabel = $("#voiceTypeLabel");
  if (voiceTypeLabel) voiceTypeLabel.textContent = voiceMode === "auto" ? "Giọng dự phòng" : "Giọng đọc";
  ["#voiceModeField", "#voiceTypeField", "#voiceStyleField", "#speechRateField", "#originalVolumeField", "#originalAudioField"].forEach(selector => {
    const field = $(selector);
    field.classList.toggle("is-disabled", !voiceEnabled);
    $$('input, select', field).forEach(control => { control.disabled = !voiceEnabled; });
  });
  const musicVolumeField = $("#musicVolumeField");
  musicVolumeField.classList.toggle("is-disabled", !musicEnabled);
  $$('input, select', musicVolumeField).forEach(control => { control.disabled = !musicEnabled; });
  const hints = {
    "1": "Giữ nguyên âm thanh gốc, chỉ dịch và chèn phụ đề theo ngôn ngữ đầu ra. Nhanh nhất và không chạy TTS.",
    "2": "Tạo giọng đọc theo ngôn ngữ đầu ra, đồng bộ từng câu và có thể giữ tiếng gốc ở mức nhỏ.",
    "3": "Giống chế độ lồng tiếng, đồng thời lặp nhạc nền vừa đủ thời lượng và tự cân bằng âm lượng.",
  };
  $("#modeHint").textContent = hints[mode];
  $$(".mode-card").forEach(card => {
    const isSelected = card.dataset.modeCard === mode;
    card.classList.toggle("is-selected", isSelected);
    card.setAttribute("aria-checked", String(isSelected));
  });
  const voiceNotice = $("#mode1VoiceNotice");
  if (voiceNotice) voiceNotice.classList.toggle("is-hidden", voiceEnabled);
  const voiceCluster = $("#voiceAudioCluster");
  if (voiceCluster) voiceCluster.classList.toggle("mode-sub-only", !voiceEnabled);
  $("#subtitleStyle").value = JSON.stringify(subtitleStyle());
  const preview = $(".subtitle-preview");
  preview.classList.remove("mode-replace", "mode-above", "mode-bottom");
  preview.classList.add(placement === "replace_original" ? "mode-replace" : placement === "above_original" ? "mode-above" : "mode-bottom");
  $("#previewVietnamese").style.fontSize = $("#matchSourceSize").checked ? "17px" : "21px";
}

function initWorkflowModeCards() {
  $$(".mode-card").forEach(card => {
    card.addEventListener("click", () => {
      const targetMode = card.dataset.modeCard;
      if (targetMode) {
        $("#mode").value = targetMode;
        updateFormSummary();
      }
    });
  });
  $("#switchToDubbingBtn")?.addEventListener("click", () => {
    $("#mode").value = "2";
    updateFormSummary();
  });
}

function bindWorkflowStepNav() {
  const links = $$(".workflow-steps-nav a");
  const sections = links.map(link => $(link.getAttribute("href"))).filter(Boolean);
  if (!sections.length) return;
  const setActive = id => links.forEach(link => link.classList.toggle("is-active", link.getAttribute("href") === `#${id}`));
  const observer = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (visible) setActive(visible.target.id);
  }, { rootMargin: "-18% 0px -62% 0px", threshold: [0.15, 0.4, 0.7] });
  sections.forEach(section => observer.observe(section));
  links.forEach(link => link.addEventListener("click", () => setActive(link.getAttribute("href").slice(1))));
}

let activeView = null;

function showView() {
  let view = location.hash === "#tiktok" ? "tiktok" : location.hash === "#jobs" ? "jobs" : location.hash === "#douyin" ? "douyin" : location.hash === "#admin" ? "admin" : location.hash === "#billing" ? "billing" : "create";
  if (["admin", "douyin"].includes(view) && !["owner", "admin"].includes(state.auth.role || "owner")) view = "create";
  if (view === "tiktok" && state.auth.role === "viewer") view = "jobs";
  $("#createView").classList.toggle("is-hidden", view !== "create");
  $("#jobsView").classList.toggle("is-hidden", view !== "jobs");
  $("#tiktokView").classList.toggle("is-hidden", view !== "tiktok");
  if (view === "tiktok" && state.appStarted) tiktokWorkspace.enter();
  else tiktokWorkspace.leave();
  $("#douyinView").classList.toggle("is-hidden", view !== "douyin");
  $("#adminView").classList.toggle("is-hidden", view !== "admin");
  $("#billingView").classList.toggle("is-hidden", view !== "billing");
  $$('[data-view-link]').forEach(link => link.classList.toggle("active", link.dataset.viewLink === view));
  if (view === "jobs" && state.appStarted) loadJobs();
  if (view === "douyin" && state.appStarted) startDouyinBrowserPolling();
  else stopDouyinBrowserPolling();
  if (view === "admin" && state.appStarted) loadAdminAccounts();
  if (view === "billing" && state.appStarted) loadBilling();
  $("#mainNavigation").classList.remove("open");
  $("#navToggle").setAttribute("aria-expanded", "false");
  if (activeView !== view) {
    activeView = view;
    window.scrollTo(0, 0);
  }
}

const billingLabels = {
  payment: { trial: "Đang dùng thử", active: "Đang hoạt động", past_due: "Cần thanh toán", cancelled: "Đã hủy", exempt: "Miễn thanh toán" },
  order: { pending: "Chờ thanh toán", paid: "Đã thanh toán", failed: "Thất bại", expired: "Hết hạn", cancelled: "Đã hủy" },
};

function money(value) {
  return value == null ? "Liên hệ" : new Intl.NumberFormat("vi-VN", { style: "currency", currency: "VND", maximumFractionDigits: 0 }).format(value);
}

function selectBillingPlan(planKey, { scroll = true } = {}) {
  const plan = state.billing?.plans?.find(item => item.key === planKey && Number(item.amount) > 0);
  const select = $("#paymentPlan");
  if (!plan || !select || ![...select.options].some(option => option.value === planKey)) return;
  select.value = planKey;
  $$("[data-plan-card]", $("#billingPlans")).forEach(card => {
    const selected = card.dataset.planCard === planKey;
    card.classList.toggle("is-selected", selected);
    card.setAttribute("aria-selected", String(selected));
  });
  $("#checkoutTitle").textContent = `Thanh toán gói ${plan.name}`;
  $("#checkoutDescription").textContent = `${plan.minutes.toLocaleString("vi-VN")} phút xử lý mỗi tháng · ${money(plan.amount)}/tháng. Chọn phương thức thanh toán để tiếp tục.`;
  $("#createPaymentLabel").textContent = `Tiếp tục với ${plan.name} · ${money(plan.amount)}`;
  const notice = $("#billingNotice");
  notice.innerHTML = `<strong>Đã chọn gói ${escapeHtml(plan.name)}</strong><span>${plan.minutes.toLocaleString("vi-VN")} phút/tháng · ${money(plan.amount)}. Hoàn tất phương thức thanh toán bên dưới.</span>`;
  notice.classList.remove("is-hidden");
  if (scroll) {
    const checkout = $(".payment-checkout");
    checkout.classList.remove("is-focused");
    requestAnimationFrame(() => checkout.classList.add("is-focused"));
    checkout.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => checkout.classList.remove("is-focused"), 1400);
  }
}

function renderBilling(data) {
  const account = data.account;
  $("#billingCurrentPlan").textContent = String(account.plan || "free").toUpperCase();
  $("#billingPaymentStatus").textContent = billingLabels.payment[account.payment_status] || account.payment_status;

  const quota = data.quota;
  if (quota && $("#billingQuotaUsage")) {
    const period = quota.period || "";
    $("#billingQuotaPeriod").textContent = period;
    const limit = quota.limit_minutes;
    const reserved = quota.reserved_minutes || 0;
    const consumed = quota.consumed_minutes || 0;
    const used = reserved + consumed;
    if (limit == null) {
      $("#billingQuotaUsage").textContent = `${used} phút (Không giới hạn)`;
      $("#billingQuotaProgress").style.width = "100%";
      $("#billingQuotaRemaining").textContent = "Gói Doanh nghiệp không giới hạn thời lượng";
    } else {
      const remaining = quota.remaining_minutes ?? Math.max(0, limit - used);
      const percent = Math.min(100, Math.round((used / limit) * 100));
      $("#billingQuotaUsage").textContent = `${used} / ${limit} phút (${percent}%)`;
      $("#billingQuotaProgress").style.width = `${percent}%`;
      $("#billingQuotaProgress").style.background = percent > 90 ? "#ef4444" : percent > 70 ? "#f59e0b" : "#ef6b3a";
      $("#billingQuotaRemaining").textContent = `Còn lại: ${remaining} phút ${reserved > 0 ? `(Đang giữ chỗ: ${reserved} phút)` : ""}`;
    }
  }

  const planBadges = {
    free: { icon: "🌱", label: "Gói Khởi động" },
    creator: { icon: "⚡", label: "Nhà sáng tạo" },
    pro: { icon: "🚀", label: "Phổ biến nhất" },
    enterprise: { icon: "🏢", label: "Doanh nghiệp" },
  };

  const paidPlans = data.plans.filter(plan => Number(plan.amount) > 0 && plan.key !== account.plan);
  const previousSelection = $("#paymentPlan").value;
  $("#paymentPlan").innerHTML = paidPlans.map(plan => `<option value="${escapeHtml(plan.key)}">${escapeHtml(plan.name)} (${plan.minutes.toLocaleString("vi-VN")} phút/tháng) · ${money(plan.amount)}</option>`).join("");

  $("#billingPlans").innerHTML = data.plans.map(plan => {
    const isCurrent = plan.key === account.plan;
    const isFeatured = plan.key === "pro";
    const isSelectable = Number(plan.amount) > 0 && !isCurrent;
    const badgeText = isCurrent ? "Gói hiện tại" : isFeatured ? "Phổ biến" : "VidTrans";
    const planMeta = planBadges[plan.key] || { icon: "✦", label: "VidTrans" };

    return `
      <article class="card billing-plan ${isCurrent ? "current" : ""} ${isFeatured ? "featured" : ""} ${isSelectable ? "is-selectable" : ""}" ${isSelectable ? `data-plan-card="${escapeHtml(plan.key)}" role="button" tabindex="0" aria-selected="false"` : ""}>
        <div class="plan-card-header">
          <div class="plan-badge-row">
            <span class="plan-badge ${isCurrent ? "badge-current" : isFeatured ? "badge-featured" : "badge-standard"}">
              ${isCurrent ? '<span class="status-pulse-dot" aria-hidden="true"></span>' : isFeatured ? '<span class="star-icon" aria-hidden="true">★</span>' : ""}
              <span>${badgeText}</span>
            </span>
            <span class="plan-icon" aria-hidden="true">${planMeta.icon}</span>
          </div>
          <h2>${escapeHtml(plan.name)}</h2>
          <p>${plan.minutes == null ? "Thiết kế riêng theo quy mô và khối lượng đội ngũ" : `<strong>${plan.minutes} phút</strong> video mỗi tháng`}</p>
        </div>
        <div class="plan-price-wrap">
          <strong class="plan-price">${money(plan.amount)}${plan.amount ? "<small>/tháng</small>" : ""}</strong>
        </div>
        <ul class="plan-features">
          <li><span class="feature-icon check">✓</span><span>Xử lý video đa luồng & phụ đề AI</span></li>
          <li><span class="feature-icon check">✓</span><span>Lưu trữ kết quả đám mây an toàn</span></li>
          <li>${plan.key === "free" ? '<span class="feature-icon dash">—</span><span class="feature-muted">Hỗ trợ ưu tiên 24/7</span>' : '<span class="feature-icon check">✓</span><span>Hỗ trợ ưu tiên chuyên sâu 24/7</span>'}</li>
        </ul>
        <div class="plan-card-action">
          ${plan.key === "enterprise" ? `
            <a class="button secondary enterprise-btn" href="mailto:support@vidtrans.local">
              <span>Liên hệ tư vấn</span>
              <span>→</span>
            </a>` : plan.key === "free" ? (isCurrent ? `
            <button class="button secondary current-plan-btn" type="button" disabled>
              ✓ Đang kích hoạt
            </button>` : `
            <button class="button secondary" type="button" data-switch-free="true">
              Sử dụng miễn phí
            </button>`) : `
            <button class="button ${isFeatured ? "primary" : isCurrent ? "secondary current-plan-btn" : "secondary"}" type="button" data-select-plan="${plan.key}" ${isCurrent ? "disabled" : ""}>
              ${isCurrent ? "✓ Đang kích hoạt" : "Chọn gói"}
            </button>`}
        </div>
      </article>`;
  }).join("");

  const initialPlan = paidPlans.some(plan => plan.key === previousSelection)
    ? previousSelection
    : paidPlans[0]?.key;
  if (initialPlan) selectBillingPlan(initialPlan, { scroll: false });

  $("#paymentProviders").innerHTML = `<legend>Phương thức thanh toán</legend><div class="payment-methods-grid">${Object.entries(data.providers).map(([key, available]) => `
    <label class="payment-method ${available ? "" : "disabled"}">
      <input type="radio" name="provider" value="${key}" ${key === "bank" && available ? "checked" : ""} ${available ? "" : "disabled"}/>
      <span class="payment-logo payment-logo-${key}">
        ${key === "bank" ? "🏦" : key === "momo" ? "M" : "Z"}
      </span>
      <span class="payment-meta">
        <strong>${key === "bank" ? "Chuyển khoản Ngân hàng (VietQR)" : key === "momo" ? "Ví điện tử MoMo" : "Ví ZaloPay"}</strong>
        <small>${escapeHtml(data.provider_notes[key])}</small>
      </span>
      ${available ? '<span class="method-check" aria-hidden="true">●</span>' : '<i class="method-unavailable">Chưa khả dụng</i>'}
    </label>`).join("")}</div>`;

  renderBillingOrders(data.orders || []);
}

function renderPaymentInstructions(order) {
  const instructions = $("#paymentInstructions");
  instructions.innerHTML = `
    <div class="bank-qr-wrap">
      <div class="bank-qr-frame">
        <img src="${escapeHtml(order.bank.qr_url)}" alt="Mã QR chuyển khoản ${escapeHtml(order.bank.name)}" loading="eager"/>
      </div>
      <small class="bank-qr-caption">Quét mã bằng App ngân hàng bất kỳ (VietQR 24/7)</small>
    </div>
    <div class="bank-transfer-detail">
      <div class="detail-kicker">
        <span class="live-pulse-dot" aria-hidden="true"></span>
        <span>Yêu cầu thanh toán ${escapeHtml(String(order.plan).toUpperCase())}</span>
      </div>
      <h3 class="transfer-total">${money(order.amount)}</h3>
      <dl class="transfer-specs">
        <div><dt>Ngân hàng thụ hưởng</dt><dd>${escapeHtml(order.bank.name)}</dd></div>
        <div>
          <dt>Số tài khoản</dt>
          <dd class="copyable-value">
            <span>${escapeHtml(order.bank.account_no)}</span>
            <button class="mini-copy-btn" type="button" data-copy="${escapeHtml(order.bank.account_no)}" title="Sao chép số tài khoản">📋</button>
          </dd>
        </div>
        <div><dt>Tên chủ tài khoản</dt><dd>${escapeHtml(order.bank.account_name)}</dd></div>
        <div>
          <dt>Nội dung chuyển khoản (bắt buộc)</dt>
          <dd class="copyable-value highlight-content">
            <span>${escapeHtml(order.transfer_content)}</span>
            <button class="mini-copy-btn" type="button" data-copy="${escapeHtml(order.transfer_content)}" title="Sao chép nội dung">📋</button>
          </dd>
        </div>
      </dl>
      <div class="transfer-note-box">
        <span class="note-icon" aria-hidden="true">💡</span>
        <p>Mã QR đã chứa đúng số tiền và cú pháp. Bạn có thể bấm xác nhận bên dưới sau khi chuyển khoản thành công để kích hoạt gói ngay lập tức.</p>
      </div>
      <div class="transfer-action-buttons">
        <button class="button primary transfer-confirm-btn" type="button" data-confirm-order="${escapeHtml(order.order_id)}">
          <span>✓ Xác nhận đã chuyển khoản & Kích hoạt gói</span>
        </button>
        <button class="button secondary transfer-cancel-btn" type="button" data-cancel-order="${escapeHtml(order.order_id)}">
          <span>✕ Hủy yêu cầu này</span>
        </button>
      </div>
    </div>`;
  instructions.classList.remove("is-hidden");
  instructions.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderBillingOrders(orders) {
  $("#billingOrders").innerHTML = orders.length ? orders.map(order => `
    <article class="billing-order-item" data-order-id="${escapeHtml(order.order_id)}">
      <div class="order-main">
        <span class="order-icon" aria-hidden="true">🧾</span>
        <div class="order-info">
          <strong class="order-code">${escapeHtml(order.transfer_content)}</strong>
          <small class="order-date">${escapeHtml(new Date(order.created_at).toLocaleString("vi-VN"))} · Gói ${escapeHtml(String(order.plan).toUpperCase())}</small>
        </div>
      </div>
      <div class="order-amount-status">
        <b class="order-amount">${money(order.amount)}</b>
        <span class="order-status-badge order-${order.status}">
          <span class="status-dot-mini" aria-hidden="true"></span>
          <span>${billingLabels.order[order.status] || order.status}</span>
        </span>
        ${order.status === "pending" ? `
          <div class="order-actions">
            <button class="button secondary mini-btn" type="button" data-view-order="${escapeHtml(order.order_id)}" title="Xem mã QR thanh toán">Xem QR</button>
            <button class="button primary mini-btn" type="button" data-confirm-order="${escapeHtml(order.order_id)}" title="Kích hoạt gói">Kích hoạt</button>
            <button class="button secondary mini-btn cancel-btn" type="button" data-cancel-order="${escapeHtml(order.order_id)}" title="Hủy yêu cầu">Hủy</button>
          </div>` : ""}
      </div>
    </article>`).join("") : '<div class="empty-state"><div><div class="empty-icon">💳</div><strong>Chưa có giao dịch</strong><br><small>Đơn thanh toán mới sẽ xuất hiện tại đây sau khi bạn tạo yêu cầu.</small></div></div>';
}

async function loadBilling() {
  try {
    state.billing = await requestJson("/api/v1/billing/overview");
    renderBilling(state.billing);
  } catch (error) { toast(error.message, true); }
}

async function createPayment(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const provider = form.querySelector('[name="provider"]:checked');
  if (!provider) return toast("Chưa có phương thức thanh toán khả dụng", true);
  const button = $("#createPaymentButton");
  button.disabled = true;
  try {
    const body = new FormData();
    body.set("plan", $("#paymentPlan").value);
    body.set("provider", provider.value);
    const order = await requestJson("/api/v1/billing/orders", { method: "POST", body });
    if (order.checkout_url) {
      window.location.assign(order.checkout_url);
      return;
    }
    if (order.bank) {
      renderPaymentInstructions(order);
    }
    toast("Đã tạo yêu cầu thanh toán thành công");
    await loadBilling();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

const adminLabels = {
  role: { owner: "Chủ sở hữu", admin: "Quản trị viên", editor: "Biên tập viên", viewer: "Người xem" },
  approval: { pending: "Chờ duyệt", approved: "Đã duyệt", suspended: "Tạm khóa", rejected: "Từ chối" },
  payment: { trial: "Dùng thử", active: "Đang hoạt động", past_due: "Quá hạn", cancelled: "Đã hủy", exempt: "Miễn thanh toán" },
};

function adminSelect(name, value, options, label, disabled = false) {
  const extraClass = name === "approval_status" ? ` status-${value}` : name === "role" ? ` role-${value}` : name === "plan" ? ` plan-${value}` : "";
  return `<label class="admin-account-field field-${name}${extraClass}">
    <span class="field-label-inline">${label}</span>
    <select name="${name}" aria-label="${label}" ${disabled ? "disabled" : ""}>
      ${options.map(([key, optionLabel]) => `<option value="${key}" ${key === value ? "selected" : ""}>${optionLabel}</option>`).join("")}
    </select>
  </label>`;
}

const ADMIN_RETENTION_DAYS = [1, 3, 7, 14, 30, 60, 90, 180, 365];

function adminRetentionOptions(current) {
  const defaultDays = state.adminRetentionDefaultDays;
  const options = [["default", `Mặc định (${defaultDays ? `${defaultDays} ngày` : "không xoá"})`]];
  const days = new Set(ADMIN_RETENTION_DAYS);
  if (current != null && Number(current) > 0) days.add(Number(current));
  [...days].sort((a, b) => a - b).forEach(value => options.push([String(value), `Xoá sau ${value} ngày`]));
  options.push(["0", "Không tự xoá"]);
  return options;
}

function renderAdminAccounts() {
  const summary = state.adminAccountSummary || {};
  $("#adminTotalCount").textContent = summary.total || 0;
  $("#adminApprovedCount").textContent = summary.approved || 0;
  $("#adminPendingCount").textContent = summary.pending || 0;
  $("#adminPaymentAttentionCount").textContent = summary.payment_attention || 0;
  const list = $("#adminAccountsList");
  if (!state.adminAccounts.length) {
    list.innerHTML = '<div class="empty-state"><div><div class="empty-icon">👥</div><strong>Không có tài khoản phù hợp</strong><br><small>Thử thay đổi bộ lọc phê duyệt hoặc từ khóa tìm kiếm.</small></div></div>';
    return;
  }
  const roles = Object.entries(adminLabels.role);
  const approvals = Object.entries(adminLabels.approval);
  const payments = Object.entries(adminLabels.payment);
  const plans = [["free", "Free"], ["creator", "Creator"], ["pro", "Pro"], ["enterprise", "Enterprise"]];
  list.innerHTML = state.adminAccounts.map(item => {
    const quota = item.quota || {};
    const quotaUsed = Number(quota.consumed_minutes || 0) + Number(quota.reserved_minutes || 0);
    const quotaText = quota.limit_minutes == null
      ? `${quotaUsed} phút · Không giới hạn`
      : `${quotaUsed}/${quota.limit_minutes} phút · còn ${quota.remaining_minutes ?? 0}`;
    return `
    <article class="admin-account-row" data-admin-account="${escapeHtml(item.user_id)}">
      <div class="admin-account-user">
        <span class="admin-avatar avatar-${escapeHtml(item.role)}" aria-hidden="true">${escapeHtml((item.display_name || item.email || "U").trim().charAt(0).toUpperCase())}</span>
        <div class="user-meta-stack">
          <div class="name-edit-wrap">
            <input name="display_name" maxlength="120" value="${escapeHtml(item.display_name)}" aria-label="Tên hiển thị" title="Bấm để chỉnh sửa tên hiển thị" />
          </div>
          <strong class="user-email" title="${escapeHtml(item.email)}">${escapeHtml(item.email)}</strong>
          <small class="user-join-date">Tham gia ${escapeHtml(new Date(item.created_at).toLocaleDateString("vi-VN"))}</small>
          <small class="admin-user-quota" title="Quota chu kỳ ${escapeHtml(quota.period || "")}">⏱ ${escapeHtml(quotaText)}</small>
        </div>
      </div>
      ${adminSelect("role", item.role, roles, "Vai trò", item.role === "owner")}
      ${adminSelect("approval_status", item.approval_status, approvals, "Phê duyệt", item.role === "owner")}
      ${adminSelect("plan", item.plan, plans, "Gói dịch vụ")}
      ${adminSelect("payment_status", item.payment_status, payments, "Thanh toán")}
      ${adminSelect("video_retention_days", item.video_retention_days == null ? "default" : String(item.video_retention_days), adminRetentionOptions(item.video_retention_days), "Lưu video")}
      <div class="admin-action-cell" style="display: flex; gap: 6px; align-items: center;">
        <button class="button secondary btn-admin-save" type="button" data-admin-save title="Lưu thay đổi tài khoản này">
          <span class="save-icon" aria-hidden="true">✓</span>
          <span>Lưu</span>
        </button>
        ${item.role !== "owner" ? `
          <button class="button secondary mini-btn cancel-btn" type="button" data-admin-delete="${escapeHtml(item.user_id)}" title="Xóa tài khoản">
            <span>✕</span>
          </button>` : ""}
      </div>
    </article>`;
  }).join("");

  const userFilter = $("#adminJobUserFilter");
  if (userFilter) {
    const currentVal = userFilter.value;
    userFilter.innerHTML = '<option value="">Tất cả người dùng</option>' + state.adminAccounts.map(a => `
      <option value="${escapeHtml(a.user_id)}" ${a.user_id === currentVal ? "selected" : ""}>${escapeHtml(a.email || a.display_name || a.user_id)}</option>
    `).join("");
  }
}

async function loadAdminAccounts() {
  try {
    const params = new URLSearchParams();
    const query = $("#adminUserSearch")?.value.trim() || "";
    const status = $("#adminApprovalFilter")?.value || "";
    if (query) params.set("query", query);
    if (status) params.set("status", status);
    const [summary, accounts] = await Promise.all([
      requestJson("/api/v1/admin/accounts/summary"),
      requestJson(`/api/v1/admin/accounts${params.size ? `?${params}` : ""}`),
    ]);
    state.adminAccountSummary = summary;
    state.adminAccounts = accounts.items || [];
    state.adminRetentionDefaultDays = accounts.video_retention?.default_days ?? null;
    renderAdminAccounts();
  } catch (error) {
    $("#adminAccountsList").innerHTML = `<div class="empty-state error"><strong>${escapeHtml(error.message)}</strong></div>`;
  }
}

async function saveAdminAccount(row, button) {
  button.disabled = true;
  try {
    const formData = new FormData();
    ["display_name", "role", "approval_status", "plan", "payment_status", "video_retention_days"].forEach(name => formData.set(name, row.querySelector(`[name="${name}"]`).value));
    await requestJson(`/api/v1/admin/accounts/${encodeURIComponent(row.dataset.adminAccount)}`, { method: "POST", body: formData });
    toast("Đã cập nhật tài khoản");
    await loadAdminAccounts();
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
}

async function createAdminAccount(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await requestJson("/api/v1/admin/accounts", { method: "POST", body: new FormData(form) });
    form.reset();
    toast("Đã tạo tài khoản chờ phê duyệt");
    await loadAdminAccounts();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

async function deleteAdminAccount(userId) {
  if (!confirm("Bạn có chắc chắn muốn xóa tài khoản này? Toàn bộ file và dữ liệu liên quan sẽ bị dọn dẹp theo chính sách bảo mật.")) return;
  try {
    await requestJson(`/api/v1/admin/accounts/${encodeURIComponent(userId)}`, { method: "DELETE" });
    toast("Đã xóa tài khoản và dọn dẹp dữ liệu");
    await loadAdminAccounts();
  } catch (error) { toast(error.message, true); }
}

async function loadAdminJobs() {
  const list = $("#adminJobsList");
  if (!list) return;
  list.innerHTML = '<div class="empty-state"><strong>Đang tải danh sách jobs toàn hệ thống…</strong></div>';
  try {
    const params = new URLSearchParams();
    const userId = $("#adminJobUserFilter")?.value || "";
    const status = $("#adminJobStatusFilter")?.value || "";
    const search = $("#adminJobSearch")?.value.trim() || "";
    if (userId) params.set("user_id", userId);
    if (status) params.set("status", status);
    if (search) params.set("search", search);
    params.set("limit", "100");

    const data = await requestJson(`/api/v1/admin/jobs?${params}`);
    renderAdminJobs(data.items || []);
  } catch (err) {
    list.innerHTML = `<div class="empty-state error"><strong>${escapeHtml(err.message)}</strong></div>`;
  }
}

function renderAdminJobs(jobs) {
  const list = $("#adminJobsList");
  if (!list) return;
  if (!jobs.length) {
    list.innerHTML = '<div class="empty-state"><strong>Không có tiến trình nào phù hợp</strong></div>';
    return;
  }
  list.innerHTML = jobs.map(job => {
    const status = job.status || "queued";
    const statusBadge = `<span class="badge ${status === "completed" ? "badge-success" : status === "failed" ? "badge-danger" : "badge-warning"}">${status}</span>`;
    const userLabel = job.owner_email ? `${escapeHtml(job.owner_email)}` : (job.owner_user_id || "Chưa rõ");
    const progress = Math.round((job.progress || 0) * 100);
    const createdAt = job.created_at ? new Date(job.created_at).toLocaleString("vi-VN") : "—";
    const fileName = escapeHtml(job.filename || "Video");

    return `
      <div class="admin-jobs-row" data-job-id="${escapeHtml(job.job_id)}">
        <code>${escapeHtml(job.job_id)}</code>
        <div class="user-meta" title="${escapeHtml(job.owner_user_id || "")}">
          <strong>${userLabel}</strong>
          ${job.owner_display_name ? `<br><small>${escapeHtml(job.owner_display_name)}</small>` : ""}
        </div>
        <div class="job-filename" title="${fileName}">${fileName}</div>
        <div>${statusBadge}</div>
        <div>${progress}%</div>
        <small>${createdAt}</small>
        <div>
          <button class="button secondary mini-btn" type="button" data-admin-view-job="${escapeHtml(job.job_id)}" title="Xem chi tiết (Có ghi Audit)">Chi tiết</button>
        </div>
      </div>
    `;
  }).join("");
}

async function loadAdminAudit() {
  const list = $("#adminAuditList");
  if (!list) return;
  list.innerHTML = '<div class="empty-state"><strong>Đang tải nhật ký kiểm toán…</strong></div>';
  try {
    const params = new URLSearchParams();
    const action = $("#adminAuditActionFilter")?.value || "";
    if (action) params.set("action", action);
    params.set("limit", "100");

    const data = await requestJson(`/api/v1/admin/audit-events?${params}`);
    renderAdminAudit(data.items || []);
  } catch (err) {
    list.innerHTML = `<div class="empty-state error"><strong>${escapeHtml(err.message)}</strong></div>`;
  }
}

function renderAdminAudit(events) {
  const list = $("#adminAuditList");
  if (!list) return;
  if (!events.length) {
    list.innerHTML = '<div class="empty-state"><strong>Chưa có bản ghi kiểm toán nào</strong></div>';
    return;
  }
  list.innerHTML = events.map(evt => {
    const time = evt.created_at ? new Date(evt.created_at).toLocaleString("vi-VN") : "—";
    const actor = evt.actor_email || evt.actor_user_id;
    const target = evt.target_email || evt.target_user_id || "—";
    const metaStr = JSON.stringify(evt.metadata || {});

    return `
      <div class="admin-audit-row">
        <small>${time}</small>
        <strong>${escapeHtml(actor)}</strong>
        <div><span class="badge badge-info">${escapeHtml(evt.action)}</span></div>
        <div><small>${escapeHtml(evt.resource_type || "—")}${evt.resource_id ? `:${escapeHtml(evt.resource_id)}` : ""}</small></div>
        <div>${escapeHtml(target)}</div>
        <div class="audit-metadata-code" title="${escapeHtml(metaStr)}">${escapeHtml(metaStr)}</div>
      </div>
    `;
  }).join("");
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
  renderSourceLinks();
  const sourceText = $("#sourceLinks").value.trim();
  if (!state.files.length && !state.links.length) {
    return toast(sourceText ? "Không tìm thấy link TikTok, Douyin, YouTube hoặc Facebook hợp lệ" : "Hãy chọn file hoặc dán ít nhất một link video", true);
  }
  if (state.files.length + state.links.length > 50) return toast("Mỗi batch chỉ nhận tối đa 50 video", true);
  if ($("#sourceLanguage").value !== "auto" && $("#sourceLanguage").value === $("#targetLanguage").value) {
    return toast("Ngôn ngữ nguồn và đầu ra phải khác nhau", true);
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
  const endSubmitLoading = beginApiActionLoading();
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
    endSubmitLoading();
    pendingUpload.request = null;
    $("#submitButton").disabled = false;
    $("#uploadProgress").classList.add("is-hidden");
    $("#uploadFill").style.width = "0";
  }
}

function jobActions(job) {
  const terminal = ["completed", "failed", "cancelled"].includes(job.status);
  let downloads = "";
  if (job.video_url) {
    const videoName = job.output_video || `${job.job_id}.mp4`;
    downloads += `<button type="button" class="btn-job-action btn-download" data-download-url="${escapeHtml(job.video_url)}" data-download-name="${escapeHtml(videoName)}" title="Tải video MP4 hoàn tất"><svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg><span>Video</span></button>`;
  }
  if (job.download_all_url) {
    const zipName = `${job.job_id}.artifacts.zip`;
    downloads += (downloads ? " " : "") + `<button type="button" class="btn-job-action btn-download btn-zip" data-download-url="${escapeHtml(job.download_all_url)}" data-download-name="${escapeHtml(zipName)}" title="Tải toàn bộ tài nguyên (ZIP)"><svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg><span>ZIP</span></button>`;
  }
  const cancel = ["queued", "scheduled", "processing", "cancelling"].includes(job.status)
    ? `<button type="button" class="btn-job-action btn-cancel" data-action="cancel" data-job="${job.job_id}" title="Hủy tiến trình"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg><span>Hủy</span></button>`
    : "";
  const retry = terminal
    ? `<button type="button" class="btn-job-action btn-retry" data-action="retry" data-job="${job.job_id}" title="Chạy lại video này"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg><span>Chạy lại</span></button>`
    : "";
  const remove = terminal
    ? `<button type="button" class="btn-job-action btn-delete" data-action="delete" data-job="${job.job_id}" title="Xóa video và dữ liệu liên quan"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg><span>Xóa</span></button>`
    : "";
  const refreshTikTok = job.tiktok_publish_id && !["PUBLISH_COMPLETE", "FAILED"].includes(job.tiktok_publish_status)
    ? `<button type="button" class="btn-job-action btn-tiktok-status" data-action="tiktok-status" data-job="${job.job_id}" title="Cập nhật trạng thái TikTok"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64c.298-.002.595.042.88.13V9.4a6.33 6.33 0 0 0-1-.08A6.34 6.34 0 0 0 3 15.66a6.34 6.34 0 0 0 10.82 4.49 6.27 6.27 0 0 0 1.86-4.49v-7a8.16 8.16 0 0 0 4.77 1.52v-3.4a4.85 4.85 0 0 1-.86-.09z"/></svg><span>TikTok</span></button>`
    : "";
  const review = job.status === "completed" && job.video_url
    ? `<button type="button" data-action="tiktok-review" data-job="${escapeHtml(job.job_id)}" class="btn-job-action tiktok-review-action" title="Duyệt bài và đăng video lên TikTok"><svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64c.298-.002.595.042.88.13V9.4a6.33 6.33 0 0 0-1-.08A6.34 6.34 0 0 0 3 15.66a6.34 6.34 0 0 0 10.82 4.49 6.27 6.27 0 0 0 1.86-4.49v-7a8.16 8.16 0 0 0 4.77 1.52v-3.4a4.85 4.85 0 0 1-.86-.09z"/></svg><span>Duyệt đăng</span></button>`
    : "";
  return review + downloads + refreshTikTok + cancel + retry + remove;
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
  $("#selectedJobsCount").textContent = selectedVisible.length ? `Đã chọn ${selectedVisible.length} tiến trình` : "Chưa chọn job";
  const counts = {
    cancel: eligibleJobs("cancel").length,
    retry: eligibleJobs("retry").length,
    delete: eligibleJobs("delete").length,
  };
  const labels = { cancel: "Hủy", retry: "Chạy lại", delete: "Xóa" };
  const icons = {
    cancel: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`,
    retry: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>`,
    delete: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>`,
  };
  for (const [action, count] of Object.entries(counts)) {
    const button = $(`[data-bulk-action="${action}"]`);
    button.disabled = count === 0;
    button.innerHTML = `<span class="btn-icon">${icons[action]}</span><span>${count ? `${labels[action]} (${count})` : labels[action]}</span>`;
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
    ? `<button class="btn-job-action btn-cancel" data-upload-cancel="${escapeHtml(upload.id)}" type="button"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg><span>Hủy</span></button>`
    : `<button class="btn-job-action btn-dismiss" data-upload-dismiss="${escapeHtml(upload.id)}" type="button"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg><span>Ẩn</span></button>`;
  const uploadIcon = active
    ? `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>`
    : `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;
  return `<article class="job-row upload-row ${active ? "is-uploading" : "upload-failed"}" data-upload-row="${escapeHtml(upload.id)}">
    <span class="job-select upload-indicator" aria-hidden="true">${active ? "↑" : "!"}</span>
    <div class="job-file">
      <div class="job-file-icon upload-icon ${active ? "active-upload-icon" : "failed-upload-icon"}">${uploadIcon}</div>
      <div class="job-file-meta">
        <div class="job-title-row">
          <strong class="job-name-text" title="${escapeHtml(upload.batchName)}">${escapeHtml(upload.batchName)}</strong>
          <span class="upload-badge ${active ? "badge-uploading" : "badge-failed"}">${active ? "Đang upload" : "Upload lỗi"}</span>
        </div>
        <div class="source-url-row" title="${escapeHtml(sourceSummary)}">
          <svg class="source-link-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>
          <span class="url-truncate">${escapeHtml(sourceSummary)}</span>
        </div>
      </div>
    </div>
    <div class="job-owner job-owner-pending">—</div>
    <span class="status-pill status-${statusClass}"><span class="status-dot"></span><span>${statusText}</span></span>
    <div class="job-progress"><div class="progress-track ${active ? "progress-active" : "progress-failed"}"><span style="width:${active ? progress : 100}%"></span></div><small>${active ? `${progress}%` : "—"}</small></div>
    <div class="job-step"><strong>${progress >= 100 && active ? "Đang tạo tiến trình" : active ? "Đang tải batch" : "Tải lên không thành công"}</strong><small title="${escapeHtml(upload.detail)}">${escapeHtml(upload.detail)}</small></div>
    <div class="job-actions">${action}</div>
  </article>`;
}

function renderJobs(jobs) {
  const pendingHtml = state.pendingUploads.map(renderPendingUpload).join("");
  if (!jobs.length && !pendingHtml) {
    $("#jobsList").innerHTML = `<div class="empty-state">
      <div class="empty-icon-wrap">
        <svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>
      </div>
      <strong>Chưa có tiến trình phù hợp</strong>
      <small>Tạo batch mới hoặc thay đổi bộ lọc trạng thái để xem video.</small>
      <a class="button primary empty-state-btn" href="#create">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
        <span>Tạo batch video mới</span>
      </a>
    </div>`;
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
    const batchBadge = job.batch_id ? `<span class="batch-tag">${escapeHtml(job.batch_id)}</span>` : `<span class="single-tag">Video đơn</span>`;
    const ownerName = job.owner_display_name || job.owner_email || job.owner_user_id || "Chưa xác định";
    const ownerEmail = job.owner_email && job.owner_email !== ownerName ? job.owner_email : "";
    return `<article class="job-row${selected ? " selected" : ""}" data-job-row="${escapeHtml(job.job_id)}">
      <label class="job-select" title="Chọn job"><input type="checkbox" data-job-select="${escapeHtml(job.job_id)}" ${selected ? "checked" : ""} aria-label="Chọn ${escapeHtml(job.filename || job.job_id)}" /></label>
      <div class="job-file">
        <div class="job-file-icon video-icon-badge status-accent-${escapeHtml(job.status)}" aria-hidden="true">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
        </div>
        <div class="job-file-meta">
          <strong title="${escapeHtml(job.filename || job.job_id)}">${escapeHtml(job.filename || job.job_id)}</strong>
          <small>${batchBadge}${queue}</small>
        </div>
      </div>
      <div class="job-owner" title="${escapeHtml(job.owner_email || ownerName)}">
        <strong>${escapeHtml(ownerName)}</strong>
        ${ownerEmail ? `<small>${escapeHtml(ownerEmail)}</small>` : ""}
      </div>
      <span class="status-pill status-${escapeHtml(job.status)}"><span class="status-dot"></span><span>${statusLabels[job.status] || escapeHtml(job.status)}</span></span>
      <div class="job-progress"><div class="progress-track progress-${escapeHtml(job.status)}"><span style="width:${progress}%"></span></div><small>${progress}%</small></div>
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
  const queuedCount = (counts.queued || 0) + (counts.scheduled || 0);
  const processingCount = (counts.processing || 0) + (counts.cancelling || 0) + activeUploads;
  const completedCount = counts.completed || 0;
  const failedCount = (counts.failed || 0) + (counts.cancelled || 0) + failedUploads;

  const queuedEl = $("#queuedMetric");
  if (queuedEl) queuedEl.textContent = String(queuedCount);
  const processingEl = $("#processingMetric");
  if (processingEl) processingEl.textContent = String(processingCount);
  const completedEl = $("#completedMetric");
  if (completedEl) completedEl.textContent = String(completedCount);
  const failedEl = $("#failedMetric");
  if (failedEl) failedEl.textContent = String(failedCount);

  const runningCount = queuedCount + processingCount;
  const runningBadge = $("#runningBadge");
  if (runningBadge) {
    runningBadge.textContent = String(runningCount);
    runningBadge.classList.toggle("has-active", runningCount > 0);
    runningBadge.classList.toggle("is-zero", runningCount === 0);
  }
}

// Coalesce re-renders: several pushed updates in one frame cost one DOM pass.
function scheduleJobRender() {
  if (state.jobRenderFrame) return;
  state.jobRenderFrame = requestAnimationFrame(() => {
    state.jobRenderFrame = null;
    renderJobDashboard();
  });
}

function scheduleJobsReload() {
  clearTimeout(state.jobsReloadTimer);
  state.jobsReloadTimer = setTimeout(loadJobs, 300);
}

const jobEvents = createJobEvents({
  isActive: () => state.appStarted,
  poll: () => loadJobs(),
  onReconnected: () => loadJobs(),
  onChanges(message) {
    const { jobs, reload } = applyJobChanges(state.jobs, message, { statusFilter: $("#statusFilter").value });
    state.jobs = jobs;
    scheduleJobRender();
    if (reload) scheduleJobsReload();
  },
});

async function loadJobs() {
  if (!state.appStarted) return;
  // Never stack list requests on a slow server; run one more afterwards.
  if (state.jobsLoading) {
    state.jobsReloadQueued = true;
    return;
  }
  state.jobsLoading = true;
  const refreshButton = $("#refreshJobs");
  if (refreshButton) refreshButton.classList.add("is-refreshing");
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
  } finally {
    state.jobsLoading = false;
    if (refreshButton) {
      setTimeout(() => refreshButton.classList.remove("is-refreshing"), 400);
    }
    if (state.jobsReloadQueued) {
      state.jobsReloadQueued = false;
      scheduleJobsReload();
    }
  }
}

async function jobAction(action, jobId) {
  if (action === "tiktok-review") {
    location.hash = "tiktok";
    return tiktokWorkspace.openDraft(jobId);
  }
  if (action === "delete" && !confirm("Xóa job và toàn bộ file kết quả?")) return;
  if (action === "cancel" && !confirm("Yêu cầu dừng job này?")) return;
  const method = action === "delete" ? "DELETE" : "POST";
  const endpoint = action === "delete" ? `/api/v1/jobs/${jobId}` : action === "tiktok-status" ? `/api/v1/jobs/${jobId}/refresh-tiktok-status` : `/api/v1/jobs/${jobId}/${action}`;
  try {
    const response = await apiFetch(endpoint, { method });
    if (response.status === 401) lockApplication();
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Không thể ${action} job`);
    toast(action === "retry" ? `Đã chạy lại job ${data.job_id}` : action === "cancel" ? "Đã gửi yêu cầu hủy" : action === "tiktok-status" ? `TikTok: ${data.tiktok_publish_status}` : "Đã xóa job");
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

let authMode = "login";

function setAuthMode(mode) {
  authMode = mode === "register" ? "register" : "login";
  const registering = authMode === "register";
  $("#authLoginForm").classList.toggle("is-hidden", registering);
  $("#authRegisterForm").classList.toggle("is-hidden", !registering);
  $("#authTitle").textContent = registering ? "Đăng ký VidTrans" : "Đăng nhập VidTrans";
  $("#authDescription").textContent = registering
    ? "Tạo tài khoản miễn phí để bắt đầu xử lý video."
    : "Đăng nhập để truy cập pipeline, tiến trình và file video.";
  $("#authModeToggle").textContent = registering
    ? "Đã có tài khoản? Đăng nhập"
    : "Chưa có tài khoản? Đăng ký miễn phí";
  const message = $("#authMessage");
  message.textContent = registering
    ? "Tài khoản mới sẽ được kích hoạt với gói Free."
    : "OAuth2 Password + JWT · phiên đăng nhập bảo mật bằng cookie HttpOnly";
  message.classList.remove("error");
  setTimeout(() => (registering ? $("#registerDisplayName") : $("#authUsername")).focus(), 0);
}

$("#authModeToggle").addEventListener("click", () => setAuthMode(authMode === "login" ? "register" : "login"));

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

$("#authRegisterForm").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("#authRegisterSubmit");
  const message = $("#authMessage");
  const password = $("#registerPassword").value;
  if (password !== $("#registerPasswordConfirm").value) {
    message.textContent = "Mật khẩu nhập lại chưa khớp";
    message.classList.add("error");
    return;
  }
  button.disabled = true;
  message.textContent = "Đang tạo tài khoản Free…";
  message.classList.remove("error");
  try {
    const body = new FormData();
    body.set("display_name", $("#registerDisplayName").value.trim());
    body.set("email", $("#registerEmail").value.trim());
    body.set("password", password);
    await requestJson("/api/v1/auth/register", { method: "POST", body });
    form.reset();
    if (await refreshApplicationAuth()) {
      await startProtectedApplication();
      toast("Đăng ký thành công · Gói Free đã được kích hoạt");
    }
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  } finally {
    button.disabled = !state.auth.configured;
  }
});

$("#appLogoutButton").addEventListener("click", async () => {
  if (!state.auth.enabled) {
    $("#userMenu").classList.remove("open");
    $("#userMenuButton").setAttribute("aria-expanded", "false");
    toast("Không thể đăng xuất: hệ thống đang chạy chế độ local với VIDTRANS_AUTH_ENABLED=0.", true);
    return;
  }
  try {
    await requestJson("/api/v1/auth/session", { method: "DELETE" });
    $("#authPassword").value = "";
    lockApplication("Đã đăng xuất. Nhập tài khoản quản trị để tiếp tục.", false);
  } catch (error) {
    toast(error.message, true);
  }
});
$("#navToggle").addEventListener("click", () => {
  $("#userMenu").classList.remove("open");
  $("#userMenuButton").setAttribute("aria-expanded", "false");
  const open = $("#mainNavigation").classList.toggle("open");
  $("#navToggle").setAttribute("aria-expanded", String(open));
});
$("#userMenuButton").addEventListener("click", () => {
  $("#mainNavigation").classList.remove("open");
  $("#navToggle").setAttribute("aria-expanded", "false");
  const open = $("#userMenu").classList.toggle("open");
  $("#userMenuButton").setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", event => {
  if (event.target.closest("#userMenu a") || !$("#userMenu").contains(event.target)) {
    $("#userMenu").classList.remove("open");
    $("#userMenuButton").setAttribute("aria-expanded", "false");
  }
  if (event.target.closest("#mainNavigation a") || (!$("#mainNavigation").contains(event.target) && !$("#navToggle").contains(event.target))) {
    $("#mainNavigation").classList.remove("open");
    $("#navToggle").setAttribute("aria-expanded", "false");
  }
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  $("#userMenu").classList.remove("open");
  $("#mainNavigation").classList.remove("open");
  $("#userMenuButton").setAttribute("aria-expanded", "false");
  $("#navToggle").setAttribute("aria-expanded", "false");
});
$("#paymentForm").addEventListener("submit", createPayment);
$("#paymentPlan").addEventListener("change", event => selectBillingPlan(event.target.value, { scroll: false }));
$("#billingPlans").addEventListener("keydown", event => {
  if (!event.target.matches("[data-plan-card]") || !["Enter", " "].includes(event.key)) return;
  event.preventDefault();
  selectBillingPlan(event.target.dataset.planCard);
});

$("#billingView").addEventListener("click", async event => {
  // 1. Copy to clipboard
  const copyBtn = event.target.closest("[data-copy]");
  if (copyBtn) {
    try {
      await navigator.clipboard.writeText(copyBtn.dataset.copy);
      toast("Đã sao chép: " + copyBtn.dataset.copy);
    } catch {
      toast(copyBtn.dataset.copy);
    }
    return;
  }

  // 2. Select plan from pricing cards
  const planTarget = event.target.closest("[data-select-plan], [data-plan-card]");
  if (planTarget) {
    const planKey = planTarget.dataset.selectPlan || planTarget.dataset.planCard;
    const bankRadio = $(`#paymentForm [name="provider"][value="bank"]`);
    if (bankRadio && !bankRadio.disabled) bankRadio.checked = true;
    selectBillingPlan(planKey);
    return;
  }

  // 3. Switch to Free plan
  const switchFreeBtn = event.target.closest("[data-switch-free]");
  if (switchFreeBtn) {
    if (!confirm("Bạn muốn chuyển về gói Free (30 phút/tháng)?")) return;
    switchFreeBtn.disabled = true;
    try {
      await requestJson("/api/v1/billing/switch-free", { method: "POST" });
      toast("Đã chuyển về gói Free thành công");
      await loadBilling();
      await refreshApplicationAuth();
    } catch (err) {
      toast(err.message, true);
    } finally {
      switchFreeBtn.disabled = false;
    }
    return;
  }

  // 4. Confirm payment order & activate plan
  const confirmBtn = event.target.closest("[data-confirm-order]");
  if (confirmBtn) {
    const orderId = confirmBtn.dataset.confirmOrder;
    confirmBtn.disabled = true;
    try {
      await requestJson(`/api/v1/billing/orders/${orderId}/confirm`, { method: "POST" });
      toast("Đã xác nhận thanh toán & kích hoạt gói thành công!");
      $("#paymentInstructions").classList.add("is-hidden");
      await loadBilling();
      await refreshApplicationAuth();
    } catch (err) {
      toast(err.message, true);
    } finally {
      confirmBtn.disabled = false;
    }
    return;
  }

  // 5. Cancel payment order
  const cancelBtn = event.target.closest("[data-cancel-order]");
  if (cancelBtn) {
    const orderId = cancelBtn.dataset.cancelOrder;
    if (!confirm("Bạn muốn hủy yêu cầu thanh toán này?")) return;
    cancelBtn.disabled = true;
    try {
      await requestJson(`/api/v1/billing/orders/${orderId}/cancel`, { method: "POST" });
      toast("Đã hủy yêu cầu thanh toán");
      $("#paymentInstructions").classList.add("is-hidden");
      await loadBilling();
    } catch (err) {
      toast(err.message, true);
    } finally {
      cancelBtn.disabled = false;
    }
    return;
  }

  // 6. View QR instructions for an existing order
  const viewOrderBtn = event.target.closest("[data-view-order]");
  if (viewOrderBtn) {
    const orderId = viewOrderBtn.dataset.viewOrder;
    viewOrderBtn.disabled = true;
    try {
      const order = await requestJson(`/api/v1/billing/orders/${orderId}`);
      if (order.bank) {
        renderPaymentInstructions(order);
      } else {
        toast("Đơn này không sử dụng chuyển khoản VietQR");
      }
    } catch (err) {
      toast(err.message, true);
    } finally {
      viewOrderBtn.disabled = false;
    }
    return;
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
$$('[data-source-browser-tab]').forEach(button => button.addEventListener("click", () => selectSourceBrowserTab(button.dataset.sourceBrowserTab)));
for (const platform of ["youtube", "facebook"]) {
  $(`#${platform}BrowserSync`).addEventListener("click", () => syncSourceBrowser(platform));
  $(`#${platform}BrowserReload`).addEventListener("click", () => reloadSourceBrowser(platform));
  $(`#${platform}BrowserLogout`).addEventListener("click", () => logoutSourceBrowser(platform));
  $(`#${platform}BrowserFrame`).addEventListener("load", () => $(`#${platform}BrowserLoading`).classList.add("is-hidden"));
}
$("#douyinLogoutButton").addEventListener("click", async () => {
  try {
    await requestJson("/api/v1/douyin-auth", { method: "DELETE" });
    await refreshDouyinAuthStatus();
    toast("Đã xóa phiên đăng nhập Douyin");
  } catch (error) { toast(error.message, true); }
});
[$("#mode"), $("#sourceLanguage"), $("#targetLanguage"), $("#subtitleSource"), document.querySelector('[name="voice_mode"]')].forEach(element => element.addEventListener("change", updateFormSummary));
[$("#placementMode"), $("#matchSourceSize"), $("#minFontSize"), $("#maxFontSize"), $("#positionGap"), $("#maskOriginal")].forEach(element => element.addEventListener("change", updateFormSummary));
$("#refreshJobs").addEventListener("click", loadJobs);
$("#refreshAdminAccounts").addEventListener("click", loadAdminAccounts);
$("#adminApprovalFilter").addEventListener("change", loadAdminAccounts);
$("#adminUserSearch").addEventListener("input", () => { clearTimeout(state.adminSearchTimer); state.adminSearchTimer = setTimeout(loadAdminAccounts, 300); });
$("#adminCreateAccountForm").addEventListener("submit", createAdminAccount);
$("#adminAccountsList").addEventListener("click", event => {
  const button = event.target.closest("[data-admin-save]");
  if (button) saveAdminAccount(button.closest("[data-admin-account]"), button);
  const deleteBtn = event.target.closest("[data-admin-delete]");
  if (deleteBtn) deleteAdminAccount(deleteBtn.dataset.adminDelete);
});
$$("[data-admin-subtab]").forEach(tabBtn => {
  tabBtn.addEventListener("click", () => {
    const tabName = tabBtn.dataset.adminSubtab;
    $$("[data-admin-subtab]").forEach(btn => btn.classList.toggle("active", btn === tabBtn));
    $$(".admin-subtab-pane").forEach(pane => pane.classList.add("is-hidden"));
    if (tabName === "accounts") {
      $("#adminSubtabAccounts")?.classList.remove("is-hidden");
      loadAdminAccounts();
    } else if (tabName === "jobs") {
      $("#adminSubtabJobs")?.classList.remove("is-hidden");
      loadAdminJobs();
    } else if (tabName === "audit") {
      $("#adminSubtabAudit")?.classList.remove("is-hidden");
      loadAdminAudit();
    }
  });
});
$("#refreshAdminJobs")?.addEventListener("click", loadAdminJobs);
$("#adminJobUserFilter")?.addEventListener("change", loadAdminJobs);
$("#adminJobStatusFilter")?.addEventListener("change", loadAdminJobs);
$("#adminJobSearch")?.addEventListener("input", () => {
  clearTimeout(state.adminJobSearchTimer);
  state.adminJobSearchTimer = setTimeout(loadAdminJobs, 300);
});
$("#adminJobsList")?.addEventListener("click", async event => {
  const viewBtn = event.target.closest("[data-admin-view-job]");
  if (viewBtn) {
    const jobId = viewBtn.dataset.adminViewJob;
    try {
      const job = await requestJson(`/api/v1/admin/jobs/${jobId}`);
      alert(`[Audit Logged] Chi tiết tiến trình ${jobId}:\nNgười dùng: ${job.owner_email || job.owner_user_id}\nTrạng thái: ${job.status}\nTiến độ: ${Math.round((job.progress || 0) * 100)}%\nTạo lúc: ${job.created_at}`);
    } catch (err) { toast(err.message, true); }
  }
});
$("#refreshAdminAudit")?.addEventListener("click", loadAdminAudit);
$("#adminAuditActionFilter")?.addEventListener("change", loadAdminAudit);
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
  const download = event.target.closest("[data-download-url]");
  if (download) {
    saveDownload(download.dataset.downloadUrl, download.dataset.downloadName, download);
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
const tiktokWorkspace = createTikTokWorkspace({ requestJson, toast });
initWorkflowModeCards();
updateFormSummary();
renderFiles();
showView();
bindWorkflowStepNav();
const oauthParams = new URLSearchParams(location.search);
if (oauthParams.get("tiktok") === "connected") toast("Đã kết nối tài khoản TikTok thành công");
if (oauthParams.get("tiktok_error")) toast(oauthParams.get("tiktok_error"), true);
if (oauthParams.get("instagram") === "connected") toast("Đã kết nối tài khoản Instagram thành công");
if (oauthParams.get("instagram_error")) toast(oauthParams.get("instagram_error"), true);
if (oauthParams.get("threads") === "connected") toast("Đã kết nối tài khoản Threads thành công");
if (oauthParams.get("threads_error")) toast(oauthParams.get("threads_error"), true);
if (oauthParams.get("facebook") === "connected") toast("Đã kết nối Facebook Page thành công");
if (oauthParams.get("facebook_error")) toast(oauthParams.get("facebook_error"), true);
if (oauthParams.get("youtube") === "connected") toast("Đã kết nối YouTube thành công");
if (oauthParams.get("youtube_error")) toast(oauthParams.get("youtube_error"), true);
if (["tiktok", "tiktok_error", "instagram", "instagram_error", "threads", "threads_error", "facebook", "facebook_error", "youtube", "youtube_error"].some(key => oauthParams.has(key))) {
  history.replaceState(null, "", `${location.pathname}${location.hash || "#create"}`);
}
bootstrapApplication();
