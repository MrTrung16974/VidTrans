const ACTIVE_STATUSES = new Set(['READY', 'CLAIMED', 'UPLOADING', 'PROCESSING_REMOTE', 'SUBMITTING', 'NEEDS_RECONCILIATION', 'SCHEDULED_LOCAL']);
const STATUS_LABELS = {
  READY: 'Sẵn sàng', SCHEDULED_LOCAL: 'Đã đặt lịch', CLAIMED: 'Đang nhận lịch',
  UPLOADING: 'Đang gửi Reel', PROCESSING_REMOTE: 'Instagram đang xử lý', SUBMITTING: 'Đang xuất bản',
  PUBLISHED: 'Đã đăng Reel', NEEDS_RECONCILIATION: 'Đang đối soát', FAILED: 'Đăng thất bại', CANCELLED: 'Đã hủy',
};

export function createInstagramWorkspace({ requestJson, toast, getDraft, getCaption }) {
  const $ = selector => document.querySelector(selector);
  let auth = null, attempt = null, key = null, timer = null, submitting = false, active = false;
  const selectedOption = () => $('#instagramAccount').selectedOptions?.[0] || null;
  const selectedAccount = () => selectedOption()?.dataset.accountId || $('#instagramAccount').value || auth?.account_id || '';
  const selectedConnection = () => selectedOption()?.dataset.connectionId || auth?.connection_id || '';

  function controls() {
    const draft = getDraft(), caption = getCaption(), mode = $('#instagramPublishMode').value;
    const scheduleValid = mode !== 'SCHEDULE_LOCAL' || Boolean($('#instagramScheduleAt').value);
    $('#instagramScheduleField').classList.toggle('is-hidden', mode !== 'SCHEDULE_LOCAL');
    $('#instagramPublish').textContent = mode === 'SCHEDULE_LOCAL' ? 'Đặt lịch Instagram Reel' : 'Đăng Instagram Reel';
    $('#instagramPublish').disabled = submitting || !draft || !auth?.connected || !selectedAccount()
      || !caption.trim() || caption.length > 2200 || !scheduleValid
      || !$('#instagramReviewConsent').checked || ACTIVE_STATUSES.has(attempt?.status);
    $('#instagramCancel').classList.toggle('is-hidden', attempt?.status !== 'SCHEDULED_LOCAL');
  }

  function renderAuth(result) {
    auth = result;
    $('#instagramStatus').textContent = result.connected
      ? `Đã kết nối${result.username ? ` · @${result.username}` : ''}`
      : result.configured ? 'Bấm Kết nối để đăng nhập tài khoản của bạn' : 'Kết nối chưa được quản trị viên kích hoạt';
    $('#instagramConnect').disabled = !result.configured;
    $('#instagramConnect').title = result.configured
      ? 'Đăng nhập và cấp quyền Instagram ngay trên website'
      : 'Chủ hệ thống cần kích hoạt ứng dụng OAuth và public HTTPS một lần';
    $('#instagramConnect').textContent = (result.accounts || []).length ? 'Thêm tài khoản' : 'Kết nối Instagram';
    const select = $('#instagramAccount'), previous = select.value || result.account_id || '';
    select.replaceChildren();
    for (const account of result.accounts || []) {
      const option = new Option(account.username ? `@${account.username}` : account.account_id, account.account_id);
      option.dataset.accountId = account.account_id || '';
      option.dataset.connectionId = account.connection_id || '';
      select.add(option);
    }
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
    select.classList.toggle('is-hidden', select.options.length < 2);
    $('#instagramDisconnect').disabled = !result.connected && !select.value;
    controls();
  }

  async function refreshAuth(accountId = '') {
    try {
      const suffix = accountId ? `?account_id=${encodeURIComponent(accountId)}` : '';
      renderAuth(await requestJson(`/api/v1/instagram-auth${suffix}`));
    } catch (error) { auth = null; $('#instagramStatus').textContent = error.message; controls(); }
  }

  function renderAttempt(value) {
    attempt = value?.status ? value : null;
    $('#instagramPublishStatus').classList.toggle('is-hidden', !attempt);
    if (!attempt) return controls();
    $('#instagramPublishStatusTitle').textContent = STATUS_LABELS[attempt.status] || attempt.status;
    let message = attempt.error_message || '';
    if (!message && attempt.status === 'SCHEDULED_LOCAL') message = `VidTrans sẽ đăng lúc ${new Date(attempt.scheduled_for).toLocaleString('vi-VN')}.`;
    if (!message && attempt.status === 'UPLOADING' && attempt.status_detail?.percent != null) message = `Đang gửi Reel · ${attempt.status_detail.percent}%`;
    if (!message && attempt.status === 'PROCESSING_REMOTE') message = 'Instagram đang tải và xử lý video từ máy chủ.';
    if (!message && attempt.status === 'PUBLISHED') message = 'Instagram đã xác nhận Reel được xuất bản.';
    $('#instagramPublishStatusMessage').textContent = message || 'Trạng thái sẽ tự cập nhật.';
    clearTimeout(timer);
    if (active && ACTIVE_STATUSES.has(attempt.status) && attempt.status !== 'SCHEDULED_LOCAL') timer = setTimeout(refreshAttempt, 5000);
    controls();
  }

  async function loadAttempts() {
    const draft = getDraft();
    if (!draft) return renderAttempt(null);
    try {
      const data = await requestJson(`/api/v1/jobs/${encodeURIComponent(draft.job_id)}/social-publishes`);
      renderAttempt((data.items || []).find(item => item.platform === 'INSTAGRAM') || null);
    } catch (error) { toast(error.message, true); }
  }

  async function refreshAttempt() {
    if (!attempt) return;
    try {
      const remote = attempt.remote_media_id || attempt.remote_post_id;
      renderAttempt(await requestJson(`/api/v1/social-publishes/${attempt.attempt_id}${remote ? '/refresh' : ''}`, remote ? { method: 'POST' } : {}));
    } catch (error) { toast(error.message, true); }
  }

  async function submit() {
    if ($('#instagramPublish').disabled) return;
    const draft = getDraft(); submitting = true; controls(); key ||= crypto.randomUUID();
    const mode = $('#instagramPublishMode').value;
    const payload = {
      platform: 'INSTAGRAM', connection_id: selectedConnection(), mode, caption: getCaption(),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
      options: { share_to_feed: $('#instagramShareToFeed').checked }, reviewed: true,
      idempotency_key: key, scheduled_for: null,
    };
    if (mode === 'SCHEDULE_LOCAL') payload.scheduled_for = new Date($('#instagramScheduleAt').value).toISOString();
    try {
      renderAttempt(await requestJson(`/api/v1/jobs/${encodeURIComponent(draft.job_id)}/social-publishes`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }));
      $('#instagramReviewConsent').checked = false;
      toast(mode === 'SCHEDULE_LOCAL' ? 'Đã lưu lịch đăng Instagram.' : 'Đang gửi Reel lên Instagram.');
    } catch (error) { toast(error.message, true); }
    finally { submitting = false; controls(); }
  }

  $('#instagramConnect').addEventListener('click', async () => {
    try { location.assign((await requestJson('/api/v1/instagram-auth/connect')).authorization_url); }
    catch (error) { toast(error.message, true); }
  });
  $('#instagramDisconnect').addEventListener('click', async () => {
    const accountId = selectedAccount(), connectionId = selectedConnection(); if (!accountId) return;
    try {
      const url = connectionId
        ? `/api/v1/social-connections/${encodeURIComponent(connectionId)}`
        : `/api/v1/instagram-auth?account_id=${encodeURIComponent(accountId)}`;
      await requestJson(url, { method: 'DELETE' }); await refreshAuth();
    }
    catch (error) { toast(error.message, true); }
  });
  $('#instagramAccount').addEventListener('change', event => { key = null; $('#instagramReviewConsent').checked = false; refreshAuth(event.target.value); });
  for (const selector of ['#instagramPublishMode', '#instagramScheduleAt', '#instagramShareToFeed']) {
    $(selector).addEventListener('change', () => { key = null; $('#instagramReviewConsent').checked = false; controls(); });
  }
  $('#instagramReviewConsent').addEventListener('change', controls);
  $('#instagramPublish').addEventListener('click', submit);
  $('#instagramCancel').addEventListener('click', async () => {
    if (attempt?.status !== 'SCHEDULED_LOCAL') return;
    try { renderAttempt(await requestJson(`/api/v1/social-publishes/${attempt.attempt_id}/cancel`, { method: 'POST' })); toast('Đã hủy lịch đăng Instagram.'); }
    catch (error) { toast(error.message, true); }
  });

  return {
    enter() { active = true; refreshAuth(); loadAttempts(); },
    leave() { active = false; clearTimeout(timer); timer = null; },
    setDraft() { key = null; attempt = null; $('#instagramReviewConsent').checked = false; loadAttempts(); controls(); },
    changed() { key = null; $('#instagramReviewConsent').checked = false; controls(); },
    lock() { active = false; clearTimeout(timer); timer = null; auth = null; attempt = null; key = null; controls(); },
  };
}
