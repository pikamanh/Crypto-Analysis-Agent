# Agentic AI Crypto Futures Trading Bot — Implementation Plan

## 1. Mục tiêu
Chuyển hướng từ "deep research token" sang xây dựng **bot giao dịch futures** tự động:
- Theo dõi thị trường **liên tục** (không chỉ trả lời theo request) và tự ra tín hiệu
- Tín hiệu giao dịch dựa **chính** vào price action (support/resistance) + option flow; sentiment/economic calendar (tin 3 sao: FOMC/CPI/NFP...) chỉ dùng để cảnh báo rủi ro biến động, không quyết định hướng lệnh
- Output tín hiệu phải **có cấu trúc** (direction, entry zone, stop-loss, take-profit, size, confidence) để bot đọc và hành động được — không chỉ là báo cáo tường thuật cho người đọc
- Có lớp **risk management** (giới hạn size, số lệnh mở, max daily loss) trước khi đặt lệnh
- Chạy **testnet/paper trading trước**, chỉ cân nhắc live khi đã kiểm chứng qua backtest + paper trading
- Research/Market Agent (whitepaper, TVL, market cap) không còn là trọng tâm — giữ lại như tool tra cứu theo yêu cầu (chatbot), không nằm trong vòng lặp ra quyết định của bot

⚠️ Đây là hệ thống chạy tiền thật tiềm năng — mọi thay đổi liên quan tới đặt lệnh/API key có quyền trade phải được xác nhận rõ ràng trước khi bật, và luôn có kill switch tắt bot ngay lập tức.

## 2. Tech stack
- **Ngôn ngữ**: Python 3.11+
- **Agent orchestration**: LangGraph (giữ cho phần phân tích/signal)
- **LLM**: bất kỳ LLM hỗ trợ tool calling (OpenAI-compatible API)
- **Scheduler/loop**: APScheduler hoặc vòng lặp asyncio riêng, chạy mỗi **5 phút** — giới hạn bởi rate limit của Binance API (không poll sát theo từng nến), mỗi lần chạy đọc nến 15m gần nhất đã đóng tại thời điểm đó
- **Execution**: `ccxt` hoặc Binance Futures connector chính thức — **testnet trước**, có cờ bật/tắt live rõ ràng trong config
- **State/persistence**: SQLite (vị thế đang mở, lịch sử lệnh, PnL) — đủ nhẹ cho giai đoạn đầu
- **Alert**: Telegram bot hoặc log file có cấu trúc (JSON) cho mọi tín hiệu + hành động
- **API layer**: FastAPI — dùng để giám sát/điều khiển bot (start/stop, xem vị thế), không phải kênh chính sinh tín hiệu nữa
- **Package/env**: `uv` hoặc `venv` + `requirements.txt`

## 3. Nguồn dữ liệu (free tier)
| Loại dữ liệu | Nguồn | Vai trò |
|---|---|---|
| OHLC / candles (chính, ngắn hạn) | Binance Futures API | **chính** — price action, support/resistance. Rate limit → poll mỗi 5 phút, không real-time từng tick |
| Option flow + GEX/DEX (OI, greeks theo strike, put/call ratio) | Deribit API | **chính** — vùng OI lớn = kháng cự/hỗ trợ; GEX/DEX = vùng dealer có khả năng ghìm/khuếch đại biến động giá |
| Economic calendar (tin 3 sao: FOMC/CPI/NFP...) | ForexFactory calendar JSON | **phụ** — cảnh báo rủi ro biến động, điều chỉnh risk (không đổi hướng) |
| Tin tức crypto / sentiment | CryptoPanic API hoặc RSS | **phụ**, tham khảo thêm |
| Giá / market cap / TVL / whitepaper | CoinGecko, DeFiLlama | **ngoài vòng lặp bot** — chỉ phục vụ tra cứu qua chatbot khi có yêu cầu |
| Social (X/Twitter) | để sau | API trả phí, chưa cần cho MVP bot |

