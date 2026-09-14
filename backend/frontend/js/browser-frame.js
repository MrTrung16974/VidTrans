// Browser desktop pages only. Never embed TikTok's website itself.
export function resolveTikTokFrameUrl(raw, pageHref) {
  if (!raw) return null;
  const page = new URL(pageHref);
  let target;
  try { target = new URL(raw, page); } catch { return null; }
  if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password) return null;
  const loopback = host => ['localhost', '127.0.0.1', '[::1]'].includes(host);
  // Old .env files can retain the development noVNC URL on a deployed VPS.
  if (!loopback(page.hostname) && loopback(target.hostname)) {
    target = new URL('/tiktok-browser/vnc_lite.html', page);
  }
  const proxied = target.origin === page.origin && target.pathname === '/tiktok-browser/vnc_lite.html';
  const local = loopback(page.hostname) && loopback(target.hostname) && target.pathname === '/vnc_lite.html';
  if (!proxied && !local) return null;
  if (page.protocol === 'https:' && target.protocol !== 'https:') return null;
  target.searchParams.set('path', proxied ? 'tiktok-browser/websockify' : 'websockify');
  target.searchParams.set('scale', 'true');
  return target.href;
}

export function frameResponseProblem(response) {
  if (response.status === 401 || response.status === 403) return 'Phiên quản trị chưa được cấp quyền mở trình duyệt. Hãy đăng nhập lại VidTrans.';
  if (!response.ok) return `Không tải được trình duyệt TikTok (HTTP ${response.status}). Kiểm tra dịch vụ tiktok-browser và cấu hình Nginx.`;
  const csp = response.headers.get('content-security-policy') || '';
  const xfo = response.headers.get('x-frame-options') || '';
  if (/frame-ancestors\s+'none'/i.test(csp) || /\bDENY\b/i.test(xfo)) {
    return 'Máy chủ đang chặn nhúng khung trình duyệt. Cập nhật cấu hình Nginx cho /tiktok-browser/ rồi tải lại. Bạn có thể dùng “Mở rộng” trong lúc chờ.';
  }
  return null;
}
