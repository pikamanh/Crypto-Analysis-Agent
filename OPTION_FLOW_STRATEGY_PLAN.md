# Option Flow Strategy Agent — Plan

## 1. Mục tiêu

Xây một **agent độc lập**, tách hoàn toàn khỏi pipeline chính (`scheduler/loop.py` →
Price Action + Option Flow + Sentiment → Signal → Risk → Execution), chỉ dựa vào
**option flow** (GEX/DEX, OI theo strike, put/call ratio, max pain, IV rank, HVL/zero-gamma)
để ra một **chiến lược giao dịch cụ thể** — không chỉ `{trend, reasoning}` như
`agents/option_flow_agent.py` hiện tại.

Agent này:
- Chạy **on-demand hoặc theo lịch riêng** (không phải chu kỳ 5 phút của bot chính)
- **Không tự đặt lệnh** — chỉ sinh setup + gửi alert, người dùng tự quyết định vào lệnh
- Dùng lại toàn bộ hạ tầng dữ liệu Deribit đã có (`api/options_engine.py` +
  `tools/deribit_tools.py`), không cần build lại data layer

Không đụng vào `agents/option_flow_agent.py` hay `agents/signal_agent.py` hiện tại —
chúng vẫn phục vụ bot chính như cũ.

## 2. Vì sao option flow một mình đủ để ra chiến lược

Option flow (đặc biệt GEX/DEX + key levels) tự thân đã cho:
- **Levels**: `call_resistance`, `put_support`, `hvl` (zero-gamma), `max_gex_strike`,
  `max_call_oi_strike`, `max_put_oi_strike` → dùng trực tiếp làm entry zone / SL / TP
- **Regime**: `gamma_regime` (positive/dampening = giá bị ghìm quanh max-OI/max-pain;
  negative/amplifying = biến động khuếch đại khi giá phá vùng) → chọn kiểu chiến lược
  (mean-reversion quanh levels vs breakout/momentum theo hướng phá HVL)
- **Bias**: `put_call_oi_ratio`, `put_call_gex_ratio` → thiên hướng
- **Rủi ro biến động**: `iv_rank_pct`, `expected_move_1d_pct/usd` → dùng để đặt SL/TP
  theo % thực tế của thị trường thay vì số cố định, và để lọc confidence khi IV rank
  quá cao/thấp

Đây chính là cách các dealer-positioning tracker công khai (SpotGamma-style) diễn giải
dữ liệu — logic đã có sẵn comment trong `api/options_engine.py`.

## 3. Kiến trúc

```
agents/option_flow_strategy_agent.py   ← MỚI, độc lập, không nằm trong LangGraph pipeline chính
 ├─ lấy dữ liệu: api.options_engine.get_options_dashboard()  (đã có sẵn, nhiều field hơn get_option_flow)
 ├─ rule engine: gamma_regime + spot_vs_hvl + key_levels → chọn kiểu chiến lược
 ├─ level mapping: entry_zone / stop_loss / take_profit từ call_resistance/put_support/hvl/expected_move
 ├─ confidence scoring: dựa trên khoảng cách tới HVL, độ lớn GEX tại level, iv_rank
 └─ output: OptionFlowStrategy {direction, strategy_type, entry_zone, stop_loss,
            take_profit, confidence, size_pct_hint, valid_context, reason}

CLI: python -m agents.option_flow_strategy_agent --currency BTC
Alert: tái dùng alert/notifier.py để đẩy setup qua Telegram khi có tín hiệu mới/level bị phá
Log: ghi mỗi setup ra file/SQLite riêng để review sau (không đụng bảng positions/orders
     của bot chính) — phục vụ forward-test thủ công
```

## 4. Hai loại chiến lược rule-based (điểm khởi đầu)

| Điều kiện | Kiểu chiến lược | Entry / SL / TP |
|---|---|---|
| `gamma_regime = dampening` (positive GEX), spot gần `max_gex_strike`/`max_pain` | **Mean-reversion / range** | Entry: quanh `call_resistance`↔`put_support`; SL: ngoài range ± `expected_move_1d_usd`; TP: `max_gex_strike` hoặc giữa range |
| `gamma_regime = amplifying` (negative GEX), spot vừa phá `hvl` | **Breakout / momentum theo hướng phá HVL** | Entry: theo hướng phá; SL: bên kia `hvl`; TP: level OI lớn tiếp theo cùng hướng (từ `gex_levels` top10) |
| Spot đang dao động sát `hvl`, chưa rõ hướng | **No-trade / chờ xác nhận** | confidence thấp, không ra entry cụ thể |

