import { createTikTokWorkspace } from "./tiktok.js?v=20261105-staff-browser";
import { resolveDouyinFrameUrl, resolveSourceBrowserFrameUrl } from "./browser-frame.js?v=20261104-shared-source";
import { applyJobChanges, createJobEvents, createLiveEvents } from "./job-events.js?v=20261105-notifications";
import { createPlatformAppsSettings } from "./platform-apps.js?v=20261018-1";
import { createComicWorkspace } from "./comic.js?v=20261104-plan-hide";
import { createClipWorkspace } from "./clips.js?v=20261105-clips-ai";
import { createBrandingWorkspace } from "./branding.js?v=20261031-apply-logo";
import { createAccessConfig } from "./access-config.js?v=20261104-plan-minutes";
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// Associate requests started by a user interaction (click, submit, file/select change,
// or after a confirm/prompt dialog) with its button, and update topbar busy state.
let apiActionTrigger = null;
let apiActionTimer = null;
const apiActionRequests = new WeakMap();
let activeGlobalApiCount = 0;
let globalApiStartedAt = 0;
let globalApiHideTimer = null;

function resolveActionTrigger(element) {
  if (!element || typeof element.closest !== "function") return null;
  if (element.matches?.('input[type="file"]')) {
    return element.closest("label.button, .button, label") || element;
  }
  const candidate = element.closest('button, .button, [role="button"], input[type="submit"], input[type="button"]');
  if (!candidate) return null;
  if (candidate.tagName === "A") {
    const href = candidate.getAttribute("href") || "";
    if (href.startsWith("#") || candidate.hasAttribute("data-view-link")) return null;
  }
  if (candidate.id === "userMenuButton" || candidate.id === "navToggle") return null;
  return candidate;
}

function rememberApiAction(element) {
  const trigger = resolveActionTrigger(element);
  if (!trigger) return;
  apiActionTrigger = trigger;
  clearTimeout(apiActionTimer);
  apiActionTimer = setTimeout(() => {
    if (apiActionTrigger === trigger) apiActionTrigger = null;
  }, 80);
}

const nativeConfirm = window.confirm.bind(window);
window.confirm = message => {
  const savedTrigger = apiActionTrigger;
  const result = nativeConfirm(message);
  if (result && savedTrigger) rememberApiAction(savedTrigger);
  else apiActionTrigger = null;
  return result;
};

const nativePrompt = window.prompt.bind(window);
window.prompt = (message, defaultValue) => {
  const savedTrigger = apiActionTrigger;
  const result = nativePrompt(message, defaultValue);
  if (result !== null && savedTrigger) rememberApiAction(savedTrigger);
  else apiActionTrigger = null;
  return result;
};

document.addEventListener("click", event => rememberApiAction(event.target), true);
document.addEventListener("submit", event => {
  rememberApiAction(event.submitter || event.target.querySelector('button[type="submit"], input[type="submit"], .button'));
}, true);
document.addEventListener("change", event => {
  if (event.target?.matches?.('input[type="file"], select')) rememberApiAction(event.target);
}, true);

function beginGlobalApiBusy(silent = false) {
  if (silent) return () => {};
  clearTimeout(globalApiHideTimer);
  if (activeGlobalApiCount === 0) globalApiStartedAt = performance.now();
  activeGlobalApiCount += 1;
  const topbar = $(".topbar");
  const systemState = $("#systemState");
  const systemText = $("#systemState .system-text");
  topbar?.classList.add("is-api-busy");
  if (systemState) {
    systemState.classList.add("is-busy");
    systemState.title = "Đang gọi API…";
  }
  if (systemText) systemText.textContent = "Đang xử lý…";
  let ended = false;
  return () => {
    if (ended) return;
    ended = true;
    activeGlobalApiCount = Math.max(0, activeGlobalApiCount - 1);
    if (activeGlobalApiCount > 0) return;
    const delay = Math.max(0, 380 - (performance.now() - globalApiStartedAt));
    globalApiHideTimer = setTimeout(() => {
      if (activeGlobalApiCount > 0) return;
      topbar?.classList.remove("is-api-busy");
      if (systemState) {
        systemState.classList.remove("is-busy");
        systemState.title = "Hệ thống sẵn sàng";
      }
      if (systemText) systemText.textContent = "Hệ thống sẵn sàng";
    }, delay);
  };
}

function beginElementLoading(trigger) {
  if (!trigger) return () => {};
  const current = apiActionRequests.get(trigger);
  const action = current || { count: 0, startedAt: performance.now() };
  action.count += 1;
  apiActionRequests.set(trigger, action);
  trigger.classList.add("is-api-loading");
  trigger.setAttribute("aria-busy", "true");
  let ended = false;
  return () => {
    if (ended) return;
    ended = true;
    const active = apiActionRequests.get(trigger);
    if (!active) return;
    active.count = Math.max(0, active.count - 1);
    // Keep trigger available across chained `await requestJson(...)` in the same handler.
    rememberApiAction(trigger);
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

function beginApiActionLoading(silent = false) {
  const endElement = beginElementLoading(apiActionTrigger);
  const endGlobal = beginGlobalApiBusy(silent);
  return () => {
    endElement();
    endGlobal();
  };
}

const JOB_PAGE_SIZES = [10, 20, 50, 100];

// A blocked localStorage (private mode) or an old/unknown value must not break the page.
function storedJobsPageSize() {
  try {
    const size = parseInt(localStorage.getItem("vidtrans_jobs_page_size") || "10", 10);
    return JOB_PAGE_SIZES.includes(size) ? size : 10;
  } catch {
    return 10;
  }
}

const state = {
  files: [], links: [], invalidLinks: [], blockedPlatforms: new Set(), jobs: [], pendingUploads: [], selectedJobIds: new Set(), pollTimer: null, searchTimer: null,
  jobsPage: 1, jobsPageSize: storedJobsPageSize(),
  tiktokAuth: null, appStarted: false,
  douyinBrowser: null, douyinBrowserPollTimer: null, douyinBrowserLoaded: false, sessionRefreshTimer: null,
  sourceBrowserPlatform: "douyin", sourceBrowsers: {},
  adminAccounts: [], adminAccountSummary: null, adminRetentionDefaultDays: null, adminSearchTimer: null,
  billing: null, userLogoUrl: null,
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
  comic_storyboard: "Đang chia cảnh truyện", comic_references: "Đang vẽ nhân vật tham chiếu", comic_images: "Đang vẽ minh họa",
  comic_voice: "Đang lồng tiếng truyện", comic_clips: "Đang dựng clip cảnh", comic_export: "Đang xuất video truyện",
  storyboard_ready: "Storyboard chờ duyệt",
  "photo-choice": "Bài đăng ảnh: chờ bạn chọn", "packing-images": "Đang gom ảnh để tải về",
  "rendering-slideshow": "Đang dựng video ghép ảnh",
};
const statusLabels = { queued: "Đang chờ", scheduled: "Đã đặt lịch", processing: "Đang chạy", completed: "Hoàn tất", failed: "Thất bại", cancelled: "Đã hủy", cancelling: "Đang hủy", awaiting_input: "Chờ bạn chọn" };

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

let pendingSessionRefresh = null;

// Exchange the HttpOnly refresh cookie for a new access token. Concurrent
// callers share one request so a burst of 401s triggers a single refresh.
function refreshSession() {
  if (!pendingSessionRefresh) {
    pendingSessionRefresh = fetch("/api/v1/auth/refresh", {
      method: "POST",
      headers: { "X-VidTrans-Request": "1" },
      credentials: "same-origin",
    })
      .then(response => response.ok)
      .catch(() => false)
      .finally(() => { pendingSessionRefresh = null; });
  }
  return pendingSessionRefresh;
}

function scheduleSessionRefresh(ttlMinutes) {
  clearInterval(state.sessionRefreshTimer);
  state.sessionRefreshTimer = null;
  if (!ttlMinutes) return;
  // Renew two minutes before the access token expires.
  const intervalMs = Math.max(60, ttlMinutes * 60 - 120) * 1000;
  state.sessionRefreshTimer = setInterval(refreshSession, intervalMs);
}

async function apiFetch(url, options = {}) {
  const { silent = false, ...fetchOptions } = options;
  const method = String(fetchOptions.method || "GET").toUpperCase();
  const isBackgroundPoll = silent || (!apiActionTrigger && method === "GET" && state.appStarted && (
    url.startsWith("/api/v1/jobs?") ||
    url.startsWith("/api/v1/douyin-auth/browser") ||
    url.startsWith("/api/v1/source-auth/")
  ));
  const send = () => {
    const headers = new Headers(fetchOptions.headers || {});
    headers.set("X-VidTrans-Request", "1");
    return fetch(url, { ...fetchOptions, headers, credentials: "same-origin" });
  };
  const endLoading = beginApiActionLoading(isBackgroundPoll);
  try {
    let response = await send();
    if (response.status === 401 && !url.startsWith("/api/v1/auth/") && await refreshSession()) {
      response = await send();
    }
    return response;
  } finally {
    endLoading();
  }
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
  clearInterval(browserPowerTimer);
  browserPowerTimer = null;
  browserPowerName = null;
  systemEvents.stop();
  clearInterval(state.pollTimer);
  clearInterval(state.douyinBrowserPollTimer);
  clearInterval(state.sessionRefreshTimer);
  state.pollTimer = null;
  state.douyinBrowserPollTimer = null;
  state.sessionRefreshTimer = null;
  state.appStarted = false;
  state.auth.authenticated = false;
  state.auth.role = "viewer";
  // Never leave the previous account's data rendered behind the login gate.
  // This also prevents a brief stale-data flash when another user signs in.
  state.jobs = [];
  seenJobStatuses.clear();
  state.pendingUploads = [];
  state.selectedJobIds.clear();
  state.files = [];
  state.links = [];
  state.blockedPlatforms = new Set();
  state.userLogoUrl = null;
  applyPermissions();
  const jobsBody = $("#jobsBody");
  if (jobsBody) renderJobs([]);
  $("#userMenu").classList.add("is-hidden");
  $("#authGate").classList.remove("is-hidden");
  $("#authLoginForm").reset();
  $("#authRegisterForm").reset();
  $("#authResetRequestForm").reset();
  $("#authResetConfirmForm").reset();
  passwordResetEmail = "";
  closeTermsDialog();
  setAuthMode("login", message);
  const authMessage = $("#authMessage");
  authMessage.classList.toggle("error", isError);
}

async function requestJson(url, options = {}) {
  const response = await apiFetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401 && !url.startsWith("/api/v1/auth/")) lockApplication();
  if (!response.ok) throw new Error(errorDetail(payload.detail) || "Yêu cầu thất bại");
  return payload;
}

const FIELD_LABELS = {
  title: "Tên truyện", description: "Giới thiệu", text: "Nội dung truyện", episode_title: "Tên tập",
  narration: "Lời kể", visual_prompt: "Prompt hình ảnh", appearance: "Ngoại hình", name: "Tên",
};

// FastAPI validation errors arrive as a list of {loc, msg, type, ctx}; turn them into readable Vietnamese.
function errorDetail(detail) {
  if (!Array.isArray(detail)) return typeof detail === "string" ? detail : "";
  return detail.map(item => {
    const field = [...(item.loc || [])].reverse().find(part => typeof part === "string" && !["body", "query", "path"].includes(part));
    const label = FIELD_LABELS[field] || field || "Dữ liệu";
    const ctx = item.ctx || {};
    if (item.type === "string_too_long") return `${label}: tối đa ${ctx.max_length} ký tự`;
    if (item.type === "string_too_short") return `${label}: cần ít nhất ${ctx.min_length} ký tự`;
    if (item.type === "missing") return `${label}: bắt buộc nhập`;
    return `${label}: ${item.msg}`;
  }).join("; ");
}

async function refreshApplicationAuth() {
  let status = await requestJson("/api/v1/auth/status");
  if (status.configured && !status.authenticated && await refreshSession()) {
    status = await requestJson("/api/v1/auth/status");
  }
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
    $("#authGuestButton").disabled = true;
    return false;
  }
  $("#authSubmit").disabled = false;
  $("#authRegisterSubmit").disabled = false;
  $("#authGuestButton").disabled = false;
  if (!status.authenticated) {
    lockApplication("Nhập tài khoản quản trị để tiếp tục.", false);
    return false;
  }
  scheduleSessionRefresh(status.token_ttl_minutes);
  $("#authGate").classList.add("is-hidden");
  applyPermissions();
  return true;
}

function roleLabel(role) {
  return ({ owner: "Chủ sở hữu", admin: "Quản trị viên", editor: "Biên tập viên", viewer: "Người xem" })[role] || "Thành viên";
}

// "Chèn logo" in the translation and comic forms needs an account logo. It is ticked
// when a logo becomes available; after that the user's own choice is kept.
function syncLogoOptions() {
  const hasLogo = Boolean(state.userLogoUrl);
  $$('input[name="apply_logo"]').forEach(input => {
    // defaultChecked too, so form.reset() after a submit keeps the logo ticked.
    if (!hasLogo) input.checked = input.defaultChecked = false;
    else if (input.disabled) input.checked = input.defaultChecked = true;
    input.disabled = !hasLogo;
  });
  $$("[data-logo-hint]").forEach(hint => hint.classList.toggle("is-hidden", hasLogo));
}

function updateUserAvatar(logoUrl = state.userLogoUrl) {
  const changed = (logoUrl || null) !== state.userLogoUrl;
  state.userLogoUrl = logoUrl || null;
  if (changed && state.jobs?.length && $("#jobsBody")) renderJobs(state.jobs);
  syncLogoOptions();
  const avatar = $("#userAvatar");
  if (!avatar) return;
  if (state.userLogoUrl) {
    avatar.classList.add("has-logo");
    avatar.innerHTML = `<img src="${escapeHtml(state.userLogoUrl)}" alt="Logo thương hiệu" />`;
  } else {
    avatar.classList.remove("has-logo");
    avatar.textContent = (state.auth.username || "U").charAt(0).toUpperCase();
  }
}

// Main navigation from the admin-configured app_menus table (GET /api/v1/me/menus).
// Until it loads, or if it fails, the role rules in the markup stay in force.
let navMenuRequest = 0;
state.navAllowedViews = null;

function applyNavMenus(items) {
  const nav = $("#mainNavigation");
  if (!nav) return;
  const allowed = new Set(items.map(item => item.key));
  state.navAllowedViews = allowed;
  const links = $$("a[data-view-link]", nav);
  links.forEach(link => {
    const visible = allowed.has(link.dataset.viewLink);
    link.hidden = !visible;
    link.classList.toggle("is-hidden", !visible);
  });
  items.forEach(item => {
    const link = links.find(candidate => candidate.dataset.viewLink === item.key);
    if (!link) return;
    const full = $(".nav-label-full", link);
    const short = $(".nav-label-short", link);
    if (full && short) {
      full.textContent = item.label;
      short.textContent = item.short_label || item.label;
    } else {
      const label = $(":scope > span", link);
      if (label) label.textContent = item.label;
    }
    link.title = item.label;
    nav.appendChild(link); // re-append in configured order
  });
}

async function loadNavMenus() {
  const request = ++navMenuRequest;
  try {
    const payload = await requestJson("/api/v1/me/menus?platform=web");
    if (request !== navMenuRequest || !state.auth.authenticated) return;
    applyNavMenus(payload.items || []);
    showView(); // leave a view the new menu no longer offers
  } catch (error) {
    console.warn("Không tải được cấu hình menu", error);
  }
}

function applyPermissions() {
  const role = state.auth.role || "viewer";
  const isAdmin = ["owner", "admin"].includes(role);
  // Start from the markup's role rules; the configured menu is applied on top once loaded.
  state.navAllowedViews = null;
  $$("#mainNavigation a[data-view-link]:not([data-admin-only])").forEach(link => {
    link.hidden = false;
    link.classList.remove("is-hidden");
  });
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
  updateUserAvatar(state.userLogoUrl);
  $("#appLogoutButton").classList.remove("is-hidden");
  $("#appLogoutButton").title = state.auth.enabled
    ? "Đăng xuất khỏi VidTrans"
    : "Xác thực đang tắt trong cấu hình môi trường local";
  $("#userMenu").classList.remove("is-hidden");
  if (state.auth.authenticated) loadNavMenus();
}

async function refreshUserBrandingAvatar() {
  if (state.auth.role === "viewer") return;
  try {
    const data = await requestJson("/api/v1/me/branding", { silent: true });
    updateUserAvatar(data?.logo?.url || null);
  } catch (_) {}
}

// Accounts that never accepted, or accepted an older /terms revision, must accept before
// working. The dialog is modal and cannot be dismissed except by accepting or signing out.
async function ensureTermsConsent() {
  if (!state.auth.enabled) return;
  try {
    const account = await requestJson("/api/v1/me");
    if (account.terms_outdated) openTermsDialog();
  } catch {
    // A failed check must not block the app; it is asked again on the next sign-in.
  }
}

function openTermsDialog() {
  const dialog = $("#termsDialog");
  if (!dialog || dialog.open) return;
  $("#termsDialogCheck").checked = false;
  $("#termsDialogAccept").disabled = true;
  $("#termsDialogError").textContent = "";
  dialog.showModal();
}

function closeTermsDialog() {
  const dialog = $("#termsDialog");
  if (dialog?.open) dialog.close();
}

$("#termsDialog")?.addEventListener("cancel", event => event.preventDefault());
$("#termsDialogCheck")?.addEventListener("change", event => {
  $("#termsDialogAccept").disabled = !event.currentTarget.checked;
});
$("#termsDialogAccept")?.addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await requestJson("/api/v1/me/terms/accept", { method: "POST" });
    closeTermsDialog();
    toast("Cảm ơn bạn đã đồng ý điều khoản");
  } catch (error) {
    $("#termsDialogError").textContent = error.message;
    button.disabled = false;
  }
});
$("#termsDialogLogout")?.addEventListener("click", () => {
  closeTermsDialog();
  $("#appLogoutButton").click();
});

async function startProtectedApplication() {
  if (state.appStarted) return;
  state.appStarted = true;
  ensureTermsConsent();
  // Open the live socket right away: waiting for the other start-up requests
  // left job statuses frozen whenever one of them was slow.
  clearInterval(state.pollTimer);
  state.pollTimer = null;
  jobEvents.start();
  const canManageSources = ["owner", "admin"].includes(state.auth.role);
  await Promise.all([
    loadDownloadPolicy(),
    loadJobs(),
    canManageSources ? refreshDouyinAuthStatus() : Promise.resolve(),
    tiktokWorkspace.refresh(),
    refreshUserBrandingAvatar(),
    loadMusicLibrary(),
  ]);
  // The first showView ran before the role was known, so role-gated views such as
  // #admin fell back to another view while the hash stayed put; clicking the same
  // link then fires no hashchange. Re-render now that the role is known.
  showView();
}

