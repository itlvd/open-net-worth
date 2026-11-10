# Auto-deploy server sau khi publish Docker image

PR deploy này dựa trên pipeline build Docker riêng, được mô tả trong
[CI guide](CI.md). Merge PR CI trước; giữ PR deploy ở Draft đến khi server sẵn sàng.

## Flow deployment

Sau khi CI thành công và Docker job đã publish manifest AMD64/ARM64, repository
Variable `AUTO_DEPLOY=true` mới cho phép gọi job dùng Environment `production`.
Bỏ qua commit đã bị main vượt qua; deploy được serialize, không cancel giữa chừng.
Server pull image bằng digest, kiểm tra cấu hình và volumes, dừng writer, backup,
cập nhật Compose và chờ Docker healthcheck thành công. Không rebuild trên server.

## GitHub setup cho deployment

- Tạo Environment `production`, giới hạn deployment branch là `main`. Muốn deploy
  hoàn toàn tự động thì không bật required reviewer; bật reviewer nếu cần approval gate.
- `AUTO_DEPLOY` là **repository Variable**, mặc định không có thì chỉ build/publish.
- Workflow manual **Deploy server** cũng yêu cầu main và Environment production.
- Registry access và `PRIVACY_DENYLIST` của CI được cấu hình theo [CI guide](CI.md).

**Variables không được mask.** Mọi giá trị muốn giữ kín phải nằm trong Secrets hoặc
chỉ trên server. Không paste secret vào issue/PR/chat/source code. Job production
không có environment URL, không in SSH diagnostics hoặc app logs ra log public.
Masking không phải bảo đảm tuyệt đối: không bật debug/xtrace hoặc in dump environment.
IP của một dịch vụ public vẫn có thể bị suy ra qua DNS/network; Secrets chỉ giữ nó
khỏi source và log thông thường, không tạo tính ẩn danh cho server.

### Environment Secrets: `production`

| Secret | Nội dung |
|---|---|
| `DEPLOY_HOST` | Hostname hoặc IP SSH server, không có scheme |
| `DEPLOY_PORT` | SSH port, bỏ trống dùng 22 |
| `DEPLOY_USER` | User SSH chuyên dùng cho deployment |
| `DEPLOY_PATH` | Thư mục tuyệt đối đã tạo trên server; chỉ chữ/số, `_`, `-`, `/` |
| `DEPLOY_SSH_KEY` | Private key OpenSSH riêng cho deployment, không passphrase |
| `DEPLOY_KNOWN_HOSTS` | Dòng known_hosts đầy đủ có host key đã verify; port khác 22 dùng `[host]:port` |

Lấy host key qua server console/kênh đã tin cậy, đối chiếu fingerprint. Không chỉ chạy
`ssh-keyscan` trên runner rồi tin kết quả. Key deploy không dùng chung với key cá nhân.
Giới hạn authorized_keys bằng `restrict` (không port forwarding/PTY/agent forwarding).
User cần quyền Docker; quyền này tương đương root trên server, chỉ cấp cho key tin cậy.

## Chuẩn bị server một lần

Yêu cầu Linux AMD64 hoặc ARM64, Docker Engine, Compose v2 có `up --wait`, `flock`,
`python3`, Bash và OpenSSH/SFTP. Cho phép runner GitHub kết nối SSH qua firewall hoặc
một network route được quản lý riêng. Workflow này dùng SSH trực tiếp, không tự mở port.

1. Tạo deploy user và thư mục deploy riêng, owner là deploy user, mode `700`.
   Cài public SSH key vào authorized_keys. Không chạy Actions self-hosted runner trên
   production server cho pull request của repo public.
2. Tạo `.env` **trực tiếp trên server**, mode `600`, trong thư mục deploy. Nội dung
   tối thiểu bên dưới. Thêm API keys và các runtime settings vào đây hoặc nhập trong app;
   không commit `.env`, không đưa chúng qua Docker build args.