`iv_rank_pct` cao (>70) → giảm confidence cho breakout (rủi ro vol crush sau spike);
thấp (<20) → giảm confidence cho mean-reversion (biến động có thể mở rộng bất ngờ).

Đây là điểm khởi đầu — sẽ tinh chỉnh ngưỡng cụ thể (bao nhiêu % GEX, bao nhiêu strike
distance) sau khi forward-test một thời gian, không cố định cứng ngay từ đầu.

## 5. Giới hạn cần biết trước

- **Không backtest được bằng dữ liệu lịch sử thật**: Deribit chỉ trả option chain hiện
  tại (đã ghi rõ trong `backtest/engine.py` docstring cho Signal Agent) — GEX/DEX/OI
  lịch sử không có sẵn. → Kế hoạch: bắt đầu ghi **snapshot hàng ngày** vào
  storage riêng ngay khi agent chạy, để sau vài tuần/tháng có dữ liệu tự tích lũy cho
  backtest thật (không có shortcut nào khác).
- **Không phải tín hiệu hướng giá tuyệt đối** — GEX/DEX phản ánh khả năng dealer
  hedging, không phải dự đoán giá. Agent chỉ nên output **context + level**, không nên
  tự tin quá mức vào 1 nguồn dữ liệu duy nhất (đây cũng là lý do bot chính dùng option
  flow làm "chính" nhưng vẫn kết hợp price action).

## 6. Các bước triển khai

### Bước 1 — Rule engine + schema (MVP)
- [ ] `agents/option_flow_strategy_agent.py`: hàm `build_strategy(dashboard: dict) -> OptionFlowStrategy`
      thuần rule-based (không cần LLM) từ output của `get_options_dashboard()`
- [ ] Pydantic schema `OptionFlowStrategy {direction, strategy_type, entry_zone: [lo, hi],
      stop_loss, take_profit, confidence: 0-1, size_pct_hint, reason}`
- [ ] CLI chạy thử: in ra JSON cho BTC/ETH

### Bước 2 — LLM layer (tùy chọn, sau khi rule engine ổn)
- [ ] Bọc rule-engine output qua LLM (giống pattern `option_flow_agent.py` hiện tại)
      chỉ để **diễn giải thành reasoning tự nhiên** — không để LLM tự bịa entry/SL/TP,
      những con số đó luôn tính bằng code (tránh lỗi model tự suy diễn sai dấu như đã
      từng gặp, ghi trong `PLAN.md` Giai đoạn 3)

### Bước 3 — Logging cho forward-test
- [ ] Ghi mỗi lần chạy ra `storage/option_flow_signals.sqlite` (bảng riêng, không đụng
      `positions`/`orders` của bot chính): timestamp, dashboard snapshot rút gọn,
      strategy output
- [ ] Script review đơn giản: so `entry/SL/TP` đã log với giá thực tế sau N giờ để tính
      win rate thủ công — chưa cần tự động, chỉ cần dữ liệu để tự đánh giá

### Bước 4 — Alerting
- [ ] Tái dùng `alert/notifier.py`: gửi Telegram khi có strategy mới với
      `confidence` vượt ngưỡng, hoặc khi `spot` phá `hvl`/`call_resistance`/`put_support`
      (regime change) — tránh spam, chỉ alert khi có thay đổi thật

### Bước 5 — Lịch chạy riêng (không đụng scheduler chính)
- [ ] Cron riêng hoặc `--loop --interval` trong chính file agent (không dùng chung
      `scheduler/loop.py` vì đó là vòng lặp signal→risk→execution của bot chính)
- [ ] Tần suất đề xuất: 15-30 phút (GEX/DEX không đổi nhanh như giá, không cần 5 phút)

### Bước 6 — (Tùy chọn, sau) Tích hợp ngược vào bot chính
- Chỉ cân nhắc nếu forward-test cho kết quả tốt: có thể refactor rule engine thành
  module dùng chung, cho cả `option_flow_strategy_agent.py` (độc lập) lẫn
  `signal_agent.py` (trong pipeline chính) cùng gọi — nhưng đây là bước sau, không
  làm ngay để tránh phá vỡ pipeline đang chạy ổn định.

## 7. Việc không làm (out of scope cho agent này)

- Không tự động đặt lệnh — `execution/execution_engine.py` chỉ phục vụ bot chính,
  agent này không gọi tới
- Không thay thế `agents/option_flow_agent.py`/`signal_agent.py` hiện tại
- Không cần Price Action/Sentiment — nếu sau này thấy option-flow-only không đủ tin
  cậy, đó là tín hiệu để **quay lại dùng Signal Agent đa nguồn** thay vì cố gắng nhồi
  thêm nguồn dữ liệu vào agent độc lập này