// Which source platforms the plan allows; links from the others are flagged as they are pasted.
async function loadDownloadPolicy() {
  try {
    const policy = await requestJson("/api/v1/download/policy", { silent: true });
    state.blockedPlatforms = new Set(Object.entries(policy.platforms || {})
      .filter(([, rule]) => rule && rule.allowed === false)
      .map(([platform]) => platform));
  } catch (_) {
    state.blockedPlatforms = new Set();  // The server still refuses blocked links.
  }
  if ($("#sourceLinks")?.value.trim()) renderSourceLinks();
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

const sourceBrowserLabels = { youtube: "YouTube", facebook: "Facebook", xiaohongshu: "Xiaohongshu", qidian: "Qidian" };

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

function syncMobileQuickBar() {
  const count = state.files.length + state.links.length;
  const mode = $("#mode")?.value || "1";
  const bar = $("#mobileQuickSubmitBar");
  if (bar) {
    bar.classList.toggle("is-active", count > 0);
  }
  const badge = $("#mobileQuickCountBadge");
  if (badge) {
    badge.textContent = `${count}/50`;
    badge.classList.toggle("has-sources", count > 0);
  }
  const quickMode = $("#mobileQuickMode");
  if (quickMode) quickMode.textContent = $("#modeSummary")?.textContent || "Phụ đề";
  const quickSub = $("#mobileQuickSub");
  if (quickSub) {
    quickSub.textContent = mode === "4"
      ? "Chỉ tải video gốc"
      : ($("#languageSummary")?.textContent || "Trung → Việt");
  }
  const quickSubmitText = $("#mobileQuickSubmitText");
  if (quickSubmitText) {
    if (mode === "4") {
      quickSubmitText.textContent = count > 0 ? `Tải ngay (${count})` : "Tải ngay";
    } else {
      quickSubmitText.textContent = count > 0 ? `Bắt đầu (${count})` : "Bắt đầu";
    }
  }
  const desktopSubmitSpan = $("#submitButton span");
  if (desktopSubmitSpan) {
    if (mode === "4") {
      desktopSubmitSpan.textContent = count > 0 ? `Bắt đầu tải (${count} video)` : "Bắt đầu tải video";
    } else {
      desktopSubmitSpan.textContent = count > 0 ? `Bắt đầu xử lý (${count} video)` : "Bắt đầu xử lý";
    }
  }
}

function renderFiles() {
  $("#fileCount").textContent = String(state.files.length + state.links.length);
  syncMobileQuickBar();
  const clearAllHeader = state.files.length > 1
    ? `<div class="source-list-toolbar"><span>Đã chọn ${state.files.length} file video</span><button class="button secondary mini-btn cancel-btn" type="button" data-clear-files="true">✕ Xóa hết file</button></div>`
    : "";
  $("#fileList").innerHTML = clearAllHeader + state.files.map((file, index) => `
    <div class="file-item">
      <span class="file-item-icon">VID</span>
      <div><strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong><small>${formatBytes(file.size)}</small></div>
      <button class="file-remove" type="button" data-file-index="${index}" aria-label="Xóa ${escapeHtml(file.name)}">✕</button>
    </div>`).join("");
}

// Mirrors social_video_download_issue() in backend/infrastructure/social_video_downloader.py:
// a link is accepted only when a downloader can fetch it, not merely when its domain is known.
const SOCIAL_PLATFORM_DOMAINS = {
  Douyin: ["douyin.com", "iesdouyin.com"],
  TikTok: ["tiktok.com"],
  YouTube: ["youtube.com", "youtu.be", "youtube-nocookie.com"],
  Facebook: ["facebook.com", "fb.com", "fb.watch"],
  Xiaohongshu: ["xiaohongshu.com", "xhslink.com"],
  "Google Drive": ["drive.google.com", "docs.google.com", "drive.usercontent.google.com"],
};
const DRIVE_ID = "[\\w-]{10,}";
const DRIVE_FILE_PATH = new RegExp(`^/(?:a/[^/]+/)?file/(?:u/\\d+/)?d/(${DRIVE_ID})(?:/[\\w-]*)?/?$`);
const DRIVE_QUERY_PATHS = new Set(["/open", "/uc", "/download", "/u/0/uc", "/u/1/uc", "/u/0/open", "/u/1/open"]);
const DRIVE_FOLDER_PATH = /\/(?:drive\/)?(?:u\/\d+\/)?folders\//;
const GOOGLE_EDITOR_PATH = /^\/(?:document|spreadsheets|presentation|forms|drawings)\//;

/** Drive file id from any share/open/download form (google_drive_file_id() in the backend). */
function googleDriveFileId(url) {
  const path = url.pathname.replace(/\/{2,}/g, "/") || "/";
  const match = path.match(DRIVE_FILE_PATH);
  if (match) return match[1];
  const id = (url.searchParams.get("id") || "").trim();
  return DRIVE_QUERY_PATHS.has(path.replace(/\/$/, "")) && new RegExp(`^${DRIVE_ID}$`).test(id) ? id : null;
}

/** Same canonical link as canonical_google_drive_url(), so duplicates collapse before submit. */
function canonicalGoogleDriveUrl(url) {
  const id = googleDriveFileId(url);
  if (!id) return url.toString();
  const key = (url.searchParams.get("resourcekey") || "").trim();
  return `https://drive.google.com/file/d/${id}/view${key ? `?resourcekey=${encodeURIComponent(key)}` : ""}`;
}

const SHORT_LINK_HOSTS = new Set(["vm.tiktok.com", "vt.tiktok.com", "v.douyin.com", "fb.watch", "youtu.be", "xhslink.com"]);
// xiaohongshu_note_url() in the backend: a note id is 24 hex characters.
const XIAOHONGSHU_NOTE_PATH = /^\/(?:explore|discovery\/item|user\/profile\/[0-9a-zA-Z]+)\/[0-9a-f]{24}\/?$/;
const TIKTOK_VIDEO_PATH = /^\/(?:@[^/]+\/(?:video|photo)\/\d+|t\/[\w-]+|v\/\d+(?:\.html)?|embed(?:\/v2)?\/\d+|player\/v1\/\d+|share\/video\/\d+)\/?$/;
const YOUTUBE_ID_PATH = /^\/(?:shorts|embed|live|v)\/[\w-]{3,}\/?$/;
// youtube_post_id() in the backend: a community post with images becomes a slideshow.
const YOUTUBE_POST_PATH = /^\/post\/[\w-]{10,}\/?$/;
const YOUTUBE_COMMUNITY_PATH = /^\/(?:@[^/]+|channel\/[\w-]+|c\/[^/]+|user\/[^/]+)\/(?:community|posts)\/?$/;
const FACEBOOK_VIDEO_PATH = /^\/(?:reels?\/[\w-]+|watch\/?|video\.php|story\.php|permalink\.php|photo\/?|photo\.php|(?:[^/]+\/)?photos\/(?:[^/]+\/)*\d+|share\/(?:v\/|r\/|p\/)?[\w-]+|(?:[^/]+\/)?videos\/(?:[^/]+\/)?\d+|[^/]+\/posts\/[\w-]+|groups\/[^/]+\/(?:posts|permalink)\/\d+)\/?$/;
const FACEBOOK_QUERY_KEYS = { "/watch": "v", "/watch/": "v", "/video.php": "v", "/story.php": "story_fbid", "/permalink.php": "story_fbid", "/photo": "fbid", "/photo/": "fbid", "/photo.php": "fbid" };
const DOUYIN_PATH_ID = /\/(?:share\/)?(video|note|slides)\/(\d+)/;
const SUPPORTED_LINK_FORMS_HINT = "Chỉ nhận link một video hoặc bài đăng ảnh: TikTok (/@user/video/…, /@user/photo/…, vm.tiktok.com), Douyin (/video/…, /note/…, modal_id=…, v.douyin.com), YouTube (watch?v=…, shorts, youtu.be, bài đăng /post/…), Facebook (reel, videos, watch?v=…, share, posts, photo, fb.watch), Xiaohongshu (xhslink.com, /explore/…, /discovery/item/…), Google Drive (drive.google.com/file/d/…, open?id=…; file video hoặc ảnh chia sẻ “Bất kỳ ai có đường liên kết”).";

function socialPlatformOf(hostname) {
  for (const [platform, domains] of Object.entries(SOCIAL_PLATFORM_DOMAINS)) {
    if (domains.some(domain => hostname === domain || hostname.endsWith(`.${domain}`))) return platform;
  }
  return null;
}

function sourceLinkDownloadIssue(url) {
  const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
  const platform = socialPlatformOf(hostname);
  if (!["http:", "https:"].includes(url.protocol)) return "link phải bắt đầu bằng http:// hoặc https://";
  if (!platform) return `chưa có phương thức tải video từ tên miền ${hostname}`;
  // "Tải video …" rows in "Phân quyền gói & menu" (GET /api/v1/download/policy).
  if (state.blockedPlatforms.has(platform)) return `gói của bạn không cho tải video từ ${platform}`;
  if (url.username || url.password || (url.port && !["80", "443"].includes(url.port))) {
    return "link chứa thông tin kết nối không được hỗ trợ";
  }
  const path = url.pathname.replace(/\/{2,}/g, "/") || "/";
  const param = key => (url.searchParams.get(key) || "").trim();
  if (platform === "Google Drive") {
    if (googleDriveFileId(url)) return null;
    if (DRIVE_FOLDER_PATH.test(path)) return "link thư mục Google Drive chưa được hỗ trợ, hãy dán link chia sẻ của từng file video hoặc ảnh";
    if (GOOGLE_EDITOR_PATH.test(path)) return "đây là tài liệu Google Docs/Sheets/Slides, không phải file video hay ảnh";
    return "không tìm thấy mã file Google Drive (cần /file/d/<id> hoặc open?id=<id>)";
  }
  if (SHORT_LINK_HOSTS.has(hostname)) return path.replace(/\//g, "") ? null : "link rút gọn thiếu mã video";
  if (platform === "TikTok") {
    return TIKTOK_VIDEO_PATH.test(path) ? null : "không phải link một video TikTok (trang cá nhân, trang chủ…)";
  }
  if (platform === "Douyin") {
    const match = path.match(DOUYIN_PATH_ID);
    // /note/ and /slides/ are image posts (图文): the job asks what to make of them.
    if (match || ["modal_id", "vid", "aweme_id"].some(key => /^\d+$/.test(param(key)))) return null;
    return "không tìm thấy mã video Douyin (cần /video/<id>, /note/<id> hoặc modal_id=<id>)";
  }
  if (platform === "Xiaohongshu") {
    return XIAOHONGSHU_NOTE_PATH.test(path) ? null : "không phải link một bài Xiaohongshu (cần xhslink.com/…, /explore/<id> hoặc /discovery/item/<id>)";
  }
  if (platform === "YouTube") {
    if (path.replace(/\/$/, "") === "/watch" && param("v")) return null;
    if (YOUTUBE_ID_PATH.test(path) || YOUTUBE_POST_PATH.test(path)) return null;
    if (YOUTUBE_COMMUNITY_PATH.test(path) && /^[\w-]{10,}$/.test(param("lb"))) return null;
    if (param("list")) return "playlist YouTube chưa được hỗ trợ, hãy dán link từng video";
    return "không phải link một video YouTube (kênh, trang chủ, tìm kiếm…)";
  }
  if (!FACEBOOK_VIDEO_PATH.test(path)) return "không phải link một video/bài đăng Facebook (trang cá nhân, fanpage…)";
  const requiredKey = FACEBOOK_QUERY_KEYS[path];
  return requiredKey && !param(requiredKey) ? `link Facebook thiếu tham số ${requiredKey}=` : null;
}

/** Split pasted text into downloadable links and rejected ones (with the reason). */
function classifySourceLinks(value) {
  const matches = String(value || "").match(/https?:\/\/[^\s<>\[\]"']+/gi) || [];
  const valid = new Set();
  const invalid = new Map();
  for (const raw of matches) {
    const candidate = raw.replace(/[.,;:!?\)\]\}，。；：！？）】》、]+$/u, "");
    let url;
    try {
      url = new URL(candidate);
    } catch (_) {
      invalid.set(candidate, "link không hợp lệ");
      continue;
    }
    url.hash = "";
    const issue = sourceLinkDownloadIssue(url);
    if (issue) invalid.set(url.toString(), issue);
    else valid.add(socialPlatformOf(url.hostname.toLowerCase()) === "Google Drive" ? canonicalGoogleDriveUrl(url) : url.toString());
  }
  return { links: [...valid], invalid: [...invalid].map(([link, reason]) => ({ link, reason })) };
}

function extractSourceLinks(value) {
  return classifySourceLinks(value).links;
}

function invalidSourceLinksMessage(invalid) {
  const first = invalid[0];
  const more = invalid.length > 1 ? ` (và ${invalid.length - 1} link khác)` : "";
  return `Link chưa có phương thức tải: ${first.link} — ${first.reason}${more}. Hãy xóa hoặc sửa link trước khi tiếp tục.`;
}

function renderSourceLinks() {
  const rawValue = $("#sourceLinks").value;
  const { links, invalid } = classifySourceLinks(rawValue);
  state.links = links;
  state.invalidLinks = invalid;
  $("#clearLinksBtn")?.classList.toggle("is-hidden", !rawValue.trim());
  const invalidHtml = invalid.length
    ? `<div class="link-warning-note" role="alert">⛔ ${invalid.length} link không thể tải và sẽ chặn việc tạo batch:<ul>${invalid.map(item => `<li><strong>${escapeHtml(item.link)}</strong> — ${escapeHtml(item.reason)} <button class="button secondary mini-btn" type="button" data-link-remove="${escapeHtml(item.link)}" data-link-invalid="true" aria-label="Xóa liên kết ${escapeHtml(item.link)}">Xóa</button></li>`).join("")}</ul><small>${escapeHtml(SUPPORTED_LINK_FORMS_HINT)}</small></div>`
    : "";
  if (rawValue.trim() && state.links.length === 0) {
    $("#linkList").innerHTML = invalidHtml || `<div class="link-warning-note">⚠️ Chưa nhận diện được liên kết hợp lệ (hỗ trợ TikTok, Douyin, YouTube, Facebook, Xiaohongshu, Google Drive).</div>`;
    renderFiles();
    return;
  }
  $("#linkList").innerHTML = invalidHtml + state.links.map(link => {
    const hostname = new URL(link).hostname.toLowerCase();
    const detected = socialPlatformOf(hostname);
    const platform = detected === "Google Drive" ? "GD"
      : detected === "Xiaohongshu" ? "XHS"
      : hostname.includes("douyin") ? "DY"
      : (hostname.includes("youtube") || hostname === "youtu.be") ? "YT"
      : (hostname.includes("facebook") || hostname === "fb.com" || hostname === "fb.watch") ? "FB"
      : "TT";
    return `<div class="file-item link-item">
      <span class="file-item-icon">${platform}</span>
      <div><strong title="${escapeHtml(link)}">${escapeHtml(link)}</strong><small>Sẽ tải trong hàng đợi nền</small></div>
      <button class="file-remove" type="button" data-link-remove="${escapeHtml(link)}" aria-label="Xóa liên kết ${escapeHtml(link)}">✕</button>
    </div>`;
  }).join("");
  renderFiles();
}

function subtitleStyle() {
  const backgroundStyle = $("#subtitleBackgroundStyle").value;
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
    text_color_css: $("#subtitleTextColor").value,
    background_color_css: $("#subtitleBackgroundColor").value,
    background_opacity: 0.82,
    background_style: backgroundStyle,
    background_box: backgroundStyle === "box",
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
  const modes = { "1": "Phụ đề", "2": "Phụ đề + Voice", "3": "Voice + Nhạc", "4": "Chỉ tải video" };
  const languageLabels = { auto: "Tự nhận diện", vi: "Việt", en: "Anh", "zh-CN": "Trung", ja: "Nhật", ko: "Hàn", th: "Thái", id: "Indonesia", es: "Tây Ban Nha" };
  const previewTexts = { vi: "Tôi muốn trở thành phiên bản tốt nhất", en: "I want to become my best self", "zh-CN": "我想成为最好的自己", ja: "最高の自分になりたい", ko: "최고의 내가 되고 싶어요", th: "ฉันอยากเป็นตัวเองในแบบที่ดีที่สุด", id: "Saya ingin menjadi versi terbaik diri saya", es: "Quiero ser mi mejor versión" };
  const placements = { replace_original: "Thay chữ gốc", above_original: "Trên chữ gốc", bottom_safe: "Vùng an toàn" };
  const mode = $("#mode").value;
  const placement = $("#placementMode").value;
  $("#modeSummary").textContent = modes[mode];
  const sourceLanguage = $("#sourceLanguage").value;
  const targetLanguage = $("#targetLanguage").value;
  $("#languageSummary").textContent = `${languageLabels[sourceLanguage]} → ${languageLabels[targetLanguage]}`;
  const swapBtn = $("#swapLanguagesBtn");
  if (swapBtn) {
    swapBtn.disabled = sourceLanguage === "auto";
    swapBtn.title = sourceLanguage === "auto"
      ? "Chọn ngôn ngữ nguồn cụ thể để đảo chiều"
      : "Đảo chiều ngôn ngữ nguồn và đích";
  }
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
  const voiceTypeLabel = $("#voiceTypeLabel");
  if (voiceTypeLabel) voiceTypeLabel.textContent = "Giọng đọc";
  ["#ttsProviderField", "#voiceModeField", "#voiceTypeField", "#voiceStyleField", "#speechRateField", "#originalVolumeField", "#originalAudioField"].forEach(selector => {
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
    "4": "Chỉ tải video nguồn về (link hoặc file), giữ nguyên hình và tiếng. Không nhận diện, dịch, lồng tiếng hay tạo caption TikTok.",
  };
  $("#modeHint").textContent = hints[mode];
  // Download-only: the language, voice, subtitle and TikTok steps do not apply.
  $$("[data-translation-only]").forEach(section => section.classList.toggle("is-hidden", mode === "4"));
  $("#submitButton span").textContent = mode === "4" ? "Bắt đầu tải video" : "Bắt đầu xử lý";
  syncMobileQuickBar();
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
  $("#previewVietnamese").style.color = $("#subtitleTextColor").value;
  const previewBackground = $("#subtitleBackgroundColor").value;
  const previewOpacity = 0.82;
  const backgroundStyle = $("#subtitleBackgroundStyle").value;
  preview.classList.toggle("background-blurred-bottom", backgroundStyle === "blurred_bottom");
  preview.style.setProperty("--subtitle-background", previewBackground);
  $("#previewVietnamese").style.backgroundColor = backgroundStyle === "box"
    ? `${previewBackground}${Math.round(previewOpacity * 255).toString(16).padStart(2, "0")}`
    : "transparent";
}

function renderProviderVoices(provider) {
  stopVoicePreview();
  const select = $("#voiceTypeSelect");
  const previewButton = $("#voicePreviewButton");
  if (!select || !previewButton) return;
  const hasVoiceList = ["elevenlabs", "cit"].includes(provider?.key);
  const voices = hasVoiceList ? provider.voices || [] : [
    { key: "female", gender: "female", label: provider?.female_voice_label || "Giọng nữ" },
    { key: "male", gender: "male", label: provider?.male_voice_label || "Giọng nam" },
  ];
  const previous = select.value;
  select.innerHTML = voices.map(voice => {
    const genderLabel = voice.gender === "male" ? "Nam" : "Nữ";
    return `<option value="${escapeHtml(voice.key)}" data-gender="${escapeHtml(voice.gender)}">${genderLabel} — ${escapeHtml(voice.label)}</option>`;
  }).join("");
  if (voices.some(voice => voice.key === previous)) select.value = previous;
  select.disabled = voices.length === 0;
  previewButton.classList.toggle("is-hidden", !VOICE_PREVIEW_PROVIDERS.has(provider?.key) || voices.length === 0);
  syncSelectedVoice();
  scheduleVoicePreviewWarmup();
}

function syncSelectedVoice() {
  const provider = $("#ttsProvider")?.value;
  const isElevenLabs = provider === "elevenlabs";
  const hasVoiceList = isElevenLabs || provider === "cit";
  const select = $("#voiceTypeSelect");
  const selected = select?.selectedOptions[0];
  const gender = selected?.dataset.gender || select?.value || "female";
  $("#voiceType").value = gender;
  // The picked voice only fills its own style; the server picks a default for the
  // other, so auto routing never reads female characters with a male voice.
  $("#elevenlabsFemaleVoice").value = isElevenLabs && gender === "female" ? select.value : "";
  $("#elevenlabsMaleVoice").value = isElevenLabs && gender === "male" ? select.value : "";
  $("#ttsFemaleVoice").value = hasVoiceList && gender === "female" ? select.value : "";
  $("#ttsMaleVoice").value = hasVoiceList && gender === "male" ? select.value : "";
}

const VOICE_PREVIEW_PROVIDERS = new Set(["edge", "elevenlabs", "cit"]);
let voicePreviewAudio = null;
let voicePreviewButton = null;
let voicePreviewWarmupTimer = null;
const warmedVoicePreviewUrls = new Set();

function voicePreviewUrl() {
  const voiceKey = $("#voiceTypeSelect")?.value;
  const provider = $("#ttsProvider")?.value || "edge";
  if (!voiceKey || !VOICE_PREVIEW_PROVIDERS.has(provider)) return "";
  return `/api/v1/tts-providers/${encodeURIComponent(provider)}/voices/${encodeURIComponent(voiceKey)}/preview`;
}

function scheduleVoicePreviewWarmup() {
  clearTimeout(voicePreviewWarmupTimer);
  // VieNeu cannot stream and may need to load its model. Start the selected clip
  // quietly while the user is configuring the form so clicking Preview can use
  // the browser HTTP cache instead of beginning CPU synthesis at that moment.
  if ($("#ttsProvider")?.value !== "cit") return;
  const url = voicePreviewUrl();
  if (!url || warmedVoicePreviewUrls.has(url)) return;
  voicePreviewWarmupTimer = setTimeout(() => {
    warmedVoicePreviewUrls.add(url);
    fetch(url, { credentials: "same-origin" })
      .then(response => {
        if (!response.ok) throw new Error(`Voice preview HTTP ${response.status}`);
        return response.arrayBuffer(); // Consume fully so the HTTP cache owns the clip.
      })
      .catch(() => warmedVoicePreviewUrls.delete(url));
  }, 300);
}

function stopVoicePreview() {
  if (voicePreviewAudio) voicePreviewAudio.pause();
  if (voicePreviewButton) {
    voicePreviewButton.classList.remove("is-playing");
    voicePreviewButton.textContent = "▶ Nghe thử";
  }
  voicePreviewAudio = null;
  voicePreviewButton = null;
}

async function playVoicePreview(button) {
  const voiceSelect = $(button.dataset.voicePreview);
  const voiceKey = voiceSelect?.value;
  const wasPlaying = voicePreviewButton === button;
  stopVoicePreview();
  if (wasPlaying || !voiceKey) return;
  button.disabled = true;
  // Play the same-origin URL directly: the production CSP (default-src 'self')
  // blocks blob: media, and the session cookie authenticates this GET.
  // CIT synthesizes the sample on the server CPU, so the first play can take a while.
  const url = voicePreviewUrl();
  button.textContent = "… Đang tạo";
  const start = async () => {
    const audio = new Audio(url);
    voicePreviewAudio = audio;
    voicePreviewButton = button;
    button.classList.add("is-playing");
    button.textContent = "■ Dừng";
    audio.addEventListener("ended", () => { if (voicePreviewAudio === audio) stopVoicePreview(); });
    await audio.play();
  };
  try {
    try {
      await start();
    } catch (_) {
      // An expired access token makes the media request fail; refresh once.
      stopVoicePreview();
      if (!await refreshSession()) throw new Error();
      await start();
    }
  } catch (_) {
    stopVoicePreview();
    button.textContent = "▶ Nghe thử";
    toast("Không phát được bản nghe thử. Kiểm tra cấu hình nhà cung cấp giọng đọc hoặc log máy chủ.", true);
  } finally {
    button.disabled = false;
  }
}

$$("[data-voice-preview]").forEach(button => button.addEventListener("click", () => playVoicePreview(button)));

// Gemini and ElevenLabs need a Creator, Pro or Enterprise plan (server-enforced).
const PAID_PLAN_SUFFIX = " — cần gói Creator/Pro";

async function loadTtsProviders() {
  const select = $("#ttsProvider");
  const hint = $("#ttsProviderHint");
  if (!select || !hint) return;
  try {
    const data = await requestJson("/api/v1/tts-providers");
    const providers = new Map((data.providers || []).map(provider => [provider.key, provider]));
    [...select.options].forEach(option => {
      const provider = providers.get(option.value);
      option.disabled = provider ? !provider.configured || provider.allowed === false : true;
      if (provider?.allowed === false) option.textContent += PAID_PLAN_SUFFIX;
      else if (provider && !provider.configured) option.textContent += option.value === "cit" ? " — chưa mở ứng dụng" : " — chưa cấu hình key";
    });
    if (select.selectedOptions[0]?.disabled) select.value = "edge";
    const applyProviderConfiguration = () => {
      const selected = providers.get(select.value);
      hint.textContent = selected?.free_note || "Khóa API chỉ được lưu ở máy chủ.";
      renderProviderVoices(selected);
      const voiceMode = document.querySelector('[name="voice_mode"]');
      const autoOption = voiceMode?.querySelector('option[value="auto"]');
      if (autoOption) {
        autoOption.textContent = selected?.supports_pitch_matching === false
          ? "Tự động chọn giọng nam/nữ"
          : "Tự động theo cao độ và lời thoại";
      }
      updateFormSummary();
    };
    applyProviderConfiguration();
    select.addEventListener("change", applyProviderConfiguration);
    $("#voiceTypeSelect")?.addEventListener("change", () => {
      syncSelectedVoice();
      scheduleVoicePreviewWarmup();
    });
  } catch (_) {
    select.value = "edge";
    hint.textContent = "Edge TTS sẵn sàng; không đọc được trạng thái API khác.";
  }
}

async function loadTranslationProviders() {
  const select = $("#translationProvider");
  const hint = $("#translationProviderHint");
  if (!select || !hint) return;
  try {
    const data = await requestJson("/api/v1/translation-providers");
    const providers = new Map((data.providers || []).map(provider => [provider.key, provider]));
    [...select.options].forEach(option => {
      const provider = providers.get(option.value);
      option.disabled = provider ? !provider.configured || provider.allowed === false : true;
      if (provider?.allowed === false) {
        option.textContent += PAID_PLAN_SUFFIX;
      } else if (provider && !provider.configured && !option.textContent.includes("chưa cấu hình")) {
        option.textContent += ["translategemma", "qwen3"].includes(option.value) ? " — chưa chạy Ollama" : " — chưa cấu hình key";
      }
    });
    if (select.selectedOptions[0]?.disabled) {
      select.value = ["gemini", "translategemma"].find(key => providers.get(key)?.configured && providers.get(key)?.allowed !== false) || "google";
    }
    const updateHint = () => {
      hint.textContent = providers.get(select.value)?.description || "";
      updateFormSummary();
    };
    updateHint();
    select.addEventListener("change", updateHint);
  } catch (_) {
    select.value = "google";
    hint.textContent = "Google Translate đang được dùng làm phương án miễn phí mặc định.";
  }
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
const NAV_MENU_VIEWS = new Set(["create", "comic", "clips", "jobs", "tiktok", "douyin", "admin"]);

function showView() {
  let view = location.hash.startsWith("#comic") ? "comic" : location.hash === "#clips" ? "clips" : location.hash === "#tiktok" ? "tiktok" : location.hash === "#jobs" ? "jobs" : location.hash === "#douyin" ? "douyin" : location.hash === "#admin" ? "admin" : location.hash === "#billing" ? "billing" : location.hash === "#account" ? "account" : "create";
  if (["admin", "douyin"].includes(view) && !["owner", "admin"].includes(state.auth.role || "owner")) view = "create";
  if (["tiktok", "comic", "account"].includes(view) && state.auth.role === "viewer") view = "jobs";
  // Views behind a main-menu entry the admin hid (or the plan lacks) are closed too.
  if (state.navAllowedViews && NAV_MENU_VIEWS.has(view) && !state.navAllowedViews.has(view)) {
    view = [...state.navAllowedViews][0] || "billing";
  }
  $("#createView").classList.toggle("is-hidden", view !== "create");
  $("#jobsView").classList.toggle("is-hidden", view !== "jobs");
  $("#comicView").classList.toggle("is-hidden", view !== "comic");
  if (view === "comic" && state.appStarted) comicWorkspace.enter();
  else comicWorkspace.leave();
  $("#clipsView").classList.toggle("is-hidden", view !== "clips");
  if (view === "clips" && state.appStarted) clipWorkspace.enter();
  else clipWorkspace.leave();
  $("#tiktokView").classList.toggle("is-hidden", view !== "tiktok");
  if (view === "tiktok" && state.appStarted) tiktokWorkspace.enter();
  else tiktokWorkspace.leave();
  $("#douyinView").classList.toggle("is-hidden", view !== "douyin");
  $("#adminView").classList.toggle("is-hidden", view !== "admin");
  $("#billingView").classList.toggle("is-hidden", view !== "billing");
  $("#accountView").classList.toggle("is-hidden", view !== "account");
  if (view === "account" && state.appStarted) brandingWorkspace.enter();
  else brandingWorkspace.leave();
  $$('[data-view-link]').forEach(link => link.classList.toggle("active", link.dataset.viewLink === view));
  $("#userMenuButton")?.classList.toggle("is-active-view", ["account", "billing", "admin"].includes(view));
  if (window.innerWidth <= 1024) {
    const activeDockLink = $(`#mainNavigation a[data-view-link="${view}"]`);
    if (activeDockLink && activeDockLink.offsetParent !== null && activeDockLink.scrollIntoView) {
      try { activeDockLink.scrollIntoView({ inline: "center", behavior: "smooth", block: "nearest" }); } catch (_) {}
    }
  }
  if (view === "jobs" && state.appStarted) loadJobs();
  if (view === "create" && state.appStarted) loadDownloadPolicy();
  syncBrowserPower(view);
  if (view === "douyin" && state.appStarted) startDouyinBrowserPolling();
  else stopDouyinBrowserPolling();
  if (view === "admin" && state.appStarted) {
    loadAdminAccounts();
    refreshAdminContactBadge();
    refreshAdminReportsBadge();
  }
  syncAdminSystemEvents();
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
    trial: { icon: "🎟️", label: "Dùng thử" },
    free: { icon: "🌱", label: "Gói Khởi động" },
    creator: { icon: "⚡", label: "Nhà sáng tạo" },
    pro: { icon: "🚀", label: "Phổ biến nhất" },
    enterprise: { icon: "🏢", label: "Doanh nghiệp" },
  };

  const paidPlans = data.plans.filter(plan => Number(plan.amount) > 0 && plan.key !== account.plan);
  const previousSelection = $("#paymentPlan").value;
  $("#paymentPlan").innerHTML = paidPlans.map(plan => `<option value="${escapeHtml(plan.key)}">${escapeHtml(plan.name)} (${plan.minutes.toLocaleString("vi-VN")} phút/tháng) · ${money(plan.amount)}</option>`).join("");

  $("#billingPlans").innerHTML = data.plans.filter(plan => plan.key !== "trial" || plan.key === account.plan).map(plan => {
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
          <li><span class="feature-icon check">✓</span><span>Dịch Google, TranslateGemma · lồng tiếng Edge TTS, CIT Voice Studio</span></li>
          <li>${["free", "trial"].includes(plan.key) ? '<span class="feature-icon dash">—</span><span class="feature-muted">Gemini AI & ElevenLabs</span>' : '<span class="feature-icon check">✓</span><span>Gemini AI & ElevenLabs</span>'}</li>
          <li><span class="feature-icon check">✓</span><span>Lưu trữ kết quả đám mây an toàn</span></li>
          <li>${["free", "trial"].includes(plan.key) ? '<span class="feature-icon dash">—</span><span class="feature-muted">Hỗ trợ ưu tiên 24/7</span>' : '<span class="feature-icon check">✓</span><span>Hỗ trợ ưu tiên chuyên sâu 24/7</span>'}</li>
        </ul>
        <div class="plan-card-action">
          ${plan.key === "enterprise" ? `
            <a class="button secondary enterprise-btn" href="mailto:support@vidtrans.local">
              <span>Liên hệ tư vấn</span>
              <span>→</span>
            </a>` : plan.key === "trial" ? `
            <button class="button secondary current-plan-btn" type="button" disabled>
              ✓ Đang dùng thử
            </button>` : plan.key === "free" ? (isCurrent ? `
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
        <p>Mã QR đã chứa đúng số tiền và cú pháp. Sau khi chuyển khoản, hãy bấm báo đã chuyển khoản; gói được kích hoạt khi quản trị viên đối soát xong giao dịch.</p>
      </div>
      <div class="transfer-action-buttons">
        ${order.customer_reported_at ? `
        <button class="button primary transfer-confirm-btn" type="button" disabled>
          <span>⏳ Đã báo chuyển khoản — chờ đối soát</span>
        </button>` : `
        <button class="button primary transfer-confirm-btn" type="button" data-confirm-order="${escapeHtml(order.order_id)}">
          <span>✓ Tôi đã chuyển khoản</span>
        </button>`}
        <button class="button secondary transfer-cancel-btn" type="button" data-cancel-order="${escapeHtml(order.order_id)}">
          <span>✕ Hủy yêu cầu này</span>
        </button>
      </div>
    </div>`;
  instructions.classList.remove("is-hidden");
  instructions.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function orderStatusLabel(order) {
  if (order.status === "pending" && order.customer_reported_at) return "Chờ đối soát";
  return billingLabels.order[order.status] || order.status;
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
          <span>${orderStatusLabel(order)}</span>
        </span>
        ${order.status === "pending" ? `
          <div class="order-actions">
            <button class="button secondary mini-btn" type="button" data-view-order="${escapeHtml(order.order_id)}" title="Xem mã QR thanh toán">Xem QR</button>
            ${order.provider === "bank" && !order.customer_reported_at ? `<button class="button primary mini-btn" type="button" data-confirm-order="${escapeHtml(order.order_id)}" title="Báo đã chuyển khoản để quản trị viên đối soát">Đã chuyển khoản</button>` : ""}
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
  const plans = [["trial", "Dùng thử"], ["free", "Free"], ["creator", "Creator"], ["pro", "Pro"], ["enterprise", "Enterprise"]];
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
      <div class="admin-action-cell">
        <button class="button secondary btn-admin-save" type="button" data-admin-save title="Lưu thay đổi tài khoản này">
          <span class="save-icon" aria-hidden="true">✓</span>
          <span>Lưu</span>
        </button>
        ${canResetPassword(item) ? `
          <button class="button secondary mini-btn" type="button" data-admin-reset-password="${escapeHtml(item.user_id)}" data-email="${escapeHtml(item.email)}" title="Đặt lại mật khẩu">
            <span aria-hidden="true">🔑</span>
          </button>` : ""}
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

// Mirrors the server rule: only the owner may reset another owner/admin.
function canResetPassword(account) {
  if (state.auth.role === "owner" || account.user_id === state.auth.user_id) return true;
  return state.auth.role === "admin" && !["owner", "admin"].includes(account.role);
}

function setPasswordVisible(button, visible) {
  if (!button) return;
  const input = document.getElementById(button.dataset.passwordToggle);
  if (!input) return;
  input.type = visible ? "text" : "password";
  const label = visible ? "Ẩn mật khẩu" : "Hiện mật khẩu";
  button.setAttribute("aria-pressed", String(visible));
  button.setAttribute("aria-label", label);
  button.title = label;
}

function generatePassword(length = 16) {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%&*?";
  const values = crypto.getRandomValues(new Uint32Array(length));
  return Array.from(values, value => alphabet[value % alphabet.length]).join("");
}

function openPasswordResetDialog(userId, email) {
  const dialog = $("#passwordResetDialog");
  dialog.dataset.userId = userId;
  $("#passwordResetTarget").textContent = email;
  $("#passwordResetValue").value = "";
  setPasswordVisible($("[data-password-toggle=passwordResetValue]"), false);
  $("#passwordResetSubmit").disabled = false;
  dialog.showModal();
  $("#passwordResetValue").focus();
}

async function submitPasswordReset(event) {
  event.preventDefault();
  const dialog = $("#passwordResetDialog");
  const userId = dialog.dataset.userId;
  const button = $("#passwordResetSubmit");
  button.disabled = true;
  try {
    const body = new FormData();
    body.set("new_password", $("#passwordResetValue").value);
    const result = await requestJson(`/api/v1/admin/accounts/${encodeURIComponent(userId)}/password`, { method: "POST", body });
    dialog.close();
    $("#passwordResetValue").value = "";
    if (userId === state.auth.user_id) {
      lockApplication("Mật khẩu của bạn đã được đổi. Hãy đăng nhập lại.", false);
      return;
    }
    toast(result.message || "Đã đặt lại mật khẩu");
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
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
        <div class="admin-job-cell admin-job-cell-id">
          <span class="admin-job-label">Job ID</span>
          <code>${escapeHtml(job.job_id)}</code>
        </div>
        <div class="admin-job-cell admin-job-cell-user user-meta" title="${escapeHtml(job.owner_user_id || "")}">
          <span class="admin-job-label">Người dùng</span>
          <div class="admin-job-user-info">
            <strong>${userLabel}</strong>
            ${job.owner_display_name ? `<small>${escapeHtml(job.owner_display_name)}</small>` : ""}
          </div>
        </div>
        <div class="admin-job-cell admin-job-cell-file">
          <span class="admin-job-label">Tên video</span>
          <div class="job-filename" title="${fileName}">${fileName}</div>
        </div>
        <div class="admin-job-cell admin-job-cell-status">
          <span class="admin-job-label">Trạng thái</span>
          <div>${statusBadge}</div>
        </div>
        <div class="admin-job-cell admin-job-cell-progress">
          <span class="admin-job-label">Tiến độ</span>
          <strong class="admin-job-progress-val">${progress}%</strong>
        </div>
        <div class="admin-job-cell admin-job-cell-time">
          <span class="admin-job-label">Thời gian tạo</span>
          <small>${createdAt}</small>
        </div>
        <div class="admin-job-cell admin-job-cell-actions">
          <button class="button secondary mini-btn" type="button" data-admin-view-job="${escapeHtml(job.job_id)}" title="Xem chi tiết (Có ghi Audit)">Chi tiết</button>
        </div>
      </div>
    `;
  }).join("");
}

async function loadAdminBilling() {
  const list = $("#adminBillingList");
  if (!list) return;
  list.innerHTML = '<div class="empty-state"><strong>Đang tải đơn thanh toán…</strong></div>';
  try {
    const filter = $("#adminBillingFilter")?.value ?? "review";
    const params = new URLSearchParams({ limit: "100" });
    if (filter === "review") params.set("awaiting_review", "true");
    else if (filter) params.set("status", filter);
    const data = await requestJson(`/api/v1/admin/billing/orders?${params}`);
    renderAdminBilling(data.items || []);
  } catch (err) {
    list.innerHTML = `<div class="empty-state error"><strong>${escapeHtml(err.message)}</strong></div>`;
  }
}

function renderAdminBilling(orders) {
  const list = $("#adminBillingList");
  if (!list) return;
  if (!orders.length) {
    list.innerHTML = '<div class="empty-state"><strong>Không có đơn thanh toán nào phù hợp</strong></div>';
    return;
  }
  list.innerHTML = orders.map(order => {
    const badgeClass = order.status === "paid" ? "badge-success" : order.status === "pending" ? "badge-warning" : "badge-danger";
    const reportedAt = order.customer_reported_at ? new Date(order.customer_reported_at).toLocaleString("vi-VN") : "";
    const createdAt = order.created_at ? new Date(order.created_at).toLocaleString("vi-VN") : "—";
    const actions = order.status === "pending" ? `
      ${order.provider === "bank" ? `<button class="button primary mini-btn" type="button" data-admin-confirm-order="${escapeHtml(order.order_id)}" data-amount="${escapeHtml(money(order.amount))}" data-transfer="${escapeHtml(order.transfer_content)}" title="Xác nhận đã nhận tiền (Có ghi Audit)">Xác nhận</button>` : ""}
      <button class="button secondary mini-btn cancel-btn" type="button" data-admin-reject-order="${escapeHtml(order.order_id)}" title="Từ chối đơn (Có ghi Audit)">Từ chối</button>` : "—";
    return `
      <div class="admin-jobs-row" data-order-id="${escapeHtml(order.order_id)}">
        <div class="admin-job-cell admin-job-cell-id">
          <span class="admin-job-label">Nội dung CK</span>
          <code>${escapeHtml(order.transfer_content)}</code>
        </div>
        <div class="admin-job-cell admin-job-cell-user user-meta" title="${escapeHtml(order.user_id)}">
          <span class="admin-job-label">Người dùng</span>
          <div class="admin-job-user-info">
            <strong>${escapeHtml(order.user_email || order.user_id)}</strong>
            ${order.user_display_name ? `<small>${escapeHtml(order.user_display_name)}</small>` : ""}
          </div>
        </div>
        <div class="admin-job-cell admin-job-cell-file">
          <span class="admin-job-label">Gói · Kênh</span>
          <div>${escapeHtml(String(order.plan).toUpperCase())} · ${escapeHtml(order.provider)}</div>
        </div>
        <div class="admin-job-cell admin-job-cell-status">
          <span class="admin-job-label">Trạng thái</span>
          <div><span class="badge ${badgeClass}">${escapeHtml(orderStatusLabel(order))}</span></div>
        </div>
        <div class="admin-job-cell admin-job-cell-progress">
          <span class="admin-job-label">Số tiền</span>
          <strong class="admin-job-progress-val">${money(order.amount)}</strong>
        </div>
        <div class="admin-job-cell admin-job-cell-time">
          <span class="admin-job-label">Thời gian</span>
          <small>${escapeHtml(createdAt)}${reportedAt ? `<br>Báo CK: ${escapeHtml(reportedAt)}` : ""}</small>
        </div>
        <div class="admin-job-cell admin-job-cell-actions">${actions}</div>
      </div>`;
  }).join("");
}

function setAdminContactBadge(count) {
  const badge = $("#adminContactBadge");
  if (!badge) return;
  badge.textContent = String(count);
  badge.classList.toggle("is-zero", !count);
}

async function refreshAdminContactBadge() {
  try {
    const data = await requestJson("/api/v1/admin/contact-messages?status=new&limit=1");
    setAdminContactBadge(data.new_count || 0);
  } catch {
    // The badge is a hint; the tab itself reports errors.
  }
}

async function loadAdminContact() {
  const list = $("#adminContactList");
  if (!list) return;
  list.innerHTML = '<div class="empty-state"><strong>Đang tải tin nhắn…</strong></div>';
  try {
    const params = new URLSearchParams({ limit: "100", status: $("#adminContactFilter")?.value ?? "new" });
    const data = await requestJson(`/api/v1/admin/contact-messages?${params}`);
    setAdminContactBadge(data.new_count || 0);
    renderAdminContact(data.items || []);
  } catch (err) {
    list.innerHTML = `<div class="empty-state error"><strong>${escapeHtml(err.message)}</strong></div>`;
  }
}

function renderAdminContact(messages) {
  const list = $("#adminContactList");
  if (!list) return;
  if (!messages.length) {
    list.innerHTML = '<div class="empty-state"><strong>Không có tin nhắn nào</strong></div>';
    return;
  }
  list.innerHTML = messages.map(item => {
    const createdAt = item.created_at ? new Date(item.created_at).toLocaleString("vi-VN") : "—";
    const resolved = item.status === "resolved";
    const subject = encodeURIComponent(`Re: [VidTrans] ${item.topic_label}`);
    return `
      <article class="admin-contact-item${resolved ? " is-resolved" : ""}" data-contact-id="${escapeHtml(item.message_id)}">
        <header>
          <div>
            <strong>${escapeHtml(item.name)}</strong>
            <span class="badge ${resolved ? "badge-success" : "badge-warning"}">${resolved ? "Đã xử lý" : "Mới"}</span>
            <span class="admin-contact-topic">${escapeHtml(item.topic_label)}</span>
          </div>
          <small>${escapeHtml(createdAt)}</small>
        </header>
        <p class="admin-contact-meta">
          <a href="mailto:${escapeHtml(item.email)}?subject=${subject}">${escapeHtml(item.email)}</a>
          ${item.phone ? ` · <a href="tel:${escapeHtml(item.phone)}">${escapeHtml(item.phone)}</a>` : ""}
        </p>
        <p class="admin-contact-body">${escapeHtml(item.message)}</p>
        ${resolved ? "" : `<button class="button secondary mini-btn" type="button" data-admin-resolve-contact>Đánh dấu đã xử lý</button>`}
      </article>`;
  }).join("");
}

// ---- Problem reports users send about a processed video ----

function setAdminReportsBadge(count) {
  const badge = $("#adminReportsBadge");
  if (!badge) return;
  badge.textContent = String(count);
  badge.classList.toggle("is-zero", !count);
}

async function refreshAdminReportsBadge() {
  if (!["owner", "admin"].includes(state.auth.role)) return;
  try {
    const data = await requestJson("/api/v1/admin/job-reports?status=new&limit=1");
    setAdminReportsBadge(data.new_count || 0);
  } catch {
    // The badge is a hint; the tab itself reports errors.
  }
}

async function loadAdminReports() {
  const list = $("#adminReportsList");
  if (!list) return;
  list.innerHTML = '<div class="empty-state"><strong>Đang tải báo lỗi…</strong></div>';
  try {
    const params = new URLSearchParams({ limit: "100", status: $("#adminReportsFilter")?.value ?? "new" });
    const data = await requestJson(`/api/v1/admin/job-reports?${params}`);
    setAdminReportsBadge(data.new_count || 0);
    renderAdminReports(data.items || []);
  } catch (err) {
    list.innerHTML = `<div class="empty-state error"><strong>${escapeHtml(err.message)}</strong></div>`;
  }
}

function renderAdminReports(reports) {
  const list = $("#adminReportsList");
  if (!list) return;
  if (!reports.length) {
    list.innerHTML = '<div class="empty-state"><strong>Không có báo lỗi nào</strong></div>';
    return;
  }
  list.innerHTML = reports.map(item => {
    const createdAt = item.created_at ? new Date(item.created_at).toLocaleString("vi-VN") : "—";
    const resolved = item.status === "resolved";
    const reporter = item.reporter_email || item.reporter_user_id;
    return `
      <article class="admin-contact-item${resolved ? " is-resolved" : ""}" data-report-id="${escapeHtml(item.report_id)}">
        <header>
          <div>
            <strong>${escapeHtml(item.job_title || item.job_id)}</strong>
            <span class="badge ${resolved ? "badge-success" : "badge-warning"}">${resolved ? "Đã xử lý" : "Mới"}</span>
            <span class="admin-contact-topic">${escapeHtml(item.category_label)}</span>
          </div>
          <small>${escapeHtml(createdAt)}</small>
        </header>
        <p class="admin-contact-meta">
          ${escapeHtml(reporter)} · Job #${escapeHtml(String(item.job_id).slice(0, 8).toUpperCase())}
          ${item.video_time ? ` · tại ${escapeHtml(item.video_time)}` : ""}
        </p>
        ${item.message ? `<p class="admin-contact-body">${escapeHtml(item.message)}</p>` : ""}
        <div class="admin-report-actions">
          <button class="button secondary mini-btn" type="button" data-admin-open-report-job="${escapeHtml(item.job_id)}">Mở job</button>
          ${resolved ? "" : `<button class="button secondary mini-btn" type="button" data-admin-resolve-report>Đánh dấu đã xử lý</button>`}
        </div>
      </article>`;
  }).join("");
}

function openJobReportDialog(job) {
  const dialog = $("#jobReportDialog");
  if (!dialog || !job) return;
  dialog.dataset.jobId = job.job_id;
  $("#jobReportTarget").textContent = job.filename || job.title || job.source_url || `Job #${job.job_id.slice(0, 8).toUpperCase()}`;
  $("#jobReportForm").reset();
  try {
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  } catch {
    dialog.setAttribute("open", "");
  }
}

function closeJobReportDialog() {
  const dialog = $("#jobReportDialog");
  if (!dialog) return;
  try { if (dialog.open && typeof dialog.close === "function") dialog.close(); } catch {}
  dialog.removeAttribute("open");
}

// ---- Background-music library (admin uploads, users pick it for dubbing) ----

function formatTrackLength(seconds) {
  const total = Math.round(Number(seconds) || 0);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

let musicPreview = null;

function stopMusicPreview() {
  if (musicPreview) musicPreview.pause();
  musicPreview = null;
  const button = $("#previewBackgroundMusic");
  if (button) button.textContent = "▶ Nghe thử";
}

function syncMusicPicker() {
  const select = $("#backgroundMusicId");
  const file = $("#backgroundMusicFile");
  if (!select || !file) return;
  const fromLibrary = Boolean(select.value);
  file.classList.toggle("is-hidden", fromLibrary);
  if (fromLibrary) file.value = "";
  $("#previewBackgroundMusic").disabled = !fromLibrary;
  stopMusicPreview();
}

const MUSIC_EXTENSIONS = [".mp3", ".m4a", ".aac", ".wav", ".ogg"];
const MUSIC_MAX_BYTES = 30 * 1024 * 1024;
state.musicCategories = [];

/** `<optgroup>` per category (in the server's order), each track as an option. */
function musicTrackOptions(tracks, categories = state.musicCategories) {
  const order = [...categories, ...tracks.map(track => track.category || "Khác")];
  const groups = [...new Set(order)].map(category => [category, tracks.filter(track => (track.category || "Khác") === category)]);
  return groups.filter(([, items]) => items.length).map(([category, items]) =>
    `<optgroup label="${escapeHtml(category)}">${items.map(track =>
      `<option value="${escapeHtml(track.track_id)}">${escapeHtml(track.title)} (${formatTrackLength(track.duration_seconds)})</option>`,
    ).join("")}</optgroup>`,
  ).join("");
}

async function loadMusicLibrary() {
  const select = $("#backgroundMusicId");
  if (!select) return;
  let items = [];
  try {
    const library = await requestJson("/api/v1/music-tracks");
    items = library.items || [];
    state.musicCategories = library.categories || [];
  } catch {
    // The picker still works with a user upload when the library is unavailable.
  }
  state.musicTracks = items;
  const current = select.value;
  select.innerHTML = '<option value="">— Tự tải file nhạc của bạn —</option>' + musicTrackOptions(items);
  if (items.some(track => track.track_id === current)) select.value = current;
  syncMusicPicker();
}

$("#backgroundMusicId")?.addEventListener("change", syncMusicPicker);
$("#previewBackgroundMusic")?.addEventListener("click", () => {
  const trackId = $("#backgroundMusicId").value;
  if (!trackId) return;
  if (musicPreview) return stopMusicPreview();
  musicPreview = new Audio(`/api/v1/music-tracks/${encodeURIComponent(trackId)}/audio`);
  musicPreview.addEventListener("ended", stopMusicPreview);
  musicPreview.play().catch(() => { stopMusicPreview(); toast("Không phát được bản nhạc", true); });
  $("#previewBackgroundMusic").textContent = "⏸ Dừng";
});

async function loadAdminMusic() {
  const list = $("#adminMusicList");
  if (!list) return;
  list.innerHTML = '<div class="empty-state"><strong>Đang tải thư viện nhạc…</strong></div>';
  try {
    const library = await requestJson("/api/v1/music-tracks");
    const items = library.items || [];
    const categories = library.categories || [];
    state.musicCategories = categories;
    syncMusicCategorySelect(categories);
    if (!items.length) {
      list.innerHTML = '<div class="empty-state"><strong>Chưa có bản nhạc nào</strong><span>Tải nhạc lên để người dùng chọn khi lồng tiếng.</span></div>';
      return;
    }
    const categoryOptions = current => [...new Set([...categories, current])].map(category =>
      `<option value="${escapeHtml(category)}"${category === current ? " selected" : ""}>${escapeHtml(category)}</option>`,
    ).join("") + '<option value="__new__">+ Thể loại mới…</option>';
    const groups = categories.map(category => [category, items.filter(track => track.category === category)])
      .filter(([, tracks]) => tracks.length);
    list.innerHTML = groups.map(([category, tracks]) => `
      <h4 class="admin-music-group">${escapeHtml(category)} <small>${tracks.length} bài</small></h4>
      ${tracks.map(track => `
      <article class="admin-music-item" data-music-id="${escapeHtml(track.track_id)}" data-music-title="${escapeHtml(track.title)}" data-music-category="${escapeHtml(track.category)}">
        <div class="admin-music-info">
          <strong>${escapeHtml(track.title)}</strong>
          <small>${formatTrackLength(track.duration_seconds)} · ${formatBytes(Number(track.size_bytes))} · ${escapeHtml(track.extension.replace(".", "").toUpperCase())} · ${escapeHtml(new Date(track.created_at).toLocaleString("vi-VN"))}</small>
        </div>
        <audio controls preload="none" src="${escapeHtml(track.audio_url)}"></audio>
        <div class="admin-music-actions">
          <select class="admin-music-category" data-admin-music-category aria-label="Thể loại của ${escapeHtml(track.title)}">${categoryOptions(track.category)}</select>
          <button class="button secondary mini-btn" type="button" data-admin-rename-music>Đổi tên</button>
          <button class="button secondary mini-btn" type="button" data-admin-delete-music>Xoá</button>
        </div>
      </article>`).join("")}`).join("");
  } catch (err) {
    list.innerHTML = `<div class="empty-state error"><strong>${escapeHtml(err.message)}</strong></div>`;
  }
}

$("#refreshAdminMusic")?.addEventListener("click", loadAdminMusic);
function syncMusicCategorySelect(categories) {
  const select = $("#adminMusicCategory");
  if (!select) return;
  const current = select.value;
  select.innerHTML = '<option value="">Tự phân loại theo tên file</option>' + categories.map(category =>
    `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join("");
  if (categories.includes(current)) select.value = current;
}

const isMusicFile = file => MUSIC_EXTENSIONS.some(extension => file.name.toLowerCase().endsWith(extension));

// Many tracks at once: uploaded one after another (each up to 30 MB), every file
// reporting its own result, so one bad file never stops the rest. Files that failed on
// the way (network, server) stay in the queue to retry one by one or all together;
// a wrong type or size cannot pass on a retry and gets no button.
const musicUpload = { queue: [], category: "", running: false };

function renderMusicUploadQueue() {
  const progress = $("#adminMusicUploadProgress");
  if (!progress) return;
  const { queue, running } = musicUpload;
  progress.classList.toggle("is-hidden", !queue.length);
  const retriable = queue.filter(item => item.state === "error" && item.retriable).length;
  const done = queue.filter(item => item.state === "done").length;
  const header = queue.length ? `<li class="admin-music-progress-head"><span>${done}/${queue.length} bài đã thêm</span>${
    retriable && !running ? `<button class="button secondary mini-btn" type="button" data-music-retry-all>↻ Thử lại tất cả (${retriable})</button>` : ""
  }${!running ? '<button class="button secondary mini-btn cancel-btn" type="button" data-music-clear>Ẩn</button>' : ""}</li>` : "";
  progress.innerHTML = header + queue.map((item, index) => `
    <li class="is-${item.state}" data-upload-index="${index}">
      <span>${escapeHtml(item.file.name)}</span>
      <small>${escapeHtml(item.message)}</small>
      ${item.state === "error" && item.retriable && !running
        ? `<button class="button secondary mini-btn" type="button" data-music-retry="${index}">↻ Thử lại</button>` : ""}
    </li>`).join("");
}

async function uploadMusicQueue(indexes) {
  if (musicUpload.running) return;
  musicUpload.running = true;
  const submit = $('#adminMusicUploadForm button[type="submit"]');
  if (submit) submit.disabled = true;
  indexes.forEach(index => Object.assign(musicUpload.queue[index], { state: "waiting", message: "Đang chờ…" }));
  renderMusicUploadQueue();
  let added = 0;
  for (const index of indexes) {
    const item = musicUpload.queue[index];
    Object.assign(item, { state: "active", message: "Đang tải lên…" });
    renderMusicUploadQueue();
    const data = new FormData();
    data.set("file", item.file);
    if (musicUpload.category) data.set("category", musicUpload.category);
    try {
      const track = await requestJson("/api/v1/admin/music-tracks", { method: "POST", body: data });
      Object.assign(item, { state: "done", message: `✓ ${track.title} · ${track.category}` });
      added += 1;
    } catch (err) {
      // A refused file (bad audio, library full) fails again; a dropped connection may not.
      Object.assign(item, { state: "error", message: err.message, retriable: true });
    }
    renderMusicUploadQueue();
  }
  musicUpload.running = false;
  if (submit) submit.disabled = false;
  renderMusicUploadQueue();
  const failed = indexes.length - added;
  toast(failed ? `Đã thêm ${added}/${indexes.length} bài — bấm “Thử lại” cho các file lỗi` : `Đã thêm ${added} bài vào thư viện`, Boolean(failed));
  if (added) await Promise.all([loadAdminMusic(), loadMusicLibrary()]);
}

$("#adminMusicUploadForm")?.addEventListener("submit", async event => {
  event.preventDefault();
  if (musicUpload.running) return;
  const form = event.currentTarget;
  const files = [...($("#adminMusicFiles").files || [])];
  if (!files.length) return toast("Hãy chọn file nhạc", true);
  musicUpload.category = $("#adminMusicCategory").value;
  musicUpload.queue = files.map(file => {
    if (!isMusicFile(file)) return { file, state: "error", message: "Không phải mp3/m4a/aac/wav/ogg", retriable: false };
    if (file.size > MUSIC_MAX_BYTES) return { file, state: "error", message: "Vượt quá 30 MB", retriable: false };
    return { file, state: "waiting", message: "Đang chờ…", retriable: false };
  });
  form.reset();
  const valid = musicUpload.queue.flatMap((item, index) => (item.state === "waiting" ? [index] : []));
  renderMusicUploadQueue();
  if (valid.length) await uploadMusicQueue(valid);
  else toast("Không có file nhạc hợp lệ để tải lên", true);
});

$("#adminMusicUploadProgress")?.addEventListener("click", event => {
  if (event.target.closest("[data-music-clear]")) {
    musicUpload.queue = [];
    renderMusicUploadQueue();
    return;
  }
  const one = event.target.closest("[data-music-retry]");
  if (one) return uploadMusicQueue([Number(one.dataset.musicRetry)]);
  if (event.target.closest("[data-music-retry-all]")) {
    uploadMusicQueue(musicUpload.queue.flatMap((item, index) => (item.state === "error" && item.retriable ? [index] : [])));
  }
});

// Drop audio files anywhere on the upload form.
(form => {
  if (!form) return;
  ["dragenter", "dragover"].forEach(type => form.addEventListener(type, event => {
    event.preventDefault();
    form.classList.add("is-dragging");
  }));
  ["dragleave", "drop"].forEach(type => form.addEventListener(type, () => form.classList.remove("is-dragging")));
  form.addEventListener("drop", event => {
    event.preventDefault();
    const dropped = [...(event.dataTransfer?.files || [])].filter(isMusicFile);
    if (!dropped.length) return toast("Chỉ nhận file mp3, m4a, aac, wav hoặc ogg", true);
    const transfer = new DataTransfer();
    dropped.forEach(file => transfer.items.add(file));
    $("#adminMusicFiles").files = transfer.files;
    toast(`Đã chọn ${dropped.length} bài — bấm Tải lên`);
  });
})($("#adminMusicUploadForm"));

$("#adminMusicList")?.addEventListener("change", async event => {
  const select = event.target.closest("[data-admin-music-category]");
  const item = event.target.closest("[data-music-id]");
  if (!select || !item) return;
  let category = select.value;
  if (category === "__new__") {
    category = (prompt("Tên thể loại mới:") || "").trim();
    if (!category) {
      select.value = item.dataset.musicCategory;
      return;
    }
  }
  const body = new FormData();
  body.set("category", category);
  try {
    const track = await requestJson(`/api/v1/admin/music-tracks/${encodeURIComponent(item.dataset.musicId)}`, { method: "POST", body });
    toast(`Đã chuyển “${track.title}” sang ${track.category}`);
    await Promise.all([loadAdminMusic(), loadMusicLibrary()]);
  } catch (err) {
    select.value = item.dataset.musicCategory;
    toast(err.message, true);
  }
});
$("#adminMusicList")?.addEventListener("click", async event => {
  const item = event.target.closest("[data-music-id]");
  if (!item) return;
  const trackId = encodeURIComponent(item.dataset.musicId);
  try {
    if (event.target.closest("[data-admin-rename-music]")) {
      const title = prompt("Tên mới cho bản nhạc:", item.dataset.musicTitle);
      if (!title || !title.trim()) return;
      const body = new FormData();
      body.set("title", title.trim());
      await requestJson(`/api/v1/admin/music-tracks/${trackId}`, { method: "POST", body });
      toast("Đã đổi tên");
    } else if (event.target.closest("[data-admin-delete-music]")) {
      if (!confirm(`Xoá "${item.dataset.musicTitle}" khỏi thư viện? Các job đã tạo vẫn giữ nhạc của mình.`)) return;
      await requestJson(`/api/v1/admin/music-tracks/${trackId}`, { method: "DELETE" });
      toast("Đã xoá bản nhạc");
    } else {
      return;
    }
    await Promise.all([loadAdminMusic(), loadMusicLibrary()]);
  } catch (err) {
    toast(err.message, true);
  }
});

const SYSTEM_REFRESH_MS = 5000;

function usageLevel(percent, warn = 70, danger = 85) {
  if (!Number.isFinite(percent)) return "";
  return percent >= danger ? "level-danger" : percent >= warn ? "level-warn" : "level-ok";
}

function formatPercent(value) {
  return Number.isFinite(value) ? `${value.toFixed(1)}%` : "—";
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return days ? `${days} ngày ${hours} giờ` : hours ? `${hours} giờ ${minutes} phút` : `${minutes} phút`;
}

function systemBar(label, percent, detail, warn, danger) {
  const width = Number.isFinite(percent) ? Math.min(100, Math.max(0, percent)) : 0;
  return `
    <div class="sys-row">
      <div class="sys-row-head"><span>${escapeHtml(label)}</span><strong>${escapeHtml(detail)}</strong></div>
      <div class="sys-bar ${usageLevel(percent, warn, danger)}"><span style="width:${width}%"></span></div>
    </div>`;
}

function systemValues(items) {
  return `<div class="sys-kv">${items.map(([label, value, level = ""]) => `
    <div class="${level}"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`).join("")}</div>`;
}

function setSystemCard(name, value, detail, percent, warn, danger) {
  const card = document.querySelector(`[data-sys-card="${name}"]`);
  if (!card) return;
  card.classList.remove("level-ok", "level-warn", "level-danger");
  const level = usageLevel(percent, warn, danger);
  if (level) card.classList.add(level);
  card.querySelector("strong").textContent = value;
  card.querySelector(".sys-metric-detail").textContent = detail;
}

function renderAdminSystem(data) {
  const host = data.host || {};
  const cpu = data.cpu || {};
  const memory = data.memory || {};
  const pressure = data.pressure || {};
  const container = data.container;
  const jobs = data.jobs || {};
  const cpuCount = host.cpu_count || 1;
  const load = host.load_average || [];
  const loadPercent = Number.isFinite(load[0]) ? (load[0] / cpuCount) * 100 : NaN;

  setSystemCard("cpu", formatPercent(cpu.usage_percent),
    `${cpuCount} nhân · iowait ${formatPercent(cpu.iowait_percent)} · steal ${formatPercent(cpu.steal_percent)}`,
    cpu.usage_percent, 75, 90);
  setSystemCard("memory", formatPercent(memory.used_percent),
    memory.total_bytes ? `${formatBytes(memory.used_bytes)} / ${formatBytes(memory.total_bytes)} · còn ${formatBytes(memory.available_bytes)}` : "Không có dữ liệu",
    memory.used_percent, 75, 90);
  setSystemCard("swap", memory.swap_total_bytes ? formatPercent(memory.swap_used_percent) : "Tắt",
    memory.swap_total_bytes ? `${formatBytes(memory.swap_used_bytes)} / ${formatBytes(memory.swap_total_bytes)}` : "Không có swap",
    memory.swap_used_percent, 50, 80);
  setSystemCard("load", Number.isFinite(load[0]) ? load[0].toFixed(2) : "—",
    load.length ? `5 phút ${load[1].toFixed(2)} · 15 phút ${load[2].toFixed(2)} · ${cpuCount} nhân` : "Không có dữ liệu",
    loadPercent, 80, 120);

  const alerts = [];
  if (memory.available_bytes && memory.available_bytes < 1024 ** 3) alerts.push(["danger", `RAM trống chỉ còn ${formatBytes(memory.available_bytes)} — nguy cơ OOM-kill.`]);
  if (memory.swap_used_percent >= 80) alerts.push(["danger", `Swap đã dùng ${formatPercent(memory.swap_used_percent)} — máy đang thiếu RAM.`]);
  if (pressure.memory >= 10) alerts.push(["warn", `Áp lực bộ nhớ cao: tiến trình bị chờ RAM ${pressure.memory.toFixed(1)}% thời gian (10 giây gần nhất).`]);
  if (container?.oom_kill_count) alerts.push(["danger", `Container backend đã bị OOM-kill ${container.oom_kill_count} lần kể từ khi khởi động.`]);
  const restartLimit = jobs.max_job_restarts || 0;
  (jobs.active || []).filter(job => job.restart_count > 0).forEach(job => {
    alerts.push([restartLimit && job.restart_count >= restartLimit ? "danger" : "warn",
      `Job ${job.job_id} đã bị chạy lại ${job.restart_count}/${restartLimit} lần do server khởi động lại.`]);
  });
  if (data.ollama && !data.ollama.reachable) alerts.push(["warn", "Không kết nối được Ollama — model dịch nội bộ không khả dụng."]);
  $("#sysAlerts").innerHTML = alerts.map(([level, text]) => `<div class="sys-alert ${level === "warn" ? "warn" : ""}">⚠️ ${escapeHtml(text)}</div>`).join("");

  $("#sysMemoryPanel").innerHTML = memory.total_bytes ? [
    systemBar("RAM đã dùng", memory.used_percent, `${formatBytes(memory.used_bytes)} / ${formatBytes(memory.total_bytes)}`, 75, 90),
    memory.swap_total_bytes ? systemBar("Swap đã dùng", memory.swap_used_percent, `${formatBytes(memory.swap_used_bytes)} / ${formatBytes(memory.swap_total_bytes)}`, 50, 80) : "",
    systemValues([
      ["RAM còn trống", formatBytes(memory.available_bytes), memory.available_bytes < 1024 ** 3 ? "level-danger" : ""],
      ["Cache / buffer", formatBytes(memory.cached_bytes)],
      ["Chờ RAM (PSI)", formatPercent(pressure.memory), pressure.memory >= 10 ? "level-danger" : ""],
      ["Chờ CPU (PSI)", formatPercent(pressure.cpu)],
      ["Chờ I/O (PSI)", formatPercent(pressure.io)],
      ["Uptime máy chủ", formatDuration(host.uptime_seconds)],
    ]),
  ].join("") : '<div class="sys-empty">Không đọc được thông tin bộ nhớ (máy chủ không phải Linux).</div>';

  const process = data.process || {};
  $("#sysContainerPanel").innerHTML = (container ? [
    container.memory_limit_bytes
      ? systemBar("RAM container / giới hạn", container.memory_used_percent, `${formatBytes(container.memory_bytes)} / ${formatBytes(container.memory_limit_bytes)}`, 75, 90)
      : "",
  ].join("") : "") + systemValues([
    ["RAM container", container ? formatBytes(container.memory_bytes) : "—"],
    ["Giới hạn RAM", container ? (container.memory_limit_bytes ? formatBytes(container.memory_limit_bytes) : "Không giới hạn") : "—"],
    ["CPU container", container && Number.isFinite(container.cpu_percent) ? `${container.cpu_percent.toFixed(1)}%` : "—"],
    ["Swap container", container && Number.isFinite(container.swap_bytes) ? formatBytes(container.swap_bytes) : "—"],
    ["OOM-kill", container ? String(container.oom_kill_count) : "—", container?.oom_kill_count ? "level-danger" : ""],
    ["RSS API (uvicorn)", Number.isFinite(process.rss_bytes) ? formatBytes(process.rss_bytes) : "—"],
    ["Số luồng", process.threads != null ? String(process.threads) : "—"],
    ["ID container", host.hostname || "—"],
  ]);

  const disks = data.disks || [];
  $("#sysDiskPanel").innerHTML = disks.length
    ? disks.map(disk => systemBar(disk.label, disk.used_percent, `${formatBytes(disk.used_bytes)} / ${formatBytes(disk.total_bytes)} · trống ${formatBytes(disk.free_bytes)}`, 80, 92)).join("")
    : '<div class="sys-empty">Không có dữ liệu ổ đĩa.</div>';

  const ollama = data.ollama;
  const models = ollama?.models || [];
  $("#sysOllamaPanel").innerHTML = !ollama
    ? '<div class="sys-empty">Ollama chưa được cấu hình.</div>'
    : !ollama.reachable
      ? '<div class="sys-empty">Không kết nối được Ollama.</div>'
      : models.length
        ? systemValues(models.map(model => [
            model.name,
            `${formatBytes(model.size_bytes)}${model.vram_bytes ? ` · VRAM ${formatBytes(model.vram_bytes)}` : " · CPU"}${model.expires_at ? ` · nhả lúc ${new Date(model.expires_at).toLocaleTimeString("vi-VN")}` : ""}`,
          ]))
        : '<div class="sys-empty">Không có model nào đang nạp trong RAM.</div>';

  const counts = jobs.counts || {};
  const active = jobs.active || [];
  $("#sysJobsPanel").innerHTML = systemValues([
    ["Job đang xử lý", `${counts.processing || 0} / ${jobs.worker_concurrency || "—"} job song song`],
    ["Concurrent trong TTS", jobs.tts_concurrency === "auto" ? "Tự động theo CPU/provider" : `${jobs.tts_concurrency} worker/job`],
    ["Đang chờ", String((counts.queued || 0) + (counts.scheduled || 0))],
    ["Hoàn tất", String(counts.completed || 0)],
    ["Thất bại", String(counts.failed || 0), counts.failed ? "level-danger" : ""],
    ["Giới hạn chạy lại", jobs.max_job_restarts ? `${jobs.max_job_restarts} lần` : "—"],
  ]) + (active.length ? active.map(job => {
    const progress = Math.round((job.progress || 0) * 100);
    const started = job.started_at ? new Date(job.started_at).toLocaleString("vi-VN") : "—";
    return `
      <div class="sys-job">
        <strong>${escapeHtml(job.job_id)} · ${escapeHtml(job.step || "")}</strong>
        <strong>${progress}%</strong>
        <small>${escapeHtml(job.filename || "")}</small>
        <small>${escapeHtml(job.step_detail || "")} · bắt đầu ${escapeHtml(started)}${job.restart_count ? ` · chạy lại ${job.restart_count} lần` : ""}</small>
      </div>`;
  }).join("") : '<div class="sys-empty">Không có job nào đang xử lý.</div>');

  const collected = data.collected_at ? new Date(data.collected_at * 1000) : new Date();
  $("#sysUpdatedAt").textContent = `Cập nhật lúc ${collected.toLocaleTimeString("vi-VN")} · số liệu RAM/CPU/tải là của toàn máy chủ`;
}

function renderAdminProcesses(data) {
  state.adminProcesses = data;
  const sortBy = state.adminProcessSort || "memory";
  $$("[data-sys-sort]").forEach(btn => btn.classList.toggle("active", btn.dataset.sysSort === sortBy));
  $("#sysProcessScope").textContent = data.host_wide
    ? `Toàn máy chủ · ${data.process_count} tiến trình · CPU 100% = 1 nhân · chỉ hiện tên tiến trình, không hiện dòng lệnh`
    : `Chỉ trong container backend (${data.process_count} tiến trình) — chưa mount /proc của máy chủ`;
  $("#sysProcessGroups").innerHTML = (data.groups || []).map(group => `
    <span class="sys-chip"><strong>${escapeHtml(group.name)}</strong> · ${formatBytes(group.rss_bytes)} · CPU ${group.cpu_percent.toFixed(1)}% · ${group.count} tiến trình</span>`).join("");
  const rows = sortBy === "cpu" ? data.by_cpu : data.by_memory;
  $("#sysProcessList").innerHTML = (rows || []).length ? rows.map(item => `
    <div class="sys-process-row">
      <strong title="PID ${item.pid}">${escapeHtml(item.name)} <small>#${item.pid}</small></strong>
      <span data-label="Nhóm:">${escapeHtml(item.group)}</span>
      <span data-label="RAM:" class="${usageLevel(item.memory_percent, 15, 30)}">${formatBytes(item.rss_bytes)}</span>
      <span data-label="% RAM:">${formatPercent(item.memory_percent)}</span>
      <span data-label="CPU:" class="${usageLevel(item.cpu_percent, 80, 150)}">${formatPercent(item.cpu_percent)}</span>
      <span data-label="Đã chạy:">${formatDuration(item.uptime_seconds)}</span>
    </div>`).join("") : '<div class="sys-empty">Không đọc được danh sách tiến trình.</div>';
}

function adminSystemVisible() {
  return !document.hidden
    && !$("#adminView")?.classList.contains("is-hidden")
    && !$("#adminSubtabSystem")?.classList.contains("is-hidden");
}

async function loadAdminSystem() {
  if (state.adminSystemLoading) return;
  state.adminSystemLoading = true;
  try {
    const [metrics, processes] = await Promise.allSettled([
      requestJson("/api/v1/admin/system-metrics"),
      requestJson("/api/v1/admin/system-processes?limit=15"),
    ]);
    if (metrics.status === "fulfilled") renderAdminSystem(metrics.value);
    else $("#sysUpdatedAt").textContent = metrics.reason?.message || "Không tải được số liệu";
    if (processes.status === "fulfilled") renderAdminProcesses(processes.value);
    else $("#sysProcessList").innerHTML = `<div class="sys-empty">${escapeHtml(processes.reason?.message || "Không tải được tiến trình")}</div>`;
  } finally {
    state.adminSystemLoading = false;
  }
}

// The server pushes a metrics + process snapshot every few seconds while the
// page is open; HTTP polling only covers the time the socket is down.
const systemEvents = createLiveEvents({
  path: "/api/v1/admin/system/events",
  messageType: "system",
  isActive: () => state.appStarted && adminSystemVisible(),
  poll: () => { if (adminSystemVisible()) loadAdminSystem(); },
  fallbackPollMs: SYSTEM_REFRESH_MS,
  onMessage(message) {
    if (message.error) {
      $("#sysUpdatedAt").textContent = message.error;
      return;
    }
    if (message.metrics) renderAdminSystem(message.metrics);
    if (message.processes) renderAdminProcesses(message.processes);
  },
});

function syncAdminSystemEvents() {
  if (state.appStarted && adminSystemVisible() && $("#sysAutoRefresh")?.checked) systemEvents.start();
  else systemEvents.stop();
}

function startAdminSystemRefresh() {
  syncAdminSystemEvents();
  // The socket sends a snapshot as soon as it opens; without it load once.
  if (!$("#sysAutoRefresh")?.checked) loadAdminSystem();
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
    const actor = evt.actor_email || evt.actor_user_id || "—";
    const target = evt.target_email || evt.target_user_id || "—";
    const metaStr = JSON.stringify(evt.metadata || {});
    const resourceType = evt.resource_type || "—";
    const resourceId = evt.resource_id ? `:${evt.resource_id}` : "";
    const resourceFull = `${resourceType}${resourceId}`;

    return `
      <div class="admin-audit-row">
        <div class="audit-col audit-col-time">
          <span class="audit-col-label">Thời gian</span>
          <span class="audit-time-val">${escapeHtml(time)}</span>
        </div>
        <div class="audit-col audit-col-actor">
          <span class="audit-col-label">Quản trị viên</span>
          <strong class="audit-actor-val" title="${escapeHtml(actor)}">${escapeHtml(actor)}</strong>
        </div>
        <div class="audit-col audit-col-action">
          <span class="audit-col-label">Hành động</span>
          <span class="badge badge-info audit-badge" title="${escapeHtml(evt.action || "—")}">${escapeHtml(evt.action || "—")}</span>
        </div>
        <div class="audit-col audit-col-resource">
          <span class="audit-col-label">Tài nguyên</span>
          <span class="audit-resource-val" title="${escapeHtml(resourceFull)}">${escapeHtml(resourceFull)}</span>
        </div>
        <div class="audit-col audit-col-target">
          <span class="audit-col-label">Mục tiêu</span>
          <span class="audit-target-val" title="${escapeHtml(target)}">${escapeHtml(target)}</span>
        </div>
        <div class="audit-col audit-col-meta">
          <span class="audit-col-label">Chi tiết</span>
          <div class="audit-metadata-code" title="${escapeHtml(metaStr)}">${escapeHtml(metaStr)}</div>
        </div>
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
        reject(new Error(errorDetail(payload.detail) || "Không thể tạo batch"));
      }
    });
    request.addEventListener("error", () => reject(new Error("Mất kết nối khi tải video")));
    request.addEventListener("abort", () => reject(new Error("Đã hủy tải batch")));
    request.send(formData);
  });
}

let pendingBatchSubmission = null;

function showConfirmJobModal(formData) {
  pendingBatchSubmission = formData;
  const count = state.files.length + state.links.length;
  const mode = $("#mode")?.value || "1";
  const modeText = $("#modeSummary")?.textContent?.trim() || (mode === "4" ? "Chỉ tải video" : (mode === "2" ? "Phụ đề + Voice" : (mode === "3" ? "Voice + Nhạc" : "Phụ đề")));
  const langText = mode === "4"
    ? "Chỉ tải video gốc"
    : ($("#languageSummary")?.textContent?.trim() || `${$("#sourceLanguage option:checked")?.textContent || "Tự động"} → ${$("#targetLanguage option:checked")?.textContent || "Việt"}`);

  if ($("#confirmSourceCount")) $("#confirmSourceCount").textContent = `${count} video (${state.links.length} link, ${state.files.length} file)`;
  if ($("#confirmMode")) $("#confirmMode").textContent = modeText;
  if ($("#confirmLang")) $("#confirmLang").textContent = langText;

  const voiceWrapper = $("#confirmVoiceWrapper");
  if (voiceWrapper) {
    if (mode === "1" || mode === "4") {
      voiceWrapper.style.display = "none";
    } else {
      voiceWrapper.style.display = "";
      const voiceSelect = $("#ttsVoice");
      const voiceOption = voiceSelect?.selectedOptions?.[0];
      if ($("#confirmVoice")) $("#confirmVoice").textContent = voiceOption?.textContent || "Tự động theo giọng";
    }
  }

  const batchName = String(formData.get("batch_name") || "").trim() || "Batch video mới";
  if ($("#confirmBatchName")) $("#confirmBatchName").textContent = batchName;
  if ($("#confirmSourcesCount")) $("#confirmSourcesCount").textContent = String(count);

  const sourcesList = $("#confirmSourcesList");
  if (sourcesList) {
    const items = [
      ...state.files.map(f => ({ isFile: true, text: f.name })),
      ...state.links.map(l => ({ isFile: false, text: l })),
    ];
    const preview = items.slice(0, 3);
    const hasMore = items.length > 3;
    sourcesList.innerHTML = preview.map(item => `
      <li>
        <span class="source-icon">${item.isFile ? "FILE" : "LINK"}</span>
        <span class="source-text" title="${escapeHtml(item.text)}">${escapeHtml(item.text)}</span>
      </li>
    `).join("") + (hasMore ? `<li class="source-more">+ ${items.length - 3} video khác...</li>` : "");
  }

  const dialog = $("#confirmJobDialog");
  if (dialog) {
    document.body.classList.add("modal-open");
    try {
      if (dialog.open && typeof dialog.close === "function") {
        dialog.close();
      }
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    } catch (err) {
      dialog.setAttribute("open", "");
    }
  }
}

function closeConfirmJobDialog() {
  pendingBatchSubmission = null;
  document.body.classList.remove("modal-open");
  const dialog = $("#confirmJobDialog");
  if (dialog) {
    try {
      if (dialog.open && typeof dialog.close === "function") {
        dialog.close();
      }
    } catch (err) {}
    dialog.removeAttribute("open");
  }
}

async function onConfirmJobProceed() {
  if (!pendingBatchSubmission) return;
  const formData = pendingBatchSubmission;
  const proceedBtn = $("#confirmJobProceed");
  const proceedText = $("#confirmJobProceedText");
  const prevText = proceedText ? proceedText.textContent : "Xác nhận tạo job";
  if (proceedBtn) proceedBtn.disabled = true;
  if (proceedText) proceedText.textContent = "Đang khởi tạo…";

  closeConfirmJobDialog();
  if (proceedBtn) proceedBtn.disabled = false;
  if (proceedText) proceedText.textContent = prevText;

  await executeSubmitBatch(formData);
}

async function executeSubmitBatch(formData) {
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
  if ($("#mobileQuickSubmitButton")) $("#mobileQuickSubmitButton").disabled = true;
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
    if ($("#mobileQuickSubmitButton")) $("#mobileQuickSubmitButton").disabled = false;
    $("#uploadProgress").classList.add("is-hidden");
    $("#uploadFill").style.width = "0";
  }
}

async function submitBatch(event) {
  event?.preventDefault?.();
  requestJobNotificationPermission();
  // This tab already says the jobs were created; the live "created" notice is for other tabs/devices.
  state.lastJobSubmitAt = Date.now();
  renderSourceLinks();
  const sourceText = $("#sourceLinks")?.value?.trim() || "";
  if (!state.files.length && !state.links.length) {
    $("#workflow-source")?.scrollIntoView({ behavior: "smooth", block: "start" });
    const dropZone = $("#dropZone");
    if (dropZone) {
      dropZone.classList.add("source-highlight-pulse");
      setTimeout(() => dropZone.classList.remove("source-highlight-pulse"), 1600);
    }
    return toast(sourceText ? "Không tìm thấy link TikTok, Douyin, YouTube, Facebook hoặc Google Drive hợp lệ" : "Hãy chọn file hoặc dán ít nhất một link video", true);
  }
  if (state.invalidLinks?.length) {
    $("#workflow-source")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return toast(invalidSourceLinksMessage(state.invalidLinks), true);
  }
  if (state.files.length + state.links.length > 50) return toast("Mỗi batch chỉ nhận tối đa 50 video", true);
  if ($("#mode")?.value !== "4" && $("#sourceLanguage")?.value !== "auto" && $("#sourceLanguage")?.value === $("#targetLanguage")?.value) {
    return toast("Ngôn ngữ nguồn và đầu ra phải khác nhau", true);
  }
  updateFormSummary();
  const form = $("#batchForm");
  if (!form) return toast("Không tìm thấy form tạo batch", true);
  const formData = new FormData(form);
  // An empty multiple-file input is otherwise serialized as a zero-byte file
  // by some browsers. Link-only batches must not be mistaken for bad uploads.
  if (!state.files.length) formData.delete("files");
  const keepOriginal = Boolean($('[name="keep_original_audio"]', form)?.checked);
  formData.set("keep_original_audio", String(keepOriginal));
  const music = formData.get("background_music");
  if (music instanceof File && !music.name) formData.delete("background_music");
  const sourceCookies = formData.get("source_cookies");
  if (sourceCookies instanceof File && !sourceCookies.name) formData.delete("source_cookies");
  if ($("#mode")?.value === "3") {
    // An uploaded file wins over the library pick; send only one of them.
    if (formData.has("background_music")) formData.delete("background_music_id");
    else if (!formData.get("background_music_id")) return toast("Chế độ nhạc nền: hãy chọn nhạc trong thư viện hoặc tải file nhạc lên", true);
  } else {
    formData.delete("background_music");
    formData.delete("background_music_id");
  }

  showConfirmJobModal(formData);
}

function jobActions(job) {
  const terminal = ["completed", "failed", "cancelled"].includes(job.status);
  const awaitingChoice = job.status === "awaiting_input";
  const detail = `<button type="button" class="btn-job-action btn-detail" data-action="detail" data-job="${escapeHtml(job.job_id)}" title="Xem chi tiết tiến trình" aria-label="Xem chi tiết tiến trình"><svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg><span>Chi tiết</span></button>`;
  let downloads = "";
  if (job.video_url) {
    const videoName = job.output_video || `${job.job_id}.mp4`;
    downloads += `<button type="button" class="btn-job-action btn-download" data-download-url="${escapeHtml(job.video_url)}" data-download-name="${escapeHtml(videoName)}" title="Tải video MP4 hoàn tất" aria-label="Tải video MP4 hoàn tất"><svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg><span translate="no">Video</span></button>`;
  }
  if (job.images_url) {
    downloads += (downloads ? " " : "") + `<button type="button" class="btn-job-action btn-download btn-zip" data-download-url="${escapeHtml(job.images_url)}" data-download-name="${escapeHtml(`${job.job_id}_images.zip`)}" title="Tải ảnh của bài đăng (ZIP)" aria-label="Tải ảnh của bài đăng (ZIP)"><span>Ảnh (ZIP)</span></button>`;
  }
  if (awaitingChoice) {
    downloads += (downloads ? " " : "") + `<button type="button" class="btn-job-action btn-detail" data-action="detail" data-job="${escapeHtml(job.job_id)}" title="Chọn tải ảnh hoặc tạo video ghép ảnh" aria-label="Chọn tải ảnh hoặc tạo video ghép ảnh"><span>Chọn cách xử lý</span></button>`;
  }
  if (job.download_all_url) {
    const zipName = `${job.job_id}.artifacts.zip`;
    downloads += (downloads ? " " : "") + `<button type="button" class="btn-job-action btn-download btn-zip" data-download-url="${escapeHtml(job.download_all_url)}" data-download-name="${escapeHtml(zipName)}" title="Tải toàn bộ tài nguyên (ZIP)" aria-label="Tải toàn bộ tài nguyên (ZIP)"><svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg><span translate="no">ZIP</span></button>`;
  }
  const cancel = ["queued", "scheduled", "processing"].includes(job.status)
    ? `<button type="button" class="btn-job-action btn-cancel" data-action="cancel" data-job="${job.job_id}" title="Hủy tiến trình" aria-label="Hủy tiến trình"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg><span>Hủy</span></button>`
    : "";
  const retry = terminal
    ? `<button type="button" class="btn-job-action btn-retry" data-action="retry" data-job="${job.job_id}" title="Chạy lại video này" aria-label="Chạy lại video này"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg><span>Chạy lại</span></button>`
    : "";
  const remove = terminal || awaitingChoice
    ? `<button type="button" class="btn-job-action btn-delete" data-action="delete" data-job="${job.job_id}" title="Xóa video và dữ liệu liên quan" aria-label="Xóa video và dữ liệu liên quan"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg><span>Xóa</span></button>`
    : "";
  const refreshTikTok = job.tiktok_publish_id && !["PUBLISH_COMPLETE", "FAILED"].includes(job.tiktok_publish_status)
    ? `<button type="button" class="btn-job-action btn-tiktok-status" data-action="tiktok-status" data-job="${job.job_id}" title="Cập nhật trạng thái TikTok" aria-label="Cập nhật trạng thái TikTok"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64c.298-.002.595.042.88.13V9.4a6.33 6.33 0 0 0-1-.08A6.34 6.34 0 0 0 3 15.66a6.34 6.34 0 0 0 10.82 4.49 6.27 6.27 0 0 0 1.86-4.49v-7a8.16 8.16 0 0 0 4.77 1.52v-3.4a4.85 4.85 0 0 1-.86-.09z"/></svg><span translate="no">TikTok</span></button>`
    : "";
  const review = job.status === "completed" && job.video_url
    && (state.auth.role === "owner" || !state.auth.user_id || job.owner_user_id === state.auth.user_id)
    ? `<button type="button" data-action="tiktok-review" data-job="${escapeHtml(job.job_id)}" class="btn-job-action tiktok-review-action" title="Mở trang chuẩn bị và đăng bài" aria-label="Mở trang chuẩn bị và đăng bài"><svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"></path><path d="m22 2-7 20-4-9-9-4Z"></path></svg><span>Đăng bài</span></button>`
    : "";
  return detail + downloads + refreshTikTok + cancel + retry + remove + review;
}

function eligibleJobs(action) {
  const selected = state.jobs.filter(job => state.selectedJobIds.has(job.job_id));
  if (action === "cancel") return selected.filter(job => ["queued", "scheduled", "processing"].includes(job.status));
  if (action === "delete") return selected.filter(job => ["completed", "failed", "cancelled", "awaiting_input"].includes(job.status));
  return selected.filter(job => ["completed", "failed", "cancelled"].includes(job.status));
}

function getCurrentPageJobs() {
  return state.jobs || [];
}

function getPaginationPages(current, total) {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  if (current <= 4) {
    return [1, 2, 3, 4, 5, "...", total];
  }
  if (current >= total - 3) {
    return [1, "...", total - 4, total - 3, total - 2, total - 1, total];
  }
  return [1, "...", current - 1, current, current + 1, "...", total];
}

function renderJobsPagination(totalJobs, startIndex, endIndex, page, totalPages, pageSize) {
  const container = $("#jobsPagination");
  if (!container) return;

  if (totalJobs === 0) {
    container.innerHTML = `
      <div class="pagination-info">
        <span class="pagination-text">Chưa có tiến trình nào</span>
      </div>
    `;
    return;
  }

  const pages = getPaginationPages(page, totalPages);
  const pagesHtml = pages.map(p => {
    if (p === "...") {
      return `<span class="pagination-ellipsis" aria-hidden="true">…</span>`;
    }
    const isCurrent = p === page;
    return `
      <button type="button" class="pagination-page-btn${isCurrent ? " active" : ""}" data-page="${p}" ${isCurrent ? 'aria-current="page"' : ""} aria-label="Trang ${p}">
        ${p}
      </button>
    `;
  }).join("");

  container.innerHTML = `
    <div class="pagination-info">
      <span class="pagination-text">Hiển thị <strong>${startIndex + 1}</strong> – <strong>${endIndex}</strong> trong số <strong>${totalJobs}</strong> tiến trình</span>
      <div class="pagination-size-wrap">
        <label for="jobsPageSizeSelect">Hiển thị:</label>
        <select id="jobsPageSizeSelect" class="pagination-size-select" aria-label="Số tiến trình mỗi trang">
          <option value="10" ${pageSize === 10 ? "selected" : ""}>10 / trang</option>
          <option value="20" ${pageSize === 20 ? "selected" : ""}>20 / trang</option>
          <option value="50" ${pageSize === 50 ? "selected" : ""}>50 / trang</option>
          <option value="100" ${pageSize === 100 ? "selected" : ""}>100 / trang</option>
        </select>
      </div>
    </div>

    <div class="pagination-nav" role="navigation" aria-label="Phân trang tiến trình">
      <button type="button" class="pagination-btn pagination-first" data-page="1" ${page <= 1 ? "disabled" : ""} title="Trang đầu" aria-label="Trang đầu">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="11 17 6 12 11 7"></polyline><polyline points="18 17 13 12 18 7"></polyline></svg>
      </button>
      <button type="button" class="pagination-btn pagination-prev" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""} title="Trang trước" aria-label="Trang trước">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>
        <span class="btn-label-prev">Trước</span>
      </button>

      <div class="pagination-pages">
        ${pagesHtml}
      </div>

      <button type="button" class="pagination-btn pagination-next" data-page="${page + 1}" ${page >= totalPages ? "disabled" : ""} title="Trang sau" aria-label="Trang sau">
        <span class="btn-label-next">Sau</span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
      </button>
      <button type="button" class="pagination-btn pagination-last" data-page="${totalPages}" ${page >= totalPages ? "disabled" : ""} title="Trang cuối" aria-label="Trang cuối">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="13 17 18 12 13 7"></polyline><polyline points="6 17 11 12 6 7"></polyline></svg>
      </button>
    </div>
  `;
}

function updateJobSelectionUI() {
  const pageJobs = getCurrentPageJobs();
  const pageIds = pageJobs.map(job => job.job_id);
  const selectedOnPage = pageIds.filter(jobId => state.selectedJobIds.has(jobId));
  const selectAll = $("#selectAllJobs");
  if (selectAll) {
    selectAll.checked = pageIds.length > 0 && selectedOnPage.length === pageIds.length;
    selectAll.indeterminate = selectedOnPage.length > 0 && selectedOnPage.length < pageIds.length;
    selectAll.disabled = pageIds.length === 0;
  }
  const totalSelected = state.selectedJobIds.size;
  $("#selectedJobsCount").textContent = totalSelected ? `Đã chọn ${totalSelected} tiến trình` : "Chưa chọn job";
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
    if (button) {
      button.disabled = count === 0;
      button.innerHTML = `<span class="btn-icon">${icons[action]}</span><span>${count ? `${labels[action]} (${count})` : labels[action]}</span>`;
    }
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
    ? `<button class="btn-job-action btn-cancel" data-upload-cancel="${escapeHtml(upload.id)}" type="button" title="Hủy tải lên" aria-label="Hủy tải lên"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg><span>Hủy</span></button>`
    : `<button class="btn-job-action btn-dismiss" data-upload-dismiss="${escapeHtml(upload.id)}" type="button" title="Ẩn dòng này" aria-label="Ẩn dòng này"><svg class="btn-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg><span>Ẩn</span></button>`;
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
    <time class="job-created job-created-pending">Đang tạo…</time>
    <span class="status-pill status-${statusClass}"><span class="status-dot"></span><span>${statusText}</span></span>
    <div class="job-progress"><div class="progress-track ${active ? "progress-active" : "progress-failed"}"><span style="width:${active ? progress : 100}%"></span></div><small>${active ? `${progress}%` : "—"}</small></div>
    <div class="job-step"><strong>${progress >= 100 && active ? "Đang tạo tiến trình" : active ? "Đang tải batch" : "Tải lên không thành công"}</strong><small title="${escapeHtml(upload.detail)}">${escapeHtml(upload.detail)}</small></div>
    <div class="job-actions">${action}</div>
  </article>`;
}

// The account logo stands in for the play icon on the user's own jobs; the status
// colour stays as a ring around it. Other owners' jobs (admin view) keep the icon.
function jobIconHtml(job) {
  const ownJob = !job.owner_user_id || !state.auth.user_id || job.owner_user_id === state.auth.user_id;
  const status = escapeHtml(job.status);
  if (ownJob && state.userLogoUrl) {
    return `<div class="job-file-icon video-icon-badge has-logo status-accent-${status}" aria-hidden="true"><img src="${escapeHtml(state.userLogoUrl)}" alt="" loading="lazy" /></div>`;
  }
  return `<div class="job-file-icon video-icon-badge status-accent-${status}" aria-hidden="true">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
        </div>`;
}

function renderJobs(jobs) {
  const pageJobs = jobs || [];
  const totalJobs = Number.isFinite(state.jobsTotal) ? state.jobsTotal : pageJobs.length;
  const pageSize = state.jobsPageSize || 10;
  const totalPages = Math.max(1, Math.ceil(totalJobs / pageSize));

  if (state.jobsPage > totalPages) state.jobsPage = totalPages;
  if (state.jobsPage < 1) state.jobsPage = 1;

  const page = state.jobsPage;
  const startIndex = (page - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageJobs.length, totalJobs);

  const pendingHtml = page === 1 ? state.pendingUploads.map(renderPendingUpload).join("") : "";
  if (!totalJobs && !state.pendingUploads.length) {
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
    renderJobsPagination(0, 0, 0, 1, 1, pageSize);
    updateJobSelectionUI();
    return;
  }
  const jobsHtml = pageJobs.map(job => {
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
    const batchBadge = job.batch_id ? `<span class="batch-tag" translate="no">${escapeHtml(job.batch_id)}</span>` : `<span class="single-tag">Video đơn</span>`;
    const ownerName = job.owner_display_name || job.owner_email || job.owner_user_id || "Chưa xác định";
    const ownerEmail = job.owner_email && job.owner_email !== ownerName ? job.owner_email : "";
    const createdDate = job.created_at ? new Date(job.created_at) : null;
    const hasValidCreatedDate = createdDate && !Number.isNaN(createdDate.getTime());
    const createdDateLabel = hasValidCreatedDate
      ? createdDate.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" })
      : "—";
    const createdTimeLabel = hasValidCreatedDate
      ? createdDate.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })
      : "";
    const createdDateTime = hasValidCreatedDate ? createdDate.toISOString() : "";
    return `<article class="job-row${selected ? " selected" : ""}" data-job-row="${escapeHtml(job.job_id)}">
      <label class="job-select" title="Chọn job"><input type="checkbox" data-job-select="${escapeHtml(job.job_id)}" ${selected ? "checked" : ""} aria-label="Chọn ${escapeHtml(job.filename || job.job_id)}" /></label>
      <div class="job-file">
        ${jobIconHtml(job)}
        <div class="job-file-meta">
          <strong title="${escapeHtml(job.filename || job.job_id)}">${escapeHtml(job.filename || job.job_id)}</strong>
          <small>${batchBadge}${queue}</small>
        </div>
      </div>
      <div class="job-owner" title="${escapeHtml(job.owner_email || ownerName)}">
        <strong>${escapeHtml(ownerName)}</strong>
        ${ownerEmail ? `<small>${escapeHtml(ownerEmail)}</small>` : ""}
      </div>
      <time class="job-created" ${createdDateTime ? `datetime="${createdDateTime}" title="${createdDateLabel} ${createdTimeLabel}"` : ""}>
        <strong>${createdDateLabel}</strong>
        ${createdTimeLabel ? `<small>${createdTimeLabel}</small>` : ""}
      </time>
      <span class="status-pill status-${escapeHtml(job.status)}"><span class="status-dot"></span><span>${statusLabels[job.status] || escapeHtml(job.status)}</span></span>
      <div class="job-progress"><div class="progress-track progress-${escapeHtml(job.status)}"><span style="width:${progress}%"></span></div><small>${progress}%</small></div>
      <div class="job-step"><strong>${escapeHtml(stepLabels[job.step] || job.step || "Đang cập nhật")}</strong><small title="${escapeHtml(detail)}">${escapeHtml(detail)}</small></div>
      <div class="job-actions">${jobActions(job)}</div>
    </article>`;
  }).join("");
  $("#jobsList").innerHTML = pendingHtml + jobsHtml;
  renderJobsPagination(totalJobs, startIndex, endIndex, page, totalPages, pageSize);
  updateJobSelectionUI();
}

function renderJobDashboard() {
  renderJobs(state.jobs);
  const counts = state.jobStatusCounts || state.jobs.reduce((result, job) => ({ ...result, [job.status]: (result[job.status] || 0) + 1 }), {});
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
  poll: () => {
    loadJobs();
    comicWorkspace.refreshLive();
  },
  onReconnected: () => {
    loadJobs();
    comicWorkspace.refreshLive();
  },
  onNotification: handleLiveNotification,
  onChanges(message) {
    const { jobs, reload } = applyJobChanges(state.jobs, message, { statusFilter: $("#statusFilter").value });
    state.jobs = jobs;
    trackJobOutcomes(message.items || []);
    scheduleJobRender();
    if (reload) scheduleJobsReload();
    comicWorkspace.onJobChanges(message);
    clipWorkspace.onJobChanges(message);
    if (activeDetailJobId) {
      const activeJob = state.jobs.find(j => j.job_id === activeDetailJobId);
      if (activeJob) renderJobDetailModal(activeJob, { preserveMedia: true });
    }
  },
});

const JOBS_REQUEST_TIMEOUT_MS = 30000;

// The TikTok browser sleeps on a blank page unless the Đăng bài view is open: the view sends
// a heartbeat that wakes it and keeps it awake. Only the owner may switch it off.
// The source-login browser (#douyin) is always on.
const BROWSER_POWER_VIEWS = { tiktok: "tiktok" };
const BROWSER_HEARTBEAT_MS = 60000;
let browserPowerTimer = null;
let browserPowerName = null;

function renderBrowserPower(name, status, { waking = false } = {}) {
  const bar = document.querySelector(`[data-browser-power="${name}"]`);
  if (!bar) return;
  const minutes = Math.round((status?.idle_timeout_seconds || 600) / 60);
  let stateName = "parked";
  let text = "Trình duyệt đang nghỉ để nhường CPU cho xử lý video; tự bật khi bạn mở trang này.";
  if (!status || status.available === false) {
    stateName = "down";
    text = "Không kết nối được trình duyệt.";
  } else if (!status.enabled) {
    stateName = "off";
    text = status.can_toggle
      ? "Trình duyệt đang tắt. Bấm “Bật trình duyệt” để dùng."
      : "Trình duyệt đang được chủ sở hữu tắt.";
  } else if (waking) {
    stateName = "waking";
    text = "Đang bật trình duyệt…";
  } else if (status.awake) {
    stateName = "awake";
    text = `Trình duyệt đang chạy; tự nghỉ sau ${minutes} phút không mở trang này.`;
  }
  bar.dataset.state = stateName;
  bar.querySelector(".browser-power-text").textContent = text;
  const toggle = bar.querySelector(".browser-power-toggle");
  const canToggle = Boolean(status?.can_toggle) && status.available !== false;
  toggle.classList.toggle("is-hidden", !canToggle);
  toggle.textContent = status?.enabled ? "Tắt trình duyệt" : "Bật trình duyệt";
  toggle.dataset.enable = status?.enabled ? "false" : "true";
}

async function sendBrowserHeartbeat(name) {
  if (document.hidden) return;
  try {
    const status = await requestJson(`/api/v1/browser-power/${name}/heartbeat`, { method: "POST", silent: true });
    if (browserPowerName === name) renderBrowserPower(name, status);
  } catch {
    if (browserPowerName === name) renderBrowserPower(name, null);
  }
}

function syncBrowserPower(view) {
  const name = state.appStarted ? BROWSER_POWER_VIEWS[view] || null : null;
  if (name === browserPowerName) return;
  clearInterval(browserPowerTimer);
  browserPowerTimer = null;
  browserPowerName = name;
  if (!name) return;
  renderBrowserPower(name, { available: true, enabled: true, awake: false }, { waking: true });
  sendBrowserHeartbeat(name);
  browserPowerTimer = setInterval(() => sendBrowserHeartbeat(name), BROWSER_HEARTBEAT_MS);
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && browserPowerName) sendBrowserHeartbeat(browserPowerName);
});
document.querySelectorAll(".browser-power-toggle").forEach(button => {
  button.addEventListener("click", async () => {
    const name = button.closest("[data-browser-power]").dataset.browserPower;
    const enabled = button.dataset.enable === "true";
    button.disabled = true;
    if (enabled) renderBrowserPower(name, { available: true, enabled: true, awake: false, can_toggle: true }, { waking: true });
    try {
      const status = await requestJson(`/api/v1/browser-power/${name}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      renderBrowserPower(name, status);
      toast(enabled ? "Đã bật trình duyệt" : "Đã tắt trình duyệt");
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
});

// Back on the tab, back from bfcache or back online: timers and the socket may
// have been frozen, so reconnect and fetch the current statuses once.
function resumeLiveJobs() {
  if (!state.appStarted) return;
  jobEvents.resume();
  loadJobs();
  comicWorkspace.refreshLive();
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") resumeLiveJobs();
});
window.addEventListener("pageshow", event => { if (event.persisted) resumeLiveJobs(); });
window.addEventListener("online", resumeLiveJobs);

