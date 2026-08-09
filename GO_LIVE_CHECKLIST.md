# Go-Live Checklist

Checklist xác nhận **thủ công** trước khi đặt `BINANCE_LIVE_TRADING=true`.
Không có bước nào ở đây được tự động hoá — mỗi mục phải được người vận hành
tự kiểm tra và tick, đúng theo nguyên tắc "mặc định testnet/dry-run, bật live
phải là hành động rõ ràng" trong `PLAN.md`.

## 1. Bằng chứng trước khi live
- [ ] Backtest (`backtest/engine.py`) đã chạy trên ≥ 90 ngày dữ liệu cho từng symbol dự định trade, kết quả win rate / R:R / max drawdown được ghi lại và chấp nhận được
- [ ] Paper trading: đã chạy `scheduler/loop.py` trên **testnet** liên tục ít nhất vài tuần, không có lỗi treo vòng lặp, không có lệch state (SQLite) so với vị thế thật trên exchange
- [ ] Đã review log `alert/alerts.jsonl` của giai đoạn paper trading — không có tín hiệu/risk decision nào bất thường không giải thích được

## 2. API key & quyền hạn
- [ ] API key live (`BINANCE_FUTURES_LIVE_API_KEY`) chỉ có quyền **Futures trade**, KHÔNG có quyền rút tiền (withdraw)
- [ ] API key live giới hạn theo IP whitelist (nếu Binance hỗ trợ cho tài khoản này)
- [ ] API key live và key testnet là hai cặp **hoàn toàn tách biệt**, không dùng chung `.env` giữa máy dev và máy chạy live
- [ ] `.env` chứa key live không được commit vào git (kiểm tra `.gitignore`)

## 3. Risk limits
- [ ] `RISK_MAX_POSITION_PCT`, `RISK_MAX_CONCURRENT_POSITIONS`, `RISK_MAX_DAILY_LOSS_PCT`, `RISK_MIN_CONFIDENCE` trong `.env` đã được set ở mức thận trọng cho vốn thật (không dùng giá trị mặc định/test)
- [ ] Đã test thủ công: khi daily loss chạm `RISK_MAX_DAILY_LOSS_PCT`, `risk_manager.evaluate()` reject đúng như kỳ vọng (có thể giả lập bằng cách chèn tạm 1 dòng `positions` đã đóng lỗ vào `trading_state.db`)
- [ ] Vốn phân bổ cho bot là vốn có thể chấp nhận mất toàn bộ — không phải vốn thiết yếu

## 4. Kill switch
- [ ] Đã test `python scheduler/loop.py --kill "test"` rồi chạy `--once` — xác nhận log ghi "Kill switch active ... no new orders this cycle"
- [ ] Đã test `python scheduler/loop.py --release` — bot tiếp tục trade bình thường sau đó
- [ ] Đã test kill switch qua API: `POST /stop` rồi `GET /status` xác nhận `kill_switch_active=true`; `POST /start` để release
- [ ] Kill switch **không** đóng các vị thế đang mở, chỉ chặn lệnh mới — người vận hành biết rõ điều này và có quy trình đóng vị thế thủ công qua Binance app/web nếu cần khẩn cấp
- [ ] Telegram alert (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`) đã cấu hình và test nhận được thông báo `kill_switch` thật

## 5. Vận hành
- [ ] Có người trực theo dõi Telegram alert trong ít nhất tuần đầu tiên chạy live (không chạy "quên luôn" ngay từ đầu)
- [ ] Đã xác định rõ quy trình rollback: nếu phát hiện bug, engage kill switch ngay (`--kill` hoặc `POST /stop`) rồi mới điều tra
- [ ] `scheduler/loop.py` chạy trong process có giám sát (systemd/supervisor/tmux+monitor) để tự restart nếu crash, nhưng **không** tự động release kill switch khi restart

## 6. Xác nhận cuối
- [ ] Người chịu trách nhiệm cuối cùng đã đọc và tick đủ tất cả mục trên
- [ ] `BINANCE_LIVE_TRADING=true` chỉ được set sau khi tick hết checklist này — không set trước "để test luôn cho nhanh"
