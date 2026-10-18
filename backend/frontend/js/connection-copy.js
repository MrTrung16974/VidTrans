// Connection copy shared by every OAuth platform card. Customers never see
// "contact admin" wording; operators are pointed to the admin setup tab.
const isOperator = () => ['owner', 'admin'].includes(document.body.dataset.role);

export function unconfiguredStatus() {
  return isOperator()
    ? 'Chưa bật: vào Quản trị → Kết nối nền tảng để nhập App ID một lần'
    : 'Đang mở kết nối cho nền tảng này, vui lòng quay lại sau';
}

export function unconfiguredTitle() {
  return isOperator()
    ? 'Khai báo ứng dụng OAuth một lần trên trang Quản trị, sau đó mọi user tự kết nối'
    : 'Nền tảng sẽ sẵn sàng sớm';
}