// Job outcome notifications: a toast on the page, plus a system notification
// while the tab is in the background. Only a change this page saw happen is
// announced (last known status not final -> completed/failed), so opening the
// app never replays old results.
// "awaiting_input": a photo post waits for the owner to choose what to make of it.
const FINAL_JOB_STATUSES = new Set(["completed", "failed", "awaiting_input"]);
const seenJobStatuses = new Map();

function trackJobOutcomes(items) {
  for (const job of items) {
    if (!job?.job_id) continue;
    const previous = seenJobStatuses.get(job.job_id);
    seenJobStatuses.set(job.job_id, job.status);
    if (!previous || previous === job.status || FINAL_JOB_STATUSES.has(previous)) continue;
    if (!FINAL_JOB_STATUSES.has(job.status)) continue;
    // Admins receive every account's jobs; announce only their own.
    if (job.owner_user_id && state.auth.user_id && job.owner_user_id !== state.auth.user_id) continue;
    announceJobOutcome(job);
  }
}

// A job that failed for lack of speech can still hand back its source (mode 4).
const NO_SPEECH_ERROR = "no_speech";

function isNoSpeechJob(job) {
  return job?.status === "failed" && job.error_code === NO_SPEECH_ERROR;
}

// Photo post waiting for the owner: zip the images, or a slideshow of a chosen length.
let photoMusicTracks = null;

