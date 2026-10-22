// Story -> comic video workspace: create projects, review the storyboard, edit scenes and re-render.
const ACTIVE = new Set(["queued", "scheduled", "processing", "cancelling"]);
const STEP_LABELS = {
  queued: "Đang xếp hàng", comic_storyboard: "Phân tích truyện & chia cảnh", comic_references: "Ảnh tham chiếu nhân vật",
  comic_images: "Sinh ảnh minh họa", comic_voice: "Lồng tiếng", comic_clips: "Dựng clip từng cảnh",
  comic_export: "Ghép & xuất video", storyboard_ready: "Storyboard sẵn sàng để duyệt", completed: "Đã xuất video",
  failed: "Thất bại", cancelled: "Đã hủy", cancelling: "Đang dừng",
};
const MOTION_LABELS = {
  slow_zoom_in: "Zoom vào chậm", slow_zoom_out: "Zoom ra chậm", pan_left: "Lia sang trái", pan_right: "Lia sang phải",
  pan_up: "Lia lên", pan_down: "Lia xuống", static: "Đứng yên",
};
const POLL_MS = 2500;

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

  const api = (path, init = {}) => requestJson(`/api/v1/comic${path}`, init);
  const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  const isActive = job => ACTIVE.has(job?.status);
  const statusClass = job => isActive(job) ? "processing" : job?.status === "completed" ? "completed" : job?.status === "failed" ? "failed" : "queued";

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
    const gemini = options.image_providers.find(item => item.key === "gemini");
    fillSelect(form.elements.image_provider, options.image_providers, gemini?.configured ? "gemini" : "placeholder");
    $("#comicStoryLimit").textContent = `Tối đa ${options.max_story_chars.toLocaleString("vi-VN")} ký tự mỗi dự án.`;
  }

  async function loadProjects() {
    const list = $("#comicProjectList");
    try {
      const data = await requestJson("/api/v1/jobs?limit=200");
      const projects = (data.items || []).filter(job => job.job_action === "comic_video");
      if (!projects.length) {
        list.innerHTML = '<p class="comic-empty">Chưa có dự án nào. Dán một chương truyện bên cạnh để bắt đầu.</p>';
        return;
      }
      list.innerHTML = projects.map(job => {
        const comic = job.comic || {};
        const meta = [comic.scenes ? `${comic.scenes} cảnh` : "", comic.aspect_ratio || ""].filter(Boolean).join(" · ");
        return `<button type="button" class="comic-project${current?.job?.job_id === job.job_id ? " is-active" : ""}" data-job="${escapeHtml(job.job_id)}">
          <strong>${escapeHtml(comic.title || job.filename || job.job_id)}</strong>
          <span class="status-pill status-${statusClass(job)}">${escapeHtml(STEP_LABELS[job.step] || job.status)}</span>
          <small>${escapeHtml(meta)}</small></button>`;
      }).join("");
    } catch (error) {
      list.innerHTML = `<p class="comic-empty">${escapeHtml(error.message)}</p>`;
    }
  }

  async function open(jobId) {
    current = await api(`/projects/${encodeURIComponent(jobId)}`);
    // A shareable link straight to this project.
    if (location.hash !== `#comic/${jobId}`) history.replaceState(null, "", `#comic/${jobId}`);
    render();
    schedulePoll();
    loadProjects();
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!entered || !current || !isActive(current.job)) return;
    pollTimer = setTimeout(async () => {
      try {
        const wasActive = isActive(current.job);
        current = await api(`/projects/${encodeURIComponent(current.job.job_id)}`);
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
    $("#comicMeta").textContent = storyboard
      ? `Bản ${storyboard.revision} · ${storyboard.scenes.length} cảnh · ${storyboard.characters.length} nhân vật · chia cảnh: ${storyboard.analysis_method}`
      : "Storyboard đang được tạo…";
    renderCharacters(storyboard, media, locked);
    renderScenes(storyboard, media, locked);
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
          <small class="field-hint">id: ${escapeHtml(c.id)} · sửa ngoại hình sẽ vẽ lại ảnh tham chiếu và các cảnh có nhân vật này</small>
          <button class="button secondary" type="submit"${locked ? " disabled" : ""}>Lưu nhân vật</button>
        </div></form>`;
    }).join("");
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
      render();
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
      await open(created.job_id);
      editor.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      toast(error.message, true);
    } finally {
      submit.disabled = false;
    }
  });

  form.elements.text.addEventListener("input", () => {
    $("#comicStoryCount").textContent = `${form.elements.text.value.length.toLocaleString("vi-VN")} ký tự`;
  });

  $("#comicProjectList").addEventListener("click", event => {
    const button = event.target.closest("[data-job]");
    if (button) open(button.dataset.job).catch(error => toast(error.message, true));
  });

  editor.addEventListener("click", async event => {
    const action = event.target.closest("[data-comic-action]")?.dataset.comicAction;
    const op = event.target.closest("[data-scene-op]")?.dataset.sceneOp;
    if (!current || (!action && !op)) return;
    const jobId = encodeURIComponent(current.job.job_id);
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
        await open(current.job.job_id);
      } catch (error) { toast(error.message, true); }
      return;
    }
    const sceneIndex = event.target.closest("[data-scene]")?.dataset.scene;
    if (op === "delete") {
      if (!confirm(`Xóa cảnh ${sceneIndex}?`)) return;
      await mutate(api(`/projects/${jobId}/scenes/${sceneIndex}`, { method: "DELETE" }), "Đã xóa cảnh");
    } else if (op === "clear-image") {
      await mutate(api(`/projects/${jobId}/scenes/${sceneIndex}/image`, { method: "DELETE" }), "Đã bỏ ảnh tải lên");
    } else if (op === "insert") {
      const narration = prompt("Lời dẫn cho cảnh mới:");
      if (!narration?.trim()) return;
      const visual = prompt("Mô tả hình ảnh (tiếng Anh):", narration) || narration;
      await mutate(api(`/projects/${jobId}/scenes`, json("POST", { after: Number(sceneIndex), narration, visual_prompt: visual })), "Đã chèn cảnh");
    }
  });

  editor.addEventListener("change", async event => {
    const input = event.target.closest("[data-scene-upload]");
    if (!input?.files.length || !current) return;
    const data = new FormData();
    data.append("file", input.files[0]);
    const sceneIndex = input.closest("[data-scene]").dataset.scene;
    await mutate(api(`/projects/${encodeURIComponent(current.job.job_id)}/scenes/${sceneIndex}/image`, { method: "POST", body: data }),
      `Đã thay ảnh cảnh ${sceneIndex}`);
  });

  editor.addEventListener("submit", async event => {
    event.preventDefault();
    if (!current) return;
    const target = event.target;
    const jobId = encodeURIComponent(current.job.job_id);
    const values = Object.fromEntries(new FormData(target));
    if (target.dataset.character !== undefined) {
      await mutate(api(`/projects/${jobId}/characters/${encodeURIComponent(target.dataset.character)}`, json("PATCH", values)), "Đã lưu nhân vật");
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
      await mutate(api(`/projects/${jobId}/scenes/${sceneIndex}`, json("PATCH", patch)), `Đã lưu cảnh ${sceneIndex}`);
    } catch (error) {
      toast(error.message, true);
    }
  });

  return {
    async enter() {
      entered = true;
      try {
        await loadOptions();
      } catch (error) {
        toast(error.message, true);
      }
      const linked = location.hash.match(/^#comic\/([A-Za-z0-9_-]{1,64})$/)?.[1];
      if (linked && linked !== current?.job?.job_id) {
        await open(linked).catch(error => toast(error.message, true));
      } else {
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
