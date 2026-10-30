// Account & branding page: display name, the account's logo and how it is stamped,
// plus "stamp my logo on this video" jobs and their history. Everything is per user.
const ACTIVE = new Set(["queued", "scheduled", "processing", "cancelling"]);
const STATUS_LABELS = {
  queued: "Đang chờ", processing: "Đang xử lý", cancelling: "Đang hủy",
  completed: "Hoàn tất", failed: "Thất bại", cancelled: "Đã hủy",
};
const POSITION_LABELS = {
  "top-right": "góc trên phải", "top-left": "góc trên trái", "bottom-right": "góc dưới phải", "bottom-left": "góc dưới trái",
};
const POLL_MS = 2000;

export function createBrandingWorkspace({ requestJson, toast, escapeHtml, onLogoChange }) {
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const form = $("#brandingForm");
  let branding = null;
  let pollTimer = null;
  let active = false;

  function percentFields(config) {
    return { ...config, opacity: Math.round(config.opacity * 100) };
  }

  function formConfig() {
    const data = new FormData(form);
    return {
      auto_detect: form.elements.auto_detect.checked,
      cover_original: form.elements.cover_original.checked,
      // Read from the element: a disabled select is left out of FormData.
      cover_method: form.elements.cover_method.value,
      default_position: data.get("default_position"),
      logo_size: Number(data.get("logo_size")),
      opacity: Number(data.get("opacity")) / 100,
      margin: Number(data.get("margin")),
      multi_logo_strategy: data.get("multi_logo_strategy"),
      low_confidence_action: data.get("low_confidence_action"),
      min_confidence: branding?.config?.min_confidence ?? 0.55,
    };
  }

  function updateOutputs() {
    const config = formConfig();
    form.querySelector('[data-output="logo_size"]').textContent = `${config.logo_size}%`;
    form.querySelector('[data-output="opacity"]').textContent = `${Math.round(config.opacity * 100)}%`;
    form.querySelector('[data-output="margin"]').textContent = `${config.margin}%`;
    renderPreview(config);
  }

  // Same geometry as the renderer: width share of the video, margin share of the shorter side.
  function renderPreview(config) {
    const logo = branding?.logo;
    $$("[data-preview-frame]").forEach(frame => {
      frame.querySelector(".preview-logo")?.remove();
      if (!logo) return;
      const width = frame.clientWidth;
      const height = frame.clientHeight;
      let logoWidth = width * config.logo_size / 100;
      let logoHeight = logoWidth * logo.height / logo.width;
      if (logoHeight > height * 0.3) {
        logoWidth *= height * 0.3 / logoHeight;
        logoHeight = height * 0.3;
      }
      const margin = Math.min(width, height) * config.margin / 100;
      const image = document.createElement("img");
      image.className = "preview-logo";
      image.src = logo.url;
      image.alt = "";
      image.style.width = `${logoWidth}px`;
      image.style.opacity = config.opacity;
      image.style[config.default_position.startsWith("top") ? "top" : "bottom"] = `${margin}px`;
      image.style[config.default_position.endsWith("left") ? "left" : "right"] = `${margin}px`;
      frame.append(image);
    });
  }

  function renderBranding(data) {
    branding = data;
    const config = percentFields(data.config);
    for (const [name, value] of Object.entries(config)) {
      const field = form.elements[name];
      if (!field) continue;
      if (field.type === "checkbox") field.checked = Boolean(value);
      else field.value = value;
    }
    const preview = $("#logoPreview");
    if (data.logo) {
      preview.classList.remove("is-empty");
      preview.innerHTML = `<img src="${escapeHtml(data.logo.url)}" alt="Logo thương hiệu" />
        <small>${data.logo.width}×${data.logo.height}px${data.logo.transparent ? " · nền trong suốt" : ""}</small>`;
    } else {
      preview.classList.add("is-empty");
      preview.innerHTML = "<span>Chưa có logo</span>";
    }
    $("#logoDeleteButton").disabled = !data.logo;
    const templatePreview = $("#templatePreview");
    templatePreview.classList.toggle("is-empty", !data.template);
    templatePreview.innerHTML = data.template
      ? `<img src="${escapeHtml(data.template.url)}" alt="Logo mẫu cần thay" /><small>${data.template.width}×${data.template.height}px</small>`
      : "<span>Chưa có mẫu</span>";
    $("#templateDeleteButton").disabled = !data.template;
    form.elements.cover_method.disabled = !form.elements.cover_original.checked;
    onLogoChange?.(data.logo?.url || null);
    updateOutputs();
  }

  function placementText(job) {
    const info = job.branding;
    if (!info) return "";
    if (info.override && info.override !== "auto") return `Ép ${POSITION_LABELS[info.override] || info.override}`;
    const detection = info.detection;
    if (!detection) return "Không nhận diện · vị trí mặc định";
    const cover = info.cover_method === "inpaint" ? " · đã khôi phục nền logo cũ" : info.cover_method === "blur" ? " · đã làm mờ logo cũ" : "";
    const via = detection.method === "template" ? " theo mẫu" : "";
    const moving = info.placements.some(item => item.source === "tracked") ? " · logo di chuyển, đã bám theo" : "";
    if (detection.status === "found") {
      const spans = info.placements.length > 1 ? ` · ${info.placements.length} đoạn` : "";
      return `Tìm thấy logo${via} (độ tin cậy ${Math.round(detection.confidence * 100)}%)${spans}${moving}${cover}`;
    }
    if (detection.status === "uncertain") return "Không chắc chắn · dùng vị trí mặc định";
    return "Không có logo · vị trí mặc định";
  }

  function renderHistory(payload) {
    const list = $("#brandingHistory");
    if (!payload.items.length) {
      list.innerHTML = '<div class="empty-state"><strong>Chưa có video nào</strong></div>';
      return;
    }
    list.innerHTML = payload.items.map(job => {
      const status = job.status || "queued";
      const progress = Math.round((job.progress || 0) * 100);
      const review = job.branding?.needs_review
        ? `<div class="branding-review">Cần xác nhận vị trí logo. Chọn lại:
            ${Object.entries(POSITION_LABELS).map(([value, label]) =>
              `<button class="button secondary small-btn" type="button" data-reprocess="${escapeHtml(job.job_id)}" data-override="${value}">${label}</button>`).join("")}
          </div>`
        : "";
      const actions = [];
      if (status === "completed") actions.push(`<a class="button primary small-btn" href="/api/v1/branding/videos/${encodeURIComponent(job.job_id)}/download">Tải video</a>`);
      if (["completed", "failed", "cancelled"].includes(status)) actions.push(`<button class="button secondary small-btn" type="button" data-reprocess="${escapeHtml(job.job_id)}" data-override="auto">Xử lý lại</button>`);
      if (["queued", "processing"].includes(status)) actions.push(`<button class="button danger small-btn" type="button" data-cancel="${escapeHtml(job.job_id)}">Hủy</button>`);
      return `<article class="branding-job">
        <div class="branding-job-main">
          <strong title="${escapeHtml(job.filename)}">${escapeHtml(job.filename || job.job_id)}</strong>
          <small>${escapeHtml(new Date(job.created_at || Date.now()).toLocaleString("vi-VN"))} · <span class="status-pill status-${escapeHtml(status)}">${STATUS_LABELS[status] || escapeHtml(status)}</span>
            ${job.error ? ` · ${escapeHtml(job.error)}` : ""}</small>
          <small>${escapeHtml(placementText(job) || job.step_detail || "")}</small>
          ${ACTIVE.has(status) ? `<div class="progress-track progress-${escapeHtml(status)}"><span style="width:${progress}%"></span></div>` : ""}
          ${review}
        </div>
        <div class="branding-job-actions">${actions.join("")}</div>
      </article>`;
    }).join("");
  }

  async function loadHistory({ silent = false } = {}) {
    const payload = await requestJson("/api/v1/branding/videos?limit=20", { silent });
    renderHistory(payload);
    clearTimeout(pollTimer);
    if (active && payload.items.some(job => ACTIVE.has(job.status))) {
      pollTimer = setTimeout(() => loadHistory({ silent: true }).catch(() => {}), POLL_MS);
    }
  }

  async function load() {
    $("#logoPreview")?.classList.add("is-loading");
    $("#templatePreview")?.classList.add("is-loading");
    try {
      const [account, data] = await Promise.all([requestJson("/api/v1/me"), requestJson("/api/v1/me/branding")]);
      $("#accountEmail").value = account.email || "";
      $("#accountDisplayName").value = account.display_name || "";
      renderBranding(data);
      await loadHistory();
    } finally {
      $("#logoPreview")?.classList.remove("is-loading");
      $("#templatePreview")?.classList.remove("is-loading");
    }
  }

  async function run(action, success, previewSelector = null) {
    const preview = previewSelector ? $(previewSelector) : null;
    preview?.classList.add("is-loading");
    try {
      await action();
      if (success) toast(success);
    } catch (error) {
      toast(error.message, true);
    } finally {
      preview?.classList.remove("is-loading");
    }
  }

  $("#accountProfileForm").addEventListener("submit", event => {
    event.preventDefault();
    run(async () => {
      const account = await requestJson("/api/v1/me", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: $("#accountDisplayName").value.trim() }),
      });
      $("#userMenuName").textContent = account.display_name;
      $("#userPopoverName").textContent = account.display_name;
    }, "Đã lưu tên hiển thị");
  });

  $("#logoFileInput").addEventListener("change", event => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    run(async () => renderBranding(await requestJson("/api/v1/me/branding/logo", { method: "POST", body })), "Đã cập nhật logo", "#logoPreview");
  });

  $("#templateFileInput").addEventListener("change", event => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    run(async () => renderBranding(await requestJson("/api/v1/me/branding/template", { method: "POST", body })), "Đã lưu logo mẫu", "#templatePreview");
  });

  $("#templateDeleteButton").addEventListener("click", () => {
    run(async () => renderBranding(await requestJson("/api/v1/me/branding/template", { method: "DELETE" })), "Đã xóa logo mẫu", "#templatePreview");
  });

  $("#logoDeleteButton").addEventListener("click", () => {
    if (!confirm("Xóa logo của tài khoản?")) return;
    run(async () => renderBranding(await requestJson("/api/v1/me/branding/logo", { method: "DELETE" })), "Đã xóa logo", "#logoPreview");
  });

  form.addEventListener("input", () => {
    form.elements.cover_method.disabled = !form.elements.cover_original.checked;
    updateOutputs();
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    run(async () => renderBranding(await requestJson("/api/v1/me/branding", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(formConfig()),
    })), "Đã lưu cấu hình logo");
  });

  $("#brandingCopyButton").addEventListener("click", () => run(
    () => navigator.clipboard.writeText(JSON.stringify(formConfig(), null, 2)), "Đã sao chép cấu hình",
  ));

  $("#brandingVideoForm").addEventListener("submit", event => {
    event.preventDefault();
    const videoForm = event.currentTarget;
    if (!branding?.logo) {
      toast("Hãy tải logo lên trước", true);
      return;
    }
    const button = videoForm.querySelector('[type="submit"]');
    button.disabled = true;
    run(async () => {
      await requestJson("/api/v1/branding/videos", { method: "POST", body: new FormData(videoForm) });
      videoForm.reset();
      await loadHistory();
    }, "Đã gửi video, đang chèn logo").finally(() => { button.disabled = false; });
  });

  $("#brandingHistoryRefresh").addEventListener("click", () => run(loadHistory));

  $("#brandingHistory").addEventListener("click", event => {
    const reprocess = event.target.closest("[data-reprocess]");
    const cancel = event.target.closest("[data-cancel]");
    if (reprocess) {
      run(async () => {
        await requestJson(`/api/v1/branding/videos/${encodeURIComponent(reprocess.dataset.reprocess)}/reprocess`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ override: reprocess.dataset.override }),
        });
        await loadHistory();
      }, "Đã đưa video vào hàng đợi xử lý lại");
    } else if (cancel) {
      run(async () => {
        await requestJson(`/api/v1/jobs/${encodeURIComponent(cancel.dataset.cancel)}/cancel`, { method: "POST" });
        await loadHistory();
      }, "Đã yêu cầu hủy");
    }
  });

  return {
    enter() {
      if (active) return;
      active = true;
      load().catch(error => toast(error.message, true));
    },
    leave() {
      active = false;
      clearTimeout(pollTimer);
    },
  };
}
