// Shown on a platform card while no OAuth app (App ID / Secret) is available.
// Users fix this themselves in "Cấu hình ứng dụng của bạn"; no admin needed.
export function unconfiguredStatus() {
  return 'Chưa có App ID: mở "⚙️ Cấu hình ứng dụng của bạn" phía trên để nhập';
}

export function unconfiguredTitle() {
  return 'Nhập App ID / Secret của bạn ở mục Cấu hình ứng dụng, sau đó bấm Kết nối';
}
