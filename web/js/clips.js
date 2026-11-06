// "Cắt clip": cut a finished (translated/dubbed) video into several clips. A feature of its own
// (menu #clips), not part of the translate form. The plan is edited here; every clip becomes a
// job in one batch, cut from the finished video, so voices and subtitles stay as translated.
const ACTIVE = new Set(["queued", "scheduled", "processing", "cancelling"]);
const STATUS_LABELS = {
  queued: "Đang chờ", processing: "Đang cắt", cancelling: "Đang dừng", completed: "Xong",
  failed: "Lỗi", cancelled: "Đã hủy",
};
const FRAME_LABELS = { original: "Giữ khung gốc", vertical_blur: "Dọc 9:16 (nền mờ)", vertical_crop: "Dọc 9:16 (crop giữa)" };
// Crop needs the source before subtitles were burned in; when it is gone the option is locked.
const CROP_UNAVAILABLE = "Video nguồn chưa in phụ đề đã bị xóa, không crop giữa được";
const SOURCE_PAGE = 20;
const NEW_CLIP_SECONDS = 30;

export function formatClipTime(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(value / 60);
  const rest = value - minutes * 60;
  const hours = Math.floor(minutes / 60);
  const secs = rest.toFixed(1).padStart(4, "0");
  return hours ? `${hours}:${String(minutes % 60).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

/** "1:23.4", "1:02:03" or plain seconds "83.4" -> seconds; NaN when unreadable. */
export function parseClipTime(text) {
  const parts = String(text || "").trim().replace(",", ".").split(":");
  if (!parts.length || parts.length > 3 || parts.some(part => !/^\d+(\.\d+)?$/.test(part))) return NaN;
  return parts.reduce((total, part) => total * 60 + Number(part), 0);
}

export function createClipWorkspace({ requestJson, toast, escapeHtml }) {
  const $ = selector => document.querySelector(selector);
  const json = body => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const view = $("#clipsView");
  if (!view) return { enter() {}, leave() {}, onJobChanges() {} };
  const video = $("#clipVideo");
  const timeline = $("#clipTimeline");
  const list = $("#clipList");
  let sources = [];
  let sourceTotal = 0;
  let source = null;  // GET /api/v1/clips/sources/{id}
  let clips = [];     // [{start, end, title, frame}]
  let batchId = null;
  let previewEnd = null;
  let searchTimer = null;
  let resultsTimer = null;

  // ---- Sources -------------------------------------------------------------------------
  async function loadSources({ append = false } = {}) {
    const search = $("#clipSourceSearch").value.trim();
    const params = new URLSearchParams({ limit: String(SOURCE_PAGE), offset: String(append ? sources.length : 0) });
    if (search) params.set("search", search);
    try {
      const page = await requestJson(`/api/v1/clips/sources?${params}`);
      sources = append ? [...sources, ...page.items] : page.items;
      sourceTotal = page.total;
      renderSources();
    } catch (error) {
      $("#clipSourceList").innerHTML = `<p class="clips-note">${escapeHtml(error.message)}</p>`;
    }
  }

  function renderSources() {
    const target = $("#clipSourceList");
    if (!sources.length) {
      target.innerHTML = `<p class="clips-note">Chưa có video nào đã dịch xong. Tạo một video ở mục <a href="#create">Dịch video</a> trước.</p>`;
    } else {
      target.innerHTML = sources.map(item => `
        <button type="button" class="clip-source${source?.job_id === item.job_id ? " is-active" : ""}" data-clip-source="${escapeHtml(item.job_id)}">
          <strong>${escapeHtml(item.filename)}</strong>
          <small>${item.duration ? formatClipTime(item.duration) : "—"} · ${item.has_subtitles ? "có phụ đề" : "không phụ đề"}
            · ${item.completed_at ? new Date(item.completed_at).toLocaleDateString("vi-VN") : ""}</small>
        </button>`).join("");
    }
    $("#clipSourceMore").classList.toggle("is-hidden", sources.length >= sourceTotal);
  }

  async function openSource(jobId) {
    try {
      source = await requestJson(`/api/v1/clips/sources/${encodeURIComponent(jobId)}`);
    } catch (error) {
      toast(error.message, true);
      return;
    }
    clips = [];
    batchId = null;
    $("#clipEditorEmpty").classList.add("is-hidden");
    $("#clipEditorBody").classList.remove("is-hidden");
    $("#clipSourceTitle").textContent = source.filename;
    $("#clipSourceMeta").textContent = `Dài ${formatClipTime(source.duration)} · ${source.cues.length} câu phụ đề`;
    $("#clipResults").classList.add("is-hidden");
    const defaultSelect = $("#clipDefaultFrame");
    if (!source.can_crop && defaultSelect.value === "vertical_crop") defaultSelect.value = "vertical_blur";
    defaultSelect.innerHTML = frameOptions(defaultSelect.value);
    $("#clipCropNote").classList.toggle("is-hidden", Boolean(source.can_crop));
    syncAiPanel();
    video.src = source.video_url;
    renderSources();
    renderPlan();
  }

  // ---- Plan ----------------------------------------------------------------------------
  const defaultFrame = () => $("#clipDefaultFrame").value || "original";
  const clipError = clip => {
    if (!(clip.end > clip.start)) return "Kết thúc phải sau bắt đầu";
    if (clip.end - clip.start < source.limits.min_clip_seconds) return `Ngắn hơn ${source.limits.min_clip_seconds} giây`;
    if (clip.end - clip.start > source.limits.max_clip_seconds) return `Dài hơn ${source.limits.max_clip_seconds / 60} phút`;
    if (clip.start < 0 || clip.end > source.duration + 0.05) return "Nằm ngoài độ dài video";
    return "";
  };

  function frameOptions(selected) {
    return Object.entries(FRAME_LABELS).map(([value, label]) => {
      const locked = value === "vertical_crop" && !source?.can_crop;
      return `<option value="${value}"${selected === value ? " selected" : ""}${locked ? ` disabled title="${CROP_UNAVAILABLE}"` : ""}>${label}</option>`;
    }).join("");
  }

  function renderPlan() {
    if (!source) return;
    const duration = source.duration || 1;
    const ticks = source.cues.map(cue => `<span class="clip-cue" style="left:${(cue.start / duration) * 100}%;width:${Math.max(0.15, ((cue.end - cue.start) / duration) * 100)}%"></span>`).join("");
    const ranges = clips.map((clip, index) => `<span class="clip-range${clipError(clip) ? " is-invalid" : ""}" data-range-index="${index}" style="left:${(clip.start / duration) * 100}%;width:${((clip.end - clip.start) / duration) * 100}%" title="${escapeHtml(clip.title || `Phần ${index + 1}`)}"><span class="clip-range-handle start" data-handle="start" title="Kéo mốc bắt đầu"></span><span class="clip-range-label">${index + 1}</span><span class="clip-range-handle end" data-handle="end" title="Kéo mốc kết thúc"></span></span>`).join("");
    timeline.innerHTML = `${ticks}${ranges}<span class="clip-playhead" id="clipPlayhead"></span>`;
    syncPlayhead();
    list.innerHTML = clips.map((clip, index) => {
      const error = clipError(clip);
      return `<li class="clip-row${error ? " is-invalid" : ""}" data-clip-index="${index}">
        <span class="clip-number">${index + 1}</span>
        <input class="clip-title" data-field="title" maxlength="120" value="${escapeHtml(clip.title)}" placeholder="Phần ${index + 1}" aria-label="Tiêu đề clip ${index + 1}" />
        <label class="clip-time clip-start">Từ <input data-field="start" inputmode="decimal" value="${formatClipTime(clip.start)}" />
          <button type="button" class="button secondary mini-btn" data-action="start-minus" title="Lùi 0.5s">-0.5s</button>
          <button type="button" class="button secondary mini-btn" data-action="start-plus" title="Tiến 0.5s">+0.5s</button>
          <button type="button" class="button secondary mini-btn" data-action="start-here" title="Lấy vị trí đang xem">⌖</button></label>
        <label class="clip-time clip-end">đến <input data-field="end" inputmode="decimal" value="${formatClipTime(clip.end)}" />
          <button type="button" class="button secondary mini-btn" data-action="end-minus" title="Lùi 0.5s">-0.5s</button>
          <button type="button" class="button secondary mini-btn" data-action="end-plus" title="Tiến 0.5s">+0.5s</button>
          <button type="button" class="button secondary mini-btn" data-action="end-here" title="Lấy vị trí đang xem">⌖</button></label>
        <select data-field="frame" aria-label="Khung hình clip ${index + 1}">
          ${frameOptions(clip.frame)}
        </select>
        <span class="clip-duration">${error ? escapeHtml(error) : formatClipTime(clip.end - clip.start)}</span>
        <button type="button" class="button secondary mini-btn" data-action="preview" title="Xem đoạn này">▶</button>
        <button type="button" class="button secondary mini-btn cancel-btn" data-action="remove" title="Xóa đoạn">✕</button>
      </li>`;
    }).join("") || `<li class="clips-note">Chưa có đoạn nào. Bấm “Chia đều” hoặc “Thêm đoạn”.</li>`;
    const valid = clips.length && clips.every(clip => !clipError(clip));
    const total = clips.reduce((sum, clip) => sum + Math.max(0, clip.end - clip.start), 0);
    $("#clipSummary").textContent = clips.length ? `${clips.length} clip · tổng ${formatClipTime(total)}` : "";
    $("#clipCreate").disabled = !valid || clips.length > source.limits.max_clips;
    $("#clipCreate").textContent = clips.length ? `Tạo ${clips.length} clip` : "Tạo clip";
  }

  function syncPlayhead() {
    const head = $("#clipPlayhead");
    if (head && source) head.style.left = `${Math.min(100, (video.currentTime / (source.duration || 1)) * 100)}%`;
    if (previewEnd !== null && video.currentTime >= previewEnd) {
      video.pause();
      previewEnd = null;
    }
  }

  async function splitEqually() {
    const seconds = Number($("#clipEvery").value);
    try {
      const plan = await requestJson("/api/v1/clips/plan", json({
        source_job_id: source.job_id, clip_seconds: seconds, frame: defaultFrame(),
      }));
      clips = plan.clips.map(({ start, end, title, frame }) => ({ start, end, title, frame }));
      renderPlan();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function syncAiPanel() {
    const ai = source?.ai_suggest || { available: false, reason: "" };
    $("#clipAiSuggest").disabled = !ai.available;
    $("#clipAiWish").disabled = !ai.available;
    const note = $("#clipAiNote");
    note.classList.toggle("clip-error", !ai.available);
    note.innerHTML = ai.available ? "AI đọc phụ đề rồi đề xuất, bạn sửa tiếp trước khi tạo clip."
      : `${escapeHtml(ai.reason || "")}${/nâng cấp/i.test(ai.reason || "") ? ' <a href="#billing">Xem gói</a>' : ""}`;
  }

  async function suggestWithAi() {
    if (clips.length && !window.confirm("Thay plan hiện tại bằng các đoạn AI gợi ý?")) return;
    const button = $("#clipAiSuggest");
    button.disabled = true;
    const label = button.textContent;
    button.textContent = "AI đang đọc phụ đề…";
    try {
      const result = await requestJson("/api/v1/clips/suggest", json({
        source_job_id: source.job_id,
        wish: $("#clipAiWish").value.trim(),
        count: Number($("#clipAiCount").value),
        min_seconds: Number($("#clipAiMin").value),
        max_seconds: Number($("#clipAiMax").value),
        frame: defaultFrame(),
      }));
      clips = result.clips.map(({ start, end, title, frame }) => ({ start, end, title, frame }));
      toast(`AI gợi ý ${clips.length} đoạn — xem lại rồi bấm Tạo clip`);
      renderPlan();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.textContent = label;
      syncAiPanel();
    }
  }

  function addClipHere() {
    const start = Math.min(Math.max(0, video.currentTime || 0), Math.max(0, source.duration - source.limits.min_clip_seconds));
    clips.push({ start, end: Math.min(source.duration, start + NEW_CLIP_SECONDS), title: "", frame: defaultFrame() });
    clips.sort((a, b) => a.start - b.start);
    renderPlan();
  }

  function onRowInput(event) {
    const row = event.target.closest("[data-clip-index]");
    const field = event.target.dataset.field;
    if (!row || !field) return;
    const clip = clips[Number(row.dataset.clipIndex)];
    if (field === "title" || field === "frame") {
      clip[field] = event.target.value;
      if (field === "frame") renderPlan();
      return;
    }
    if (event.type !== "change") return;
    const seconds = parseClipTime(event.target.value);
    if (Number.isNaN(seconds)) {
      toast("Thời điểm phải có dạng phút:giây, ví dụ 1:23.5", true);
    } else {
      clip[field] = Math.min(seconds, source.duration);
    }
    renderPlan();
  }

  function onRowClick(event) {
    const button = event.target.closest("[data-action]");
    const row = event.target.closest("[data-clip-index]");
    if (!button || !row) return;
    const index = Number(row.dataset.clipIndex);
    const clip = clips[index];
    const action = button.dataset.action;
    if (action === "remove") clips.splice(index, 1);
    if (action === "start-here") clip.start = video.currentTime;
    if (action === "end-here") clip.end = video.currentTime;
    if (action === "start-minus") clip.start = Math.max(0, Number((clip.start - 0.5).toFixed(2)));
    if (action === "start-plus") clip.start = Math.min(clip.end - 0.5, Number((clip.start + 0.5).toFixed(2)));
    if (action === "end-minus") clip.end = Math.max(clip.start + 0.5, Number((clip.end - 0.5).toFixed(2)));
    if (action === "end-plus") clip.end = Math.min(source.duration, Number((clip.end + 0.5).toFixed(2)));
    if (action === "preview") {
      video.currentTime = clip.start;
      previewEnd = clip.end;
      video.play().catch(() => {});
      return;
    }
    renderPlan();
  }

  async function createClips() {
    const button = $("#clipCreate");
    button.disabled = true;
    try {
      const created = await requestJson("/api/v1/clips", json({
        source_job_id: source.job_id,
        snap: $("#clipSnap").checked,
        clips: clips.map(({ start, end, title, frame }) => ({ start, end, title: title.trim(), frame })),
      }));
      batchId = created.batch_id;
      toast(`Đã tạo ${created.jobs.length} clip, đang cắt…`);
      renderResults(created.jobs);
    } catch (error) {
      toast(error.message, true);
    } finally {
      renderPlan();
    }
  }

  // ---- Results -------------------------------------------------------------------------
  async function loadResults() {
    if (!batchId) return;
    try {
      const page = await requestJson(`/api/v1/jobs?batch_id=${encodeURIComponent(batchId)}&limit=50`, { silent: true });
      renderResults([...page.items].sort((a, b) => (a.clip?.index || 0) - (b.clip?.index || 0)));
    } catch (_) {}
  }

  function renderResults(jobs) {
    $("#clipResults").classList.remove("is-hidden");
    $("#clipResultList").innerHTML = jobs.map(job => {
      const links = [
        job.video_url && `<a class="button secondary mini-btn" href="${escapeHtml(job.video_url)}" download>Tải video</a>`,
        job.subtitle_url && `<a class="button secondary mini-btn" href="${escapeHtml(job.subtitle_url)}" download>Phụ đề</a>`,
        job.tiktok_text_url && `<a class="button secondary mini-btn" href="${escapeHtml(job.tiktok_text_url)}" target="_blank" rel="noopener">Caption</a>`,
      ].filter(Boolean).join("");
      return `<li class="clip-result status-${escapeHtml(job.status)}">
        <span><strong>${escapeHtml(job.clip?.title || job.filename || job.job_id)}</strong>
          <small>${job.clip ? `${formatClipTime(job.clip.start)} – ${formatClipTime(job.clip.end)} · ${FRAME_LABELS[job.clip.frame] || ""}` : ""}</small></span>
        <span class="status-pill status-${escapeHtml(job.status)}"><span class="status-dot"></span>${STATUS_LABELS[job.status] || escapeHtml(job.status)}</span>
        <span class="clip-result-links">${links}${job.status === "failed" && job.error ? `<small class="clip-error">${escapeHtml(job.error)}</small>` : ""}</span>
      </li>`;
    }).join("");
  }

  function onJobChanges(message) {
    if (!batchId || view.classList.contains("is-hidden")) return;
    const touched = message.resync || (message.items || []).some(item => item.batch_id === batchId);
    if (!touched) return;
    clearTimeout(resultsTimer);
    resultsTimer = setTimeout(loadResults, 400);
  }

  // ---- Wiring --------------------------------------------------------------------------
  $("#clipSourceList").addEventListener("click", event => {
    const button = event.target.closest("[data-clip-source]");
    if (button) openSource(button.dataset.clipSource);
  });
  $("#clipSourceMore").addEventListener("click", () => loadSources({ append: true }));
  $("#clipSourceSearch").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadSources(), 300);
  });
  $("#clipSplitEqual").addEventListener("click", splitEqually);
  $("#clipAddHere").addEventListener("click", addClipHere);
  $("#clipAiSuggest").addEventListener("click", suggestWithAi);
  $("#clipCreate").addEventListener("click", createClips);
  list.addEventListener("input", onRowInput);
  list.addEventListener("change", onRowInput);
  list.addEventListener("click", onRowClick);
  video.addEventListener("timeupdate", syncPlayhead);
  let activeDrag = null;
  timeline.addEventListener("pointerdown", event => {
    const handle = event.target.closest("[data-handle]");
    const range = event.target.closest("[data-range-index]");
    if (handle && range) {
      event.preventDefault();
      event.stopPropagation();
      activeDrag = {
        index: Number(range.dataset.rangeIndex),
        handle: handle.dataset.handle,
      };
      try { timeline.setPointerCapture(event.pointerId); } catch (_) {}
    }
  });
  timeline.addEventListener("pointermove", event => {
    if (!activeDrag || !source) return;
    const box = timeline.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    const time = Number((ratio * source.duration).toFixed(2));
    const clip = clips[activeDrag.index];
    if (!clip) return;
    if (activeDrag.handle === "start") {
      clip.start = Math.max(0, Math.min(clip.end - 0.5, time));
      video.currentTime = clip.start;
    } else {
      clip.end = Math.min(source.duration, Math.max(clip.start + 0.5, time));
      video.currentTime = clip.end;
    }
    renderPlan();
  });
  const stopTimelineDrag = event => {
    if (activeDrag) {
      try { timeline.releasePointerCapture(event.pointerId); } catch (_) {}
      activeDrag = null;
    }
  };
  timeline.addEventListener("pointerup", stopTimelineDrag);
  timeline.addEventListener("pointercancel", stopTimelineDrag);

  timeline.addEventListener("click", event => {
    if (!source || activeDrag) return;
    if (event.target.closest("[data-handle]")) return;
    const box = timeline.getBoundingClientRect();
    video.currentTime = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) * source.duration;
    previewEnd = null;
  });

  return {
    enter() {
      loadSources();
      if (batchId) loadResults();
    },
    leave() {
      if (!video.paused) video.pause();
    },
    onJobChanges,
  };
}