## 4. Kiến trúc mục tiêu
```
Scheduler (vòng lặp mỗi 5 phút — giới hạn bởi rate limit Binance API)
 └─ Analysis Pipeline (LangGraph)
     ├─ Price Action Agent  → OHLC (nến 15m gần nhất), support/resistance, xu hướng (Binance Futures)
     ├─ Option Flow Agent   → OI theo strike, put/call ratio, max pain, GEX/DEX (Deribit)
     ├─ Sentiment Agent     → tin 3 sao sắp/đang diễn ra (ForexFactory) + CryptoPanic phụ
     └─ Signal Agent        → hợp nhất Price Action + Option Flow (chính), Sentiment (điều chỉnh risk)
                              → output có cấu trúc: {direction, entry_zone, stop_loss, take_profit,
                                                      confidence, size_pct, reason}
 └─ Risk Manager
     - Kiểm tra vị thế đang mở, exposure hiện tại, max daily loss, max concurrent positions
     - Approve / reject / điều chỉnh size của tín hiệu từ Signal Agent
 └─ Execution Engine
     - Đặt/đóng/điều chỉnh lệnh qua exchange API (testnet trước, cờ bật live riêng)
 └─ Position/State Tracker (SQLite)
     - Vị thế mở, lịch sử lệnh, PnL
 └─ Monitoring/Alert
     - Telegram/log mọi tín hiệu, quyết định risk, và hành động lệnh

Research/Market Agent (CoinGecko/DeFiLlama) → tra cứu theo yêu cầu, KHÔNG nằm trong vòng lặp trên
Chatbot/API layer → hỏi-đáp tra cứu + điều khiển bot (start/stop, xem vị thế/PnL)
```

## 5. Cấu trúc thư mục đề xuất
```
crypto-researcher/
├── agents/            # research, market, price_action, option_flow, sentiment, signal
├── tools/             # wrapper cho LLM tool-calling + registry.py
│                       #  (coingecko_tools.py, defillama_tools.py, binance_tools.py,
│                       #   deribit_tools.py, calendar_tools.py)
├── data/              # client gọi API + models
│                       #  (coingecko.py, defillama.py, binance.py, deribit.py,
│                       #   economic_calendar.py, indicators.py, sheets.py)
├── risk/              # risk_manager.py — rule kiểm tra size/exposure/daily loss
├── execution/          # execution_engine.py — đặt/đóng lệnh qua exchange API (testnet/live)
├── storage/            # state.py — SQLite: vị thế, lịch sử lệnh, PnL
├── scheduler/           # loop.py — vòng lặp theo timeframe, gọi analysis pipeline
├── backtest/            # engine.py — replay rule-based proxy trên dữ liệu lịch sử Binance
├── api/                # FastAPI app — monitor/control bot
├── tests/
├── requirements.txt
└── README.md
```

## 6. Lộ trình MVP theo giai đoạn

### Giai đoạn 1 — Nền tảng (đã xong, giữ nguyên)
- [x] Setup repo, cấu trúc thư mục, `.env` cho LLM key
- [x] `data/coingecko.py`, `data/defillama.py` + Research Agent
- **Kết quả**: hỏi 1 coin → nhận báo cáo (giá, TVL, mô tả dự án) — nay dùng như tool tra cứu phụ, không còn là lõi hệ thống

### Giai đoạn 2 — Market & Price Action (đã xong, giữ nguyên)
- [x] Market Agent (xu hướng giá/volume)
- [x] Price Action Agent (OHLC, support/resistance, xu hướng kỹ thuật)
- [x] Orchestrator LangGraph ghép Research + Market + Price Action
- **Kết quả**: 1 lệnh gọi → chạy song song 3 agent → gộp báo cáo (nền tảng phân tích tái sử dụng ở Giai đoạn 3)

