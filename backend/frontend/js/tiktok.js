export function createTikTokWorkspace({ requestJson, toast }) {
  const $ = selector => document.querySelector(selector);
  let status = null, draft = null, timer = null, refreshing = false, loading = false, submitting = false;
  let suggestion = null, suggesting = false;
  let requestVersion = 0, locked = false, attemptId = null;
  const edits = new Map();
  const busyStates = new Set(['queued', 'opening', 'uploading']);
  const labels = { queued: 'Đang chuẩn bị', opening: 'Đang mở TikTok Studio', uploading: 'Đang chuyển video', awaiting_review: 'Chờ bạn hoàn tất trên TikTok', needs_login: 'Cần đăng nhập TikTok', needs_review: 'Cần bạn kiểm tra' };

  function controls() {
    const caption = $('#tiktokCaption').value;
    $('#tiktokCaptionCount').textContent = `${caption.length.toLocaleString('vi-VN')} / 2.200`;
    $('#tiktokPrepareButton').disabled = locked || submitting || loading || !draft || !status?.available || Boolean(status?.attempt) || !caption.trim() || caption.length > 2200 || !$('#tiktokReviewConsent').checked;
    $('#tiktokSuggestCaption').disabled = locked || loading || suggesting || !draft || Boolean(status?.attempt);
    $('#tiktokResolveButton').disabled = !$('#tiktokResolveConsent').checked;
  }

  function render(result) {
    status = result;
    $('#tiktokBrowserStatus').textContent = result.message;
    $('#tiktokSetupStatus').textContent = result.available ? 'Trình duyệt sẵn sàng. Duyệt video tại khu vực Đăng TikTok.' : 'Kết nối tài khoản tại khu vực Đăng TikTok sau khi trình duyệt sẵn sàng.';
    $('#tiktokBrowserOpen').disabled = !result.available || busyStates.has(result.attempt?.state);
    $('#tiktokBrowserCheck').disabled = !result.available;
    $('#tiktokBrowserLoginRetry').disabled = !result.available || Boolean(result.attempt);
    $('#tiktokBrowserLogout').disabled = !result.available || Boolean(result.attempt);
    const frame = $('#tiktokBrowserFrame');
    const external = $('#tiktokBrowserExternal');
    let browserUrl = null;
    if (result.browser_url) {
      const candidate = new URL(result.browser_url, location.href);
      if (['http:', 'https:'].includes(candidate.protocol)) browserUrl = candidate.href;
    }
    if (!browserUrl) {
      frame.removeAttribute('src');
      frame.classList.add('is-hidden');
      $('#tiktokBrowserPlaceholder').classList.remove('is-hidden');
      $('#tiktokBrowserHelp').textContent = 'Trình duyệt chưa sẵn sàng. Khởi động dịch vụ TikTok trên máy chủ rồi bấm Kiểm tra phiên.';
      external.classList.add('is-hidden');
      // Keep a recovery action available even when the sidecar starts later.
      $('#tiktokBrowserCheck').disabled = false;
    } else {
      external.href = browserUrl;
      external.classList.remove('is-hidden');
      if (location.hash === '#tiktok' && !locked) {
        if (frame.getAttribute('src') !== browserUrl) frame.src = browserUrl;
        frame.classList.remove('is-hidden');
        $('#tiktokBrowserPlaceholder').classList.add('is-hidden');
      }
    }
    const attempt = result.attempt;
    if (attemptId !== attempt?.id) $('#tiktokResolveConsent').checked = false;
    attemptId = attempt?.id;
    $('#tiktokAttemptPanel').classList.toggle('is-hidden', !attempt);
    if (attempt) {
      $('#tiktokAttemptTitle').textContent = labels[attempt.state] || 'Kiểm tra TikTok Studio';
      $('#tiktokAttemptMessage').textContent = attempt.message;
      $('#tiktokResolveControls').classList.toggle('is-hidden', busyStates.has(attempt.state));
    }
    controls();
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
      $('#tiktokPreviousAttempt').classList.toggle('is-hidden', !data.latest_attempt);
      $('#tiktokPreviousAttempt').textContent = 'Video này đã có lượt chuẩn bị trước. Kiểm tra tài khoản TikTok để tránh đăng trùng trước khi tải lại.';
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
      toast('Đã nhận yêu cầu chuẩn bị. Theo dõi và hoàn tất trong TikTok Studio.');
    } catch (error) {
      toast(error.message, true);
      await refresh(); // A lost response may still have started an attempt.
    } finally { submitting = false; controls(); }
  }

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
  $('#tiktokBrowserCheck').addEventListener('click', () => refresh(true));
  $('#tiktokBrowserLoginRetry').addEventListener('click', async () => {
    $('#tiktokBrowserLoginRetry').disabled = true;
    await browserAction('login');
    await refresh();
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
  $('#tiktokReviewConsent').addEventListener('change', controls);
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
  $('#tiktokResolveButton').addEventListener('click', async () => {
    if (!status?.attempt || !$('#tiktokResolveConsent').checked) return;
    $('#tiktokResolveButton').disabled = true;
    const body = new FormData(); body.set('reviewed', 'true');
    try {
      const result = await requestJson(`/api/v1/tiktok-browser/attempts/${status.attempt.id}/resolve`, { method: 'POST', body });
      render(result);
      $('#tiktokReviewConsent').checked = false;
      if (draft) await openDraft(draft.job_id);
      toast('Đã kết thúc lượt chuẩn bị. Trạng thái đăng bài được kiểm tra trên TikTok.');
    } catch (error) { toast(error.message, true); controls(); }
  });

  return {
    refresh: () => { locked = false; return refresh(); }, openDraft,
    enter() {
      locked = false;
      clearInterval(timer);
      loadVideos(); refresh();
      if (status) render(status);
      timer = setInterval(() => refresh(), 3000);
    },
    leave() {
      clearInterval(timer); timer = null;
      $('#tiktokVideoPreview').pause();
      $('#tiktokReviewConsent').checked = false;
      $('#tiktokBrowserFrame').removeAttribute('src');
    },
    lock() {
      locked = true; ++requestVersion; draft = null; status = null; edits.clear();
      clearInterval(timer); timer = null;
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
