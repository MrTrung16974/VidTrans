// Shot-level editor markup for animated storyboards (scenes -> shots -> keyframe + motion).
export const SHOT_TYPE_LABELS = {
  establishing: "Toàn cảnh mở đầu", wide: "Toàn thân", medium: "Trung cảnh", close_up: "Cận mặt",
  extreme_close_up: "Đặc tả", over_shoulder: "Qua vai",
};
export const SHOT_MOVEMENT_LABELS = {
  static: "Đứng yên", push_in: "Đẩy vào", pull_out: "Kéo ra", pan_left: "Lia trái", pan_right: "Lia phải",
  tilt_up: "Ngửa lên", tilt_down: "Cúi xuống", tracking: "Bám theo", handheld: "Cầm tay",
};
export const SHOT_ANGLE_LABELS = { eye_level: "Ngang mắt", low: "Góc thấp", high: "Góc cao", overhead: "Từ trên xuống" };

function select(name, values, labels, selected, disabled, escapeHtml) {
  return `<select name="${name}"${disabled}>${values.map(value =>
    `<option value="${escapeHtml(value)}"${value === selected ? " selected" : ""}>${escapeHtml(labels[value] || value)}</option>`).join("")}</select>`;
}

function dialogueText(lines) {
  return (lines || []).map(line => `${line.character}: ${line.text}`).join("\n");
}

function statusBadges(report, shot, escapeHtml) {
  if (!report) return '<b class="comic-chip">Chưa tạo</b>';
  const chips = [];
  if (report.fallback) {
    chips.push(`<b class="comic-chip is-warning" title="${escapeHtml(report.fallback_reason || "")}">Dự phòng 2.5D</b>`);
  } else if (report.provider) {
    chips.push(`<b class="comic-chip${report.provider === "camera_2_5d" ? "" : " is-ai"}">${report.provider === "camera_2_5d" ? "Camera 2.5D" : "AI · " + escapeHtml(report.model || report.provider)}</b>`);
  }
  if (report.qc && !report.qc.passed) chips.push(`<b class="comic-chip is-warning" title="${escapeHtml((report.qc.reasons || []).join("; "))}">QC chưa đạt</b>`);
  if (report.attempts > 1) chips.push(`<b class="comic-chip">${report.attempts} lần thử</b>`);
  if (report.billed_seconds) chips.push(`<b class="comic-chip">AI ${report.billed_seconds}s</b>`);
  if (report.too_long) chips.push('<b class="comic-chip is-warning" title="Lời thoại dài hơn 10 giây; nên tách shot">Shot quá dài</b>');
  if (report.duration) chips.push(`<b class="comic-chip">${Number(report.duration).toFixed(1)}s</b>`);
  chips.push(`<b class="comic-chip">take ${shot.animation.take}</b>`);
  return chips.join("");
}