async function photoMusicOptions(hasOriginalMusic) {
  if (photoMusicTracks === null) {
    try {
      const library = await requestJson("/api/v1/music-tracks");
      photoMusicTracks = library.items || [];
      state.musicCategories = library.categories || state.musicCategories;
    } catch {
      photoMusicTracks = [];
    }
  }
  return '<option value="">+ Thêm bài nhạc từ thư viện…</option>' + musicTrackOptions(photoMusicTracks);
}

// Library tracks picked for the slideshow, in play order.
const PHOTO_MAX_MUSIC_TRACKS = 10;
let photoChosenTracks = [];
let photoMusicPreview = null;
let photoMusicPreviewTrackId = null;

function syncPhotoMusicPreviewButtons() {
  $("#photoChoiceMusicList")?.querySelectorAll('[data-action="preview"]').forEach(button => {
    const isPlaying = button.dataset.trackId === photoMusicPreviewTrackId;
    button.classList.toggle("is-playing", isPlaying);
    button.setAttribute("aria-pressed", String(isPlaying));
    button.setAttribute("aria-label", isPlaying ? "Dừng nghe thử bài này" : "Nghe thử bài này");
    button.innerHTML = isPlaying ? "⏸ <span>Dừng</span>" : "▶ <span>Nghe thử</span>";
  });
}

