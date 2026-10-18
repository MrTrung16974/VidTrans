// Admin tab: register each platform's OAuth developer app once, on the web.
// After that every user connects their own account with one click.
export function createPlatformAppsAdmin({ requestJson, toast, escapeHtml }) {
  const list = () => document.querySelector("#adminPlatformAppsList");

  function callbackUrl(item) {
    return item.redirect_uri || `${location.origin}${item.callback_path}`;
  }

  function render(items) {
    list().innerHTML = items.map(item => {
      const status = item.configured
        ? `<span class="platform-app-badge ok">✓ Đang hoạt động${item.source === "env" ? " · từ .env" : ""}</span>`
        : '<span class="platform-app-badge off">Chưa cấu hình</span>';
      return `
      <form class="card platform-app-card" data-platform-app="${escapeHtml(item.platform)}">
        <div class="platform-app-head">
          <strong>${escapeHtml(item.label)}</strong>
          ${status}
        </div>
        <p class="platform-app-hint">
          Tạo ứng dụng tại <a href="${escapeHtml(item.console_url)}" target="_blank" rel="noopener">${escapeHtml(new URL(item.console_url).host)}</a>
          và khai báo Callback URL bên dưới. Chỉ làm một lần; sau đó mọi user tự bấm <b>Kết nối</b>.
        </p>
        <label>${escapeHtml(item.id_label)}
          <input name="client_id" required maxlength="256" autocomplete="off" value="${escapeHtml(item.client_id)}" />
        </label>
        <label>Secret
          <input name="client_secret" type="password" maxlength="512" autocomplete="new-password"
            placeholder="${item.has_secret ? "•••••• đã lưu — để trống nếu không đổi" : "Nhập App Secret / Client Secret"}" ${item.has_secret ? "" : "required"} />
        </label>
        <label>Callback URL (Redirect URI)
          <input name="redirect_uri" required maxlength="512" value="${escapeHtml(callbackUrl(item))}" />
        </label>
        <div class="platform-app-actions">
          <button class="button primary" type="submit">Lưu & kích hoạt</button>
          ${item.source === "web" ? '<button class="button secondary" type="button" data-platform-app-reset>Xoá cấu hình web</button>' : ""}
        </div>
      </form>`;
    }).join("");
  }

  async function load() {
    try {
      render((await requestJson("/api/v1/admin/platform-apps")).items || []);
    } catch (error) {
      list().innerHTML = `<div class="empty-state error"><strong>${escapeHtml(error.message)}</strong></div>`;
    }
  }

  async function save(form) {
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      await requestJson(`/api/v1/admin/platform-apps/${encodeURIComponent(form.dataset.platformApp)}`, {
        method: "POST", body: new FormData(form),
      });
      toast("Đã lưu. User có thể bấm Kết nối ngay.");
      await load();
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
    }
  }

  async function reset(form) {
    if (!confirm("Xoá cấu hình ứng dụng lưu trên web cho nền tảng này?")) return;
    try {
      await requestJson(`/api/v1/admin/platform-apps/${encodeURIComponent(form.dataset.platformApp)}`, { method: "DELETE" });
      toast("Đã xoá cấu hình web");
      await load();
    } catch (error) { toast(error.message, true); }
  }

  list().addEventListener("submit", event => {
    event.preventDefault();
    save(event.target.closest("[data-platform-app]"));
  });
  list().addEventListener("click", event => {
    if (event.target.closest("[data-platform-app-reset]")) reset(event.target.closest("[data-platform-app]"));
  });
  return { load };
}
