# Sửa lỗi SSL khi Docker tải dependency

Lỗi `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain` thường xảy
ra khi proxy/firewall của công ty giải mã HTTPS bằng CA nội bộ. Không sửa bằng cách tắt
xác minh SSL hoặc thêm `trusted-host`.

## Mạng thông thường

Dockerfile đã cài và cập nhật `ca-certificates`. Thử build lại không dùng cache:

```bash
docker build --no-cache -t vidtrans .
```

## Mạng có CA nội bộ

Xuất CA root/intermediate của tổ chức thành file PEM/CRT, sau đó truyền bằng BuildKit secret:

```bash
docker build \
  --secret id=corporate_ca,src=/duong-dan/ca-noi-bo.crt \
  -t vidtrans .
```

Dockerfile ghép CA này với bundle hệ thống chỉ trong bước chạy `pip`, sau đó xóa file tạm.
Không đặt CA vào repository, không truyền bằng build argument và không dùng
`PIP_TRUSTED_HOST`.

Nếu đang dùng Docker Compose:

```bash
export VIDTRANS_CORPORATE_CA_FILE=/duong-dan/ca-noi-bo.crt
docker compose -f docker-compose.yml -f docker-compose.corporate-ca.yml build vidtrans
docker compose up -d
```

File override chỉ gắn CA làm build secret cho service `vidtrans`; các service khác và cấu
hình Compose mặc định không bị thay đổi.

Nếu vẫn lỗi, kiểm tra CA cung cấp có đủ root/intermediate chain và proxy có chặn cả
`download.pytorch.org` lẫn hostname CDN chuyển hướng `download-r2.pytorch.org` hay không.