function stopPhotoMusicPreview() {
  if (photoMusicPreview) {
    photoMusicPreview.pause();
    photoMusicPreview.removeAttribute("src");
    photoMusicPreview.load();
  }
  photoMusicPreview = null;
  photoMusicPreviewTrackId = null;
  syncPhotoMusicPreviewButtons();
}

function togglePhotoMusicPreview(trackId) {
  if (photoMusicPreviewTrackId === trackId) {
    stopPhotoMusicPreview();
    return;
  }

  stopPhotoMusicPreview();
  const audio = new Audio(`/api/v1/music-tracks/${encodeURIComponent(trackId)}/audio`);
  photoMusicPreview = audio;
  photoMusicPreviewTrackId = trackId;
  audio.addEventListener("ended", stopPhotoMusicPreview, { once: true });
  audio.addEventListener("error", () => {
    if (photoMusicPreview !== audio) return;
    stopPhotoMusicPreview();
    toast("Không phát được bản nhạc", true);
  }, { once: true });
  syncPhotoMusicPreviewButtons();
  audio.play().catch(() => {
    if (photoMusicPreview !== audio) return;
    stopPhotoMusicPreview();
    toast("Không phát được bản nhạc", true);
  });
}

function renderPhotoChosenTracks(hasOriginalMusic) {
  const list = $("#photoChoiceMusicList");
  const byId = new Map((photoMusicTracks || []).map(track => [track.track_id, track]));
  list.innerHTML = photoChosenTracks.map((trackId, index) => {
    const track = byId.get(trackId);
    const label = track ? `${track.title} (${formatTrackLength(track.duration_seconds)})` : trackId;
    return `<li>
      <span class="photo-music-track-title">${escapeHtml(label)}</span>
      <button class="photo-music-preview-btn" type="button" data-action="preview" data-index="${index}" data-track-id="${escapeHtml(trackId)}" aria-label="Nghe thử bài này" aria-pressed="false">▶ <span>Nghe thử</span></button>
      <button class="photo-music-remove-btn" type="button" data-action="remove" data-index="${index}" aria-label="Bỏ bài này">✕</button>
    </li>`;
  }).join("");
  list.querySelectorAll('[data-action="preview"]').forEach(button => {
    button.onclick = () => togglePhotoMusicPreview(photoChosenTracks[Number(button.dataset.index)]);
  });
  list.querySelectorAll('[data-action="remove"]').forEach(button => {
    button.onclick = () => {
      const removedTrackId = photoChosenTracks.splice(Number(button.dataset.index), 1)[0];
      if (removedTrackId === photoMusicPreviewTrackId) stopPhotoMusicPreview();
      renderPhotoChosenTracks(hasOriginalMusic);
    };
  });
  syncPhotoMusicPreviewButtons();
  $("#photoChoiceMusicHint").textContent = photoChosenTracks.length > 1
    ? "Các bài phát nối tiếp theo thứ tự trên; video dài hơn thì lặp lại từ bài đầu."
    : photoChosenTracks.length
      ? "Bài nhạc lặp lại cho đủ độ dài video."
      : hasOriginalMusic
        ? "Chưa chọn bài nào: dùng nhạc gốc của bài đăng."
        : "Chưa chọn bài nào: bài đăng không có nhạc gốc nên video sẽ im lặng.";
}

