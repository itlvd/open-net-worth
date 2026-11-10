# CI cho fork: build Docker AMD64 và ARM64

Fork này chỉ publish server image; không build hoặc publish desktop, wheel hay release
của upstream. `Dockerfile` vẫn dùng lockfile và hai TypeScript contract trong `desktop/`
vì chúng là dependency của server/web, không phải build Electron.

## Flow

1. Pull request: CI hiện có (privacy, lint, typecheck, tests, frontend, wheel smoke)
   và Docker build/boot trên hai runner native `ubuntu-24.04` và `ubuntu-24.04-arm`.
   PR dùng token read-only, không login registry hoặc push image.
2. Push/merge vào `main`: CI phải thành công. `workflow_run` chỉ nhận run từ sự kiện
   `push`, branch `main`, chính repo này; checkout đúng `head_sha` đã qua CI.
3. Build cả `linux/amd64` và `linux/arm64`, boot từng image, probe API/SPA/docs.
   Push đúng image đã test, tạo multi-arch manifest trên GHCR.
4. Publish `ghcr.io/itlvd/open-net-worth:sha-<full-commit-sha>` và `latest`.
   `latest` chỉ cập nhật nếu commit vẫn là HEAD của main. Các tag `build-*` là
   artifact từng kiến trúc. Khi sử dụng image, ưu tiên digest để cố định nội dung.

Không có path filter: thay đổi backend/frontend/docs/lockfile đều được build.
Giữ CI hiện có, kể cả wheel smoke, để không mất regression coverage; wheel không
được publish. Xóa workflow desktop/release/mirror của upstream khỏi fork để không
build desktop hay publish nhầm sang tài nguyên upstream.

Pipeline này dừng ở build/test/publish image; không chứa job deploy, SSH scripts,
production Environment, server credentials hoặc cờ AUTO_DEPLOY.

## GitHub setup

- Bật Actions cho fork nếu GitHub đang disable. Merge PR build vào `main` để đăng ký
  workflow; `workflow_run` được đọc từ default branch.
- Giữ branch protection/ruleset cho main, yêu cầu CI và hai job Docker của PR.
  Không cho người lạ quyền write workflows hoặc quyền merge không review.
- Thêm repository Secret `PRIVACY_DENYLIST`: danh sách định danh riêng cần ngăn commit,
  mỗi dòng một mục. CI upstream bắt buộc secret này trên push main; không bỏ privacy gate.
- Package GHCR được publish bằng `GITHUB_TOKEN` với `packages: write`; không cần PAT để
  build. Sau lần publish đầu, xác nhận package liên kết repo này và Actions có write access.
  Package public/private là cấu hình riêng với repo public/private.
- Không đưa runtime credentials vào source, build args hoặc public log. GitHub Variables
  không được mask; mọi giá trị cần giữ kín phải dùng Secrets.

## Kiểm chứng

PR phải có hai Docker native smoke jobs xanh. Sau merge cần CI xanh, rồi job publish
xác minh manifest có đúng hai platforms Linux AMD64 và ARM64. Docker job summary cung
cấp image digest. Production server có thể dùng digest này sau khi tự cấu hình deployment.