### Giai đoạn 3 — Option Flow + Sentiment (calendar) + Signal có cấu trúc
- [x] `data/deribit.py`: option chain BTC/ETH (OI, greeks theo strike từ Deribit `public/ticker`, giới hạn theo `max_days_to_expiry` + `strike_range_pct`), put/call OI ratio, max pain — gọi ticker song song (`ThreadPoolExecutor`) + retry/backoff cho 429 để chạy đủ nhanh trong chu kỳ 5 phút
- [x] Tính **GEX** (Σ OI × gamma × spot² × 0.01, quy ước dealer long call / short put) và **DEX** (Σ OI × delta × spot, delta lấy dấu sẵn từ Deribit) theo strike, tổng hợp `total_gex`/`total_dex` + `zero_gamma_level` (nội suy điểm cumulative GEX đổi dấu)
- [x] Thêm `gex_regime` ("dampening"/"amplifying"/"neutral") và `spot_vs_zero_gamma` ("above"/"below") tính sẵn bằng code trong `data/deribit.py` — tránh việc model nhỏ (3B) tự suy dấu `total_gex` rồi đọc ngược (đã xảy ra khi test); Option Flow Agent + Signal Agent giờ dùng trực tiếp 2 field này thay vì tự diễn giải từ số
- [x] `tools/deribit_tools.py`: bọc `data/deribit.py` thành tool cho LLM tool-calling, đăng ký vào `tools/registry.py` (`get_option_flow`)
- [x] Option Flow Agent (`agents/option_flow_agent.py`): vùng OI lớn (kháng cự/hỗ trợ từ option), thiên hướng put/call, vùng GEX dương/âm (dealer ghìm giá vs khuếch đại biến động), zero-gamma level — output có cấu trúc `{trend, reasoning}` như các agent khác, sẵn sàng ghép vào orchestrator
- [x] `data/economic_calendar.py` + `tools/calendar_tools.py`: lọc sự kiện impact="High" (3 sao) từ ForexFactory JSON (`ff_calendar_thisweek.json`), retry/backoff cho 429, đăng ký `get_high_impact_calendar` vào `tools/registry.py`
- [x] Sentiment Agent (`agents/sentiment_agent.py`): `risk_level` (high/elevated/normal) dựa vào cửa sổ theo dõi tin 3 sao — chỉ cảnh báo rủi ro/khung giờ, không suy ra hướng giá
- [x] Signal Agent (`agents/signal_agent.py`): output schema có cấu trúc — `{direction, entry_zone, stop_loss, take_profit, confidence, size_pct, reason}`, tổng hợp Price Action + Option Flow (chính), Sentiment chỉ chỉnh `size_pct`/cảnh báo, không đổi `direction`
- **Kết quả**: mỗi lần chạy pipeline → ra 1 tín hiệu có cấu trúc, máy đọc được, kèm lý do — đã test end-to-end với LLM thật (xem ghi chú test bên dưới)