function renderPhotoChoiceCard(job) {
  const card = $("#jobDetailPhotoCard");
  if (!card) return;
  const waiting = job.status === "awaiting_input";
  card.classList.toggle("is-hidden", !waiting);
  if (!waiting) {
    stopPhotoMusicPreview();
    return;
  }
  const count = job.photo_post?.image_count || 0;
  $("#jobDetailPhotoSummary").textContent = `Link này là bài đăng ${count} ảnh, không phải video. Chọn cách xử lý:`;
  const select = $("#photoChoiceMusic");
  const hasOriginalMusic = Boolean(job.photo_post?.has_music);
  if (select.dataset.jobId !== job.job_id) {
    stopPhotoMusicPreview();
    select.dataset.jobId = job.job_id;
    photoChosenTracks = [];
    renderPhotoChosenTracks(hasOriginalMusic);
    photoMusicOptions(hasOriginalMusic).then(html => {
      if (select.dataset.jobId === job.job_id) select.innerHTML = html;
    });
  }
  select.onchange = () => {
    const trackId = select.value;
    select.value = "";
    if (!trackId) return;
    if (photoChosenTracks.length >= PHOTO_MAX_MUSIC_TRACKS) {
      toast(`Chọn tối đa ${PHOTO_MAX_MUSIC_TRACKS} bài nhạc`, true);
      return;
    }
    photoChosenTracks.push(trackId);
    renderPhotoChosenTracks(hasOriginalMusic);
  };
  const send = async (button, choice) => {
    button.disabled = true;
    try {
      const response = await apiFetch(`/api/v1/jobs/${job.job_id}/photo-post`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(choice),
      });
      if (response.status === 401) lockApplication();
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorDetail(data.detail) || "Không gửi được lựa chọn");
      toast(choice.action === "images" ? "Đang gom ảnh để tải về" : "Đang dựng video ghép ảnh");
      await loadJobs();
      openJobDetailModal(job.job_id, { refresh: true });
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  };
  $("#photoChoiceImages").onclick = event => send(event.currentTarget, { action: "images" });
  $("#photoChoiceVideo").onclick = event => {
    const minutes = Number($("#photoChoiceMinutes").value);
    if (!Number.isFinite(minutes) || minutes < 0.25 || minutes > 10) {
      toast("Độ dài video phải từ 0,25 đến 10 phút", true);
      return;
    }
    send(event.currentTarget, { action: "video", minutes, music_track_ids: [...photoChosenTracks] });
  };
}

async function requestDownloadOnly(jobIds) {
  let started = 0;
  const failures = [];
  for (const jobId of jobIds) {
    try {
      const response = await apiFetch(`/api/v1/jobs/${jobId}/download-only`, { method: "POST" });
      if (response.status === 401) lockApplication();
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorDetail(data.detail) || "Không thể chuyển sang chỉ tải video");
      started += 1;
    } catch (error) {
      failures.push(error.message);
    }
  }
  if (started) toast(started === 1 ? "Đang chuẩn bị video gốc để tải về" : `Đang chuẩn bị ${started} video gốc để tải về`);
  if (failures.length) toast(failures[0], true);
  await loadJobs();
  return started;
}

function announceJobOutcome(job) {
  const name = job.filename || job.title || job.source_url || "Video của bạn";
  const succeeded = job.status === "completed";
  const noSpeech = isNoSpeechJob(job);
  const awaitingChoice = job.status === "awaiting_input";
  const title = succeeded ? "Đã xử lý xong" : awaitingChoice ? "Bài đăng ảnh: chọn cách xử lý"
    : noSpeech ? "Video không có lời để dịch" : "Xử lý thất bại";
  const body = succeeded
    ? `${name} đã sẵn sàng.`
    : awaitingChoice
      ? `${name} là bài đăng ${job.photo_post?.image_count || ""} ảnh. Mở job để chọn tải ảnh về hoặc tạo video ghép ảnh có nhạc.`
    : noSpeech
      ? `${name} chỉ có nhạc nên không tạo được phụ đề hay lồng tiếng. Mở job để chọn chỉ tải video gốc về.`
      : `${name} gặp lỗi. Mở job để xem chi tiết hoặc chạy lại.`;
  toast(`${title}: ${body}`, !succeeded && !awaitingChoice);
  if (!document.hidden || typeof Notification === "undefined" || Notification.permission !== "granted") return;
  try {
    const notification = new Notification(title, { body, tag: `${job.job_id}:${job.status}`, icon: "/assets/vidtrans-mark.svg" });
    notification.onclick = () => {
      window.focus();
      notification.close();
      openJobDetailModal(job.job_id);
    };
  } catch {
    // Some browsers only allow notifications from a service worker.
  }
}

