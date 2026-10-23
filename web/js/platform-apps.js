// Each user enters their own OAuth developer app (App ID / Secret) per platform.
// Values are stored per user in the database; secrets are never sent back.
export function createPlatformAppsSettings({ requestJson, toast, escapeHtml, onChange = () => {} }) {
  const list = () => document.querySelector("#platformAppsList");

  function callbackUrl(item) {
    return item.redirect_uri || `${location.origin}${item.callback_path}`;
  }

  function render(items) {
    list().innerHTML = items.map(item => {
      const status = item.configured
        ? `<span class="platform-app-badge ok">✓ ${item.source === "user" ? "Đang dùng app của bạn" : "Đang dùng app mặc định"}</span>`
        : '<span class="platform-app-badge off">Chưa cấu hình</span>';
      return `
      <form class="card platform-app-card" data-platform-app="${escapeHtml(item.platform)}">
        <div class="platform-app-head">
          <strong>${escapeHtml(item.label)}</strong>
          ${status}
        </div>
        <p class="platform-app-hint">
          Tạo ứng dụng tại <a href="${escapeHtml(item.console_url)}" target="_blank" rel="noopener">${escapeHtml(new URL(item.console_url).host)}</a>
          rồi khai báo đúng Callback URL bên dưới trong app. Lưu xong, bấm <b>Kết nối</b> ở thẻ nền tảng để đăng nhập.
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
          ${item.source === "user" ? '<button class="button secondary" type="button" data-platform-app-reset>Xoá cấu hình</button>' : ""}
        </div>
      </form>`;
    }).join("");
  }

  async function load() {
    try {
      render((await requestJson("/api/v1/me/platform-apps")).items || []);
    } catch (error) {
      list().innerHTML = `<div class="empty-state error"><strong>${escapeHtml(error.message)}</strong></div>`;
    }
  }

  async function save(form) {
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      await requestJson(`/api/v1/me/platform-apps/${encodeURIComponent(form.dataset.platformApp)}`, {
        method: "POST", body: new FormData(form),
      });
      toast("Đã lưu. Bấm Kết nối ở thẻ nền tảng để đăng nhập.");
      await load();
      onChange();
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
    }
  }

  async function reset(form) {
    if (!confirm("Xoá App ID / Secret bạn đã lưu cho nền tảng này?")) return;
    try {
      await requestJson(`/api/v1/me/platform-apps/${encodeURIComponent(form.dataset.platformApp)}`, { method: "DELETE" });
      toast("Đã xoá cấu hình");
      await load();
      onChange();
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
