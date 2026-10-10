import { resolveTikTokFrameUrl, frameResponseProblem } from "./browser-frame.js?v=20260914-1";
export function createTikTokWorkspace({ requestJson, toast }) {
  const $ = selector => document.querySelector(selector);
  let status = null, apiStatus = null, draft = null, timer = null, apiTimer = null, refreshing = false, loading = false, submitting = false;
  let suggestion = null, suggesting = false;
  let requestVersion = 0, locked = false, attemptId = null;
  let publishAttempt = null, publishTimer = null, publishSubmitting = false, publishKey = null;
  let emailSubmitting = false;
  const edits = new Map();
  let checkedFrameUrl = null, frameProblem = null, frameChecking = false;
  function checkFrame(url) {
    if (checkedFrameUrl === url || frameChecking) return;
    checkedFrameUrl = url; frameChecking = true; frameProblem = null;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    fetch(url, { method: 'HEAD', credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
      .then(response => { if (checkedFrameUrl === url) frameProblem = frameResponseProblem(response); })
      .catch(() => { if (checkedFrameUrl === url) frameProblem = 'Không kết nối được khung TikTok. Kiểm tra kết nối hoặc dùng “Mở rộng”.'; })
      .finally(() => { clearTimeout(timeout); frameChecking = false; if (status && !locked) render(status); });
  }
  const busyStates = new Set(['queued', 'opening', 'uploading', 'publishing']);
  const labels = { queued: 'Đang chuẩn bị', opening: 'Đang mở TikTok Studio', uploading: 'Đang chuyển video', awaiting_review: 'Sẵn sàng đăng bài', publishing: 'Đang đăng bài', publish_submitted: 'Đã gửi yêu cầu đăng', needs_login: 'Cần đăng nhập TikTok', needs_review: 'Cần bạn kiểm tra' };
  const publishLabels = { READY: 'Sẵn sàng', SCHEDULED_LOCAL: 'Đã đặt lịch', UPLOADING: 'Đang tải video', PROCESSING_UPLOAD: 'TikTok đang xử lý', SUBMITTED: 'TikTok đã nhận yêu cầu', SEND_TO_USER_INBOX: 'Bản nháp đã tới TikTok', PUBLISH_COMPLETE: 'Đã đăng thành công', NEEDS_RECONCILIATION: 'Cần kiểm tra trên TikTok', FAILED: 'Đăng thất bại', CANCELLED: 'Đã hủy lịch' };
  const publishBusy = new Set(['READY', 'UPLOADING', 'PROCESSING_UPLOAD', 'PROCESSING_DOWNLOAD', 'SUBMITTED', 'NEEDS_RECONCILIATION']);

  function controls() {
    const caption = $('#tiktokCaption').value;
    $('#tiktokCaptionCount').textContent = `${caption.length.toLocaleString('vi-VN')} / 2.200`;
    $('#tiktokPrepareButton').disabled = locked || submitting || loading || !draft || !status?.available || Boolean(status?.attempt) || !caption.trim() || caption.length > 2200 || !$('#tiktokReviewConsent').checked;
    const mode = $('#tiktokPublishMode').value;
    const needsCaption = mode !== 'INBOX_DRAFT';
    const hasScope = mode === 'INBOX_DRAFT' ? apiStatus?.can_upload_draft : apiStatus?.can_direct_publish;
    const scheduleValid = mode !== 'SCHEDULE' || Boolean($('#tiktokScheduleAt').value);
    $('#tiktokOfficialPublish').disabled = locked || publishSubmitting || loading || !draft || !hasScope || (needsCaption && !caption.trim()) || caption.length > 2200 || !scheduleValid || !$('#tiktokReviewConsent').checked || publishBusy.has(publishAttempt?.status) || publishAttempt?.status === 'SCHEDULED_LOCAL';
    $('#tiktokSuggestCaption').disabled = locked || loading || suggesting || !draft || Boolean(status?.attempt);
    const reviewed = $('#tiktokResolveConsent').checked;
    $('#tiktokResolveButton').disabled = !reviewed || busyStates.has(status?.attempt?.state);
    $('#tiktokPublishButton').disabled = !reviewed || status?.attempt?.state !== 'awaiting_review';
  }

  function render(result) {
    status = result;
    $('#tiktokBrowserStatus').textContent = result.message;
    $('#tiktokSetupStatus').textContent = result.available ? 'Trình duyệt sẵn sàng. Duyệt video tại khu vực Đăng TikTok.' : 'Kết nối tài khoản tại khu vực Đăng TikTok sau khi trình duyệt sẵn sàng.';
    $('#tiktokBrowserOpen').disabled = !result.available || busyStates.has(result.attempt?.state);
    $('#tiktokBrowserCheck').disabled = !result.available;
    const loginUnavailable = !result.available || Boolean(result.attempt);
    const rateLimited = result.login_state === 'rate_limited';
    // OTP throttling must block another email/phone submission, while QR
    // remains available as TikTok's alternative login method.
    $('#tiktokBrowserLoginQr').disabled = loginUnavailable;
    $('#tiktokBrowserLoginEmail').disabled = loginUnavailable || rateLimited;
    $('#tiktokEmailSubmit').disabled = loginUnavailable || rateLimited || emailSubmitting;
    if (rateLimited) {
      $('#tiktokEmailStatus').textContent = 'TikTok đang giới hạn OTP · hãy dùng QR hoặc thử lại sau';
    }
    $('#tiktokBrowserLogout').disabled = !result.available || Boolean(result.attempt);
    const frame = $('#tiktokBrowserFrame');
    const external = $('#tiktokBrowserExternal');
    const browserUrl = resolveTikTokFrameUrl(result.browser_url, location.href);
    if (!browserUrl) {
      frame.removeAttribute('src');
      frame.classList.add('is-hidden');
      $('#tiktokBrowserPlaceholder').classList.remove('is-hidden');
      $('#tiktokBrowserHelp').textContent = result.available ? 'Địa chỉ khung TikTok không hợp lệ. Trên VPS cần dùng /tiktok-browser/vnc_lite.html qua Nginx.' : 'Trình duyệt chưa sẵn sàng. Khởi động dịch vụ TikTok trên máy chủ rồi bấm Kiểm tra phiên.';
      external.classList.add('is-hidden');
      // Keep a recovery action available even when the sidecar starts later.
      $('#tiktokBrowserCheck').disabled = false;
    } else {
      external.href = browserUrl;
      external.classList.remove('is-hidden');
      if (location.hash === '#tiktok' && !locked) {
        const sameOrigin = new URL(browserUrl).origin === location.origin;
        if (sameOrigin) checkFrame(browserUrl);
        if (sameOrigin && (frameChecking || frameProblem)) {
          frame.removeAttribute('src'); frame.classList.add('is-hidden');
          $('#tiktokBrowserPlaceholder').classList.remove('is-hidden');
          $('#tiktokBrowserHelp').textContent = frameProblem || 'Đang kiểm tra kết nối trình duyệt…';
        } else {
        if (frame.getAttribute('src') !== browserUrl) frame.src = browserUrl;
        frame.classList.remove('is-hidden');
        $('#tiktokBrowserPlaceholder').classList.add('is-hidden');
        }
      }
    }
    const attempt = result.attempt;
    if (attemptId !== attempt?.id) $('#tiktokResolveConsent').checked = false;
    attemptId = attempt?.id;
    $('#tiktokAttemptPanel').classList.toggle('is-hidden', !attempt);
    if (attempt) {
      $('#tiktokAttemptTitle').textContent = labels[attempt.state] || 'Kiểm tra TikTok Studio';
      $('#tiktokAttemptMessage').textContent = attempt.message;
      $('#tiktokResolveButton').textContent = attempt.state === 'publish_submitted' ? 'Đã kiểm tra kết quả, kết thúc lượt' : 'Kết thúc không đăng';
      $('#tiktokResolveControls').classList.toggle('is-hidden', busyStates.has(attempt.state));
    }
    controls();
    scheduleStatusRefresh();
  }

  function scheduleStatusRefresh() {
    clearTimeout(timer);
    if (locked || location.hash !== '#tiktok') return;
    const delay = busyStates.has(status?.attempt?.state) ? 1200 : 5000;
    timer = setTimeout(async () => { await refresh(); scheduleStatusRefresh(); }, delay);
  }

  async function refresh(check = false) {
    if (refreshing || locked) return;
    refreshing = true;
    try {
      const result = await requestJson(`/api/v1/tiktok-browser/${check ? 'check' : 'status'}`, check ? { method: 'POST' } : {});
      if (!locked) render(result);
    } catch (error) {
      status = null;
      $('#tiktokBrowserStatus').textContent = error.message;
      $('#tiktokSetupStatus').textContent = 'Chưa kiểm tra được trình duyệt TikTok.';
      controls();
    } finally { refreshing = false; }
  }

  async function refreshApi() {
    try {
      apiStatus = await requestJson('/api/v1/tiktok-auth');
      $('#tiktokApiStatus').textContent = apiStatus.can_direct_publish
        ? 'Đã kết nối · sẵn sàng đăng trực tiếp và gửi bản nháp'
        : apiStatus.connected ? 'Cần kết nối lại và cấp quyền video.publish' : apiStatus.configured ? 'Cần kết nối TikTok' : 'Máy chủ chưa cấu hình TikTok Developer App';
      $('#tiktokApiConnect').disabled = !apiStatus.configured || (apiStatus.can_direct_publish && apiStatus.can_upload_draft);
      $('#tiktokApiDisconnect').disabled = !apiStatus.connected;
    } catch (error) {
      apiStatus = null;
      $('#tiktokApiStatus').textContent = error.message;
    }
    controls();
  }

  async function loadVideos() {
    try {
      const data = await requestJson('/api/v1/jobs?status=completed&limit=200');
      if (locked) return;
      const select = $('#tiktokJobSelect');
      const selected = draft?.job_id || select.value;
      select.replaceChildren(new Option('Chọn video đã hoàn tất…', ''));
      for (const job of data.items || []) {
        if (job.video_url) select.add(new Option(job.filename || job.output_video || job.job_id, job.job_id));
      }
      if (draft && ![...select.options].some(o => o.value === selected)) select.add(new Option(draft.filename, selected));
      select.value = selected;
    } catch (error) { toast(error.message, true); }
  }

  async function openDraft(jobId) {
    if (draft) edits.set(draft.job_id, $('#tiktokCaption').value);
    const version = ++requestVersion;
    suggestion = null;
    $('#tiktokCaptionSuggestion').classList.add('is-hidden');
    loading = true;
    $('#tiktokReviewConsent').checked = false;
    controls();
    try {
      const data = await requestJson(`/api/v1/jobs/${encodeURIComponent(jobId)}/tiktok-draft`);
      if (version !== requestVersion || locked) return;
      draft = data;
      const select = $('#tiktokJobSelect');
      if (![...select.options].some(o => o.value === jobId)) select.add(new Option(data.filename, jobId));
      select.value = jobId;
      $('#tiktokVideoPreview').src = data.video_url;
      $('#tiktokDraftName').textContent = data.filename;
      $('#tiktokCaption').value = edits.get(jobId) ?? data.caption;
      $('#tiktokDraftEmpty').classList.add('is-hidden');
      $('#tiktokDraftEditor').classList.remove('is-hidden');
      // Warn only when an earlier, finished attempt actually handed the file to TikTok;
      // the active attempt is already shown in the attempt panel.
      const previous = data.latest_attempt;
      const warn = Boolean(previous?.file_sent) && !previous.active;
      $('#tiktokPreviousAttempt').classList.toggle('is-hidden', !warn);
      if (warn) {
        const when = new Date(previous.created_at * 1000).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
        $('#tiktokPreviousAttempt').textContent = `Video này đã được tải lên TikTok Studio lúc ${when}. Kiểm tra mục Bài đăng / Bản nháp trên TikTok để tránh đăng trùng trước khi tải lại.`;
      }
      await loadLatestPublish(jobId);
    } catch (error) {
      if (version !== requestVersion) return;
      draft = null;
      $('#tiktokDraftEmpty').classList.remove('is-hidden');
      $('#tiktokDraftEditor').classList.add('is-hidden');
      toast(error.message, true);
    } finally {
      if (version === requestVersion) { loading = false; controls(); }
    }
  }

  function renderPublish(attempt) {
    publishAttempt = attempt?.status ? attempt : null;
    const panel = $('#tiktokPublishStatus');
    panel.classList.toggle('is-hidden', !publishAttempt);
    if (!publishAttempt) { controls(); return; }
    $('#tiktokPublishStatusTitle').textContent = publishLabels[publishAttempt.status] || publishAttempt.status;
    let message = publishAttempt.error || '';
    if (!message && publishAttempt.status === 'SCHEDULED_LOCAL') message = `VidTrans sẽ đăng lúc ${new Date(publishAttempt.scheduled_for).toLocaleString('vi-VN')}.`;
    if (!message && publishAttempt.status === 'SEND_TO_USER_INBOX') message = 'Mở ứng dụng TikTok để chỉnh sửa và hoàn tất bài đăng.';
    if (!message && publishAttempt.status === 'PUBLISH_COMPLETE') message = 'TikTok đã xác nhận bài đăng hoàn tất.';
    if (!message) message = 'Trạng thái sẽ được cập nhật sau khi TikTok xử lý.';
    $('#tiktokPublishStatusMessage').textContent = message;
    $('#tiktokCancelSchedule').classList.toggle('is-hidden', publishAttempt.status !== 'SCHEDULED_LOCAL');
    clearTimeout(publishTimer);
    if (!locked && publishBusy.has(publishAttempt.status)) publishTimer = setTimeout(() => refreshPublish(true), 3000);
    controls();
  }

  async function loadLatestPublish(jobId) {
    try {
      const result = await requestJson(`/api/v1/jobs/${encodeURIComponent(jobId)}/tiktok-publishes/latest`);
      if (draft?.job_id === jobId) renderPublish(result);
    } catch (error) { toast(error.message, true); }
  }

  async function refreshPublish(remote = false) {
    if (!publishAttempt) return;
    try {
      const suffix = remote && publishAttempt.remote_publish_id ? '?refresh=true' : '';
      renderPublish(await requestJson(`/api/v1/tiktok-publishes/${publishAttempt.attempt_id}${suffix}`));
    } catch (error) { toast(error.message, true); }
  }

  function updatePublishMode() {
    const mode = $('#tiktokPublishMode').value;
    $('#tiktokScheduleField').classList.toggle('is-hidden', mode !== 'SCHEDULE');
    $('#tiktokPrivacyField').classList.toggle('is-hidden', mode === 'INBOX_DRAFT');
    $('#tiktokOfficialPublish').textContent = mode === 'SCHEDULE' ? 'Đặt lịch đăng' : mode === 'INBOX_DRAFT' ? 'Gửi bản nháp tới TikTok' : 'Đăng ngay qua TikTok API';
    $('#tiktokReviewConsent').checked = false;
    publishKey = null;
    controls();
  }

  async function submitOfficialPublish() {
    if ($('#tiktokOfficialPublish').disabled || !draft) return;
    publishSubmitting = true; controls();
    publishKey ||= crypto.randomUUID();
    const mode = $('#tiktokPublishMode').value;
    const body = new FormData();
    body.set('caption', $('#tiktokCaption').value);
    body.set('mode', mode);
    body.set('privacy_level', $('#tiktokPrivacy').value);
    body.set('reviewed', 'true');
    body.set('idempotency_key', publishKey);
    body.set('timezone_name', Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC');
    if (mode === 'SCHEDULE') body.set('scheduled_for', new Date($('#tiktokScheduleAt').value).toISOString());
    try {
      const result = await requestJson(`/api/v1/jobs/${encodeURIComponent(draft.job_id)}/tiktok-publishes`, { method: 'POST', body });
      renderPublish(result);
      $('#tiktokReviewConsent').checked = false;
      toast(mode === 'SCHEDULE' ? 'Đã lưu lịch đăng TikTok.' : mode === 'INBOX_DRAFT' ? 'Đang gửi bản nháp tới TikTok.' : 'Đang đăng video lên TikTok.');
    } catch (error) { toast(error.message, true); }
    finally { publishSubmitting = false; controls(); }
  }

  async function prepare() {
    if ($('#tiktokPrepareButton').disabled) return;
    submitting = true;
    const current = draft;
    controls();
    try {
      const body = new FormData();
      body.set('caption', $('#tiktokCaption').value);
      body.set('reviewed', 'true');
      await requestJson(`/api/v1/jobs/${encodeURIComponent(current.job_id)}/tiktok-browser/prepare`, { method: 'POST', body });
      $('#tiktokReviewConsent').checked = false;
      await refresh();
      toast('Đã nhận yêu cầu chuẩn bị. Theo dõi và hoàn tất trong TikTok Web.');
    } catch (error) {
      toast(error.message, true);
      await refresh();
    } finally { submitting = false; controls(); }
  }

  $('#tiktokApiConnect').addEventListener('click', async () => {
    try {
      const result = await requestJson('/api/v1/tiktok-auth/connect');
      location.assign(result.authorization_url);
    } catch (error) { toast(error.message, true); }
  });
  $('#tiktokApiDisconnect').addEventListener('click', async () => {
    try { await requestJson('/api/v1/tiktok-auth', { method: 'DELETE' }); await refreshApi(); }
    catch (error) { toast(error.message, true); }
  });

  async function browserAction(path, method = 'POST') {
    try {
      const result = await requestJson(`/api/v1/tiktok-browser/${path}`, { method });
      if (!locked) render(result);
    } catch (error) { toast(error.message, true); }
  }

  $('#tiktokBrowserOpen').addEventListener('click', async () => {
    $('#tiktokBrowserOpen').disabled = true;
    await browserAction('open');
    await refresh();
  });
  $('#tiktokBrowserCheck').addEventListener('click', () => { checkedFrameUrl = null; frameProblem = null; refresh(true); });
  $('#tiktokBrowserLoginQr').addEventListener('click', async () => {
    $('#tiktokBrowserLoginQr').disabled = true;
    await browserAction('login?method=qr');
    await refresh();
  });
  $('#tiktokBrowserLoginEmail').addEventListener('click', async () => {
    // Toggle the email panel open and focus the email field
    const panel = $('#tiktokEmailLoginPanel');
    panel.open = true;
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    setTimeout(() => $('#tiktokLoginEmail').focus(), 300);
  });
  $('#tiktokEmailLoginForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (emailSubmitting) return;
    const email = $('#tiktokLoginEmail').value.trim();
    const password = $('#tiktokLoginPassword').value;
    if (!email || !password) return;
    emailSubmitting = true;
    const statusEl = $('#tiktokEmailStatus');
    const submitBtn = $('#tiktokEmailSubmit');
    submitBtn.disabled = true;
    statusEl.textContent = 'Đang đăng nhập…';
    try {
      const body = new FormData();
      body.set('email', email);
      body.set('password', password);
      const result = await requestJson('/api/v1/tiktok-browser/login-email', { method: 'POST', body });
      render(result);
      const state = result.login_state || '';
      if (state === 'success') {
        statusEl.textContent = '✓ Đăng nhập thành công';
        $('#tiktokLoginPassword').value = '';          // clear password immediately
        $('#tiktokEmailLoginPanel').open = false;       // collapse form
        toast('Đã đăng nhập TikTok bằng email thành công!');
      } else if (state === 'rate_limited') {
        statusEl.textContent = 'TikTok đang giới hạn xác minh';
        toast(result.message, true);
      } else if (state === 'captcha_required') {
        statusEl.textContent = '⚠ Cần xác minh thêm';
        toast(result.message, true);
      } else if (state === 'failed') {
        statusEl.textContent = '✗ Đăng nhập thất bại';
        toast(result.message, true);
      } else {
        statusEl.textContent = '⏳ Đang chờ xác nhận';
        toast(result.message);
      }
      await refresh();
    } catch (error) {
      statusEl.textContent = '✗ Lỗi';
      toast(error.message, true);
    } finally {
      emailSubmitting = false;
      submitBtn.disabled = false;
      // Always clear the password after the attempt
      $('#tiktokLoginPassword').value = '';
    }
  });
  $('#tiktokBrowserLogout').addEventListener('click', () => {
    if (confirm('Xóa phiên đăng nhập TikTok của trình duyệt này?')) browserAction('session', 'DELETE');
  });
  $('#tiktokJobSelect').addEventListener('change', event => {
    if (event.target.value) openDraft(event.target.value);
    else {
      ++requestVersion;
      if (draft) edits.set(draft.job_id, $('#tiktokCaption').value);
      draft = null; loading = false;
      $('#tiktokVideoPreview').pause();
      $('#tiktokDraftEditor').classList.add('is-hidden');
      $('#tiktokDraftEmpty').classList.remove('is-hidden');
      controls();
    }
  });
  $('#tiktokCaption').addEventListener('input', () => { $('#tiktokReviewConsent').checked = false; controls(); });
  $('#tiktokPublishMode').addEventListener('change', updatePublishMode);
  $('#tiktokPrivacy').addEventListener('change', () => { $('#tiktokReviewConsent').checked = false; publishKey = null; controls(); });
  $('#tiktokScheduleAt').addEventListener('change', () => { $('#tiktokReviewConsent').checked = false; publishKey = null; controls(); });
  $('#tiktokReviewConsent').addEventListener('change', controls);
  $('#tiktokOfficialPublish').addEventListener('click', submitOfficialPublish);
  $('#tiktokCancelSchedule').addEventListener('click', async () => {
    if (!publishAttempt || publishAttempt.status !== 'SCHEDULED_LOCAL') return;
    try {
      renderPublish(await requestJson(`/api/v1/tiktok-publishes/${publishAttempt.attempt_id}/cancel`, { method: 'POST' }));
      toast('Đã hủy lịch đăng TikTok.');
    } catch (error) { toast(error.message, true); }
  });
  $('#tiktokPrepareButton').addEventListener('click', prepare);
  $('#tiktokSuggestCaption').addEventListener('click', async () => {
    if (!draft || suggesting) return;
    const version = requestVersion;
    suggesting = true; controls();
    $('#tiktokSuggestCaption').textContent = 'Đang gợi ý…';
    try {
      const result = await requestJson(`/api/v1/jobs/${encodeURIComponent(draft.job_id)}/tiktok-caption`, { method: 'POST' });
      if (locked || version !== requestVersion) return;
      suggestion = result.caption;
      $('#tiktokCaptionSuggestionText').textContent = suggestion;
      $('#tiktokCaptionSuggestion').classList.remove('is-hidden');
    } catch (error) { if (!locked && version === requestVersion) toast(error.message, true); }
    finally { suggesting = false; $('#tiktokSuggestCaption').textContent = '✦ Gợi ý caption'; controls(); }
  });
  $('#tiktokApplyCaption').addEventListener('click', () => {
    if (!draft || !suggestion || status?.attempt) return;
    $('#tiktokCaption').value = suggestion;
    edits.set(draft.job_id, suggestion);
    $('#tiktokReviewConsent').checked = false;
    $('#tiktokCaptionSuggestion').classList.add('is-hidden');
    controls();
  });
  $('#tiktokDismissCaption').addEventListener('click', () => $('#tiktokCaptionSuggestion').classList.add('is-hidden'));
  $('#tiktokCopyCaption').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#tiktokCaption').value); toast('Đã sao chép caption'); }
    catch { $('#tiktokCaption').select(); toast('Hãy sao chép phần caption đã chọn.'); }
  });
  $('#tiktokActiveDraft').addEventListener('click', () => status?.attempt && openDraft(status.attempt.job_id));
  $('#tiktokResolveConsent').addEventListener('change', controls);
  $('#tiktokPublishButton').addEventListener('click', async () => {
    if (status?.attempt?.state !== 'awaiting_review' || !$('#tiktokResolveConsent').checked) return;
    $('#tiktokPublishButton').disabled = true;
    const body = new FormData(); body.set('reviewed', 'true');
    try {
      const result = await requestJson(`/api/v1/tiktok-browser/attempts/${status.attempt.id}/publish`, { method: 'POST', body });
      render(result);
      toast('Đã bấm Đăng trên TikTok. Kiểm tra thông báo kết quả trong khung TikTok Studio.');
    } catch (error) { toast(error.message, true); await refresh(); }
  });
  $('#tiktokResolveButton').addEventListener('click', async () => {
    if (!status?.attempt || !$('#tiktokResolveConsent').checked) return;
    $('#tiktokResolveButton').disabled = true;
    const body = new FormData(); body.set('reviewed', 'true');
    try {
      const result = await requestJson(`/api/v1/tiktok-browser/attempts/${status.attempt.id}/resolve`, { method: 'POST', body });
      render(result);
      $('#tiktokReviewConsent').checked = false;
      if (draft) await openDraft(draft.job_id);
      toast('Đã kết thúc lượt chuẩn bị.');
    } catch (error) { toast(error.message, true); controls(); }
  });

  return {
    refresh: () => { locked = false; return refresh(); }, openDraft,
    enter() {
      locked = false;
      clearTimeout(timer); clearInterval(apiTimer);
      loadVideos(); refresh(); refreshApi();
      if (status) render(status);
      scheduleStatusRefresh();
      apiTimer = setInterval(refreshApi, 15000);
    },
    leave() {
      clearTimeout(timer); timer = null;
      clearTimeout(publishTimer); publishTimer = null;
      clearInterval(apiTimer); apiTimer = null;
      $('#tiktokVideoPreview').pause();
      $('#tiktokReviewConsent').checked = false;
      $('#tiktokBrowserFrame').removeAttribute('src');
    },
    lock() {
      locked = true; ++requestVersion; draft = null; status = null; edits.clear();
      publishAttempt = null; publishKey = null;
      clearTimeout(timer); timer = null;
      clearTimeout(publishTimer); publishTimer = null;
      clearInterval(apiTimer); apiTimer = null;
      $('#tiktokBrowserFrame').removeAttribute('src');
      $('#tiktokVideoPreview').pause();
      $('#tiktokVideoPreview').removeAttribute('src');
      $('#tiktokCaption').value = '';
      $('#tiktokDraftEditor').classList.add('is-hidden');
      $('#tiktokDraftEmpty').classList.remove('is-hidden');
      controls();
    },
  };
}