function systemNotification(title, body, tag, onClick) {
  if (!document.hidden || typeof Notification === "undefined" || Notification.permission !== "granted") return;
  try {
    const notification = new Notification(title, { body, tag, icon: "/assets/vidtrans-mark.svg" });
    notification.onclick = () => {
      window.focus();
      notification.close();
      onClick?.();
    };
  } catch {
    // Some browsers only allow notifications from a service worker.
  }
}

// New jobs of this account arrive one notice each; a batch of 50 becomes one toast.
const pendingCreatedJobs = [];
let createdJobsTimer = null;

function announceCreatedJobs() {
  createdJobsTimer = null;
  const jobs = pendingCreatedJobs.splice(0);
  if (!jobs.length) return;
  const title = "Đã tạo video";
  const body = jobs.length === 1 ? jobs[0].body : `${jobs.length} video đã được tạo và đang chờ xử lý.`;
  toast(`${title}: ${body}`);
  systemNotification(title, body, `created:${jobs[0].job_id}`, () => openJobDetailModal(jobs[0].job_id));
}

function handleLiveNotification(notice) {
  if (!notice || !state.appStarted) return;
  if (notice.type === "job_status" && notice.status === "created") {
    // Created from this tab moments ago: its own toast already said so.
    if (Date.now() - (state.lastJobSubmitAt || 0) < 15000) return;
    pendingCreatedJobs.push(notice);
    if (!createdJobsTimer) createdJobsTimer = setTimeout(announceCreatedJobs, 1200);
    return;
  }
  if (notice.type === "job_report" && ["owner", "admin"].includes(state.auth.role)) {
    toast(`${notice.title}: ${notice.body}`, true);
    systemNotification(notice.title, notice.body, `report:${notice.report_id}`, () => {
      if (location.hash !== "#admin") location.hash = "#admin";
      setTimeout(() => $('[data-admin-subtab="reports"]')?.click(), 0);
    });
    refreshAdminReportsBadge();
    if (!$("#adminSubtabReports")?.classList.contains("is-hidden")) loadAdminReports();
  }
}

// Ask once, from the click that submits work, so the browser shows the prompt.
function requestJobNotificationPermission() {
  if (typeof Notification === "undefined" || Notification.permission !== "default") return;
  try {
    Notification.requestPermission().catch(() => {});
  } catch {
    // Older Safari: callback-only API.
  }
}

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
  const pageSize = state.jobsPageSize || 10;
  const page = Math.max(1, state.jobsPage || 1);
  const params = new URLSearchParams({
    page: String(page),
    limit: String(pageSize),
    offset: String((page - 1) * pageSize),
  });
  const status = $("#statusFilter").value;
  const search = $("#jobSearch").value.trim();
  if (status) params.set("status", status);
  if (search) params.set("search", search);
  // A stalled request (phone asleep, network switch) would otherwise keep
  // jobsLoading set for up to the proxy timeout and swallow every later reload.
  const controller = new AbortController();
  const abortTimer = setTimeout(() => controller.abort(), JOBS_REQUEST_TIMEOUT_MS);
  try {
    const response = await apiFetch(`/api/v1/jobs?${params}`, { signal: controller.signal });
    if (response.status === 401) lockApplication();
    if (!response.ok) throw new Error("Không tải được danh sách job");
    const data = await response.json();
    // The user moved to another page, page size, filter or search while this request ran:
    // its rows belong to the old view, and writing its page number back would undo the
    // click. A reload for the current view is queued instead.
    const stale = state.jobsPage !== page || (state.jobsPageSize || 10) !== pageSize
      || $("#statusFilter").value !== status || $("#jobSearch").value.trim() !== search;
    if (stale) {
      state.jobsReloadQueued = true;
      return;
    }
    state.jobs = data.items || [];
    trackJobOutcomes(state.jobs);
    state.jobsTotal = Number(data.total ?? state.jobs.length);
    // The server moves a page past the end (jobs deleted) back to the last one.
    if (data.page) state.jobsPage = Number(data.page);
    if (data.counts) state.jobStatusCounts = data.counts;
    const visibleIds = new Set(state.jobs.map(job => job.job_id));
    state.selectedJobIds = new Set([...state.selectedJobIds].filter(jobId => visibleIds.has(jobId)));
    renderJobDashboard();
  } catch (error) {
    if (error.name === "AbortError") {
      toast("Máy chủ phản hồi chậm, đang thử tải lại danh sách job…", true);
      state.jobsReloadQueued = true;
    } else {
      toast(error.message, true);
    }
  } finally {
    clearTimeout(abortTimer);
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
    if (!response.ok) throw new Error(errorDetail(data.detail) || `Không thể ${action} job`);
    toast(action === "retry" ? `Đã chạy lại job ${data.job_id}` : action === "cancel" ? (data.status === "cancelling" ? "Đang dừng job an toàn…" : "Đã hủy job") : action === "tiktok-status" ? `TikTok: ${data.tiktok_publish_status}` : "Đã xóa job");
    await loadJobs();
  } catch (error) { toast(error.message, true); }
}

const jobModeNames = {
  1: "Phụ đề",
  2: "Phụ đề + Voice",
  3: "Voice + Nhạc",
  4: "Chỉ tải video",
};

const jobActionNames = {
  translation: "Dịch video",
  logo_branding: "Đóng dấu logo",
  comic_video: "Video truyện tranh",
  publish_tiktok: "Đăng bài TikTok",
};

const langNameMap = {
  auto: "Tự nhận diện",
  "zh-CN": "Tiếng Trung",
  zh: "Tiếng Trung",
  vi: "Tiếng Việt",
  en: "Tiếng Anh",
  ja: "Tiếng Nhật",
  ko: "Tiếng Hàn",
  th: "Tiếng Thái",
  id: "Tiếng Indonesia",
  es: "Tiếng Tây Ban Nha",
};

const ttsProviderNames = {
  edge: "Edge TTS",
  cit: "CIT Voice Studio",
  azure: "Azure Speech",
  elevenlabs: "ElevenLabs",
};

const translationProviderNames = {
  google: "Google Translate",
  translategemma: "TranslateGemma",
  qwen3: "Qwen3",
  gemini: "Gemini",
};

let activeDetailJobId = null;

function formatDurationSeconds(seconds) {
  if (seconds == null || isNaN(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const remainderSeconds = s % 60;
  if (hours > 0) return `${hours} giờ ${minutes} phút`;
  if (minutes > 0) return `${minutes} phút ${remainderSeconds} giây`;
  return `${remainderSeconds} giây`;
}

function formatJobTime(isoString) {
  if (!isoString) return "—";
  const d = new Date(isoString);
  if (isNaN(d.getTime())) return "—";
  const time = d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
  const date = d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
  return `${time} · ${date}`;
}

function getElapsedSeconds(job) {
  const start = job.started_at ? new Date(job.started_at).getTime() : null;
  if (!start) return null;
  const finish = (job.finished_at || job.completed_at) ? new Date(job.finished_at || job.completed_at).getTime() : null;
  const end = finish || (["queued", "scheduled", "processing", "cancelling"].includes(job.status) ? Date.now() : null);
  if (!end || end < start) return null;
  return (end - start) / 1000;
}

function renderJobDetailModal(job, { preserveMedia = false } = {}) {
  if (!job) return;
  activeDetailJobId = job.job_id;

  const title = job.filename || job.source_url || `Tiến trình #${job.job_id.slice(0, 8)}`;
  if ($("#jobDetailTitle")) $("#jobDetailTitle").textContent = title;
  if ($("#jobDetailCode")) $("#jobDetailCode").textContent = `Job #${job.job_id.slice(0, 8).toUpperCase()}`;
  // A finished (or failed) video can be reported to the admins.
  $("#jobDetailReport")?.classList.toggle("is-hidden", !["completed", "failed"].includes(job.status));

  const statusPill = $("#jobDetailStatusPill");
  if (statusPill) {
    statusPill.className = `status-pill status-${job.status}`;
    const statusText = $("#jobDetailStatusText");
    if (statusText) statusText.textContent = statusLabels[job.status] || job.status;
  }

  const queueBadge = $("#jobDetailQueueBadge");
  if (queueBadge) {
    if (job.queue_position != null) {
      queueBadge.textContent = `Hàng đợi #${job.queue_position}`;
      queueBadge.classList.remove("is-hidden");
    } else {
      queueBadge.classList.add("is-hidden");
    }
  }

  // Progress Card
  const progressCard = $("#jobDetailProgressCard");
  const percent = Math.max(0, Math.min(100, Math.round(Number(job.progress || 0) * 100)));
  const isActive = ["queued", "scheduled", "processing", "cancelling"].includes(job.status);
  if (progressCard) {
    if (isActive) {
      progressCard.classList.remove("is-hidden");
      if ($("#jobDetailStepLabel")) $("#jobDetailStepLabel").textContent = stepLabels[job.step] || job.step || "Đang xử lý";
      if ($("#jobDetailProgressPercent")) $("#jobDetailProgressPercent").textContent = `${percent}%`;
      if ($("#jobDetailProgressBar")) $("#jobDetailProgressBar").style.width = `${percent}%`;
      const detail = job.status === "scheduled" && job.tiktok_publish_at
        ? `Sẽ đăng TikTok lúc ${formatScheduledTime(job.tiktok_publish_at)}`
        : (job.step_detail || "Đang xử lý trong hàng đợi...");
      if ($("#jobDetailStepDetail")) $("#jobDetailStepDetail").textContent = detail;
    } else {
      progressCard.classList.add("is-hidden");
    }
  }

  renderPhotoChoiceCard(job);

  // Error Card
  const errorCard = $("#jobDetailErrorCard");
  if (errorCard) {
    if (job.error || (job.status === "failed" && job.step_detail)) {
      errorCard.classList.remove("is-hidden");
      if ($("#jobDetailErrorMsg")) $("#jobDetailErrorMsg").textContent = job.error || job.step_detail || "Lỗi không xác định";
    } else {
      errorCard.classList.add("is-hidden");
    }
  }

  // Video Preview Card
  const videoCard = $("#jobDetailVideoCard");
  const videoPlayer = $("#jobDetailVideoPlayer");
  if (videoCard && videoPlayer) {
    if (job.video_url) {
      videoCard.classList.remove("is-hidden");
      if (!preserveMedia || videoPlayer.getAttribute("src") !== job.video_url) {
        videoPlayer.src = job.video_url;
      }
      const downloadBtn = $("#jobDetailVideoDownloadBtn");
      if (downloadBtn) {
        downloadBtn.href = job.video_url;
        downloadBtn.setAttribute("download", job.output_video || `${job.job_id}.mp4`);
      }
      const publishBtn = $("#jobDetailPublishTikTokBtn");
      if (publishBtn) {
        publishBtn.onclick = () => {
          closeJobDetailModal();
          location.hash = "tiktok";
          tiktokWorkspace.openDraft(job.job_id);
        };
      }
    } else {
      videoCard.classList.add("is-hidden");
      if (!preserveMedia) {
        videoPlayer.pause();
        videoPlayer.removeAttribute("src");
        videoPlayer.load();
      }
    }
  }

  // Artifacts list
  const artifactsCard = $("#jobDetailArtifactsCard");
  const artifactsList = $("#jobDetailArtifactsList");
  if (artifactsCard && artifactsList) {
    const items = [];
    if (job.video_url) {
      items.push({
        label: "Video thành phẩm (MP4)",
        url: job.video_url,
        name: job.output_video || `${job.job_id}.mp4`,
        icon: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"></rect><line x1="7" y1="2" x2="7" y2="22"></line><line x1="17" y1="2" x2="17" y2="22"></line><line x1="2" y1="12" x2="22" y2="12"></line><line x1="2" y1="7" x2="7" y2="7"></line><line x1="2" y1="17" x2="7" y2="17"></line><line x1="17" y1="17" x2="22" y2="17"></line><line x1="17" y1="7" x2="22" y2="7"></line></svg>`
      });
    }
    if (job.subtitle_url) {
      items.push({
        label: "Phụ đề song ngữ (SRT/ASS)",
        url: job.subtitle_url,
        name: job.subtitle_file || `${job.job_id}.srt`,
        icon: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><path d="M7 15h3"></path><path d="M14 15h3"></path><path d="M7 11h10"></path></svg>`
      });
    }
    if (job.translation_url) {
      items.push({
        label: "Dữ liệu bản dịch (JSON)",
        url: job.translation_url,
        name: job.translation_file || `${job.job_id}.json`,
        icon: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>`
      });
    }
    if (job.images_url) {
      items.push({
        label: "Ảnh của bài đăng (ZIP)",
        url: job.images_url,
        name: `${job.job_id}_images.zip`,
        icon: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>`
      });
    }
    if (job.download_all_url) {
      items.push({
        label: "Toàn bộ tài nguyên (ZIP)",
        url: job.download_all_url,
        name: `${job.job_id}.artifacts.zip`,
        icon: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>`
      });
    }

    if (items.length > 0) {
      artifactsCard.classList.remove("is-hidden");
      artifactsList.innerHTML = items.map(it => `
        <a class="artifact-download-item" href="${escapeHtml(it.url)}" download="${escapeHtml(it.name)}" data-download-url="${escapeHtml(it.url)}" data-download-name="${escapeHtml(it.name)}">
          <span>${it.icon} <span>${escapeHtml(it.label)}</span></span>
          <strong>↓</strong>
        </a>
      `).join("");
    } else {
      artifactsCard.classList.add("is-hidden");
    }
  }

  // Info items
  const actionLabel = jobActionNames[job.action || job.job_action] || (job.action === "comic_video" ? "Video truyện tranh" : "Dịch video");
  if ($("#jobDetailAction")) $("#jobDetailAction").textContent = actionLabel;
  if ($("#jobDetailBatch")) $("#jobDetailBatch").textContent = job.batch_id || "Video đơn";

  const sourceVal = job.source_url || job.filename || "—";
  if ($("#jobDetailSourceText")) {
    $("#jobDetailSourceText").textContent = sourceVal;
    $("#jobDetailSourceText").title = sourceVal;
  }
  const copySourceBtn = $("#jobDetailCopySource");
  if (copySourceBtn) {
    if (job.source_url) {
      copySourceBtn.style.display = "";
      copySourceBtn.onclick = () => {
        navigator.clipboard.writeText(job.source_url).then(() => toast("Đã sao chép link nguồn"));
      };
    } else {
      copySourceBtn.style.display = "none";
    }
  }

  const mode = job.mode != null ? (jobModeNames[job.mode] || `Chế độ ${job.mode}`) : "Phụ đề";
  if ($("#jobDetailMode")) $("#jobDetailMode").textContent = mode;

  const srcLang = langNameMap[job.source_language] || job.source_language || "Tự động";
  const tgtLang = langNameMap[job.target_language] || job.target_language || "Tiếng Việt";
  if ($("#jobDetailLangs")) $("#jobDetailLangs").textContent = job.mode === 4 ? "Chỉ tải video gốc" : `${srcLang} → ${tgtLang}`;

  const aiTransWrap = $("#jobDetailAiTranslationWrap");
  if (aiTransWrap) {
    if (job.translation_provider) {
      aiTransWrap.style.display = "";
      if ($("#jobDetailAiTranslation")) $("#jobDetailAiTranslation").textContent = translationProviderNames[job.translation_provider] || job.translation_provider;
    } else {
      aiTransWrap.style.display = "none";
    }
  }

  const voiceWrap = $("#jobDetailVoiceWrap");
  if (voiceWrap) {
    if (job.mode === 1 || job.mode === 4) {
      voiceWrap.style.display = "none";
    } else {
      voiceWrap.style.display = "";
      const tts = job.tts_provider ? (ttsProviderNames[job.tts_provider] || job.tts_provider) : null;
      const voice = job.voice_routing?.voice_key || job.voice_key || job.tts_voice || "";
      if ($("#jobDetailVoice")) $("#jobDetailVoice").textContent = tts ? (voice ? `${tts} (${voice})` : tts) : (voice || "Mặc định hệ thống");
    }
  }

  const owner = job.owner_display_name || job.owner_email || "Chưa xác định";
  if ($("#jobDetailOwner")) $("#jobDetailOwner").textContent = owner;

  const tiktokRow = $("#jobDetailTikTokRow");
  if (tiktokRow) {
    if (job.tiktok_publish_status || job.tiktok_publish_error || job.tiktok_publish_at) {
      tiktokRow.classList.remove("is-hidden");
      const tiktokText = job.tiktok_publish_error
        ? `Lỗi: ${job.tiktok_publish_error}`
        : job.status === "scheduled" && job.tiktok_publish_at
        ? `Hẹn đăng: ${formatJobTime(job.tiktok_publish_at)}`
        : (job.tiktok_publish_status || "Đã liên kết");
      if ($("#jobDetailTikTokStatus")) $("#jobDetailTikTokStatus").textContent = tiktokText;
    } else {
      tiktokRow.classList.add("is-hidden");
    }
  }

  // Timeline
  if ($("#jobDetailCreatedAt")) $("#jobDetailCreatedAt").textContent = formatJobTime(job.created_at);
  if ($("#jobDetailStartedAt")) $("#jobDetailStartedAt").textContent = formatJobTime(job.started_at);

  const finishedLabel = job.status === "failed" ? "Lỗi lúc" : job.status === "cancelled" ? "Hủy lúc" : "Hoàn tất lúc";
  if ($("#jobDetailFinishedLabel")) $("#jobDetailFinishedLabel").textContent = finishedLabel;
  if ($("#jobDetailFinishedAt")) $("#jobDetailFinishedAt").textContent = formatJobTime(job.finished_at || job.completed_at);

  const elapsedSec = getElapsedSeconds(job);
  if ($("#jobDetailDuration")) $("#jobDetailDuration").textContent = formatDurationSeconds(elapsedSec);

  // Footer action buttons
  const cancelBtn = $("#jobDetailCancelBtn");
  if (cancelBtn) {
    if (isActive) {
      cancelBtn.classList.remove("is-hidden");
      cancelBtn.onclick = async () => {
        await jobAction("cancel", job.job_id);
        openJobDetailModal(job.job_id, { refresh: true });
      };
    } else {
      cancelBtn.classList.add("is-hidden");
    }
  }

  const retryBtn = $("#jobDetailRetryBtn");
  if (retryBtn) {
    if (["failed", "cancelled"].includes(job.status)) {
      retryBtn.classList.remove("is-hidden");
      retryBtn.onclick = async () => {
        await jobAction("retry", job.job_id);
        openJobDetailModal(job.job_id, { refresh: true });
      };
    } else {
      retryBtn.classList.add("is-hidden");
    }
  }

  const downloadOnlyBtn = $("#jobDetailDownloadOnlyBtn");
  if (downloadOnlyBtn) {
    downloadOnlyBtn.classList.toggle("is-hidden", !isNoSpeechJob(job));
    downloadOnlyBtn.onclick = async () => {
      downloadOnlyBtn.disabled = true;
      try {
        await requestDownloadOnly([job.job_id]);
      } finally {
        downloadOnlyBtn.disabled = false;
      }
      openJobDetailModal(job.job_id, { refresh: true });
    };
  }

  const deleteBtn = $("#jobDetailDeleteBtn");
  if (deleteBtn) {
    if (["completed", "failed", "cancelled", "awaiting_input"].includes(job.status)) {
      deleteBtn.classList.remove("is-hidden");
      deleteBtn.onclick = async () => {
        await jobAction("delete", job.job_id);
        closeJobDetailModal();
      };
    } else {
      deleteBtn.classList.add("is-hidden");
    }
  }

  // Copy ID chip
  const copyIdBtn = $("#jobDetailCopyId");
  if (copyIdBtn) {
    copyIdBtn.onclick = () => {
      navigator.clipboard.writeText(job.job_id).then(() => toast("Đã sao chép ID tiến trình"));
    };
  }

  // Refresh button
  const refreshBtn = $("#jobDetailRefresh");
  if (refreshBtn) {
    refreshBtn.onclick = () => openJobDetailModal(job.job_id, { refresh: true });
  }
}

async function openJobDetailModal(jobId, { refresh = false } = {}) {
  let job = state.jobs.find(j => j.job_id === jobId);
  if (job) {
    renderJobDetailModal(job, { preserveMedia: !refresh });
  }

  const dialog = $("#jobDetailDialog");
  if (dialog) {
    document.body.classList.add("modal-open");
    try {
      if (dialog.open && typeof dialog.close === "function" && refresh) {
        // keep open
      } else if (!dialog.open) {
        if (typeof dialog.showModal === "function") dialog.showModal();
        else dialog.setAttribute("open", "");
      }
    } catch {
      dialog.setAttribute("open", "");
    }
  }

  // Fetch full job data from API to ensure complete metadata
  try {
    const res = await apiFetch(`/api/v1/jobs/${jobId}`);
    if (res.ok) {
      const fresh = await res.json();
      const idx = state.jobs.findIndex(j => j.job_id === jobId);
      if (idx !== -1) state.jobs[idx] = fresh;
      renderJobDetailModal(fresh, { preserveMedia: true });
    }
  } catch (err) {
    console.warn("Could not fetch job detail", err);
  }
}

function closeJobDetailModal() {
  stopPhotoMusicPreview();
  activeDetailJobId = null;
  document.body.classList.remove("modal-open");
  const dialog = $("#jobDetailDialog");
  if (dialog) {
    try {
      const video = $("#jobDetailVideoPlayer");
      if (video) video.pause();
      if (dialog.open && typeof dialog.close === "function") dialog.close();
    } catch {}
    dialog.removeAttribute("open");
  }
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
      if (!response.ok) throw new Error(errorDetail(payload.detail) || "Thao tác thất bại");
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
let passwordResetEmail = "";

const AUTH_MODES = {
  login: {
    form: "#authLoginForm", focus: "#authUsername", title: "Đăng nhập VidTrans",
    description: "Đăng nhập để truy cập pipeline, tiến trình và file video.",
    toggle: "Chưa có tài khoản? Đăng ký miễn phí",
    message: "OAuth2 Password + JWT · phiên đăng nhập bảo mật bằng cookie HttpOnly",
  },
  register: {
    form: "#authRegisterForm", focus: "#registerDisplayName", title: "Đăng ký VidTrans",
    description: "Tạo tài khoản miễn phí để bắt đầu xử lý video.",
    toggle: "Đã có tài khoản? Đăng nhập",
    message: "Tài khoản mới sẽ được kích hoạt với gói Free.",
  },
  "reset-request": {
    form: "#authResetRequestForm", focus: "#resetEmail", title: "Quên mật khẩu",
    description: "Nhập email đã đăng ký, VidTrans sẽ gửi mã xác nhận để đặt lại mật khẩu.",
    toggle: "Quay lại đăng nhập",
    message: "",
  },
  "reset-confirm": {
    form: "#authResetConfirmForm", focus: "#resetCode", title: "Đặt lại mật khẩu",
    description: "Nhập mã xác nhận trong email và mật khẩu mới.",
    toggle: "Quay lại đăng nhập",
    message: "",
  },
};

function setAuthMode(mode, message) {
  authMode = AUTH_MODES[mode] ? mode : "login";
  const config = AUTH_MODES[authMode];
  Object.values(AUTH_MODES).forEach(item => $(item.form).classList.toggle("is-hidden", item !== config));
  $("#authTitle").textContent = config.title;
  $("#authDescription").textContent = config.description;
  $("#authModeToggle").textContent = config.toggle;
  const authMessage = $("#authMessage");
  authMessage.textContent = message ?? config.message;
  authMessage.classList.remove("error");
  $$("#authGate [data-password-toggle]").forEach(btn => setPasswordVisible(btn, false));
  setTimeout(() => $(config.focus).focus(), 0);
}

function showAuthError(error) {
  const message = $("#authMessage");
  message.textContent = error.message;
  message.classList.add("error");
}

$("#authModeToggle").addEventListener("click", () => setAuthMode(authMode === "login" ? "register" : "login"));

$("#authForgotPassword").addEventListener("click", () => {
  $("#resetEmail").value = $("#authUsername").value.includes("@") ? $("#authUsername").value.trim() : "";
  setAuthMode("reset-request");
});

async function requestPasswordResetCode(email) {
  const body = new FormData();
  body.set("email", email);
  const payload = await requestJson("/api/v1/auth/password-reset/request", { method: "POST", body });
  passwordResetEmail = email;
  return payload.message;
}

$("#authResetRequestForm").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("#authResetRequestSubmit");
  button.disabled = true;
  try {
    const message = await requestPasswordResetCode($("#resetEmail").value.trim());
    $("#authResetConfirmForm").reset();
    setAuthMode("reset-confirm", message);
  } catch (error) {
    showAuthError(error);
  } finally {
    button.disabled = false;
  }
});

$("#authResetResend").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    setAuthMode("reset-confirm", await requestPasswordResetCode(passwordResetEmail));
  } catch (error) {
    showAuthError(error);
  } finally {
    // Matches the server-side resend cooldown.
    setTimeout(() => { button.disabled = false; }, 60_000);
  }
});

