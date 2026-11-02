// Public /contact page form → POST /api/v1/contact (no sign-in needed).
const form = document.getElementById("contactForm");
const status = document.getElementById("contactFormStatus");

function setStatus(text, kind = "") {
  status.textContent = text;
  status.dataset.kind = kind;
}

function errorText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return "Vui lòng kiểm tra lại các trường đã nhập.";
  return "Không gửi được tin nhắn. Hãy thử lại hoặc gửi email trực tiếp.";
}

form?.addEventListener("submit", async event => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  setStatus("Đang gửi…");
  try {
    const response = await fetch("/api/v1/contact", { method: "POST", body: new FormData(form), credentials: "same-origin" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorText(payload.detail));
    form.reset();
    setStatus(payload.message || "Đã gửi tin nhắn.", "success");
  } catch (error) {
    setStatus(error.message || "Không gửi được tin nhắn.", "error");
  } finally {
    button.disabled = false;
  }
});
