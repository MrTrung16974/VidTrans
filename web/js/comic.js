// Story -> comic / animated video workspace: create projects, review the storyboard, edit scenes or shots
// and re-render only what changed.
import { renderAnimatedScenes, renderReferenceGallery, shotPatchFromForm } from "./animation-editor.js?v=20261023-smooth";

const ACTIVE = new Set(["queued", "scheduled", "processing", "cancelling"]);
const STEP_LABELS = {
  queued: "Đang xếp hàng", comic_storyboard: "Phân tích truyện & chia cảnh", comic_references: "Ảnh tham chiếu nhân vật",
  comic_images: "Sinh ảnh minh họa", comic_voice: "Lồng tiếng", comic_clips: "Dựng clip từng cảnh",
  comic_export: "Ghép & xuất video", storyboard_ready: "Storyboard sẵn sàng để duyệt", completed: "Đã xuất video",
  comic_keyframes: "Sinh keyframe", comic_animation: "Tạo chuyển động AI", shot_preview_ready: "Đã dựng xem trước shot",
  failed: "Thất bại", cancelled: "Đã hủy", cancelling: "Đang dừng",
};
const MOTION_LABELS = {
  slow_zoom_in: "Zoom vào chậm", slow_zoom_out: "Zoom ra chậm", pan_left: "Lia sang trái", pan_right: "Lia sang phải",
  pan_up: "Lia lên", pan_down: "Lia xuống", static: "Đứng yên",
};
const POLL_MS = 2500;
// #comic/new shows the "new story" form, #comic/<job id> one project; both go through the
// hash so the browser's Back button moves between them.
const NEW_ROUTE = "#comic/new";
const projectRoute = jobId => `#comic/${encodeURIComponent(jobId)}`;
const narrowLayout = () => window.matchMedia("(max-width: 960px)").matches;

