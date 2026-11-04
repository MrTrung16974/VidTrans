// Import chapters of a web novel (Qidian, Jinjiang) into a comic story form.
// The server reads the chapters in its source browser, paced like a reader, and splits
// them into episodes that each fit one comic project; this widget drives that and hands
// the episodes to the form.

const POLL_MS = 3000;
const MAX_CHAPTERS = 200;

export function createNovelImporter({ container, requestJson, toast, escapeHtml, onEpisodes }) {
  const api = (path, init = {}) => requestJson(`/api/v1/comic/sources${path}`, init);
  container.innerHTML = `
    <details class="novel-import">
      <summary><span class="novel-import-badge">📖 Qidian · Jinjiang</span> Nhập chương từ link truyện (tự động chia tập)</summary>
      <div class="novel-import-body">
        <div class="novel-import-row">
          <input type="url" data-novel="url" placeholder="https://www.qidian.com/book/… hoặc https://www.jjwxc.net/onebook.php?novelid=…" aria-label="Link truyện" />
          <button type="button" class="button secondary" data-novel="catalog">Tải mục lục</button>
        </div>
        <div data-novel="catalog-view" class="is-hidden">
          <p data-novel="book" class="novel-import-book"></p>
          <div class="novel-import-row">
            <label>Từ chương <input type="number" min="1" data-novel="from" /></label>
            <label>đến chương <input type="number" min="1" data-novel="to" /></label>
            <label class="comic-check"><input type="checkbox" data-novel="skip-locked" checked /> Bỏ qua chương bị khóa (VIP chưa mua)</label>
          </div>
          <p data-novel="range" class="field-hint"></p>
          <label class="comic-check"><input type="checkbox" data-novel="rights" /> Tôi là tác giả, hoặc đã được tác giả/đơn vị giữ bản quyền (ví dụ Yuewen/Qidian, Jinjiang) cho phép chuyển thể truyện này thành video, và tự chịu trách nhiệm về bản quyền theo <a href="/terms#content" target="_blank" rel="noopener">Điều khoản mục 4</a>.</label>
          <div class="novel-import-row">
            <button type="button" class="button primary" data-novel="start">Tải chương</button>
            <button type="button" class="button secondary is-hidden" data-novel="cancel">Hủy</button>
          </div>
        </div>
        <p data-novel="progress" class="field-hint" role="status"></p>
        <div data-novel="result"></div>
      </div>
    </details>`;
  const $ = name => container.querySelector(`[data-novel="${name}"]`);
  let catalog = null;
  let importId = null;
  let pollTimer = null;
  let limit = 59000;

  function selection() {
    if (!catalog) return [];
    const total = catalog.chapters.length;
    const from = Math.min(Math.max(1, parseInt($("from").value, 10) || 1), total);
    const to = Math.min(Math.max(from, parseInt($("to").value, 10) || from), total);
    const chapters = catalog.chapters.slice(from - 1, to);
    return $("skip-locked").checked ? chapters.filter(chapter => chapter.access === "accessible") : chapters;
  }

  function describeSelection() {
    const chosen = selection();
    const words = chosen.reduce((sum, chapter) => sum + (chapter.word_count || 0), 0);
    const locked = chosen.filter(chapter => chapter.access !== "accessible").length;
    const cached = chosen.filter(chapter => chapter.cached).length;
    const episodes = Math.max(1, Math.ceil(words / limit));
    const first = chosen[0]?.title || "—";
    const last = chosen[chosen.length - 1]?.title || "—";
    $("range").textContent = chosen.length
      ? `${chosen.length} chương (${escapeText(first)} → ${escapeText(last)}), khoảng ${words.toLocaleString("vi-VN")} chữ`
        + ` → ${episodes} tập. ${cached} chương đã có sẵn. `
        + (locked ? `${locked} chương bị khóa sẽ chỉ có phần xem trước và bị bỏ qua. ` : "")
        + (chosen.length > MAX_CHAPTERS ? `Mỗi lần tối đa ${MAX_CHAPTERS} chương.` : "")
      : "Không có chương nào đọc được trong khoảng này.";
    $("start").disabled = !chosen.length || chosen.length > MAX_CHAPTERS;
  }

  const escapeText = value => String(value).slice(0, 40);

  async function loadCatalog() {
    const url = $("url").value.trim();
    if (!url) {
      toast("Hãy dán link truyện", true);
      return;
    }
    const button = $("catalog");
    button.disabled = true;
    $("progress").textContent = "Đang mở mục lục trong trình duyệt nguồn… (có thể mất 10–30 giây)";
    try {
      catalog = await api(`/catalog?url=${encodeURIComponent(url)}`);
      limit = catalog.episode_chars || limit;
      const accessible = catalog.chapters.filter(chapter => chapter.access === "accessible").length;
      $("book").innerHTML = `<strong>${escapeHtml(catalog.title)}</strong>${catalog.author ? ` · ${escapeHtml(catalog.author)}` : ""}`
        + ` · ${catalog.chapters.length} chương, ${accessible} chương đọc được`;
      $("from").max = $("to").max = String(catalog.chapters.length);
      $("from").value = "1";
      $("to").value = String(Math.min(catalog.chapters.length, 10));
      $("catalog-view").classList.remove("is-hidden");
      $("progress").textContent = "";
      describeSelection();
    } catch (error) {
      $("progress").textContent = "";
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  function stopPolling() {
    clearTimeout(pollTimer);
    pollTimer = null;
  }

  function renderProgress(state) {
    const counts = {};
    for (const chapter of state.chapters) counts[chapter.status] = (counts[chapter.status] || 0) + 1;
    const running = ["queued", "running"].includes(state.status);
    $("progress").textContent = running
      ? `Đang tải chương ${state.done}/${state.total}… (nghỉ vài giây giữa các chương để không làm phiền trang nguồn)`
      : `Xong: ${counts.ok || 0} chương lấy được`
        + (counts.preview ? `, ${counts.preview} chương chỉ có xem trước` : "")
        + (counts.protected ? `, ${counts.protected} chương bị bảo vệ` : "")
        + (counts.failed ? `, ${counts.failed} chương lỗi` : "")
        + (state.status === "cancelled" ? " (đã hủy)" : "");
    $("cancel").classList.toggle("is-hidden", !running);
    $("start").disabled = running;
  }

  function renderResult(state) {
    const skipped = state.chapters.filter(chapter => chapter.status !== "ok" && chapter.status !== "pending");
    const episodes = state.episodes || [];
    $("result").innerHTML = (episodes.length > 1
      ? `<p><strong>${episodes.length} tập</strong>: tập 1 được điền vào form; các tập sau tự thêm vào truyện khi bạn tạo.</p>
         <ol class="novel-import-episodes">${episodes.map(episode => `<li>${escapeHtml(episode.chapters[0] || "")}`
           + `${episode.chapters.length > 1 ? ` → ${escapeHtml(episode.chapters[episode.chapters.length - 1])}` : ""}`
           + ` · ${episode.chars.toLocaleString("vi-VN")} ký tự</li>`).join("")}</ol>`
      : "")
      + (skipped.length
        ? `<details><summary>${skipped.length} chương bị bỏ qua</summary><ul>${skipped.map(chapter =>
          `<li>${escapeHtml(chapter.title)}: ${escapeHtml(chapter.reason)}</li>`).join("")}</ul></details>`
        : "");
  }

  async function poll() {
    if (!importId) return;
    try {
      let state = await api(`/imports/${importId}`, { silent: true });
      renderProgress(state);
      if (["queued", "running"].includes(state.status)) {
        pollTimer = setTimeout(poll, POLL_MS);
        return;
      }
      state = await api(`/imports/${importId}?include_text=true`, { silent: true });
      importId = null;
      renderResult(state);
      if (state.episodes?.length) {
        onEpisodes(state.episodes.map(episode => ({
          title: episodeTitle(episode), text: episode.text, chapters: episode.chapters,
        })), { title: state.title, author: state.author });
        toast(state.episodes.length > 1 ? `Đã tải ${state.episodes.length} tập` : "Đã điền nội dung truyện");
      } else if (state.error) {
        toast(state.error, true);
      }
    } catch (error) {
      toast(error.message, true);
      pollTimer = setTimeout(poll, POLL_MS * 2);
    }
  }

  function episodeTitle(episode) {
    const first = episode.chapters[0] || "";
    const last = episode.chapters[episode.chapters.length - 1] || "";
    return (episode.chapters.length > 1 ? `${first} – ${last}` : first).slice(0, 200);
  }

  async function start() {
    const chosen = selection();
    if (!$("rights").checked) {
      toast("Hãy xác nhận bạn là tác giả hoặc đã được phép chuyển thể truyện này", true);
      return;
    }
    $("start").disabled = true;
    $("result").innerHTML = "";
    try {
      const state = await api("/imports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: $("url").value.trim(), chapter_ids: chosen.map(chapter => chapter.id), rights_confirmed: true }),
      });
      importId = state.import_id;
      renderProgress(state);
      stopPolling();
      pollTimer = setTimeout(poll, 1000);
    } catch (error) {
      $("start").disabled = false;
      toast(error.message, true);
    }
  }

  async function cancel() {
    if (!importId) return;
    try {
      renderProgress(await api(`/imports/${importId}`, { method: "DELETE" }));
    } catch (error) {
      toast(error.message, true);
    }
  }

  // The plan may not include chapter import (admin "Phân quyền gói & menu"): ask the
  // server once, when the section is first opened, and lock the form if it refuses.
  let accessChecked = false;
  container.querySelector("details.novel-import").addEventListener("toggle", async event => {
    if (!event.currentTarget.open || accessChecked) return;
    accessChecked = true;
    try {
      await api("", { silent: true });
    } catch (error) {
      container.querySelectorAll("input, button").forEach(element => { element.disabled = true; });
      $("progress").textContent = error.message;
    }
  });
  $("catalog").addEventListener("click", loadCatalog);
  $("url").addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      loadCatalog();
    }
  });
  for (const name of ["from", "to", "skip-locked"]) $(name).addEventListener("input", describeSelection);
  $("start").addEventListener("click", start);
  $("cancel").addEventListener("click", cancel);

  return {
    reset() {
      $("result").innerHTML = "";
      $("progress").textContent = "";
    },
  };
}