export function renderAnimatedScenes({ storyboard, media, shots: reports, options, locked, escapeHtml }) {
  const disabled = locked ? " disabled" : "";
  return storyboard.scenes.map(scene => {
    const shotCards = scene.shots.map(shot => {
      const label = `${scene.scene_index}.${shot.shot_index}`;
      const report = reports?.[label];
      const clip = media.previews?.[label] || media.animations?.[label];
      const still = media.keyframe_uploads?.[label] || media.keyframes?.[label];
      const visual = clip
        ? `<video src="${escapeHtml(clip)}" ${still ? `poster="${escapeHtml(still)}"` : ""} muted loop playsinline controls preload="metadata"></video>`
        : still ? `<img src="${escapeHtml(still)}" alt="Keyframe ${label}" loading="lazy">`
          : '<span class="comic-scene-noimg">Chưa có keyframe — bấm "Chỉ sinh keyframe" hoặc "Xem trước shot"</span>';
      return `<form class="comic-shot" data-shot="${label}">
        <div class="comic-shot-visual-col">
          <header class="comic-shot-head">
            <div class="comic-shot-title-group">
              <strong class="comic-shot-title">Shot ${label}</strong>
              <div class="comic-shot-status">${statusBadges(report, shot, escapeHtml)}</div>
            </div>
            <div class="comic-shot-head-actions">
              <button class="button secondary comic-shot-icon-btn" type="button" data-shot-op="insert"${disabled} title="Chèn thêm shot ngay sau shot này">+ Shot sau</button>
              <button class="button secondary comic-danger comic-shot-icon-btn" type="button" data-shot-op="delete"${disabled} title="Xóa shot này">Xóa</button>
            </div>
          </header>
          <div class="comic-shot-media">${visual}${shot.keyframe_override ? '<b class="comic-badge">Keyframe tải lên</b>' : ""}</div>
          <div class="comic-shot-media-bar">
            ${select("mode", ["image_to_video", "camera_2_5d"], { image_to_video: "Chuyển động theo dự án", camera_2_5d: "Chỉ camera 2.5D" }, shot.animation.mode, disabled, escapeHtml)}
            <label class="button secondary comic-upload${locked ? " is-disabled" : ""}">Đổi keyframe<input type="file" accept="image/png,image/jpeg,image/webp" data-shot-upload${disabled} hidden></label>
            ${shot.keyframe_override ? `<button class="button secondary" type="button" data-shot-op="clear-keyframe"${disabled}>Bỏ keyframe</button>` : ""}
          </div>
          <div class="comic-shot-camera">
            <label class="comic-camera-field"><span>Cỡ cảnh</span>${select("shot_type", options.shot_types, SHOT_TYPE_LABELS, shot.camera.shot_type, disabled, escapeHtml)}</label>
            <label class="comic-camera-field"><span>Chuyển động máy</span>${select("movement", options.shot_movements, SHOT_MOVEMENT_LABELS, shot.camera.movement, disabled, escapeHtml)}</label>
            <label class="comic-camera-field"><span>Góc máy</span>${select("angle", options.shot_angles, SHOT_ANGLE_LABELS, shot.camera.angle, disabled, escapeHtml)}</label>
          </div>
        </div>
        <div class="comic-shot-body-col">
          <label class="field"><span>Lời dẫn</span><textarea name="narration" rows="2" maxlength="800"${disabled}>${escapeHtml(shot.narration)}</textarea></label>
          <label class="field"><span>Lời thoại — <code>id: lời</code></span><textarea name="dialogue" rows="1" placeholder="id_nhan_vat: Lời thoại…"${disabled}>${escapeHtml(dialogueText(shot.dialogue))}</textarea></label>
          <label class="field"><span>Chuyển động (tiếng Anh)</span><textarea name="motion_prompt" rows="2" maxlength="800"${disabled}>${escapeHtml(shot.motion_prompt)}</textarea></label>
          <details class="comic-shot-details"><summary>Keyframe, nhân vật, thời lượng</summary>
            <div class="comic-shot-details-body">
              <label class="field"><span>Khung hình đầu (tiếng Anh)</span><textarea name="keyframe_prompt" rows="3" maxlength="1200"${disabled}>${escapeHtml(shot.keyframe_prompt)}</textarea></label>
              <label class="field"><span>Nhân vật (id, dấu phẩy)</span><input name="characters" value="${escapeHtml(shot.characters.join(", "))}"${disabled}></label>
              <div class="comic-inline">
                <label class="field"><span>Thời lượng tối thiểu (s)</span><input name="duration_seconds" type="number" min="2" max="10" step="0.5" value="${shot.duration_seconds}"${disabled}></label>
                <label class="field"><span>Độ mạnh chuyển động</span><input name="motion_strength" type="number" min="0" max="1" step="0.05" value="${shot.animation.motion_strength}"${disabled}></label>
              </div>
              <label class="field"><span>Negative prompt</span><input name="negative_prompt" value="${escapeHtml(shot.negative_prompt)}" maxlength="600"${disabled}></label>
            </div>
          </details>
          <div class="comic-scene-actions comic-shot-footer">
            <button class="button primary" type="submit"${disabled}>Lưu</button>
            <button class="button secondary" type="button" data-shot-op="preview"${disabled}>Xem trước shot</button>
            <button class="button secondary" type="button" data-shot-op="regenerate"${disabled} title="Giữ keyframe và giọng, tạo chuyển động mới với seed khác">Tạo lại chuyển động</button>
          </div>
        </div>
      </form>`;
    }).join("");
    return `<article class="comic-scene-group card" data-scene="${scene.scene_index}">
      <header class="comic-scene-group-head"><strong>Cảnh ${scene.scene_index}</strong>
        <small>${scene.shots.length} shot${scene.location ? ` · ${escapeHtml(scene.location)}` : ""}${scene.emotion ? ` · ${escapeHtml(scene.emotion)}` : ""}</small>
        <button class="button secondary comic-danger" type="button" data-scene-op="delete"${disabled}>Xóa cảnh</button></header>
      <div class="comic-shots">${shotCards}</div>
    </article>`;
  }).join("");
}

export function shotPatchFromForm(form, parseDialogue) {
  const values = Object.fromEntries(new FormData(form));
  return {
    narration: values.narration,
    dialogue: parseDialogue(values.dialogue || ""),
    motion_prompt: values.motion_prompt,
    keyframe_prompt: values.keyframe_prompt,
    negative_prompt: values.negative_prompt,
    characters: String(values.characters || "").split(",").map(v => v.trim()).filter(Boolean),
    duration_seconds: Number(values.duration_seconds),
    camera: { shot_type: values.shot_type, movement: values.movement, angle: values.angle },
    animation: { mode: values.mode, motion_strength: Number(values.motion_strength) },
  };
}

export function renderReferenceGallery(data, escapeHtml, locked) {
  const disabled = locked ? " disabled" : "";
  const variants = data.variants || [];
  const items = variants.map(v => `<figure class="comic-ref${v.approved ? " is-approved" : ""}">
      <img src="${escapeHtml(v.url)}" alt="Phiên bản ${v.version}" loading="lazy">
      <figcaption>v${v.version}${v.uploaded ? " · tải lên" : ""}${v.approved ? " · <b>Đã duyệt</b>" : ""}</figcaption>
      ${v.approved ? "" : `<button class="button secondary" type="button" data-ref-op="approve" data-ref-name="${escapeHtml(v.name)}"${disabled}>Duyệt bản này</button>`}
    </figure>`).join("");
  return `<div class="comic-ref-grid">${items || '<p class="comic-empty">Chưa có ảnh tham chiếu. Render hoặc "Chỉ sinh keyframe" với Gemini, hoặc tải ảnh lên.</p>'}</div>
    <div class="comic-scene-actions">
      <label class="button secondary comic-upload${locked ? " is-disabled" : ""}">Tải ảnh tham chiếu<input type="file" accept="image/png,image/jpeg,image/webp" data-ref-upload${disabled} hidden></label>
      ${data.approved ? `<button class="button secondary" type="button" data-ref-op="unpin"${disabled}>Bỏ ghim (dùng theo mô tả)</button>` : ""}
    </div>
    <small class="field-hint">Bản đã duyệt được dùng nguyên vẹn cho mọi keyframe; sửa mô tả chỉ tạo phiên bản mới, không ghi đè bản đã duyệt.</small>`;
}
