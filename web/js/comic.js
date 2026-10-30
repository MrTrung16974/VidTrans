// Story -> comic / animated video workspace: a story (series) holds many episode videos that share its
// settings and character bible; each episode is a project whose storyboard can be edited and re-rendered.
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
const LIVE_REFRESH_MS = 800;
const COMIC_JOB_ACTION = "comic_video";
// #comic/new shows the "new story" form, #comic/s/<series id> one story and its episodes, #comic/<job id>
// one episode's editor; all go through the hash so the browser's Back button moves between them.
const NEW_ROUTE = "#comic/new";
const projectRoute = jobId => `#comic/${encodeURIComponent(jobId)}`;
const seriesRoute = seriesId => `#comic/s/${encodeURIComponent(seriesId)}`;
const SERIES_ROUTE = /^#comic\/s\/([0-9a-f]{12})$/;
const narrowLayout = () => window.matchMedia("(max-width: 960px)").matches;

export function createComicWorkspace({ requestJson, toast, escapeHtml }) {
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const view = $("#comicView");
  const form = $("#comicForm");
  const editor = $("#comicEditor");
  const seriesView = $("#comicSeries");
  const episodeForm = $("#comicEpisodeForm");
  let options = null;
  let current = null;
  let currentSeries = null;
  let sidebarTab = "series";
  try {
    if (localStorage.getItem("vidtrans_comic_sidebar_tab") === "videos") sidebarTab = "videos";
  } catch (_) {}
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
      const locked = item.allowed === false;
      const disabled = locked || item.configured === false;
      const label = item.label + (locked ? " — cần gói Creator/Pro" : disabled ? " — chưa cấu hình" : "");
      return `<option value="${escapeHtml(item.key)}"${disabled ? " disabled" : ""}${item.key === selected ? " selected" : ""}>${escapeHtml(label)}</option>`;
    }).join("");
  }

  async function loadOptions() {
    if (options) return;
    options = await api("/options");
    fillSelect(form.elements.style, options.styles, "manhua");
    fillSelect(form.elements.language, options.languages, "vi");
    fillSelect(form.elements.llm_provider, options.llm_providers, "auto");
    const comfyui = options.image_providers.find(item => item.key === "comfyui");
    const cloudflare = options.image_providers.find(item => item.key === "cloudflare");
    const gemini = options.image_providers.find(item => item.key === "gemini");
    fillSelect(form.elements.image_provider, options.image_providers,
      comfyui?.configured ? "comfyui" : cloudflare?.configured ? "cloudflare"
        : gemini?.configured && gemini.allowed !== false ? "gemini" : "pollinations");
    fillSelect(form.elements.animation_provider, options.animation_providers, "camera_2_5d");
    const cit = options.tts_providers.find(item => item.key === "cit");
    fillSelect(form.elements.tts_provider, options.tts_providers, cit?.configured ? "cit" : "edge");
    syncTtsVoices();
    renderGenres();
    $("#comicStoryLimit").textContent = `Tối đa ${options.max_story_chars.toLocaleString("vi-VN")} ký tự mỗi dự án.`;
  }

  // Genre presets: one click fills the world line (in the AI request) and a matching drawing style.
  function renderGenres() {
    const box = $("#comicGenres");
    if (!box) return;
    box.innerHTML = (options.genres || []).map(genre => `<button type="button" class="comic-genre" data-genre="${escapeHtml(genre.key)}"
      role="radio" aria-checked="false" title="${escapeHtml(genre.world_vi)}">${escapeHtml(genre.label)}</button>`).join("");
  }

  $("#comicGenres")?.addEventListener("click", event => {
    const button = event.target.closest("[data-genre]");
    if (!button) return;
    const selected = form.elements.genre.value === button.dataset.genre ? "" : button.dataset.genre;
    form.elements.genre.value = selected;
    $$("#comicGenres [data-genre]").forEach(item => {
      const on = item.dataset.genre === selected;
      item.classList.toggle("is-selected", on);
      item.setAttribute("aria-checked", String(on));
    });
    const preset = (options.genres || []).find(genre => genre.key === selected);
    if (preset && [...form.elements.style.options].some(option => option.value === preset.style)) {
      form.elements.style.value = preset.style;
    }
  });

  // ---------------------------------------------------------------- story bible review
  // The AI proposes characters, places and the world from the story; the user reviews them in
  // Vietnamese. Changing a Vietnamese look clears its English prompt, which the server translates again.
  const asciiId = (text, fallback) => (String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/gi, "d").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || fallback);

  function bibleCharacterCard(character, index) {
    const genders = { female: "Nữ", male: "Nam", unknown: "Không rõ" };
    return `<div class="comic-bible-card" data-bible-card="characters" data-index="${index}">
      <div class="comic-bible-card-head">
        <input name="name" value="${escapeHtml(character.name || "")}" maxlength="120" placeholder="Tên nhân vật" aria-label="Tên nhân vật">
        ${character.status === "new" ? '<b class="comic-bible-badge">Mới</b>' : character.status === "known" ? '<b class="comic-bible-badge known">Đã có</b>' : ""}
        <button type="button" class="comic-bible-remove" data-bible-remove title="Bỏ nhân vật này" aria-label="Bỏ">✕</button>
      </div>
      <div class="comic-inline">
        <select name="gender" aria-label="Giới tính">${Object.entries(genders).map(([value, label]) => `<option value="${value}"${(character.gender || "unknown") === value ? " selected" : ""}>${label}</option>`).join("")}</select>
        <input name="age" value="${escapeHtml(character.age || "")}" maxlength="60" placeholder="Tuổi" aria-label="Tuổi">
      </div>
      <textarea name="appearance_vi" rows="3" maxlength="600" placeholder="Ngoại hình: khuôn mặt, tóc, mắt, dáng người, dấu hiệu dễ nhận ra">${escapeHtml(character.appearance_vi || "")}</textarea>
      <input name="outfit_vi" value="${escapeHtml(character.outfit_vi || "")}" maxlength="400" placeholder="Trang phục thường mặc">
      ${character.appearance ? `<details class="comic-bible-en"><summary>Prompt tiếng Anh (tự dịch)</summary><small>${escapeHtml([character.appearance, character.outfit].filter(Boolean).join(" · "))}</small></details>` : ""}
    </div>`;
  }

  function bibleLocationCard(place, index) {
    return `<div class="comic-bible-card" data-bible-card="locations" data-index="${index}">
      <div class="comic-bible-card-head">
        <input name="name" value="${escapeHtml(place.name || "")}" maxlength="120" placeholder="Tên bối cảnh" aria-label="Tên bối cảnh">
        ${place.status === "new" ? '<b class="comic-bible-badge">Mới</b>' : place.status === "known" ? '<b class="comic-bible-badge known">Đã có</b>' : ""}
        <button type="button" class="comic-bible-remove" data-bible-remove title="Bỏ bối cảnh này" aria-label="Bỏ">✕</button>
      </div>
      <textarea name="description_vi" rows="3" maxlength="600" placeholder="Kiến trúc/cảnh quan, vật liệu, màu sắc, đồ vật cố định, ánh sáng">${escapeHtml(place.description_vi || "")}</textarea>
      ${place.description ? `<details class="comic-bible-en"><summary>Prompt tiếng Anh (tự dịch)</summary><small>${escapeHtml(place.description)}</small></details>` : ""}
    </div>`;
  }

  // One editor per review box: the create-story review and the new-episode review.
  // ``withWorld`` is off for episodes: the world belongs to the whole series and is edited there.
  function createBibleEditor(box, { withWorld = true } = {}) {
    let draft = null;
    const render = () => {
      box.innerHTML = `${withWorld ? `
        <label class="field comic-bible-world"><span>Thế giới &amp; thời đại</span>
          <textarea data-bible-world rows="2" maxlength="600" placeholder="Ví dụ: Trung Quốc thời Đường, cung điện mái ngói đỏ, tông màu ấm">${escapeHtml(draft.world_vi || "")}</textarea></label>` : ""}
        <div class="comic-block-head"><h3>Nhân vật <small>(${draft.characters.length})</small></h3><button type="button" class="button secondary" data-bible-add="characters">+ Thêm nhân vật</button></div>
        <div class="comic-bible-grid">${draft.characters.map(bibleCharacterCard).join("") || '<p class="comic-empty">Chưa có nhân vật — bấm "+ Thêm nhân vật" hoặc để AI tự nhận ra khi chia cảnh.</p>'}</div>
        <div class="comic-block-head"><h3>Bối cảnh <small>(${draft.locations.length})</small></h3><button type="button" class="button secondary" data-bible-add="locations">+ Thêm bối cảnh</button></div>
        <div class="comic-bible-grid">${draft.locations.map(bibleLocationCard).join("") || '<p class="comic-empty">Chưa có bối cảnh.</p>'}</div>`;
    };
    // Keep what is typed when the list is re-rendered (add / remove).
    const sync = () => {
      if (!draft) return;
      const world = box.querySelector("[data-bible-world]");
      if (world && world.value !== (draft.world_vi || "")) { draft.world_vi = world.value; draft.world = ""; }
      box.querySelectorAll("[data-bible-card]").forEach(card => {
        const item = draft[card.dataset.bibleCard][Number(card.dataset.index)];
        card.querySelectorAll("input, select, textarea").forEach(field => {
          const english = { appearance_vi: "appearance", outfit_vi: "outfit", description_vi: "description" }[field.name];
          if (english && field.value !== (item[field.name] || "")) item[english] = "";
          item[field.name] = field.value;
        });
      });
    };
    box.addEventListener("click", event => {
      const add = event.target.closest("[data-bible-add]");
      const remove = event.target.closest("[data-bible-remove]");
      if (!add && !remove) return;
      sync();
      if (add) {
        draft[add.dataset.bibleAdd].push(add.dataset.bibleAdd === "characters"
          ? { name: "", gender: "unknown", age: "", appearance_vi: "", outfit_vi: "", status: "new" }
          : { name: "", description_vi: "", status: "new" });
      } else {
        const card = remove.closest("[data-bible-card]");
        draft[card.dataset.bibleCard].splice(Number(card.dataset.index), 1);
      }
      render();
      if (add) box.querySelector(`[data-bible-card="${add.dataset.bibleAdd}"]:last-of-type input[name="name"]`)?.focus();
    });
    return {
      show(bible) {
        draft = JSON.parse(JSON.stringify(bible));
        render();
      },
      // The approved bible as the API expects it; only "new" entries when ``onlyNew`` (episodes).
      collect({ onlyNew = false } = {}) {
        sync();
        const used = new Set();
        const uniqueId = (item, fallback) => {
          let id = item.id || asciiId(item.name, fallback);
          for (let n = 2; used.has(id); n += 1) id = `${asciiId(item.name, fallback)}_${n}`;
          used.add(id);
          return id;
        };
        const keep = item => String(item.name || "").trim() && (!onlyNew || item.status !== "known");
        const strip = ({ status, ...rest }) => rest;
        return {
          world: withWorld ? draft.world || "" : "", world_vi: withWorld ? (draft.world_vi || "").trim() : "",
          characters: draft.characters.filter(keep).map(item => strip({
            ...item, id: uniqueId(item, "character"), name: item.name.trim(),
            species: item.species || "human", appearance: item.appearance || "", outfit: item.outfit || "",
          })),
          locations: draft.locations.filter(keep).map(item => strip({
            ...item, id: uniqueId(item, "place"), name: item.name.trim(), description: item.description || "",
          })),
        };
      },
      counts() {
        return { characters: draft.characters.filter(item => item.status !== "known").length,
                 locations: draft.locations.filter(item => item.status !== "known").length };
      },
    };
  }

  const bibleEditor = createBibleEditor($("#comicBibleBody"));
  const episodeBibleEditor = createBibleEditor($("#comicEpisodeBibleBody"), { withWorld: false });

  async function suggestBible(fields) {
    const data = new FormData();
    for (const [key, value] of Object.entries(fields)) if (value !== undefined && value !== null) data.append(key, value);
    return api("/bible/suggest", { method: "POST", body: data });
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
        const series = comic.series_id ? `${comic.series_title || "Truyện"} · Tập ${comic.episode_number}` : "Video lẻ";
        const meta = [comic.video_mode === "animated" ? "Hoạt hình" : "Truyện tranh", comic.scenes ? `${comic.scenes} cảnh` : "",
          comic.shots ? `${comic.shots} shot` : "", comic.aspect_ratio || ""].filter(Boolean).join(" · ");
        const active = current?.job?.job_id === job.job_id;
        const title = comic.title || job.filename || job.job_id;
        return `<div class="comic-project-row"><button type="button" class="comic-project${active ? " is-active" : ""}"${active ? ' aria-current="true"' : ""} data-job="${escapeHtml(job.job_id)}">
          <strong>${escapeHtml(title)}</strong>
          <span class="status-pill status-${statusClass(job)}">${escapeHtml(STEP_LABELS[job.step] || job.status)}</span>
          <small>${escapeHtml(series)}<br>${escapeHtml(meta)}</small></button>
          <button type="button" class="comic-project-delete" data-delete-job="${escapeHtml(job.job_id)}" data-title="${escapeHtml(title)}"${isActive(job) ? " disabled" : ""} aria-label="Xóa ${escapeHtml(title)}" title="Xóa video và toàn bộ file">✕</button></div>`;
      }).join("");
      renderProjectPagination();
    } catch (error) {
      list.innerHTML = `<p class="comic-empty">${escapeHtml(error.message)}</p>`;
      $("#comicProjectPagination")?.classList.add("is-hidden");
    }
  }

  const styleLabel = key => options?.styles?.find(style => style.key === key)?.label || key || "";
  const episodeTitle = comic => {
    const name = comic?.title || "";
    if (!comic?.series_id) return name;
    const number = `Tập ${comic.episode_number}`;
    return name && name !== number ? `${number} · ${name}` : number;
  };
  const seriesActive = () => (currentSeries?.episode_list || []).some(isActive);

  function refreshLists() {
    loadSeriesList();
    loadProjects();
  }

  // Deleting removes the files on the server too, so both actions confirm first.
  async function deleteProject(jobId, title) {
    if (!confirm(`Xóa vĩnh viễn "${title}"?\nVideo, ảnh, âm thanh và mọi file của dự án sẽ bị xóa khỏi máy chủ, không khôi phục được.`)) return;
    try {
      await api(`/projects/${encodeURIComponent(jobId)}`, { method: "DELETE" });
      toast("Đã xóa video và toàn bộ file");
      if (current?.job?.job_id === jobId) location.hash = NEW_ROUTE;
      refreshLists();
    } catch (error) { toast(error.message, true); }
  }

  async function deleteSeries(seriesId, title, episodes) {
    if (!confirm(`Xóa truyện "${title}"?`)) return;
    const withEpisodes = episodes > 0 && confirm(
      `Xóa luôn ${episodes} video tập của truyện và toàn bộ file của chúng?\n` +
      "OK = xóa cả các tập (không khôi phục được) · Hủy = giữ các tập trong mục Tất cả video.");
    try {
      await api(`/series/${encodeURIComponent(seriesId)}${withEpisodes ? "?with_episodes=true" : ""}`, { method: "DELETE" });
      toast(withEpisodes ? "Đã xóa truyện, các tập và file" : "Đã xóa truyện");
      if (currentSeries?.series_id === seriesId || (withEpisodes && current?.job?.comic?.series_id === seriesId)) {
        location.hash = NEW_ROUTE;
      }
      refreshLists();
    } catch (error) { toast(error.message, true); }
  }

  function showSidebarTab(tab) {
    sidebarTab = tab;
    $$("[data-comic-tab]").forEach(button => {
      const selected = button.dataset.comicTab === tab;
      button.classList.toggle("is-active", selected);
      button.setAttribute("aria-selected", String(selected));
    });
    $$("[data-comic-pane]").forEach(pane => pane.classList.toggle("is-hidden", pane.dataset.comicPane !== tab));
    try { localStorage.setItem("vidtrans_comic_sidebar_tab", tab); } catch (_) {}
  }

  async function loadSeriesList() {
    const list = $("#comicSeriesList");
    try {
      const items = (await api("/series")).items || [];
      if (!items.length) {
        list.innerHTML = '<p class="comic-empty">Chưa có truyện nào. Bấm "Truyện mới" để bắt đầu.</p>';
        return;
      }
      const activeId = currentSeries?.series_id || current?.job?.comic?.series_id;
      list.innerHTML = items.map(series => {
        const active = series.series_id === activeId;
        const meta = [`${series.episodes} tập`, `${series.characters} nhân vật`, styleLabel(series.style),
          series.video_mode === "animated" ? "Hoạt hình" : "Truyện tranh"].filter(Boolean).join(" · ");
        return `<div class="comic-project-row"><button type="button" class="comic-project${active ? " is-active" : ""}"${active ? ' aria-current="true"' : ""} data-series="${escapeHtml(series.series_id)}">
          <strong>${escapeHtml(series.title)}</strong>
          <small>${escapeHtml(meta)}</small></button>
          <button type="button" class="comic-project-delete" data-delete-series="${escapeHtml(series.series_id)}" data-title="${escapeHtml(series.title)}" data-episodes="${Number(series.episodes) || 0}" aria-label="Xóa ${escapeHtml(series.title)}" title="Xóa truyện">✕</button></div>`;
      }).join("");
    } catch (error) {
      list.innerHTML = `<p class="comic-empty">${escapeHtml(error.message)}</p>`;
    }
  }

  async function openSeries(seriesId, { reveal = false } = {}) {
    currentSeries = await api(`/series/${encodeURIComponent(seriesId)}`);
    current = null;
    if (location.hash !== seriesRoute(seriesId)) history.replaceState(null, "", seriesRoute(seriesId));
    render();
    refreshLists();
    if (reveal && narrowLayout()) seriesView.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function open(jobId, { reveal = false, keepPage = false, scrollScenes = false } = {}) {
    if (!keepPage && current?.job?.job_id !== jobId) scenePage = 1;
    current = await api(withSceneQuery(`/projects/${encodeURIComponent(jobId)}`));
    currentSeries = null;
    if (current?.scenes_pagination?.page) scenePage = current.scenes_pagination.page;
    // A shareable link straight to this project.
    if (location.hash !== projectRoute(jobId)) history.replaceState(null, "", projectRoute(jobId));
    render();
    refreshLists();
    // On one-column layouts the project opens below the list; bring it into view.
    if (reveal && narrowLayout()) editor.scrollIntoView({ behavior: "smooth", block: "start" });
    if (scrollScenes) $("#comicScenes")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function showNew() {
    current = null;
    currentSeries = null;
    render();
    refreshLists();
    if (narrowLayout()) form.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // Live progress arrives over the shared job WebSocket (app.js forwards each message
  // here); the open project or story is reloaded only when one of its own jobs changed.
  let liveTimer = null;
  let liveLists = false;

  function watchedJobs() {
    if (current) return [current.job];
    return currentSeries?.episode_list || [];
  }

  function onJobChanges(message) {
    if (!entered) return;
    if (message.resync) {
      scheduleLiveRefresh({ project: true, lists: true });
      return;
    }
    const watched = new Map(watchedJobs().map(job => [job.job_id, job]));
    let project = false;
    let lists = false;
    for (const item of message.items || []) {
      const known = watched.get(item.job_id);
      if (known && (isActive(item) || item.status !== known.status)) project = true;
      // A comic job finished or appeared: the sidebar counts and statuses may have changed.
      if (item.job_action === COMIC_JOB_ACTION && (!isActive(item) || !known)) lists = true;
      // A new episode of the open story starts after the story was loaded.
      if (!known && currentSeries && item.comic?.series_id === currentSeries.series_id) project = true;
    }
    if (project || lists) scheduleLiveRefresh({ project, lists });
  }

  function scheduleLiveRefresh({ project = true, lists = false } = {}) {
    liveLists ||= lists;
    if (!project && !liveLists) return;
    if (liveTimer) return;
    // Coalesce bursts of progress messages into one reload.
    liveTimer = setTimeout(() => refreshLive(project), LIVE_REFRESH_MS);
  }

  async function refreshLive(project = true) {
    liveTimer = null;
    let lists = liveLists;
    liveLists = false;
    if (!entered) return;
    try {
      if (project && current) {
        const jobId = current.job.job_id;
        const wasActive = isActive(current.job);
        const next = await api(withSceneQuery(`/projects/${encodeURIComponent(jobId)}`));
        if (current?.job?.job_id !== jobId) return;  // The user opened something else meanwhile.
        current = next;
        if (current?.scenes_pagination?.page) scenePage = current.scenes_pagination.page;
        render();
        if (wasActive && !isActive(current.job)) {
          lists = true;
          toast(current.job.status === "failed" ? "Dự án truyện gặp lỗi" : "Dự án truyện đã xử lý xong", current.job.status === "failed");
        }
      } else if (project && currentSeries) {
        const seriesId = currentSeries.series_id;
        const wasActive = seriesActive();
        const next = await api(`/series/${encodeURIComponent(seriesId)}`);
        if (current || currentSeries?.series_id !== seriesId) return;
        currentSeries = next;
        render();
        if (wasActive && !seriesActive()) {
          lists = true;
          toast("Các tập truyện đã xử lý xong");
        }
      }
    } catch (_) { /* The next change or the fallback poll retries. */ }
    if (lists) refreshLists();
  }

  function render() {
    // The main column shows the new-story form, one story, or one episode's editor — never two.
    form.classList.toggle("is-hidden", Boolean(current || currentSeries));
    seriesView.classList.toggle("is-hidden", Boolean(current) || !currentSeries);
    $("#comicNewButton")?.classList.toggle("is-current", !current && !currentSeries);
    if (!current) {
      editor.classList.add("is-hidden");
      if (currentSeries) renderSeries();
      return;
    }
    editor.classList.remove("is-hidden");
    const { job, storyboard, media } = current;
    const comic = job.comic || {};
    const crumb = $("#comicBreadcrumb");
    crumb.classList.toggle("is-hidden", !comic.series_id);
    if (comic.series_id) {
      crumb.href = seriesRoute(comic.series_id);
      crumb.textContent = `← ${comic.series_title || "Truyện"} · tất cả các tập`;
    }
    const locked = isActive(job);
    const progress = Math.round((job.progress || 0) * 100);
    const stale = storyboard && current.rendered_revision !== storyboard.revision;
    $("#comicEditorTitle").textContent = episodeTitle(comic) || job.filename || job.job_id;
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
    renderLocations(storyboard, media, locked);
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

  function renderSeries() {
    const series = currentSeries;
    $("#comicSeriesTitle").textContent = series.title;
    $("#comicSeriesCount").textContent = `${series.episodes} tập`;
    $("#comicSeriesDescription").textContent = series.description || "";
    const settings = series.settings || {};
    $("#comicSeriesMeta").textContent = [styleLabel(settings.style),
      settings.video_mode === "animated" ? "Hoạt hình AI" : "Truyện tranh", settings.aspect_ratio,
      `${series.characters.length} nhân vật`].filter(Boolean).join(" · ");
    $("#comicEpisodeHeading").textContent = `Thêm tập ${series.next_episode_number}`;
    episodeForm.querySelector('[type="submit"]').textContent = `Tạo tập ${series.next_episode_number}`;
    const episodes = [...(series.episode_list || [])].sort((a, b) => a.episode_number - b.episode_number);
    $("#comicEpisodes").innerHTML = episodes.length ? episodes.map(job => {
      const comic = job.comic || {};
      const active = isActive(job);
      const meta = [comic.scenes ? `${comic.scenes} cảnh` : "", comic.shots ? `${comic.shots} shot` : "",
        comic.duration ? `${Math.round(comic.duration)} giây` : ""].filter(Boolean).join(" · ");
      const name = comic.title && comic.title !== `Tập ${job.episode_number}` ? comic.title : "";
      return `<article class="comic-episode card" data-job="${escapeHtml(job.job_id)}">
        <span class="comic-episode-number">Tập ${job.episode_number}</span>
        <div class="comic-episode-body">
          <strong>${escapeHtml(name || comic.youtube_title || `Tập ${job.episode_number}`)}</strong>
          <span class="status-pill status-${statusClass(job)}">${escapeHtml(STEP_LABELS[job.step] || job.status)}</span>
          <small>${escapeHtml(job.error || meta || job.step_detail || "")}</small>
          ${active ? `<div class="progress-track"><span style="width:${Math.round((job.progress || 0) * 100)}%"></span></div>` : ""}
        </div>
        <div class="comic-episode-actions">
          <button class="button secondary" type="button" data-episode-open>Mở biên tập</button>
          ${job.video_url ? `<a class="button secondary" href="${escapeHtml(job.video_url)}" download>Tải MP4</a>` : ""}
        </div></article>`;
    }).join("") : '<p class="comic-empty">Chưa có tập nào. Dán nội dung tập 1 vào form phía trên.</p>';
    const locked = seriesActive();
    $("#comicSeriesCharacters").innerHTML = series.characters.length ? series.characters.map(c => `
      <form class="comic-character" data-series-character="${escapeHtml(c.id)}">
        <div class="comic-character-ref">${c.reference_url ? `<img src="${escapeHtml(c.reference_url)}" alt="${escapeHtml(c.name)}" loading="lazy">` : `<span>${escapeHtml(c.name.slice(0, 1))}</span>`}</div>
        <div class="comic-character-fields">
          <div class="comic-inline">
            <label class="field"><span>Tên</span><input name="name" value="${escapeHtml(c.name)}" maxlength="120"></label>
            <label class="field"><span>Giới tính</span><select name="gender">${["female", "male", "unknown"].map(g => `<option value="${g}"${c.gender === g ? " selected" : ""}>${{ female: "Nữ", male: "Nam", unknown: "Không rõ" }[g]}</option>`).join("")}</select></label>
          </div>
          <div class="comic-inline">
            <label class="field"><span>Loài (tiếng Anh)</span><input name="species" value="${escapeHtml(c.species || "human")}" maxlength="80" placeholder="human, black cat, dragon…"></label>
            <label class="field"><span>Tuổi</span><input name="age" value="${escapeHtml(c.age || "")}" maxlength="60" placeholder="8, 25, elderly…"></label>
          </div>
          ${characterLookFields(c)}
          <small class="field-hint">id: ${escapeHtml(c.id)} · ${c.reference_url ? "ảnh tham chiếu đang ghim cho các tập" : "chưa có ảnh tham chiếu — sẽ lấy ảnh của tập đầu tiên vẽ nhân vật này"}</small>
          <div class="comic-scene-actions">
            <button class="button secondary" type="submit">Lưu nhân vật</button>
            <label class="button secondary comic-upload">Tải ảnh tham chiếu<input type="file" accept="image/png,image/jpeg,image/webp" data-series-ref-upload hidden></label>
            ${c.reference_url ? '<button class="button secondary" type="button" data-series-op="clear-ref">Bỏ ghim ảnh</button>' : ""}
          </div>
        </div></form>`).join("") : '<p class="comic-empty">Nhân vật sẽ xuất hiện ở đây sau khi tập đầu tiên được phân tích.</p>';
    $("#comicEpisodes").classList.toggle("is-busy", locked);
    const briefForm = $("#comicSeriesBriefForm");
    for (const [name, value] of [["story_brief", settings.story_brief], ["world_vi", series.world_vi], ["world", series.world]]) {
      briefForm.elements[name].value = briefForm.elements[name].defaultValue = value || "";
    }
    const places = series.locations || [];
    $("#comicSeriesLocations").innerHTML = places.length
      ? places.map(place => locationForm(place, "data-series-location", "",
        place.reference_url ? { url: place.reference_url, readonly: true } : null)).join("")
      : '<p class="comic-empty">Bối cảnh được lập từ kế hoạch hình ảnh khi tập đầu tiên được phân tích.</p>';
  }

  async function seriesMutate(promise, message) {
    try {
      currentSeries = await promise;
      render();
      loadSeriesList();
      if (message) toast(message);
    } catch (error) {
      toast(error.message, true);
    }
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
          <div class="comic-inline">
            <label class="field"><span>Loài (tiếng Anh)</span><input name="species" value="${escapeHtml(c.species || "human")}" maxlength="80" placeholder="human, black cat, dragon…"></label>
            <label class="field"><span>Tuổi</span><input name="age" value="${escapeHtml(c.age || "")}" maxlength="60" placeholder="8, 25, elderly…"></label>
          </div>
          ${characterLookFields(c)}
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

  // Looks are edited in Vietnamese; the English prompt (translated by the server) is advanced detail.
  function characterLookFields(c, disabled = "") {
    return `<label class="field"><span>Ngoại hình cố định</span><textarea name="appearance_vi" rows="2" maxlength="600"${disabled}
        placeholder="${c.appearance_vi ? "" : "Mô tả bằng tiếng Việt: khuôn mặt, tóc, mắt, dáng người…"}">${escapeHtml(c.appearance_vi || "")}</textarea></label>
      <label class="field"><span>Trang phục</span><input name="outfit_vi" value="${escapeHtml(c.outfit_vi || "")}" maxlength="400"${disabled}></label>
      <details class="comic-bible-en"><summary>Prompt tiếng Anh cho AI vẽ${c.appearance_vi && !c.appearance ? " (sẽ tự dịch khi render)" : ""}</summary>
        <label class="field"><span>Appearance</span><textarea name="appearance" rows="2" maxlength="600"${disabled}>${escapeHtml(c.appearance || "")}</textarea></label>
        <label class="field"><span>Outfit</span><input name="outfit" value="${escapeHtml(c.outfit || "")}" maxlength="400"${disabled}></label>
        <small class="field-hint">Sửa phần tiếng Việt thì prompt tiếng Anh được dịch lại tự động.</small>
      </details>`;
  }

  // Only what the user changed: an untouched English prompt is not sent, so a Vietnamese edit retranslates it.
  function lookChanges(target, names) {
    const changes = {};
    for (const name of names) {
      const field = target.elements[name];
      if (field && field.value !== field.defaultValue) changes[name] = field.value;
    }
    return changes;
  }
  const CHARACTER_FIELDS = ["name", "gender", "species", "age", "appearance_vi", "outfit_vi", "appearance", "outfit"];
  const LOCATION_FIELDS = ["name", "description_vi", "description"];

  // A planned place, editable: its English description is what every scene set there is drawn from.
  // ``picture`` (editor only): the place's image and its pin / upload actions.
  function locationForm(place, attribute, disabled, picture = null) {
    const pictureBlock = picture ? `<div class="comic-location-picture">
        ${picture.url ? `<img src="${escapeHtml(picture.url)}" alt="${escapeHtml(place.name)}" loading="lazy">`
          : '<span>Chưa có ảnh — bấm "Vẽ thử 3 cảnh" hoặc tải ảnh lên</span>'}
        ${place.approved_reference ? '<b class="comic-badge">Đã ghim</b>' : ""}
      </div>
      ${picture.readonly ? "" : `<div class="comic-scene-actions">
        ${picture.drawn && !place.approved_reference ? `<button class="button secondary" type="button" data-place-op="approve" data-place-name="${escapeHtml(picture.drawn)}"${disabled}>Ghim ảnh này</button>` : ""}
        ${place.approved_reference ? `<button class="button secondary" type="button" data-place-op="unpin"${disabled}>Bỏ ghim</button>` : ""}
        <label class="button secondary comic-upload${disabled ? " is-disabled" : ""}">Tải ảnh lên<input type="file" accept="image/png,image/jpeg,image/webp" data-place-upload${disabled} hidden></label>
      </div>`}` : "";
    return `<form class="comic-location" ${attribute}="${escapeHtml(place.id)}">${pictureBlock}
      <div class="comic-inline">
        <label class="field"><span>Tên bối cảnh</span><input name="name" value="${escapeHtml(place.name)}" maxlength="120"${disabled}></label>
        <small class="field-hint">id: ${escapeHtml(place.id)}</small>
      </div>
      <label class="field"><span>Mô tả hình ảnh cố định</span><textarea name="description_vi" rows="2" maxlength="600"${disabled}
        placeholder="${place.description_vi ? "" : "Mô tả bằng tiếng Việt: kiến trúc, màu sắc, đồ vật, ánh sáng…"}">${escapeHtml(place.description_vi || "")}</textarea></label>
      <details class="comic-bible-en"><summary>Prompt tiếng Anh cho AI vẽ${place.description_vi && !place.description ? " (sẽ tự dịch khi render)" : ""}</summary>
        <textarea name="description" rows="2" maxlength="600"${disabled}>${escapeHtml(place.description || "")}</textarea>
      </details>
      <div class="comic-scene-actions"><button class="button secondary" type="submit"${disabled}>Lưu bối cảnh</button></div>
    </form>`;
  }

  function renderLocations(storyboard, media, locked) {
    const places = storyboard?.locations || [];
    $("#comicLocationsBlock").classList.toggle("is-hidden", !places.length && !storyboard?.world);
    $("#comicPlaceHint").textContent = !places.length ? "" : media.place_references_supported
      ? "Ảnh bối cảnh được gửi kèm khi vẽ mọi cảnh ở nơi đó để giữ cảnh vật nhất quán. Ghim ảnh ưng ý để giữ cố định."
      : "AI vẽ đang chọn chỉ đọc mô tả chữ (ảnh bối cảnh chỉ dùng được với Gemini hoặc ComfyUI).";
    $("#comicWorld").textContent = storyboard?.world_vi || storyboard?.world ? `Thế giới: ${storyboard.world_vi || storyboard.world}` : "";
    const disabled = locked ? " disabled" : "";
    $("#comicLocations").innerHTML = places.map(place => locationForm(place, "data-location", disabled, {
      url: media.location_references?.[place.id], drawn: media.location_drawn?.[place.id],
    })).join("");
  }

  function locationSelect(storyboard, scene, disabled) {
    const places = storyboard.locations || [];
    if (!places.length) return "";
    return `<label class="field"><span>Bối cảnh</span><select name="location_id"${disabled}>
      <option value="">— Không gắn (dùng mô tả của cảnh)</option>
      ${places.map(place => `<option value="${escapeHtml(place.id)}"${scene.location_id === place.id ? " selected" : ""}>${escapeHtml(place.name)}</option>`).join("")}
    </select></label>`;
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
          <header><strong>Cảnh ${index}</strong><small>v${scene.version}${scene.emotion ? ` · ${escapeHtml(scene.emotion)}` : ""}${(storyboard.locations || []).find(place => place.id === scene.location_id)?.name || scene.location ? ` · ${escapeHtml((storyboard.locations || []).find(place => place.id === scene.location_id)?.name || scene.location)}` : ""}</small>
            <select name="camera_motion" aria-label="Chuyển động camera"${disabled}>${options.camera_motions.map(m => `<option value="${m}"${scene.camera_motion === m ? " selected" : ""}>${MOTION_LABELS[m] || m}</option>`).join("")}</select></header>
          <label class="field"><span>Lời dẫn chuyện</span><textarea name="narration" rows="3" maxlength="1500"${disabled}>${escapeHtml(scene.narration)}</textarea></label>
          <label class="field"><span>Lời thoại — mỗi dòng <code>id_nhân_vật: lời</code></span><textarea name="dialogue" rows="2"${disabled}>${escapeHtml(dialogueText(scene.dialogue))}</textarea></label>
          <label class="field"><span>Nhân vật xuất hiện (id, cách nhau dấu phẩy)</span><input name="characters" value="${escapeHtml(scene.characters.join(", "))}"${disabled}></label>
          ${locationSelect(storyboard, scene, disabled)}
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
      refreshLists();
      if (message) toast(message);
    } catch (error) {
      toast(error.message, true);
    }
  }

  function storyFormData() {
    const data = new FormData(form);
    for (const flag of ["burn_subtitles", "auto_render"]) data.set(flag, form.elements[flag].checked ? "true" : "false");
    for (const name of ["file", "background_music"]) {
      if (!form.elements[name].files.length) data.delete(name);
    }
    return data;
  }

  function showStoryStep(step) {
    form.classList.toggle("is-hidden", step !== 1);
    $("#comicBibleReview").classList.toggle("is-hidden", step !== 2);
    (step === 1 ? form : $("#comicBibleReview")).scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function createStory(bible, trigger) {
    const data = storyFormData();
    if (bible) data.set("bible", JSON.stringify(bible));
    trigger.disabled = true;
    try {
      const created = await api("/series", { method: "POST", body: data });
      toast(created.job_id ? "Đã tạo truyện và tập 1, hệ thống đang chia cảnh…" : "Đã tạo truyện — hãy thêm tập 1");
      form.elements.text.value = "";
      form.elements.file.value = "";
      form.elements.episode_title.value = "";
      showStoryStep(1);
      // Push the story's route so Back returns to the form.
      history.pushState(null, "", seriesRoute(created.series_id));
      await openSeries(created.series_id);
      seriesView.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      toast(error.message, true);
    } finally {
      trigger.disabled = false;
    }
  }

  // Step 1 -> AI reads the story and proposes the bible -> step 2 review.
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const submit = form.querySelector('[data-comic-step="review"]');
    const hasStory = form.elements.text.value.trim() || form.elements.file.files.length;
    if (!hasStory && !form.elements.story_brief.value.trim()) {
      toast("Hãy dán nội dung tập 1 (hoặc chọn file) để AI đề xuất nhân vật và bối cảnh", true);
      return;
    }
    const label = submit.textContent;
    submit.disabled = true;
    submit.textContent = "AI đang đọc truyện…";
    try {
      const bible = await suggestBible({
        text: form.elements.text.value, file: form.elements.file.files[0], story_brief: form.elements.story_brief.value,
        genre: form.elements.genre.value, language: form.elements.language.value,
        llm_provider: form.elements.llm_provider.value,
      });
      // Everything is new in a new story: the "Mới / Đã có" badges only matter for episodes.
      [...bible.characters, ...bible.locations].forEach(item => delete item.status);
      bibleEditor.show(bible);
      $("#comicBibleIntro").textContent = `AI đề xuất ${bible.characters.length} nhân vật và ${bible.locations.length} bối cảnh. `
        + "Sửa bằng tiếng Việt nếu cần — mọi cảnh sẽ được vẽ theo đúng hồ sơ này.";
      showStoryStep(2);
    } catch (error) {
      if (confirm(`${error.message}\n\nTạo truyện luôn và để AI tự nhận ra nhân vật khi chia cảnh?`)) {
        await createStory(null, submit);
      }
    } finally {
      submit.disabled = false;
      submit.textContent = label;
    }
  });

  form.querySelector('[data-comic-step="create"]').addEventListener("click", event => createStory(null, event.currentTarget));

  $("#comicBibleReview").addEventListener("click", event => {
    const op = event.target.closest("[data-bible-op]")?.dataset.bibleOp;
    if (op === "back") showStoryStep(1);
    else if (op === "confirm") createStory(bibleEditor.collect(), event.target.closest("button"));
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

  function episodeFormData() {
    const data = new FormData(episodeForm);
    data.set("auto_render", episodeForm.elements.auto_render.checked ? "true" : "false");
    data.set("series_id", currentSeries.series_id);
    for (const name of ["file", "background_music"]) {
      if (!episodeForm.elements[name].files.length) data.delete(name);
    }
    return data;
  }

  async function createEpisode(bible, trigger) {
    const data = episodeFormData();
    if (bible && (bible.characters.length || bible.locations.length)) data.set("bible", JSON.stringify(bible));
    trigger.disabled = true;
    try {
      const created = await api("/projects", { method: "POST", body: data });
      toast(`Đã tạo tập ${created.episode_number}, hệ thống đang chia cảnh…`);
      episodeForm.reset();
      $("#comicEpisodeCount").textContent = "0 ký tự";
      $("#comicEpisodeReview").classList.add("is-hidden");
      episodeForm.classList.remove("is-hidden");
      await openSeries(currentSeries.series_id);
    } catch (error) {
      toast(error.message, true);
    } finally {
      trigger.disabled = false;
    }
  }

  // A new chapter is compared with the series' bible: only new characters and places need review.
  episodeForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!currentSeries) return;
    const data = episodeFormData();
    if (!data.get("file") && !String(data.get("text") || "").trim()) {
      toast("Hãy dán nội dung tập hoặc chọn file TXT/DOCX", true);
      return;
    }
    const submit = episodeForm.querySelector('[type="submit"]');
    const label = submit.textContent;
    submit.disabled = true;
    submit.textContent = "AI đang tìm nhân vật & bối cảnh mới…";
    let bible = null;
    try {
      bible = await suggestBible({
        text: episodeForm.elements.text.value, file: episodeForm.elements.file.files[0],
        story_brief: episodeForm.elements.episode_brief.value, series_id: currentSeries.series_id,
        language: currentSeries.settings?.language || "vi", llm_provider: currentSeries.settings?.llm_provider || "auto",
      });
    } catch (error) {
      // No AI available: the scene split still adds new characters on its own.
      toast(`Bỏ qua bước duyệt: ${error.message}`);
    } finally {
      submit.disabled = false;
      submit.textContent = label;
    }
    const fresh = bible && [...bible.characters, ...bible.locations].some(item => item.status === "new");
    if (!fresh) {
      await createEpisode(null, submit);
      return;
    }
    bible.characters = bible.characters.filter(item => item.status === "new");
    bible.locations = bible.locations.filter(item => item.status === "new");
    episodeBibleEditor.show(bible);
    const { characters, locations } = episodeBibleEditor.counts();
    $("#comicEpisodeBibleIntro").textContent = `Phát hiện ${characters} nhân vật mới và ${locations} bối cảnh mới trong tập này. `
      + "Nhân vật và bối cảnh đã có của truyện được giữ nguyên.";
    episodeForm.classList.add("is-hidden");
    $("#comicEpisodeReview").classList.remove("is-hidden");
    $("#comicEpisodeReview").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  $("#comicEpisodeReview").addEventListener("click", event => {
    const op = event.target.closest("[data-episode-bible-op]")?.dataset.episodeBibleOp;
    const button = event.target.closest("button");
    if (op === "back") {
      $("#comicEpisodeReview").classList.add("is-hidden");
      episodeForm.classList.remove("is-hidden");
    } else if (op === "skip") {
      createEpisode(null, button);
    } else if (op === "confirm") {
      createEpisode(episodeBibleEditor.collect({ onlyNew: true }), button);
    }
  });

  episodeForm.elements.text.addEventListener("input", () => {
    $("#comicEpisodeCount").textContent = `${episodeForm.elements.text.value.length.toLocaleString("vi-VN")} ký tự`;
  });

  seriesView.addEventListener("click", async event => {
    if (!currentSeries) return;
    const seriesPath = `/series/${encodeURIComponent(currentSeries.series_id)}`;
    const episode = event.target.closest("[data-episode-open]")?.closest("[data-job]");
    if (episode) {
      location.hash = projectRoute(episode.dataset.job);
      return;
    }
    const op = event.target.closest("[data-series-op]")?.dataset.seriesOp;
    if (op === "rename") {
      const title = prompt("Tên truyện:", currentSeries.title);
      if (!title?.trim()) return;
      const description = prompt("Giới thiệu truyện:", currentSeries.description || "") ?? currentSeries.description;
      await seriesMutate(api(seriesPath, json("PATCH", { title, description })), "Đã cập nhật truyện");
    } else if (op === "delete") {
      await deleteSeries(currentSeries.series_id, currentSeries.title, (currentSeries.episode_list || []).length);
    } else if (op === "clear-ref") {
      const characterId = event.target.closest("[data-series-character]").dataset.seriesCharacter;
      await seriesMutate(api(`${seriesPath}/characters/${encodeURIComponent(characterId)}/reference`, { method: "DELETE" }),
        "Đã bỏ ghim ảnh tham chiếu");
    }
  });

  seriesView.addEventListener("change", async event => {
    const upload = event.target.closest("[data-series-ref-upload]");
    if (!upload?.files.length || !currentSeries) return;
    const data = new FormData();
    data.append("file", upload.files[0]);
    const characterId = upload.closest("[data-series-character]").dataset.seriesCharacter;
    await seriesMutate(api(`/series/${encodeURIComponent(currentSeries.series_id)}/characters/${encodeURIComponent(characterId)}/reference`,
      { method: "POST", body: data }), "Đã ghim ảnh tham chiếu cho các tập");
  });

  seriesView.addEventListener("submit", async event => {
    if (!currentSeries) return;
    const seriesPath = `/series/${encodeURIComponent(currentSeries.series_id)}`;
    if (event.target.id === "comicSeriesBriefForm") {
      event.preventDefault();
      await seriesMutate(api(seriesPath, json("PATCH", lookChanges(event.target, ["story_brief", "world_vi", "world"]))),
        "Đã lưu cho các tập sau");
      return;
    }
    const place = event.target.closest("[data-series-location]");
    if (place) {
      event.preventDefault();
      await seriesMutate(api(`${seriesPath}/locations/${encodeURIComponent(place.dataset.seriesLocation)}`,
        json("PATCH", lookChanges(place, LOCATION_FIELDS))), "Đã lưu bối cảnh chung");
      return;
    }
    const target = event.target.closest("[data-series-character]");
    if (!target) return;
    event.preventDefault();
    await seriesMutate(api(`/series/${encodeURIComponent(currentSeries.series_id)}/characters/${encodeURIComponent(target.dataset.seriesCharacter)}`,
      json("PATCH", lookChanges(target, CHARACTER_FIELDS))), "Đã lưu nhân vật của truyện");
  });

  $("#comicSeriesList").addEventListener("click", event => {
    const remove = event.target.closest("[data-delete-series]");
    if (remove) {
      deleteSeries(remove.dataset.deleteSeries, remove.dataset.title, Number(remove.dataset.episodes) || 0);
      return;
    }
    const button = event.target.closest("[data-series]");
    if (!button) return;
    if (location.hash === seriesRoute(button.dataset.series)) {
      openSeries(button.dataset.series, { reveal: true }).catch(error => toast(error.message, true));
    } else {
      location.hash = seriesRoute(button.dataset.series);  // hashchange -> enter() opens it
    }
  });

  $$("[data-comic-tab]").forEach(button => button.addEventListener("click", () => showSidebarTab(button.dataset.comicTab)));

  $("#comicProjectList").addEventListener("click", event => {
    const remove = event.target.closest("[data-delete-job]");
    if (remove) {
      if (!remove.disabled) deleteProject(remove.dataset.deleteJob, remove.dataset.title);
      return;
    }
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
    const placeOp = event.target.closest("[data-place-op]")?.dataset.placeOp;
    if (!current || (!action && !op && !shotOp && !refOp && !placeOp)) return;
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
    if (placeOp) {
      const placeId = event.target.closest("[data-location]").dataset.location;
      const base = `/projects/${jobId}/locations/${encodeURIComponent(placeId)}/references`;
      if (placeOp === "approve") {
        const name = event.target.closest("[data-place-name]").dataset.placeName;
        await mutate(api(withSceneQuery(`${base}/${encodeURIComponent(name)}/approve`), { method: "POST" }), "Đã ghim ảnh bối cảnh");
      } else if (placeOp === "unpin") {
        await mutate(api(withSceneQuery(`${base}/approval`), { method: "DELETE" }), "Đã bỏ ghim ảnh bối cảnh");
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
      const body = { images_only: action === "images" || action === "trial", reanalyze: action === "reanalyze" };
      if (action === "trial") body.preview_scenes = 3;
      try {
        await api(`/projects/${jobId}/render`, json("POST", body));
        toast(action === "trial" ? "Đang vẽ thử nhân vật, bối cảnh và 3 cảnh đầu…"
          : action === "images" ? "Đang sinh ảnh cho các cảnh đã đổi…" : "Đang render — chỉ tạo lại phần đã thay đổi");
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
    const placeUpload = event.target.closest("[data-place-upload]");
    if (placeUpload?.files.length && current) {
      const data = new FormData();
      data.append("file", placeUpload.files[0]);
      const placeId = placeUpload.closest("[data-location]").dataset.location;
      await mutate(api(withSceneQuery(`/projects/${jobId}/locations/${encodeURIComponent(placeId)}/references`),
        { method: "POST", body: data }), "Đã tải và ghim ảnh bối cảnh");
      return;
    }
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
    if (target.dataset.location !== undefined) {
      await mutate(api(withSceneQuery(`/projects/${jobId}/locations/${encodeURIComponent(target.dataset.location)}`), json("PATCH", lookChanges(target, LOCATION_FIELDS))), "Đã lưu bối cảnh — các cảnh ở đây sẽ được vẽ lại khi render");
      return;
    }
    if (target.dataset.character !== undefined) {
      await mutate(api(withSceneQuery(`/projects/${jobId}/characters/${encodeURIComponent(target.dataset.character)}`), json("PATCH", lookChanges(target, CHARACTER_FIELDS))), "Đã lưu nhân vật");
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
      if ("location_id" in values) patch.location_id = values.location_id;
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
      showSidebarTab(sidebarTab);
      if (location.hash === NEW_ROUTE) {
        showNew();
        return;
      }
      const linkedSeries = location.hash.match(SERIES_ROUTE)?.[1];
      if (linkedSeries) {
        if (linkedSeries !== currentSeries?.series_id || current) {
          await openSeries(linkedSeries, { reveal: true }).catch(error => {
            toast(error.message, true);
            showNew();
          });
        } else {
          render();
          refreshLists();
        }
        return;
      }
      const linked = location.hash.match(/^#comic\/([A-Za-z0-9_-]{1,64})$/)?.[1];
      if (linked && linked !== current?.job?.job_id) {
        await open(linked, { reveal: true }).catch(error => {
          toast(error.message, true);
          showNew();
        });
      } else {
        // Plain #comic (the nav link) keeps the last opened story or episode, or shows the form.
        render();
        refreshLists();
      }
    },
    leave() {
      entered = false;
      clearTimeout(liveTimer);
      liveTimer = null;
    },
    onJobChanges,
    // Reload what is on screen; used when the job socket reconnects or falls back to polling.
    refreshLive: () => scheduleLiveRefresh({ project: watchedJobs().some(isActive), lists: true }),
    isVisible: () => !view.classList.contains("is-hidden"),
  };
}