3. Nếu package GHCR private: login trên server bằng account/token chỉ có `read:packages`,
   dùng `docker login ghcr.io --password-stdin`. Dùng credential helper nếu có; không
   đặt registry token trên CLI argument hoặc trong repo. Với package public không cần login.
4. Cấu hình reverse proxy TLS trên server trỏ tới loopback port. Compose không publish
   app ra mọi network interface. Domain/TLS credential do server quản lý riêng.
5. Chạy workflow **Deploy server** trên main với digest từ Docker job summary để thử
   lần đầu. Sau khi kiểm tra app và backup, bật `AUTO_DEPLOY=true`.

```dotenv
AUTH_PASSWORD=replace-with-a-long-random-password
PORT=8080
```

`PORT` là host port; bên trong container vẫn là 8080. Password chỉ seed khi chưa có
password trong data; đổi password đã lưu bằng Settings của app.

Flow backup này chỉ hỗ trợ SQLite và bốn named volumes mặc định. Không đặt
`ONW_DATABASE_URL`, `DATA_DIR`, `CACHE_DIR`, `LOGS_DIR` trong `.env`. `ONW_ROOT` được cố
định về volume mặc định. Với external PostgreSQL/custom storage cần flow backup riêng;
script sẽ từ chối cấu hình database/path override.

### Server đang chạy bản cũ

Không chạy deployment đầu tiên đè lên một installation chưa kiểm tra. Script từ chối
container `open-net-worth` thuộc Compose project khác hoặc không mount đúng bốn volumes.
Nếu installation cũ có tên container khác, phải chủ động dừng/migrate trước để tránh hai
writer. Kiểm tra data, working folder, volumes, Compose project, proxy port và credentials
qua server console. Backup và thử restore trước, rồi migrate về project `open-net-worth`,
service `app`, container `open-net-worth` và các volume trong `deploy/production/compose.yml`.
Không đổi tên/xóa volume đang chứa dữ liệu chỉ để vượt qua kiểm tra.

## Backup, failure và recovery

Server pull trước khi dừng app để giảm downtime. Sau khi app dừng, script archive bốn
volume vào `backups/`, gồm database, file và CLI credentials; chỉ lưu trên server, quyền
owner-only, không upload Actions artifacts. Phải dự trù dung lượng, retention và backup
mã hóa offsite riêng. Script không tự xóa backup cũ. Đây là deploy có downtime, không phải
zero-downtime deployment.

Server giữ `deploy.log`, `backup-status`, `previous-image`, `desired-image`,
`current-image` (digest cuối cùng healthy), `compose.previous.yml` và snapshot timestamp.
Nếu fail trước bước stop, bản đang chạy được giữ. Nếu backup/update/healthcheck fail sau
stop, workflow báo fail; app có thể đang dừng hoặc unhealthy. Đọc log qua SSH riêng;
không paste log chứa token lên GitHub. Không có tự động downgrade database.

- Nếu chưa chạy migration, có thể dùng workflow manual để redeploy digest healthy cũ.
- Nếu migration đã chạy, bản cũ có thể từ chối database mới. Dừng app, snapshot trạng thái
  lỗi để giữ dữ liệu mới, restore **đúng bộ backup trước deploy** và Compose/image cũ qua
  server console, rồi probe health và kiểm tra data. Restore có thể mất ghi mới sau snapshot;
  phải đánh giá trước. Không restore khi app còn ghi, không chạy `down -v`.
- Workflow manual chỉ chạy trên main; nhập `sha256:<64 hex>` của image thuộc package fork.
  Giữ digest cũ trong GHCR để recovery. Nó dùng scripts/Compose từ main hiện tại; nếu thay
  đổi deployment contract thì recovery về Compose cũ phải làm trên server console.

## Kiểm chứng

PR phải có hai Docker native smoke jobs xanh. Sau merge cần CI xanh, manifest chứa đúng
hai platforms và deploy healthcheck thành công. Chọn digest từ summary, không copy một
tag mutable. Thử thêm failure case: host key sai phải fail trước khi deploy; healthcheck
lỗi phải báo failure và giữ snapshot; PR không được xuất hiện production deployment.
