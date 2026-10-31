// Account & branding page: display name, the account's logo and how it is stamped.
// Translation and comic forms choose per video whether to stamp it ("Chèn logo").
export function createBrandingWorkspace({ requestJson, toast, escapeHtml, onLogoChange }) {
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const form = $("#brandingForm");
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

  async function load() {
    $("#logoPreview")?.classList.add("is-loading");
    $("#templatePreview")?.classList.add("is-loading");
    try {
      const [account, data] = await Promise.all([requestJson("/api/v1/me"), requestJson("/api/v1/me/branding")]);
      $("#accountEmail").value = account.email || "";
      $("#accountDisplayName").value = account.display_name || "";
      renderBranding(data);
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

  return {
    enter() {
      if (active) return;
      active = true;
      load().catch(error => toast(error.message, true));
    },
    leave() {
      active = false;
    },
  };
}