export function createComicWorkspace({ requestJson, toast, escapeHtml }) {
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const view = $("#comicView");
  const form = $("#comicForm");
  const editor = $("#comicEditor");
  let options = null;
  let current = null;
  let pollTimer = null;
  let entered = false;
  let projectPage = 1;
  let projectPageSize = 6;
  let projectTotal = 0;
  let projectTotalPages = 1;
  let scenePage = 1;
  let scenePageSize = 6;
  try {
    const savedSceneSize = parseInt(localStorage.getItem("vidtrans_comic_scenes_page_size") || "", 10);
    if ([6, 10, 20, 40].includes(savedSceneSize)) scenePageSize = savedSceneSize;
  } catch (_) {}
  const openGalleries = new Set();

  const api = (path, init = {}) => requestJson(`/api/v1/comic${path}`, init);
  const withSceneQuery = path => {
    const sep = path.includes("?") ? "&" : "?";
    return `${path}${sep}scene_page=${scenePage}&scene_limit=${scenePageSize}`;
  };
  const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  const isActive = job => ACTIVE.has(job?.status);
  const statusClass = job => isActive(job) ? "processing" : job?.status === "completed" ? "completed" : job?.status === "failed" ? "failed" : "queued";

  function paginationPages(currentPage, totalPages) {
    if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1);
    if (currentPage <= 4) return [1, 2, 3, 4, 5, "...", totalPages];
    if (currentPage >= totalPages - 3) return [1, "...", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
    return [1, "...", currentPage - 1, currentPage, currentPage + 1, "...", totalPages];
  }

  function fillSelect(select, items, selected) {
    if (!select) return;
    select.innerHTML = items.map(item => {
      const disabled = item.configured === false;
      const label = item.label + (disabled ? " — chưa cấu hình" : "");
      return `<option value="${escapeHtml(item.key)}"${disabled ? " disabled" : ""}${item.key === selected ? " selected" : ""}>${escapeHtml(label)}</option>`;
    }).join("");
  }

  async function loadOptions() {
    if (options) return;
    options = await api("/options");
    fillSelect(form.elements.style, options.styles, "anime_comic");
    fillSelect(form.elements.language, options.languages, "vi");
    fillSelect(form.elements.llm_provider, options.llm_providers, "auto");
    const comfyui = options.image_providers.find(item => item.key === "comfyui");
    const gemini = options.image_providers.find(item => item.key === "gemini");
    fillSelect(form.elements.image_provider, options.image_providers,
      comfyui?.configured ? "comfyui" : gemini?.configured ? "gemini" : "placeholder");
    fillSelect(form.elements.animation_provider, options.animation_providers, "camera_2_5d");
    const cit = options.tts_providers.find(item => item.key === "cit");
    fillSelect(form.elements.tts_provider, options.tts_providers, cit?.configured ? "cit" : "edge");
    syncTtsVoices();
    $("#comicStoryLimit").textContent = `Tối đa ${options.max_story_chars.toLocaleString("vi-VN")} ký tự mỗi dự án.`;
  }

  // CIT Voice Studio voices come from the desktop app; the narrator list follows the narrator's gender.
  function syncTtsVoices() {
    const cit = options?.tts_providers?.find(item => item.key === "cit");
    const useCit = form.elements.tts_provider?.value === "cit";
    $$(".comic-cit-only").forEach(field => field.classList.toggle("is-hidden", !useCit));
    if (!useCit) return;
    const voices = cit?.voices || [];
    const fill = (select, gender) => {
      const previous = select.value;
      const list = voices.filter(voice => voice.gender === gender);
      select.innerHTML = (list.length ? list : voices).map(voice =>
        `<option value="${escapeHtml(voice.key)}">${escapeHtml(voice.label)}</option>`).join("");
      if ([...select.options].some(option => option.value === previous)) select.value = previous;
    };
    fill(form.elements.cit_narrator_voice, form.elements.narrator_voice.value);
    fill(form.elements.cit_female_voice, "female");
    fill(form.elements.cit_male_voice, "male");
  }

  function renderProjectPagination() {
    const nav = $("#comicProjectPagination");
    if (!nav) return;
    if (projectTotal <= 0 || projectTotalPages <= 1) {
      nav.classList.add("is-hidden");
      nav.innerHTML = "";
      return;
    }
    nav.classList.remove("is-hidden");
    nav.innerHTML = `
      <button type="button" class="pagination-btn" data-project-page="${projectPage - 1}" ${projectPage <= 1 ? "disabled" : ""} aria-label="Trang dự án trước">‹ Trước</button>
      <span class="comic-project-page-info">Trang <strong>${projectPage}</strong>/${projectTotalPages} <small>(${projectTotal})</small></span>
      <button type="button" class="pagination-btn" data-project-page="${projectPage + 1}" ${projectPage >= projectTotalPages ? "disabled" : ""} aria-label="Trang dự án sau">Sau ›</button>
    `;
  }

  async function loadProjects(page = projectPage) {
    const list = $("#comicProjectList");
    projectPage = Math.max(1, page);
    try {
      const data = await api(`/projects?page=${projectPage}&limit=${projectPageSize}`);
      const projects = data.items || [];
      projectTotal = Number(data.total ?? projects.length);
      projectTotalPages = Math.max(1, Number(data.total_pages || Math.ceil(projectTotal / projectPageSize) || 1));
      projectPage = Math.min(Math.max(1, Number(data.page || projectPage)), projectTotalPages);
      if (!projects.length) {
        list.innerHTML = '<p class="comic-empty">Chưa có dự án nào. Bấm "Tạo truyện mới" để bắt đầu.</p>';
        renderProjectPagination();
        return;
      }
      list.innerHTML = projects.map(job => {
        const comic = job.comic || {};
        const meta = [comic.video_mode === "animated" ? "Hoạt hình" : "Truyện tranh", comic.scenes ? `${comic.scenes} cảnh` : "",
          comic.shots ? `${comic.shots} shot` : "", comic.aspect_ratio || ""].filter(Boolean).join(" · ");
        const active = current?.job?.job_id === job.job_id;
        return `<button type="button" class="comic-project${active ? " is-active" : ""}"${active ? ' aria-current="true"' : ""} data-job="${escapeHtml(job.job_id)}">
          <strong>${escapeHtml(comic.title || job.filename || job.job_id)}</strong>
          <span class="status-pill status-${statusClass(job)}">${escapeHtml(STEP_LABELS[job.step] || job.status)}</span>
          <small>${escapeHtml(meta)}</small></button>`;
      }).join("");
      renderProjectPagination();
    } catch (error) {
      list.innerHTML = `<p class="comic-empty">${escapeHtml(error.message)}</p>`;
      $("#comicProjectPagination")?.classList.add("is-hidden");
    }
  }

  async function open(jobId, { reveal = false, keepPage = false, scrollScenes = false } = {}) {
    if (!keepPage && current?.job?.job_id !== jobId) scenePage = 1;
    current = await api(withSceneQuery(`/projects/${encodeURIComponent(jobId)}`));
    if (current?.scenes_pagination?.page) scenePage = current.scenes_pagination.page;
    // A shareable link straight to this project.
    if (location.hash !== projectRoute(jobId)) history.replaceState(null, "", projectRoute(jobId));
    render();
    schedulePoll();
    loadProjects();
    // On one-column layouts the project opens below the list; bring it into view.
    if (reveal && narrowLayout()) editor.scrollIntoView({ behavior: "smooth", block: "start" });
    if (scrollScenes) $("#comicScenes")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function showNew() {
    current = null;
    clearTimeout(pollTimer);
    render();
    loadProjects();
    if (narrowLayout()) form.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!entered || !current || !isActive(current.job)) return;
    pollTimer = setTimeout(async () => {
      try {
        const wasActive = isActive(current.job);
        current = await api(withSceneQuery(`/projects/${encodeURIComponent(current.job.job_id)}`));
        if (current?.scenes_pagination?.page) scenePage = current.scenes_pagination.page;
        render();
        if (wasActive && !isActive(current.job)) {
          loadProjects();
          toast(current.job.status === "failed" ? "Dự án truyện gặp lỗi" : "Dự án truyện đã xử lý xong", current.job.status === "failed");
        }
      } catch (_) { /* Keep polling through transient errors. */ }
      schedulePoll();
    }, POLL_MS);
  }

  function render() {
    // The main column shows either the new-story form or the selected project, never both.
    form.classList.toggle("is-hidden", Boolean(current));
    $("#comicNewButton")?.classList.toggle("is-current", !current);
    if (!current) { editor.classList.add("is-hidden"); return; }
    editor.classList.remove("is-hidden");
    const { job, storyboard, media } = current;
    const locked = isActive(job);
    const progress = Math.round((job.progress || 0) * 100);
    const stale = storyboard && current.rendered_revision !== storyboard.revision;
    $("#comicEditorTitle").textContent = job.comic?.title || job.filename || job.job_id;
    $("#comicEditorStatus").className = `status-pill status-${statusClass(job)}`;
    $("#comicEditorStatus").textContent = STEP_LABELS[job.step] || job.status;
    $("#comicEditorDetail").textContent = job.error || job.step_detail || "";
    $("#comicEditorDetail").classList.toggle("is-error", Boolean(job.error));
    $("#comicProgress").style.width = `${locked ? progress : job.status === "completed" ? 100 : progress}%`;
    $("#comicProgressRow").classList.toggle("is-hidden", !locked);
    $("#comicStale").classList.toggle("is-hidden", !stale || locked || !storyboard);
    $$("[data-comic-action]").forEach(button => {
      const action = button.dataset.comicAction;
      button.disabled = action === "cancel" ? !locked : locked;
      button.classList.toggle("is-hidden", action === "cancel" && !locked);
    });
    const videoReady = Boolean(job.video_url);
    $("#comicDownloads").innerHTML = videoReady
      ? `<a class="button secondary" href="${escapeHtml(job.video_url)}" download>Tải MP4</a>`
        + (job.subtitle_url ? `<a class="button secondary" href="${escapeHtml(job.subtitle_url)}" download>Tải phụ đề SRT</a>` : "")
      : "";
    const player = $("#comicPlayer");
    if (videoReady && !locked) {
      const src = `${job.video_url}?r=${current.rendered_revision || 0}&t=${encodeURIComponent(job.finished_at || "")}`;
      if (player.dataset.src !== src) { player.src = src; player.dataset.src = src; }
      player.classList.remove("is-hidden");
    } else {
      player.classList.add("is-hidden");
    }
    const warnings = storyboard?.warnings || [];
    $("#comicWarnings").innerHTML = warnings.length
      ? `<strong>Cần kiểm tra (${warnings.length})</strong><ul>${warnings.slice(0, 12).map(w => `<li>${escapeHtml(w)}</li>`).join("")}</ul>`
      : "";
    $("#comicWarnings").classList.toggle("is-hidden", !warnings.length);
    const animated = storyboard?.mode === "animated";
    const metrics = current.metrics;
    const pagination = current.scenes_pagination;
    const totalScenes = pagination?.total_scenes ?? (storyboard ? storyboard.scenes.length : 0);
    const shotCount = animated
      ? (pagination?.total_shots ?? storyboard.scenes.reduce((total, scene) => total + scene.shots.length, 0))
      : 0;
    $("#comicMeta").textContent = storyboard
      ? [`Bản ${storyboard.revision}`, `${totalScenes} cảnh`, animated ? `${shotCount} shot` : "",
        `${storyboard.characters.length} nhân vật`, `chia cảnh: ${storyboard.analysis_method}`,
        metrics ? `AI ${metrics.ai_seconds}s · ${metrics.attempts} lượt sinh · ${metrics.fallback_shots} shot dự phòng 2.5D` : ""]
        .filter(Boolean).join(" · ")
      : "Storyboard đang được tạo…";
    $('[data-comic-action="images"]').textContent = animated ? "Chỉ sinh keyframe" : "Chỉ sinh ảnh";
    renderCharacters(storyboard, media, locked);
    if (animated) {
      $("#comicScenes").innerHTML = renderAnimatedScenes({ storyboard, media, shots: current.shots, options, locked, escapeHtml });
    } else {
      renderScenes(storyboard, media, locked);
    }
    renderScenesPagination(storyboard, pagination);
  }

  function renderScenesPagination(storyboard, pagination) {
    const container = $("#comicScenesPagination");
    if (!container) return;
    const totalScenes = pagination?.total_scenes ?? (storyboard?.scenes?.length || 0);
    if (!storyboard || totalScenes === 0) {
      container.classList.add("is-hidden");
      container.innerHTML = "";
      return;
    }
    const page = pagination?.page || scenePage || 1;
    const pageSize = pagination?.limit || scenePageSize || 6;
    const totalPages = Math.max(1, pagination?.total_pages || Math.ceil(totalScenes / pageSize));
    const startIndex = pagination?.offset ?? (page - 1) * pageSize;
    const endIndex = Math.min(startIndex + storyboard.scenes.length, totalScenes);
    const pages = paginationPages(page, totalPages);
    const pagesHtml = pages.map(p => {
      if (p === "...") return `<span class="pagination-ellipsis" aria-hidden="true">…</span>`;
      const isCurrent = p === page;
      return `<button type="button" class="pagination-page-btn${isCurrent ? " active" : ""}" data-scene-page="${p}" ${isCurrent ? 'aria-current="page"' : ""} aria-label="Trang cảnh ${p}">${p}</button>`;
    }).join("");

    container.classList.remove("is-hidden");
    container.innerHTML = `
      <div class="pagination-info">
        <span class="pagination-text">Hiển thị cảnh <strong>${startIndex + 1}</strong> – <strong>${endIndex}</strong> trong số <strong>${totalScenes}</strong> cảnh</span>
        <div class="pagination-size-wrap">
          <label for="comicScenesPageSizeSelect">Hiển thị:</label>
          <select id="comicScenesPageSizeSelect" class="pagination-size-select" aria-label="Số cảnh mỗi trang">
            ${[6, 10, 20, 40].map(size => `<option value="${size}" ${pageSize === size ? "selected" : ""}>${size} cảnh / trang</option>`).join("")}
          </select>
        </div>
      </div>
      <div class="pagination-nav" role="navigation" aria-label="Phân trang cảnh storyboard">
        <button type="button" class="pagination-btn pagination-first" data-scene-page="1" ${page <= 1 ? "disabled" : ""} title="Trang đầu" aria-label="Trang đầu">«</button>
        <button type="button" class="pagination-btn pagination-prev" data-scene-page="${page - 1}" ${page <= 1 ? "disabled" : ""} title="Trang trước" aria-label="Trang trước">
          <span class="btn-label-prev">‹ Trước</span>
        </button>
        <div class="pagination-pages">${pagesHtml}</div>
        <button type="button" class="pagination-btn pagination-next" data-scene-page="${page + 1}" ${page >= totalPages ? "disabled" : ""} title="Trang sau" aria-label="Trang sau">
          <span class="btn-label-next">Sau ›</span>
        </button>
        <button type="button" class="pagination-btn pagination-last" data-scene-page="${totalPages}" ${page >= totalPages ? "disabled" : ""} title="Trang cuối" aria-label="Trang cuối">»</button>
      </div>
    `;
  }

  async function refreshGallery(characterId) {
    const box = editor.querySelector(`[data-ref-box="${CSS.escape(characterId)}"]`);
    if (!box || !current) return;
    try {
      const data = await api(`/projects/${encodeURIComponent(current.job.job_id)}/characters/${encodeURIComponent(characterId)}/references`);
      box.innerHTML = renderReferenceGallery(data, escapeHtml, isActive(current.job));
    } catch (error) {
      box.innerHTML = `<p class="comic-empty">${escapeHtml(error.message)}</p>`;
    }
  }

  function renderCharacters(storyboard, media, locked) {
    const box = $("#comicCharacters");
    const characters = storyboard?.characters || [];
    $("#comicCharactersBlock").classList.toggle("is-hidden", !characters.length);
    box.innerHTML = characters.map(c => {
      const ref = media.references?.[c.id];
      return `<form class="comic-character" data-character="${escapeHtml(c.id)}">
        <div class="comic-character-ref">${ref ? `<img src="${escapeHtml(ref)}" alt="${escapeHtml(c.name)}" loading="lazy">` : `<span>${escapeHtml(c.name.slice(0, 1))}</span>`}</div>
        <div class="comic-character-fields">
          <div class="comic-inline">
            <label class="field"><span>Tên</span><input name="name" value="${escapeHtml(c.name)}" maxlength="120"></label>
            <label class="field"><span>Giới tính</span><select name="gender">${["female", "male", "unknown"].map(g => `<option value="${g}"${c.gender === g ? " selected" : ""}>${{ female: "Nữ", male: "Nam", unknown: "Không rõ" }[g]}</option>`).join("")}</select></label>
          </div>
          <label class="field"><span>Ngoại hình cố định (tiếng Anh, giữ nhất quán)</span><textarea name="appearance" rows="2" maxlength="600">${escapeHtml(c.appearance)}</textarea></label>
          <label class="field"><span>Trang phục</span><input name="outfit" value="${escapeHtml(c.outfit)}" maxlength="400"></label>
          <small class="field-hint">id: ${escapeHtml(c.id)} · ${c.approved_reference ? "đang ghim ảnh tham chiếu đã duyệt" : "sửa ngoại hình sẽ vẽ lại ảnh tham chiếu và các cảnh có nhân vật này"}</small>
          <div class="comic-scene-actions">
            <button class="button secondary" type="submit"${locked ? " disabled" : ""}>Lưu nhân vật</button>
            <button class="button secondary" type="button" data-ref-op="toggle">${openGalleries.has(c.id) ? "Ẩn phiên bản" : "Phiên bản ảnh tham chiếu"}</button>
          </div>
          <div class="comic-ref-box${openGalleries.has(c.id) ? "" : " is-hidden"}" data-ref-box="${escapeHtml(c.id)}"></div>
        </div></form>`;
    }).join("");
    openGalleries.forEach(id => refreshGallery(id));
  }

  function dialogueText(lines) {
    return (lines || []).map(line => `${line.character}: ${line.text}`).join("\n");
  }

  function parseDialogue(value) {
    return value.split("\n").map(line => line.trim()).filter(Boolean).map(line => {
      const index = line.indexOf(":");
      if (index < 1) throw new Error(`Dòng thoại "${line}" cần dạng id_nhân_vật: lời thoại`);
      return { character: line.slice(0, index).trim(), text: line.slice(index + 1).trim() };
    });
  }

  function renderScenes(storyboard, media, locked) {
    const box = $("#comicScenes");
    if (!storyboard) { box.innerHTML = ""; return; }
    const disabled = locked ? " disabled" : "";
    box.innerHTML = storyboard.scenes.map(scene => {
      const index = String(scene.scene_index);
      const image = media.uploads?.[index] || media.scenes?.[index];
      return `<article class="comic-scene card" data-scene="${index}">
        <div class="comic-scene-media">
          ${image ? `<img src="${escapeHtml(image)}" alt="Cảnh ${index}" loading="lazy">` : '<span class="comic-scene-noimg">Chưa có ảnh — bấm "Chỉ sinh ảnh" hoặc "Render video"</span>'}
          ${scene.image_override ? '<b class="comic-badge">Ảnh tải lên</b>' : ""}
        </div>
        <form class="comic-scene-body">
          <header><strong>Cảnh ${index}</strong><small>v${scene.version}${scene.emotion ? ` · ${escapeHtml(scene.emotion)}` : ""}${scene.location ? ` · ${escapeHtml(scene.location)}` : ""}</small>
            <select name="camera_motion" aria-label="Chuyển động camera"${disabled}>${options.camera_motions.map(m => `<option value="${m}"${scene.camera_motion === m ? " selected" : ""}>${MOTION_LABELS[m] || m}</option>`).join("")}</select></header>
          <label class="field"><span>Lời dẫn chuyện</span><textarea name="narration" rows="3" maxlength="1500"${disabled}>${escapeHtml(scene.narration)}</textarea></label>
          <label class="field"><span>Lời thoại — mỗi dòng <code>id_nhân_vật: lời</code></span><textarea name="dialogue" rows="2"${disabled}>${escapeHtml(dialogueText(scene.dialogue))}</textarea></label>
          <label class="field"><span>Nhân vật xuất hiện (id, cách nhau dấu phẩy)</span><input name="characters" value="${escapeHtml(scene.characters.join(", "))}"${disabled}></label>
          <details><summary>Prompt hình ảnh</summary>
            <label class="field"><span>Mô tả khung hình (tiếng Anh)</span><textarea name="visual_prompt" rows="3" maxlength="1500"${disabled}>${escapeHtml(scene.visual_prompt)}</textarea></label>
            <label class="field"><span>Negative prompt</span><input name="negative_prompt" value="${escapeHtml(scene.negative_prompt)}" maxlength="600"${disabled}></label>
          </details>
          <div class="comic-scene-actions">
            <button class="button primary" type="submit"${disabled}>Lưu cảnh</button>
            <label class="button secondary comic-upload${locked ? " is-disabled" : ""}">Đổi ảnh<input type="file" accept="image/png,image/jpeg,image/webp" data-scene-upload${disabled} hidden></label>
            ${scene.image_override ? `<button class="button secondary" type="button" data-scene-op="clear-image"${disabled}>Bỏ ảnh tải lên</button>` : ""}
            <button class="button secondary" type="button" data-scene-op="insert"${disabled}>+ Cảnh sau</button>
            <button class="button secondary comic-danger" type="button" data-scene-op="delete"${disabled}>Xóa</button>
          </div>
        </form></article>`;
    }).join("");
  }


  async function mutate(promise, message) {
    try {
      current = await promise;
      if (current?.scenes_pagination?.page) scenePage = current.scenes_pagination.page;
      render();
      loadProjects();
      if (message) toast(message);
    } catch (error) {
      toast(error.message, true);
    }
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const data = new FormData(form);
    for (const flag of ["burn_subtitles", "auto_render"]) data.set(flag, form.elements[flag].checked ? "true" : "false");
    for (const name of ["file", "background_music"]) {
      if (!form.elements[name].files.length) data.delete(name);
    }
    if (!data.get("file") && !String(data.get("text") || "").trim()) {
      toast("Hãy dán nội dung truyện hoặc chọn file TXT/DOCX", true);
      return;
    }
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const created = await api("/projects", { method: "POST", body: data });
      toast("Đã tạo dự án, hệ thống đang chia cảnh…");
      form.elements.text.value = "";
      form.elements.file.value = "";
      // Push the project's route so Back returns to the form.
      history.pushState(null, "", projectRoute(created.job_id));
      await open(created.job_id);
      editor.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      toast(error.message, true);
    } finally {
      submit.disabled = false;
    }
  });

  const syncMode = () => {
    const animated = form.elements.video_mode.value === "animated";
    form.querySelectorAll(".comic-animated-only").forEach(element => element.classList.toggle("is-hidden", !animated));
  };
  form.querySelectorAll('[name="video_mode"]').forEach(radio => radio.addEventListener("change", syncMode));
  form.elements.tts_provider?.addEventListener("change", syncTtsVoices);
  form.elements.narrator_voice?.addEventListener("change", syncTtsVoices);

  form.elements.text.addEventListener("input", () => {
    $("#comicStoryCount").textContent = `${form.elements.text.value.length.toLocaleString("vi-VN")} ký tự`;
  });

  $("#comicProjectList").addEventListener("click", event => {
    const button = event.target.closest("[data-job]");
    if (!button) return;
    if (location.hash === projectRoute(button.dataset.job)) {
      open(button.dataset.job, { reveal: true }).catch(error => toast(error.message, true));
    } else {
      location.hash = projectRoute(button.dataset.job);  // hashchange -> enter() opens it
    }
  });

  $("#comicProjectPagination")?.addEventListener("click", event => {
    const button = event.target.closest("[data-project-page]");
    if (!button || button.disabled) return;
    const nextPage = parseInt(button.dataset.projectPage, 10);
    if (!isNaN(nextPage) && nextPage !== projectPage) {
      loadProjects(nextPage);
    }
  });

  editor.addEventListener("click", async event => {
    const scenePageBtn = event.target.closest("[data-scene-page]");
    if (scenePageBtn && !scenePageBtn.disabled && current) {
      const nextScenePage = parseInt(scenePageBtn.dataset.scenePage, 10);
      if (!isNaN(nextScenePage) && nextScenePage !== scenePage) {
        scenePage = nextScenePage;
        await open(current.job.job_id, { keepPage: true, scrollScenes: true });
      }
      return;
    }
    const viewModeBtn = event.target.closest("[data-comic-view-mode]");
    if (viewModeBtn) {
      const mode = viewModeBtn.dataset.comicViewMode;
      $("#comicScenes")?.setAttribute("data-view-mode", mode);
      $$("[data-comic-view-mode]").forEach(btn => btn.classList.toggle("is-active", btn === viewModeBtn));
      try { localStorage.setItem("vidtrans_comic_view_mode", mode); } catch (_) {}
      return;
    }
    const sidebarBtn = event.target.closest("#comicSidebarToggle");
    if (sidebarBtn) {
      const layout = view.querySelector(".comic-layout");
      const collapsed = layout?.classList.toggle("is-sidebar-collapsed");
      sidebarBtn.classList.toggle("is-active", Boolean(collapsed));
      sidebarBtn.textContent = collapsed ? "◨ Hiện dự án" : "◧ Mở rộng khung";
      return;
    }
    const action = event.target.closest("[data-comic-action]")?.dataset.comicAction;
    const op = event.target.closest("[data-scene-op]")?.dataset.sceneOp;
    const shotOp = event.target.closest("[data-shot-op]")?.dataset.shotOp;
    const refOp = event.target.closest("[data-ref-op]")?.dataset.refOp;
    if (!current || (!action && !op && !shotOp && !refOp)) return;
    const jobId = encodeURIComponent(current.job.job_id);
    if (shotOp) {
      const label = event.target.closest("[data-shot]").dataset.shot;
      const [sceneIndex, shotIndex] = label.split(".");
      const shotPath = `/projects/${jobId}/scenes/${sceneIndex}/shots/${shotIndex}`;
      if (shotOp === "preview") {
        try {
          // One key per click: a double-click or network retry cannot queue (and bill) the shot twice.
          await api(`/projects/${jobId}/render`, { ...json("POST", { shot: label }),
            headers: { "Content-Type": "application/json", "Idempotency-Key": `${label}-${current.storyboard.revision}-${Date.now()}` } });
          toast(`Đang dựng xem trước shot ${label}…`);
          await open(current.job.job_id, { keepPage: true });
        } catch (error) { toast(error.message, true); }
      } else if (shotOp === "regenerate") {
        await mutate(api(withSceneQuery(`${shotPath}/regenerate`), { method: "POST" }), `Shot ${label}: sẽ tạo chuyển động mới ở lần dựng tới`);
      } else if (shotOp === "clear-keyframe") {
        await mutate(api(withSceneQuery(`${shotPath}/keyframe`), { method: "DELETE" }), "Đã bỏ keyframe tải lên");
      } else if (shotOp === "delete") {
        if (confirm(`Xóa shot ${label}?`)) await mutate(api(withSceneQuery(shotPath), { method: "DELETE" }), "Đã xóa shot");
      } else if (shotOp === "insert") {
        const keyframe = prompt("Mô tả khung hình đầu của shot mới (tiếng Anh):");
        if (!keyframe?.trim()) return;
        const narration = prompt("Lời dẫn (có thể để trống):") || "";
        await mutate(api(withSceneQuery(`/projects/${jobId}/scenes/${sceneIndex}/shots`), json("POST", { after: Number(shotIndex), keyframe_prompt: keyframe, narration })), "Đã chèn shot");
      }
      return;
    }
    if (refOp) {
      const characterId = event.target.closest("[data-character]").dataset.character;
      const base = `/projects/${jobId}/characters/${encodeURIComponent(characterId)}/references`;
      if (refOp === "toggle") {
        if (openGalleries.has(characterId)) openGalleries.delete(characterId); else openGalleries.add(characterId);
        render();
      } else if (refOp === "approve") {
        const name = event.target.closest("[data-ref-name]").dataset.refName;
        await mutate(api(withSceneQuery(`${base}/${encodeURIComponent(name)}/approve`), { method: "POST" }), "Đã duyệt ảnh tham chiếu");
      } else if (refOp === "unpin") {
        await mutate(api(withSceneQuery(`${base}/approval`), { method: "DELETE" }), "Đã bỏ ghim ảnh tham chiếu");
      }
      return;
    }
    if (action === "cancel") {
      try { await requestJson(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" }); toast("Đã yêu cầu dừng"); }
      catch (error) { toast(error.message, true); }
      return;
    }
    if (action) {
      if (action === "reanalyze" && !confirm("Phân tích lại sẽ thay toàn bộ storyboard hiện tại (các chỉnh sửa tay sẽ mất). Tiếp tục?")) return;
      const body = { images_only: action === "images", reanalyze: action === "reanalyze" };
      try {
        await api(`/projects/${jobId}/render`, json("POST", body));
        toast(action === "images" ? "Đang sinh ảnh cho các cảnh đã đổi…" : "Đang render — chỉ tạo lại phần đã thay đổi");
        if (action === "reanalyze") scenePage = 1;
        await open(current.job.job_id, { keepPage: action !== "reanalyze" });
      } catch (error) { toast(error.message, true); }
      return;
    }
    const sceneIndex = event.target.closest("[data-scene]")?.dataset.scene;
    if (op === "delete") {
      if (!confirm(`Xóa cảnh ${sceneIndex}?`)) return;
      await mutate(api(withSceneQuery(`/projects/${jobId}/scenes/${sceneIndex}`), { method: "DELETE" }), "Đã xóa cảnh");
    } else if (op === "clear-image") {
      await mutate(api(withSceneQuery(`/projects/${jobId}/scenes/${sceneIndex}/image`), { method: "DELETE" }), "Đã bỏ ảnh tải lên");
    } else if (op === "insert") {
      const narration = prompt("Lời dẫn cho cảnh mới:");
      if (!narration?.trim()) return;
      const visual = prompt("Mô tả hình ảnh (tiếng Anh):", narration) || narration;
      await mutate(api(withSceneQuery(`/projects/${jobId}/scenes`), json("POST", { after: Number(sceneIndex), narration, visual_prompt: visual })), "Đã chèn cảnh");
    }
  });

  editor.addEventListener("change", async event => {
    const sizeSelect = event.target.closest("#comicScenesPageSizeSelect");
    if (sizeSelect && current) {
      const nextSize = parseInt(sizeSelect.value, 10);
      if (!isNaN(nextSize) && nextSize > 0) {
        scenePageSize = nextSize;
        scenePage = 1;
        try { localStorage.setItem("vidtrans_comic_scenes_page_size", String(nextSize)); } catch (_) {}
        await open(current.job.job_id, { keepPage: true, scrollScenes: true });
      }
      return;
    }
    const jobId = encodeURIComponent(current?.job?.job_id || "");
    const shotUpload = event.target.closest("[data-shot-upload]");
    const refUpload = event.target.closest("[data-ref-upload]");
    for (const upload of [shotUpload, refUpload]) {
      if (!upload?.files.length || !current) continue;
      const data = new FormData();
      data.append("file", upload.files[0]);
      if (upload === shotUpload) {
        const [sceneIndex, shotIndex] = upload.closest("[data-shot]").dataset.shot.split(".");
        await mutate(api(withSceneQuery(`/projects/${jobId}/scenes/${sceneIndex}/shots/${shotIndex}/keyframe`), { method: "POST", body: data }), "Đã thay keyframe");
      } else {
        const characterId = upload.closest("[data-character]").dataset.character;
        await mutate(api(withSceneQuery(`/projects/${jobId}/characters/${encodeURIComponent(characterId)}/references`), { method: "POST", body: data }),
          "Đã tải và duyệt ảnh tham chiếu");
      }
      return;
    }
    const input = event.target.closest("[data-scene-upload]");
    if (!input?.files.length || !current) return;
    const data = new FormData();
    data.append("file", input.files[0]);
    const sceneIndex = input.closest("[data-scene]").dataset.scene;
    await mutate(api(withSceneQuery(`/projects/${encodeURIComponent(current.job.job_id)}/scenes/${sceneIndex}/image`), { method: "POST", body: data }),
      `Đã thay ảnh cảnh ${sceneIndex}`);
  });

  editor.addEventListener("submit", async event => {
    event.preventDefault();
    if (!current) return;
    const target = event.target;
    const jobId = encodeURIComponent(current.job.job_id);
    const values = Object.fromEntries(new FormData(target));
    if (target.dataset.character !== undefined) {
      const { name, gender, appearance, outfit } = values;
      await mutate(api(withSceneQuery(`/projects/${jobId}/characters/${encodeURIComponent(target.dataset.character)}`), json("PATCH", { name, gender, appearance, outfit })), "Đã lưu nhân vật");
      return;
    }
    if (target.dataset.shot !== undefined) {
      const [sceneIndex, shotIndex] = target.dataset.shot.split(".");
      try {
        const patch = shotPatchFromForm(target, parseDialogue);
        await mutate(api(withSceneQuery(`/projects/${jobId}/scenes/${sceneIndex}/shots/${shotIndex}`), json("PATCH", patch)), `Đã lưu shot ${target.dataset.shot}`);
      } catch (error) {
        toast(error.message, true);
      }
      return;
    }
    const sceneIndex = target.closest("[data-scene]")?.dataset.scene;
    try {
      const patch = {
        narration: values.narration,
        dialogue: parseDialogue(values.dialogue || ""),
        characters: String(values.characters || "").split(",").map(v => v.trim()).filter(Boolean),
        visual_prompt: values.visual_prompt,
        negative_prompt: values.negative_prompt,
        camera_motion: values.camera_motion,
      };
      await mutate(api(withSceneQuery(`/projects/${jobId}/scenes/${sceneIndex}`), json("PATCH", patch)), `Đã lưu cảnh ${sceneIndex}`);
    } catch (error) {
      toast(error.message, true);
    }
  });

  return {
    async enter() {
      entered = true;
      try {
        const savedMode = localStorage.getItem("vidtrans_comic_view_mode");
        if (savedMode && ["grid", "wide", "list"].includes(savedMode)) {
          $("#comicScenes")?.setAttribute("data-view-mode", savedMode);
          $$("[data-comic-view-mode]").forEach(btn => btn.classList.toggle("is-active", btn.dataset.comicViewMode === savedMode));
        }
      } catch (_) {}
      try {
        await loadOptions();
      } catch (error) {
        toast(error.message, true);
      }
      if (location.hash === NEW_ROUTE) {
        showNew();
        return;
      }
      const linked = location.hash.match(/^#comic\/([A-Za-z0-9_-]{1,64})$/)?.[1];
      if (linked && linked !== current?.job?.job_id) {
        await open(linked, { reveal: true }).catch(error => {
          toast(error.message, true);
          showNew();
        });
      } else {
        // Plain #comic (the nav link) keeps the last opened project, or shows the form.
        render();
        await loadProjects();
        if (current) schedulePoll();
      }
    },
    leave() {
      entered = false;
      clearTimeout(pollTimer);
    },
    isVisible: () => !view.classList.contains("is-hidden"),
  };
}