$("#authResetConfirmForm").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("#authResetConfirmSubmit");
  const password = $("#resetNewPassword").value;
  if (password !== $("#resetNewPasswordConfirm").value) {
    showAuthError(new Error("Mật khẩu nhập lại chưa khớp"));
    return;
  }
  button.disabled = true;
  try {
    const body = new FormData();
    body.set("email", passwordResetEmail);
    body.set("code", $("#resetCode").value.trim());
    body.set("new_password", password);
    const payload = await requestJson("/api/v1/auth/password-reset/confirm", { method: "POST", body });
    form.reset();
    setPasswordVisible($("[data-password-toggle=resetNewPassword]"), false);
    setPasswordVisible($("[data-password-toggle=resetNewPasswordConfirm]"), false);
    $("#authUsername").value = passwordResetEmail;
    setAuthMode("login", payload.message);
    setTimeout(() => $("#authPassword").focus(), 0);
  } catch (error) {
    showAuthError(error);
  } finally {
    button.disabled = false;
  }
});

$("#authLoginForm").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("#authSubmit");
  const message = $("#authMessage");
  const body = new URLSearchParams({
    grant_type: "password",
    username: $("#authUsername").value.trim(),
    password: $("#authPassword").value,
    scope: "admin",
    remember: $("#authRemember").checked ? "true" : "false",
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
    setPasswordVisible($("[data-password-toggle=authPassword]"), false);
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
    // The form cannot submit without the required consent checkbox.
    body.set("accept_terms", $("#registerAcceptTerms").checked ? "true" : "false");
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

$("#authGuestButton").addEventListener("click", async event => {
  const button = event.currentTarget;
  const message = $("#authMessage");
  button.disabled = true;
  message.textContent = "Đang tạo tài khoản dùng thử…";
  message.classList.remove("error");
  try {
    // The notice under the button states that a trial means accepting the terms.
    const body = new FormData();
    body.set("accept_terms", "true");
    const payload = await requestJson("/api/v1/auth/guest", { method: "POST", body });
    if (await refreshApplicationAuth()) {
      await startProtectedApplication();
      const { email, password } = payload.credentials;
      toast(`Đã kích hoạt gói dùng thử ${payload.quota_minutes} phút video`);
      // Shown once: the server keeps only the hash.
      window.alert(`Tài khoản dùng thử của bạn (lưu lại để đăng nhập lần sau):\n\nEmail: ${email}\nMật khẩu: ${password}`);
    }
  } catch (error) {
    showAuthError(error);
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

  // 4. Report a bank transfer; an admin reconciles it before the plan activates
  const confirmBtn = event.target.closest("[data-confirm-order]");
  if (confirmBtn) {
    const orderId = confirmBtn.dataset.confirmOrder;
    confirmBtn.disabled = true;
    try {
      await requestJson(`/api/v1/billing/orders/${orderId}/confirm`, { method: "POST" });
      toast("Đã ghi nhận. Gói sẽ được kích hoạt sau khi quản trị viên đối soát giao dịch.");
      $("#paymentInstructions").classList.add("is-hidden");
      await loadBilling();
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
$("#quickPasteLinksBtn")?.addEventListener("click", async () => {
  try {
    const text = (await navigator.clipboard.readText())?.trim();
    if (!text) return toast("Bộ nhớ tạm đang trống", true);
    const input = $("#sourceLinks");
    input.value = input.value.trim() ? `${input.value.trim()}\n${text}` : text;
    renderSourceLinks();
    const { links: found, invalid } = classifySourceLinks(text);
    if (invalid.length) toast(invalidSourceLinksMessage(invalid), true);
    else toast(found.length ? `Đã dán ${found.length} liên kết video` : "Đã dán nội dung từ bộ nhớ tạm");
  } catch (_) {
    $("#sourceLinks").focus();
    toast("Hãy nhấn Ctrl+V (hoặc Cmd+V) để dán liên kết vào ô", true);
  }
});
$("#clearLinksBtn")?.addEventListener("click", () => {
  $("#sourceLinks").value = "";
  renderSourceLinks();
});
$("#linkList")?.addEventListener("click", event => {
  const removeBtn = event.target.closest("[data-link-remove]");
  if (!removeBtn) return;
  const targetUrl = removeBtn.dataset.linkRemove;
  const remaining = [...state.links, ...(state.invalidLinks || []).map(item => item.link)]
    .filter(url => url !== targetUrl);
  $("#sourceLinks").value = remaining.join("\n");
  renderSourceLinks();
});
$("#fileList").addEventListener("click", event => {
  if (event.target.closest("[data-clear-files]")) {
    state.files = [];
    syncInputFiles();
    renderFiles();
    return;
  }
  const button = event.target.closest("[data-file-index]");
  if (!button) return;
  state.files.splice(Number(button.dataset.fileIndex), 1);
  syncInputFiles(); renderFiles();
});
$("#swapLanguagesBtn")?.addEventListener("click", () => {
  const src = $("#sourceLanguage");
  const tgt = $("#targetLanguage");
  if (!src || !tgt || src.value === "auto") return;
  const prevSrc = src.value;
  src.value = tgt.value;
  tgt.value = prevSrc;
  updateFormSummary();
});
const dropZone = $("#dropZone");
["dragenter", "dragover"].forEach(type => dropZone.addEventListener(type, event => { event.preventDefault(); dropZone.classList.add("dragging"); }));
["dragleave", "drop"].forEach(type => dropZone.addEventListener(type, event => { event.preventDefault(); dropZone.classList.remove("dragging"); }));
dropZone.addEventListener("drop", event => addFiles(event.dataTransfer.files));
$("#batchForm")?.addEventListener("submit", submitBatch);
$("#submitButton")?.addEventListener("click", event => {
  event.preventDefault();
  submitBatch(event);
});
$("#mobileQuickSubmitButton")?.addEventListener("click", event => {
  event.preventDefault();
  submitBatch(event);
});
$("#mobileQuickSummaryTrigger")?.addEventListener("click", event => {
  event.preventDefault();
  submitBatch(event);
});
if ("IntersectionObserver" in window) {
  const stickyCard = $("#createView .sticky-card");
  const quickBar = $("#mobileQuickSubmitBar");
  if (stickyCard && quickBar) {
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        quickBar.classList.toggle("is-at-bottom", entry.isIntersecting);
      }
    }, { threshold: 0.15 });
    observer.observe(stickyCard);
  }
}
$("#douyinBrowserSync").addEventListener("click", syncDouyinBrowserLogin);
$("#douyinBrowserReload").addEventListener("click", reloadDouyinBrowserPage);
$("#douyinBrowserLogout").addEventListener("click", logoutDouyinBrowser);
$("#douyinBrowserFrame").addEventListener("load", () => {
  state.douyinBrowserLoaded = true;
  $("#douyinBrowserLoading").classList.add("is-hidden");
});
$$('[data-source-browser-tab]').forEach(button => button.addEventListener("click", () => selectSourceBrowserTab(button.dataset.sourceBrowserTab)));
for (const platform of ["youtube", "facebook", "xiaohongshu", "qidian"]) {
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
[$("#mode"), $("#sourceLanguage"), $("#targetLanguage"), $("#subtitleSource"), $("#translationProvider"), document.querySelector('[name="voice_mode"]')].forEach(element => element.addEventListener("change", updateFormSummary));
[$("#placementMode"), $("#matchSourceSize"), $("#minFontSize"), $("#maxFontSize"), $("#positionGap"), $("#maskOriginal"), $("#subtitleTextColor"), $("#subtitleBackgroundColor"), $("#subtitleBackgroundStyle")].forEach(element => element.addEventListener("change", updateFormSummary));
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
  const resetBtn = event.target.closest("[data-admin-reset-password]");
  if (resetBtn) openPasswordResetDialog(resetBtn.dataset.adminResetPassword, resetBtn.dataset.email);
});
$("#passwordResetForm").addEventListener("submit", submitPasswordReset);
$("#passwordResetCancel").addEventListener("click", () => $("#passwordResetDialog").close());
$("#confirmJobClose")?.addEventListener("click", closeConfirmJobDialog);
$("#confirmJobCancel")?.addEventListener("click", closeConfirmJobDialog);
$("#confirmJobProceed")?.addEventListener("click", onConfirmJobProceed);
$("#confirmJobDialog")?.addEventListener("cancel", event => {
  event.preventDefault();
  closeConfirmJobDialog();
});
$("#confirmJobDialog")?.addEventListener("click", event => {
  if (event.target === $("#confirmJobDialog")) closeConfirmJobDialog();
});
$("#jobDetailClose")?.addEventListener("click", closeJobDetailModal);
$("#jobDetailCloseBtn")?.addEventListener("click", closeJobDetailModal);
$("#jobDetailDialog")?.addEventListener("cancel", event => {
  event.preventDefault();
  closeJobDetailModal();
});
$("#jobDetailDialog")?.addEventListener("click", event => {
  if (event.target === $("#jobDetailDialog")) closeJobDetailModal();
});
$("#passwordResetGenerate").addEventListener("click", () => {
  $("#passwordResetValue").value = generatePassword();
  setPasswordVisible($("[data-password-toggle=passwordResetValue]"), true);
});
$("#passwordResetCopy").addEventListener("click", async () => {
  const value = $("#passwordResetValue").value;
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    toast("Đã sao chép mật khẩu");
  } catch {
    toast("Không thể sao chép. Hãy sao chép thủ công.", true);
  }
});
document.addEventListener("click", event => {
  const toggle = event.target.closest("[data-password-toggle]");
  if (toggle) setPasswordVisible(toggle, toggle.getAttribute("aria-pressed") !== "true");
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
    } else if (tabName === "billing") {
      $("#adminSubtabBilling")?.classList.remove("is-hidden");
      loadAdminBilling();
    } else if (tabName === "contact") {
      $("#adminSubtabContact")?.classList.remove("is-hidden");
      loadAdminContact();
    } else if (tabName === "reports") {
      $("#adminSubtabReports")?.classList.remove("is-hidden");
      loadAdminReports();
    } else if (tabName === "music") {
      $("#adminSubtabMusic")?.classList.remove("is-hidden");
      loadAdminMusic();
    } else if (tabName === "system") {
      $("#adminSubtabSystem")?.classList.remove("is-hidden");
      startAdminSystemRefresh();
    } else if (tabName === "access") {
      $("#adminSubtabAccess")?.classList.remove("is-hidden");
      accessConfig.load();
    } else if (tabName === "audit") {
      $("#adminSubtabAudit")?.classList.remove("is-hidden");
      loadAdminAudit();
    }
    if (tabName !== "system") systemEvents.stop();
  });
});
$("#refreshAdminJobs")?.addEventListener("click", loadAdminJobs);
$("#refreshAdminSystem")?.addEventListener("click", loadAdminSystem);
$("#sysAutoRefresh")?.addEventListener("change", startAdminSystemRefresh);
document.addEventListener("visibilitychange", syncAdminSystemEvents);
$$("[data-sys-sort]").forEach(btn => btn.addEventListener("click", () => {
  state.adminProcessSort = btn.dataset.sysSort;
  if (state.adminProcesses) renderAdminProcesses(state.adminProcesses);
}));
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
$("#refreshAdminBilling")?.addEventListener("click", loadAdminBilling);
$("#refreshAdminReports")?.addEventListener("click", loadAdminReports);
$("#adminReportsFilter")?.addEventListener("change", loadAdminReports);
$("#adminReportsList")?.addEventListener("click", async event => {
  const open = event.target.closest("[data-admin-open-report-job]");
  if (open) {
    openJobDetailModal(open.dataset.adminOpenReportJob);
    return;
  }
  const button = event.target.closest("[data-admin-resolve-report]");
  if (!button) return;
  const reportId = encodeURIComponent(button.closest("[data-report-id]").dataset.reportId);
  button.disabled = true;
  try {
    await requestJson(`/api/v1/admin/job-reports/${reportId}/resolve`, { method: "POST" });
    toast("Đã đánh dấu báo lỗi là đã xử lý");
    await loadAdminReports();
  } catch (err) {
    toast(err.message, true);
    button.disabled = false;
  }
});
$("#jobDetailReport")?.addEventListener("click", () => {
  const job = state.jobs.find(item => item.job_id === activeDetailJobId) || { job_id: activeDetailJobId };
  if (job.job_id) openJobReportDialog(job);
});
$("#jobReportCancel")?.addEventListener("click", closeJobReportDialog);
$("#jobReportForm")?.addEventListener("submit", async event => {
  event.preventDefault();
  const dialog = $("#jobReportDialog");
  const jobId = dialog?.dataset.jobId;
  if (!jobId) return;
  const category = $("#jobReportCategory").value;
  const message = $("#jobReportMessage").value.trim();
  if (category === "other" && message.length < 5) {
    toast("Hãy mô tả lỗi bạn gặp", true);
    $("#jobReportMessage").focus();
    return;
  }
  const submit = $("#jobReportSubmit");
  submit.disabled = true;
  try {
    const data = await requestJson(`/api/v1/jobs/${encodeURIComponent(jobId)}/reports`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category, message, video_time: $("#jobReportTime").value.trim() }),
    });
    closeJobReportDialog();
    toast(data.message || "Đã gửi báo lỗi");
  } catch (err) {
    toast(err.message, true);
  } finally {
    submit.disabled = false;
  }
});
$("#refreshAdminContact")?.addEventListener("click", loadAdminContact);
$("#adminContactFilter")?.addEventListener("change", loadAdminContact);
$("#adminContactList")?.addEventListener("click", async event => {
  const button = event.target.closest("[data-admin-resolve-contact]");
  if (!button) return;
  const messageId = encodeURIComponent(button.closest("[data-contact-id]").dataset.contactId);
  button.disabled = true;
  try {
    await requestJson(`/api/v1/admin/contact-messages/${messageId}/resolve`, { method: "POST" });
    toast("Đã đánh dấu tin nhắn là đã xử lý");
    await loadAdminContact();
  } catch (err) {
    toast(err.message, true);
    button.disabled = false;
  }
});
$("#adminBillingFilter")?.addEventListener("change", loadAdminBilling);
$("#adminBillingList")?.addEventListener("click", async event => {
  const confirmBtn = event.target.closest("[data-admin-confirm-order]");
  const rejectBtn = event.target.closest("[data-admin-reject-order]");
  const button = confirmBtn || rejectBtn;
  if (!button) return;
  let request;
  if (confirmBtn) {
    if (!confirm(`Xác nhận đã nhận ${confirmBtn.dataset.amount} với nội dung "${confirmBtn.dataset.transfer}"?\nGói của khách sẽ được kích hoạt ngay.`)) return;
    request = { method: "POST" };
  } else {
    const reason = prompt("Lý do từ chối đơn (ghi vào Audit):", "Không tìm thấy giao dịch trong sao kê");
    if (reason === null) return;
    const formData = new FormData();
    formData.set("reason", reason.slice(0, 200));
    request = { method: "POST", body: formData };
  }
  const orderId = encodeURIComponent(button.closest("[data-order-id]").dataset.orderId);
  button.disabled = true;
  try {
    await requestJson(`/api/v1/admin/billing/orders/${orderId}/${confirmBtn ? "confirm" : "reject"}`, request);
    toast(confirmBtn ? "Đã xác nhận thanh toán và kích hoạt gói" : "Đã từ chối đơn thanh toán");
    await loadAdminBilling();
  } catch (err) {
    toast(err.message, true);
    button.disabled = false;
  }
});
$("#refreshAdminAudit")?.addEventListener("click", loadAdminAudit);
$("#adminAuditActionFilter")?.addEventListener("change", loadAdminAudit);
function syncMetricFilterCards() {
  const active = $("#statusFilter")?.value || "";
  $$(".metric-card[data-status-filter]").forEach(card => {
    const selected = card.dataset.statusFilter === active;
    card.classList.toggle("is-active", selected);
    card.setAttribute("aria-pressed", String(selected));
  });
}
$("#statusFilter").addEventListener("change", () => {
  syncMetricFilterCards();
  state.jobsPage = 1;
  loadJobs();
});
$$(".metric-card[data-status-filter]").forEach(card => {
  const toggleFilter = () => {
    const select = $("#statusFilter");
    if (!select) return;
    const target = card.dataset.statusFilter;
    select.value = select.value === target ? "" : target;
    syncMetricFilterCards();
    state.jobsPage = 1;
    loadJobs();
  };
  card.addEventListener("click", toggleFilter);
  card.addEventListener("keydown", event => {
    if (["Enter", " "].includes(event.key)) {
      event.preventDefault();
      toggleFilter();
    }
  });
});
$("#jobSearch").addEventListener("input", () => {
  state.jobsPage = 1;
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(loadJobs, 300);
});
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
  if (target) {
    if (target.dataset.action === "detail") {
      openJobDetailModal(target.dataset.job);
      return;
    }
    jobAction(target.dataset.action, target.dataset.job);
    return;
  }
  const jobRow = event.target.closest(".job-row");
  if (jobRow && !event.target.closest("button, a, input, label")) {
    const jobId = jobRow.dataset.jobRow;
    if (jobId) openJobDetailModal(jobId);
  }
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
  const pageJobs = getCurrentPageJobs();
  pageJobs.forEach(job => event.target.checked ? state.selectedJobIds.add(job.job_id) : state.selectedJobIds.delete(job.job_id));
  renderJobs(state.jobs);
});
$("#jobsPagination")?.addEventListener("click", event => {
  const btn = event.target.closest("[data-page]");
  if (!btn || btn.disabled) return;
  const targetPage = parseInt(btn.dataset.page, 10);
  if (!isNaN(targetPage) && targetPage !== state.jobsPage) {
    state.jobsPage = targetPage;
    loadJobs();
    const panel = $(".jobs-panel");
    if (panel) {
      const rect = panel.getBoundingClientRect();
      if (rect.top < 0) {
        panel.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  }
});
$("#jobsPagination")?.addEventListener("change", event => {
  const sizeSelect = event.target.closest("#jobsPageSizeSelect");
  if (!sizeSelect) return;
  const newSize = parseInt(sizeSelect.value, 10);
  if (!isNaN(newSize) && newSize > 0) {
    state.jobsPageSize = newSize;
    state.jobsPage = 1;
    try {
      localStorage.setItem("vidtrans_jobs_page_size", String(newSize));
    } catch (_) {}
    loadJobs();
  }
});
$$('[data-bulk-action]').forEach(button => button.addEventListener("click", () => bulkJobAction(button.dataset.bulkAction)));
window.addEventListener("hashchange", showView);
window.addEventListener("beforeunload", event => {
  if (!state.pendingUploads.some(upload => upload.status === "uploading")) return;
  event.preventDefault();
  event.returnValue = "";
});
const tiktokWorkspace = createTikTokWorkspace({
  requestJson,
  toast,
  getCurrentUserId: () => state.auth.user_id,
  canManageAllVideos: () => state.auth.role === "owner",
});
const comicWorkspace = createComicWorkspace({ requestJson, toast, escapeHtml });
const clipWorkspace = createClipWorkspace({ requestJson, toast, escapeHtml });
const brandingWorkspace = createBrandingWorkspace({
  requestJson,
  toast,
  escapeHtml,
  onLogoChange: logoUrl => updateUserAvatar(logoUrl),
});
const accessConfig = createAccessConfig({
  requestJson,
  toast,
  escapeHtml,
  onMenusChanged: () => loadNavMenus(),
});
const platformAppsSettings = createPlatformAppsSettings({
  requestJson, toast, escapeHtml, onChange: () => tiktokWorkspace.enter(),
});
$("#platformAppsPanel").addEventListener("toggle", event => {
  if (event.currentTarget.open) platformAppsSettings.load();
});
initWorkflowModeCards();
loadTtsProviders();
loadTranslationProviders();
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