### Giai đoạn 4 — Risk Manager + Execution (testnet) + Scheduler
- [x] `risk/risk_manager.py`: rule max size theo % vốn, max số lệnh mở đồng thời, max daily loss, min confidence → approve/reject tín hiệu từ Giai đoạn 3 (chỉ giới hạn size/chặn lệnh, không tự nới direction/levels)
- [x] `storage/state.py` (SQLite): lưu vị thế mở, lịch sử lệnh, PnL — `positions`/`orders` table, `get_daily_realized_pnl()` cho risk gate
- [x] `execution/execution_engine.py`: đặt lệnh entry (MARKET) + SL/TP (algo order STOP_MARKET/TAKE_PROFIT_MARKET, `closePosition=true`) qua Binance USDS-M Futures **testnet** (`binance-sdk-derivatives-trading-usds-futures`), cờ `BINANCE_LIVE_TRADING` (mặc định `false`) chọn testnet/live URL + API key riêng biệt cho mỗi bên
- [x] `scheduler/loop.py`: vòng lặp signal → risk → execution → cập nhật state cho danh sách symbol, `reconcile_positions()` đồng bộ lại state khi SL/TP đã khớp trực tiếp trên sàn, CLI `--symbols/--interval/--once`
- [x] Alert (`alert/notifier.py`): log JSON có cấu trúc (mọi tín hiệu, quyết định risk, hành động lệnh, kill switch) + gửi Telegram nếu cấu hình `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
- [x] Kill switch: `python -m scheduler.loop --kill "<reason>"` / `--release` — file flag, vòng lặp kiểm tra mỗi chu kỳ, không đặt lệnh mới khi bật
- **Kết quả**: bot chạy tự động trên testnet theo vòng lặp thật, có risk gate, log đầy đủ, dừng được ngay khi cần

### Giai đoạn 5 — Backtest, Paper Trading & Go-live (thận trọng)
- [x] Backtest engine đơn giản: `backtest/engine.py` — walk-forward trên candles lịch sử Binance (`data.binance.get_historical_candles`, phân trang qua giới hạn 1000/call), rule-based proxy cho trend/momentum + entry/stop/target từ support/resistance (Deribit chỉ có option chain hiện tại, không có lịch sử GEX/DEX nên không replay được phần option flow của Signal Agent — đã ghi rõ trong docstring); output win rate, avg R-multiple, profit factor, max drawdown (R); CLI `python -m backtest.engine --symbol BTC --days 90 [--out report.json]` — đã test chạy thật với dữ liệu Binance
- [ ] Paper trading: chạy Giai đoạn 4 trên testnet đủ lâu (vd vài tuần) trước khi cân nhắc live — **hoạt động vận hành, cần chạy `scheduler/loop.py` liên tục trong thời gian thực, chưa thực hiện**
- [x] FastAPI app (`/status`, `/positions`, `/start`, `/stop`) để giám sát/điều khiển — `api/app.py`, chạy `uvicorn api.app:app`; `/start`,`/stop` chỉ toggle kill switch file mà `scheduler/loop.py` đã đọc mỗi cycle, không tự đặt/đóng lệnh; bảo vệ bằng HTTP Basic Auth (`API_USERNAME`/`API_PASSWORD`); đã test cả 4 endpoint chạy thật
- [x] Dashboard web đơn giản (`api/static/index.html`, phục vụ tại `/`) — equity/PNL, danh sách vị thế mở, breaker switch bật/tắt kill switch có xác nhận; đã test qua trình duyệt (login, halt, resume)
- [x] Docker hoá: `Dockerfile` + `docker-compose.yml` (service `scheduler` + `api`, `restart: unless-stopped`, dashboard chỉ bind `127.0.0.1:8000` — không lộ ra internet); đã build & chạy thử thành công local
- [x] `DEPLOY.md`: đề xuất VPS (DigitalOcean Singapore, 2GB) + hướng dẫn deploy qua SSH, truy cập dashboard qua SSH tunnel — **chưa triển khai lên VPS thật, đang chờ bạn tạo server**
- [x] Go-live checklist (xác nhận thủ công, không tự động bật): `GO_LIVE_CHECKLIST.md` — API key quyền trade giới hạn, risk limits, kill switch đã test, quy trình vận hành/rollback
- **Kết quả**: hệ thống có bằng chứng backtest/paper trading trước khi chạy tiền thật, luôn có thể tắt ngay

> ⚠️ Phát hiện ngoài kế hoạch: `.env` chứa API key thật đã bị commit + push lên
> repo GitHub public (`pikamanh/Crypto-Researcher`). Đã gỡ `.env` khỏi git tracking
> và thêm vào `.gitignore` (commit `37d823a`, chưa push). **Các key sau cần được
> revoke/tạo lại: `BINANCE_API_KEY`/`BINANCE_SECRET_KEY` (spot), `COINGECKO_API_KEY`,
> `CRYPTORANK_API_KEY`, `EHTERSCAN_API_KEY`** — key vẫn còn trong lịch sử git cho tới
> khi purge history + force-push (cần xác nhận riêng vì đây là thao tác phá hoại).

## 7. Nguyên tắc khi code
- Mỗi agent/module (analysis, risk, execution, storage) độc lập, test riêng được (mock exchange call)
- Tool wrapper tách biệt hoàn toàn khỏi agent logic; execution engine tách biệt hoàn toàn khỏi signal logic (risk manager luôn đứng giữa)
- Không hardcode API key, dùng `.env` + `python-dotenv`; API key live tách biệt hoàn toàn với testnet, không bao giờ commit
- Mặc định mọi thứ chạy testnet/dry-run; bật live phải là hành động rõ ràng, có xác nhận, không phải default
- Ưu tiên chạy được từng giai đoạn trước khi sang giai đoạn tiếp theo — không thiết kế trước cho tính năng chưa cần
