# Build Docker AMD64 và ARM64 từ fork

Pull request chạy Docker smoke test trên hai runner native. Khi push tag `docker-*`,
GitHub Actions build từ commit được tag, khởi động từng image và kiểm tra API, giao diện
web và trang docs. Khi cả hai bản pass, GHCR nhận một image có manifest cho Linux AMD64
và ARM64. Docker chọn kiến trúc phù hợp khi pull.

## Publish image

Bật GitHub Actions trên fork nếu cần. Sau khi workflow có trên `main`, tag commit
bạn muốn deploy và push tag lên fork.

```bash
git switch main
git pull --ff-only origin main
git tag -a docker-1.0.0 -m "Docker image 1.0.0"
git push origin refs/tags/docker-1.0.0
```

Workflow **Docker** dùng `GITHUB_TOKEN` với quyền `packages: write`. Image có cùng tên tag
với Git, ví dụ `ghcr.io/itlvd/open-net-worth:docker-1.0.0`. Actions hiển thị cả digest
để bạn có thể deploy đúng nội dung đã build. Chờ job **publish** xanh trước khi pull.

## Deploy thủ công

Dùng `docker-compose.yml` từ fork. Đặt `AUTH_PASSWORD` trong `.env` trên server, rồi thêm
dòng image dưới đây.

```dotenv
ONW_IMAGE=ghcr.io/itlvd/open-net-worth:docker-1.0.0
```

Nếu package GHCR là private, đăng nhập `ghcr.io` trên server bằng PAT classic có
`read:packages` và quyền đọc package. Package public có thể pull trực tiếp.

```bash
docker compose pull app
docker compose up -d --no-build --wait app
```

Khi nâng version, tạo tag mới và cập nhật `ONW_IMAGE`. Giữ Compose project và các volumes
hiện có để app tiếp tục dùng dữ liệu đã lưu.
