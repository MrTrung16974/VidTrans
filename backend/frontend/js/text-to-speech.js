// Standalone text -> audio: one synchronous request per clip; results are served same-origin
// because the production CSP blocks blob: media.
const VOICE_LIST_PROVIDERS = new Set(["elevenlabs", "cit"]);

export function createTextToSpeechWorkspace({ requestJson, toast, escapeHtml }) {
  const $ = selector => document.querySelector(selector);
  const form = $("#ttsForm");
  const providerSelect = $("#ttsStandaloneProvider");
  const voiceSelect = $("#ttsStandaloneVoice");
  const hint = $("#ttsStandaloneHint");
  const text = $("#ttsText");
  const counter = $("#ttsCharCount");
  const submit = $("#ttsSubmit");
  let providers = new Map();
  let maxChars = 5000;
  let loaded = false;

  function updateCounter() {
    const length = text.value.trim().length;
    counter.textContent = `${length.toLocaleString("vi-VN")} / ${maxChars.toLocaleString("vi-VN")} ký tự`;
    counter.classList.toggle("is-error", length > maxChars);
  }

  function renderVoices() {
    const provider = providers.get(providerSelect.value);
    hint.textContent = provider?.free_note || "";
    const voices = VOICE_LIST_PROVIDERS.has(provider?.key) ? provider.voices || [] : [
      { key: "", gender: "female", label: provider?.female_voice_label || "Giọng nữ" },
      { key: "", gender: "male", label: provider?.male_voice_label || "Giọng nam" },
    ];
    voiceSelect.innerHTML = voices.map(voice => {
      const genderLabel = voice.gender === "male" ? "Nam" : "Nữ";
      return `<option value="${escapeHtml(voice.key)}" data-gender="${escapeHtml(voice.gender)}">${genderLabel} — ${escapeHtml(voice.label)}</option>`;
    }).join("");
    voiceSelect.disabled = voices.length === 0;
  }

  async function load() {
    try {
      const [providerData, config] = await Promise.all([
        requestJson("/api/v1/tts-providers"),
        requestJson("/api/v1/text-to-speech/config"),
      ]);
      maxChars = Number(config.max_chars) || maxChars;
      providers = new Map((providerData.providers || []).map(provider => [provider.key, provider]));
      [...providerSelect.options].forEach(option => {
        const provider = providers.get(option.value);
        option.disabled = provider ? !provider.configured : true;
        option.textContent = option.textContent.replace(/ — chưa .*$/, "");
        if (provider && !provider.configured) {
          option.textContent += option.value === "cit" ? " — chưa mở ứng dụng" : " — chưa cấu hình";
        }
      });
      if (providerSelect.selectedOptions[0]?.disabled) providerSelect.value = "edge";
      loaded = true;
    } catch (_) {
      hint.textContent = "Không đọc được danh sách nhà cung cấp.";
    }
    renderVoices();
    updateCounter();
  }

  function addResult(result, sourceText) {
    $("#ttsResultEmpty")?.classList.add("is-hidden");
    const item = document.createElement("li");
    const duration = result.duration ? `${result.duration.toFixed(1)} giây` : "";
    item.innerHTML = `
      <p class="tts-result-text" title="${escapeHtml(sourceText)}">${escapeHtml(sourceText)}</p>
      <audio controls preload="metadata" src="${escapeHtml(result.url)}"></audio>
      <div class="tts-result-meta">
        <span>${escapeHtml(result.voice || "")} ${duration ? "· " + duration : ""}</span>
        <a href="${escapeHtml(result.download_url)}" download>Tải về</a>
      </div>`;
    $("#ttsResults").prepend(item);
  }

  async function onSubmit(event) {
    event.preventDefault();
    const value = text.value.trim();
    if (!value) return toast("Nhập văn bản cần đọc", true);
    if (value.length > maxChars) return toast(`Văn bản vượt ${maxChars} ký tự, hãy chia nhỏ`, true);
    const selected = voiceSelect.selectedOptions[0];
    const body = {
      text: value,
      provider: providerSelect.value,
      voice: voiceSelect.value,
      voice_type: selected?.dataset.gender || "female",
      speech_rate: Number(form.elements.speech_rate.value) || 1,
      language: form.elements.language.value,
    };
    submit.disabled = true;
    submit.textContent = "Đang tạo giọng nói…";
    try {
      const result = await requestJson("/api/v1/text-to-speech", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      addResult(result, value);
      toast("Đã tạo xong audio");
    } catch (error) {
      toast(error.message || "Không tạo được giọng nói", true);
    } finally {
      submit.disabled = false;
      submit.textContent = "Tạo giọng nói";
    }
  }

  form?.addEventListener("submit", onSubmit);
  text?.addEventListener("input", updateCounter);
  providerSelect?.addEventListener("change", renderVoices);

  return {
    // Re-check on every visit: CIT Voice Studio is a desktop app that may be opened later.
    enter() { if (form) load(); },
    get loaded() { return loaded; },
  };
}
