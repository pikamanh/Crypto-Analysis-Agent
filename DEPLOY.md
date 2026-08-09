# Deploy Guide — Paper Trading VPS

Mục tiêu: chạy `scheduler/loop.py` (testnet) + dashboard liên tục vài tuần,
theo dõi qua web mà không cần mở máy local, không expose control endpoint
ra internet công khai.

## 1. Chọn VPS

**Đề xuất: DigitalOcean Basic Droplet, region Singapore, 2GB RAM / 1 vCPU
(~$12/tháng)** — Singapore gần Việt Nam nên độ trễ tới Binance API thấp,
và không nằm trong danh sách khu vực Binance hạn chế derivatives (khác Mỹ/EU).
2GB RAM đủ dư cho 2 container Python (scheduler + api) chạy pandas/numpy +
langgraph cả ngày. Có thể dùng tier $6 (1GB) nếu muốn tiết kiệm, nhưng rủi ro
OOM cao hơn khi cả hai container cùng chạy backtest thủ công.

Lựa chọn khác tương đương: Vultr (Singapore), Hetzner CX22 (rẻ hơn nhưng
không có datacenter Singapore — gần nhất là Mỹ/Đức, độ trễ cao hơn).

Khi tạo droplet:
- Image: **Ubuntu 24.04 LTS**
- Authentication: **SSH key** (không dùng password) — thêm public key của bạn
  lúc tạo droplet
- Bật automatic backups nếu muốn an tâm hơn (tốn thêm ~20% chi phí, không bắt buộc)

Sau khi tạo xong, bạn sẽ có 1 địa chỉ IP. Cho tôi biết IP đó + xác nhận SSH
key-based access hoạt động (`ssh root@<ip>` không hỏi password), tôi sẽ chạy
phần cài đặt qua SSH từ đây.

## 2. Những gì sẽ được cài (tôi sẽ làm qua SSH sau khi có access)

1. Cài Docker + Docker Compose plugin trên VPS
2. `git clone` repo này lên VPS (qua HTTPS, không cần deploy key vì repo public
   — nhưng **sau khi bạn xoá lịch sử `.env` leak, xác nhận repo không còn lộ key**)
3. Copy thủ công (không qua git, không qua tôi) 3 file nhạy cảm lên VPS bằng `scp`:
   - `.env` (đã điền đủ `BINANCE_FUTURES_TESTNET_API_KEY/SECRET`,
     `API_USERNAME`/`API_PASSWORD` cho dashboard, `TELEGRAM_BOT_TOKEN` nếu dùng)
   - `credentials.json`, `token.pickle` (Google Sheets — cần nếu bạn gọi tool
     bằng tên coin thay vì symbol)
4. `docker compose up -d` — khởi động `scheduler` (paper trading testnet) +
   `api` (dashboard, chỉ bind `127.0.0.1:8000`, không lộ ra internet)
5. Cấu hình `ufw` chỉ mở port 22 (SSH); port 8000 **không** mở công khai —
   xem cách truy cập dashboard ở mục 3
6. Cấu hình container tự khởi động lại khi VPS reboot (`restart: unless-stopped`
   đã có sẵn trong `docker-compose.yml`, cộng thêm systemd/docker daemon
   tự start khi VPS khởi động lại là mặc định)

## 3. Truy cập dashboard an toàn (không cần domain/TLS)

Vì port 8000 chỉ bind vào `127.0.0.1` trên VPS, cách duy nhất để mở dashboard
từ máy bạn là SSH tunnel:

```bash
ssh -L 8000:localhost:8000 root@<vps-ip>
```

Giữ terminal đó mở, rồi vào `http://localhost:8000` trên trình duyệt máy bạn
— traffic đi qua đường hầm SSH đã mã hoá, không cần thêm domain hay chứng chỉ
TLS cho một bài test vài tuần. Basic Auth (`API_USERNAME`/`API_PASSWORD`)
vẫn là lớp bảo vệ thứ hai nếu sau này bạn quyết định mở port công khai.

## 4. Theo dõi trong lúc chạy

- Dashboard: equity, PNL trong ngày, vị thế mở, nút Halt/Resume (kill switch)
- Log chi tiết: `docker compose logs -f scheduler`
- Alert log: `alert/alerts.jsonl` trên VPS (mount trực tiếp từ repo, xem qua
  `cat`/`tail -f` qua SSH, hoặc thêm Telegram bot token vào `.env` để nhận
  alert real-time trên điện thoại — khuyến khích cho theo dõi vài tuần)

## 5. Sau vài tuần

Tổng hợp kết quả từ `storage/trading_state.db` (số lệnh, win rate, PNL) so
sánh với kỳ vọng từ `backtest/engine.py`, rồi mới xét tới `GO_LIVE_CHECKLIST.md`.
