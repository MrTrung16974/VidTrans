// Admin screen "Phân quyền gói & menu": the plan x feature matrix and the
// web/mobile menus, all read from and saved to the database
// (GET /api/v1/admin/access-config, PUT .../plan-entitlements, PUT .../menus).

const ROLE_LABELS = { viewer: "Người xem", editor: "Biên tập viên", admin: "Quản trị viên" };
const PLATFORM_LABELS = { web: "Web", mobile: "App mobile" };
const LOCKED_MENUS = new Set(["web:admin"]);
const ROLE_RANK = { viewer: 0, editor: 1, admin: 2 };

export function createAccessConfig({ requestJson, toast, escapeHtml, onMenusChanged }) {
  let config = null;
  let platform = "mobile";

  const enabledSet = () => new Set(
    config.entitlements.filter(row => row.enabled).map(row => `${row.plan_key}:${row.feature_key}`),
  );

  function renderMatrix() {
    const root = document.querySelector("#accessMatrix");
    if (!root) return;
    const enabled = enabledSet();
    const head = config.plans.map(plan => `<th scope="col">${escapeHtml(plan.name)}<small>${escapeHtml(plan.key)}</small></th>`).join("");
    const rows = config.features.map(feature => {
      const cells = config.plans.map(plan => {
        const id = `${plan.key}:${feature.key}`;
        return `<td><label class="access-check" title="${escapeHtml(plan.name)} · ${escapeHtml(feature.name)}">
          <input type="checkbox" data-entitlement="${escapeHtml(id)}" ${enabled.has(id) ? "checked" : ""} />
          <span class="sr-only">${escapeHtml(plan.name)} — ${escapeHtml(feature.name)}</span>
        </label></td>`;
      }).join("");
      return `<tr><th scope="row"><strong>${escapeHtml(feature.name)}</strong><small>${escapeHtml(feature.key)}</small></th>${cells}</tr>`;
    }).join("");
    root.innerHTML = `<table class="access-table">
      <thead><tr><th scope="col">Tính năng</th>${head}</tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  const formatVnd = value => value == null ? "Báo giá riêng" : `${Number(value).toLocaleString("vi-VN")} đ`;

  function renderPlans() {
    const root = document.querySelector("#accessPlans");
    if (!root) return;
    const rows = config.plans.map(plan => {
      const unlimited = plan.monthly_minutes == null;
      return `<tr data-plan-row="${escapeHtml(plan.key)}">
        <th scope="row"><strong>${escapeHtml(plan.name)}</strong><small>${escapeHtml(plan.key)}</small></th>
        <td data-label="Giá">${escapeHtml(formatVnd(plan.price_vnd))}</td>
        <td data-label="Phút mỗi tháng"><input name="monthly_minutes" type="number" min="0" max="1000000" step="1"
          value="${unlimited ? "" : escapeHtml(String(plan.monthly_minutes))}" ${unlimited ? "disabled" : ""}
          aria-label="Số phút mỗi tháng của gói ${escapeHtml(plan.name)}" /></td>
        <td data-label="Không giới hạn"><label class="access-check"><input name="unlimited" type="checkbox" ${unlimited ? "checked" : ""} /><span class="sr-only">Không giới hạn</span></label></td>
        <td data-label=""><button class="button primary small-btn" type="button" data-save-plan="${escapeHtml(plan.key)}">Lưu</button></td>
      </tr>`;
    }).join("");
    root.innerHTML = `<table class="access-table">
      <thead><tr><th scope="col">Gói</th><th scope="col">Giá</th><th scope="col">Phút mỗi tháng</th><th scope="col">Không giới hạn</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  async function savePlan(button) {
    const planKey = button.dataset.savePlan;
    const row = document.querySelector(`[data-plan-row="${CSS.escape(planKey)}"]`);
    if (!row) return;
    const unlimited = row.querySelector('[name="unlimited"]').checked;
    const raw = row.querySelector('[name="monthly_minutes"]').value.trim();
    const minutes = unlimited || raw === "" ? null : Number(raw);
    if (minutes !== null && (!Number.isInteger(minutes) || minutes < 0 || minutes > 1000000)) {
      toast("Số phút mỗi tháng phải là số nguyên từ 0 đến 1.000.000", true);
      return;
    }
    button.disabled = true;
    try {
      const saved = await requestJson(`/api/v1/admin/plans/${encodeURIComponent(planKey)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monthly_minutes: minutes }),
      });
      config.plans = config.plans.map(plan => plan.key === planKey ? { ...plan, ...saved, features: plan.features } : plan);
      renderPlans();
      toast(`Gói ${saved.name || planKey}: ${saved.monthly_minutes == null ? "không giới hạn" : `${saved.monthly_minutes.toLocaleString("vi-VN")} phút/tháng`}`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  function featureOptions(selected) {
    const options = [`<option value="">Không yêu cầu</option>`].concat(config.features.map(feature =>
      `<option value="${escapeHtml(feature.key)}" ${feature.key === selected ? "selected" : ""}>${escapeHtml(feature.name)}</option>`));
    return options.join("");
  }

  function roleOptions(selected) {
    return config.menu_roles.map(role =>
      `<option value="${role}" ${role === selected ? "selected" : ""}>${ROLE_LABELS[role] || role}</option>`).join("");
  }

  function renderMenus() {
    const root = document.querySelector("#accessMenus");
    if (!root) return;
    document.querySelectorAll("[data-access-platform]").forEach(button => {
      const active = button.dataset.accessPlatform === platform;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    const menus = config.menus.filter(menu => menu.platform === platform);
    const rows = menus.map(menu => {
      const id = `${menu.platform}:${menu.menu_key}`;
      const locked = LOCKED_MENUS.has(id);
      return `<tr data-menu-row="${escapeHtml(id)}">
        <td data-label="Mã"><code>${escapeHtml(menu.menu_key)}</code></td>
        <td data-label="Tên menu"><input name="label" maxlength="60" value="${escapeHtml(menu.label)}" aria-label="Tên menu ${escapeHtml(menu.menu_key)}" /></td>
        <td data-label="Tên ngắn"><input name="short_label" maxlength="24" value="${escapeHtml(menu.short_label)}" aria-label="Tên ngắn ${escapeHtml(menu.menu_key)}" /></td>
        <td data-label="Thứ tự"><input name="sort_order" type="number" min="0" max="999" value="${menu.sort_order}" aria-label="Thứ tự ${escapeHtml(menu.menu_key)}" /></td>
        <td data-label="Cần tính năng"><select name="required_feature" aria-label="Tính năng yêu cầu ${escapeHtml(menu.menu_key)}">${featureOptions(menu.required_feature)}</select></td>
        <td data-label="Vai trò tối thiểu"><select name="min_role" ${locked ? "disabled" : ""} aria-label="Vai trò tối thiểu ${escapeHtml(menu.menu_key)}">${roleOptions(menu.min_role)}</select></td>
        <td data-label="Hiển thị"><label class="access-check"><input name="is_enabled" type="checkbox" ${menu.is_enabled ? "checked" : ""} ${locked ? "disabled" : ""} /><span class="sr-only">Hiển thị</span></label></td>
        <td data-label=""><button class="button primary small-btn" type="button" data-save-menu="${escapeHtml(id)}">Lưu</button></td>
      </tr>`;
    }).join("");
    const note = platform === "mobile"
      ? `Thanh menu dưới của app hiện tối đa ${config.mobile_dock_size} mục đầu tiên mà người dùng được thấy. Các mục thứ tự lớn (Nâng cấp, Tài khoản) chỉ lấp vào chỗ trống khi mục khác bị ẩn.`
      : "Menu điều hướng chính trên web. Menu Quản trị luôn bật và chỉ dành cho quản trị viên.";
    root.innerHTML = `<p class="access-note">${escapeHtml(note)}</p>
      <div class="access-scroll"><table class="access-table access-menu-table">
        <thead><tr><th>Mã</th><th>Tên menu</th><th>Tên ngắn</th><th>Thứ tự</th><th>Cần tính năng</th><th>Vai trò tối thiểu</th><th>Hiển thị</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`;
    renderPreview();
  }

  // What an editor on each plan sees, computed from the unsaved matrix + saved menus.
  function renderPreview() {
    const root = document.querySelector("#accessPreview");
    if (!root) return;
    const enabled = enabledSet();
    const menus = config.menus
      .filter(menu => menu.platform === platform)
      .sort((a, b) => a.sort_order - b.sort_order || a.menu_key.localeCompare(b.menu_key));
    const rows = config.plans.map(plan => {
      let shown = menus.filter(menu => menu.is_enabled
        && ROLE_RANK[menu.min_role] <= ROLE_RANK.editor
        && (!menu.required_feature || enabled.has(`${plan.key}:${menu.required_feature}`)));
      if (platform === "mobile") shown = shown.slice(0, config.mobile_dock_size);
      const chips = shown.length
        ? shown.map(menu => `<span class="access-chip">${escapeHtml(menu.short_label || menu.label)}</span>`).join("")
        : `<span class="access-chip is-empty">Không có menu</span>`;
      return `<li><strong>${escapeHtml(plan.name)}</strong><span class="access-chips">${chips}</span></li>`;
    }).join("");
    root.innerHTML = `<p class="access-note">Xem trước menu ${escapeHtml(PLATFORM_LABELS[platform])} của người dùng vai trò Biên tập viên theo từng gói:</p><ul class="access-preview-list">${rows}</ul>`;
  }

  async function load() {
    const root = document.querySelector("#accessMatrix");
    try {
      config = await requestJson("/api/v1/admin/access-config");
      renderPlans();
      renderMatrix();
      renderMenus();
    } catch (error) {
      if (root) root.innerHTML = `<div class="empty-state"><strong>${escapeHtml(error.message)}</strong></div>`;
    }
  }

  async function saveEntitlement(input) {
    const [planKey, featureKey] = input.dataset.entitlement.split(":");
    input.disabled = true;
    try {
      await requestJson(`/api/v1/admin/plan-entitlements/${encodeURIComponent(planKey)}/${encodeURIComponent(featureKey)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: input.checked }),
      });
      const row = config.entitlements.find(item => item.plan_key === planKey && item.feature_key === featureKey);
      if (row) row.enabled = input.checked;
      renderPreview();
      toast(`Đã ${input.checked ? "cấp" : "thu hồi"} quyền cho gói ${planKey}`);
      onMenusChanged?.();
    } catch (error) {
      input.checked = !input.checked;
      toast(error.message, true);
    } finally {
      input.disabled = false;
    }
  }

  async function saveMenu(button) {
    const id = button.dataset.saveMenu;
    const row = document.querySelector(`[data-menu-row="${CSS.escape(id)}"]`);
    if (!row) return;
    const [menuPlatform, menuKey] = id.split(":");
    const value = name => row.querySelector(`[name="${name}"]`);
    const body = {
      label: value("label").value.trim(),
      short_label: value("short_label").value.trim(),
      sort_order: Number(value("sort_order").value),
      required_feature: value("required_feature").value || null,
      min_role: value("min_role").value,
      is_enabled: value("is_enabled").checked,
    };
    button.disabled = true;
    try {
      const saved = await requestJson(`/api/v1/admin/menus/${encodeURIComponent(menuPlatform)}/${encodeURIComponent(menuKey)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      config.menus = config.menus.map(menu =>
        menu.platform === menuPlatform && menu.menu_key === menuKey ? saved : menu);
      renderMenus();
      toast(`Đã lưu menu “${saved.label}”`);
      onMenusChanged?.();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  document.addEventListener("change", event => {
    const input = event.target.closest?.("[data-entitlement]");
    if (input && config) saveEntitlement(input);
    const unlimited = event.target.closest?.('[data-plan-row] [name="unlimited"]');
    if (unlimited) {
      const minutes = unlimited.closest("[data-plan-row]").querySelector('[name="monthly_minutes"]');
      minutes.disabled = unlimited.checked;
      if (!unlimited.checked && !minutes.value) minutes.focus();
    }
  });
  document.addEventListener("click", event => {
    const save = event.target.closest?.("[data-save-menu]");
    if (save && config) {
      saveMenu(save);
      return;
    }
    const savePlanButton = event.target.closest?.("[data-save-plan]");
    if (savePlanButton && config) {
      savePlan(savePlanButton);
      return;
    }
    const tab = event.target.closest?.("[data-access-platform]");
    if (tab && config) {
      platform = tab.dataset.accessPlatform;
      renderMenus();
    }
  });

  return { load };
}
