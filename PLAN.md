# Agentic AI Crypto Researcher — Implementation Plan

## 1. Mục tiêu
Xây dựng hệ thống multi-agent hỗ trợ:
- Research & báo cáo dự án crypto (whitepaper, docs, tổng quan)
- Phân tích on-chain & thị trường (giá, volume, TVL, holder, xu hướng)
- Tổng hợp tín hiệu giao dịch (không tự động đặt lệnh, chỉ đề xuất)
- Trợ lý hỏi-đáp (chatbot) tra cứu dữ liệu real-time

## 2. Tech stack
- **Ngôn ngữ**: Python 3.11+
- **Agent orchestration**: LangGraph
- **LLM**: bất kỳ LLM hỗ trợ tool calling (OpenAI-compatible API) cho reasoning + tool calling
- **API layer**: FastAPI (thêm ở Giai đoạn 4)
- **Package/env**: `uv` hoặc `venv` + `requirements.txt`

## 3. Nguồn dữ liệu (free tier)
| Loại dữ liệu | Nguồn | Ghi chú |
|---|---|---|
| Giá / market cap / volume | CoinGecko API | free, không cần key cho basic endpoints |
| TVL / project info | DeFiLlama API | free |
| On-chain (holder, tx) | Etherscan / BscScan API | free tier, cần key |
| Tin tức / sentiment | CryptoPanic API hoặc RSS | free |
| Social (X/Twitter) | để giai đoạn sau | API trả phí |

## 4. Kiến trúc mục tiêu
```
Orchestrator Agent (LangGraph)
 ├─ Research Agent   → whitepaper/docs, tóm tắt dự án
 ├─ Market Agent     → giá, volume, market cap (CoinGecko)
 ├─ On-chain Agent   → TVL, holder, transaction (DeFiLlama/Etherscan)
 ├─ Sentiment Agent  → tin tức, social (CryptoPanic)
 └─ Signal Agent     → tổng hợp toàn bộ → đề xuất tín hiệu
Chatbot/API layer    → nhận câu hỏi người dùng, route tới orchestrator
```

## 5. Cấu trúc thư mục đề xuất
```
crypto-researcher/
├── agents/            # logic từng agent (research, market, onchain, sentiment, signal, orchestrator)
├── tools/              # wrapper gọi API bên ngoài (coingecko.py, defillama.py, etherscan.py, cryptopanic.py)
├── data/               # cache local, kết quả tạm (nếu cần)
├── api/                 # FastAPI app (Giai đoạn 4)
├── tests/
├── requirements.txt
└── README.md
```

## 6. Lộ trình MVP theo giai đoạn

### Giai đoạn 1 — Nền tảng (1 agent chạy được)
- [x] Setup repo: venv, `requirements.txt`, cấu trúc thư mục ở mục 5
- [x] Cấu hình LLM API key (.env, không commit)
- [x] Viết `tools/coingecko.py`, `tools/defillama.py` (hàm gọi API, trả JSON đã parse)
- [ ] Viết Research Agent: nhận tên coin → gọi tool CoinGecko + DeFiLlama → dùng LLM tổng hợp báo cáo
- [ ] CLI đơn giản (`python -m agents.cli <coin>`) in ra báo cáo tóm tắt
- **Kết quả**: hỏi 1 coin → nhận báo cáo (giá, TVL, mô tả dự án)

### Giai đoạn 2 — Thêm data & phân tích
- [ ] Viết Market Agent: phân tích xu hướng giá/volume (so sánh khung thời gian, % thay đổi)
- [ ] Viết On-chain Agent: dùng Etherscan/BscScan, số holder, giao dịch lớn gần đây
- [ ] Dựng Orchestrator bằng LangGraph, ghép Research + Market + On-chain Agent
- [ ] Định nghĩa state schema chung (coin, dữ liệu từng agent, báo cáo cuối)
- **Kết quả**: 1 lệnh gọi → orchestrator chạy song song 3 agent → gộp báo cáo

### Giai đoạn 3 — Sentiment + Signal
- [ ] Viết `tools/cryptopanic.py`, Sentiment Agent (tóm tắt tin tức, đánh giá tích cực/tiêu cực)
- [ ] Viết Signal Agent: nhận output của 4 agent trên → đề xuất tín hiệu (bullish/bearish/neutral) kèm lý do
- [ ] Thêm disclaimer bắt buộc: đây là tổng hợp thông tin, không phải lời khuyên đầu tư
- **Kết quả**: báo cáo đầy đủ 4 chiều (research/market/onchain/sentiment) + tín hiệu tổng hợp

### Giai đoạn 4 — Chatbot/API + productionize
- [ ] Bọc orchestrator bằng FastAPI (`POST /research/{coin}`, `POST /chat`)
- [ ] Thêm cache cho API calls bên ngoài (tránh rate-limit, ví dụ TTL cache theo coin)
- [ ] Thêm logging, xử lý lỗi khi API bên ngoài fail/timeout
- [ ] (Tuỳ chọn) Giao diện Streamlit hoặc tích hợp Telegram bot
- **Kết quả**: hệ thống chạy như service, có thể hỏi-đáp qua API/chatbot

## 7. Nguyên tắc khi code
- Mỗi agent là 1 module độc lập, dễ test riêng (mock tool calls)
- Tool wrapper tách biệt hoàn toàn khỏi agent logic (agent không gọi HTTP trực tiếp)
- Không hardcode API key, dùng `.env` + `python-dotenv`
- Ưu tiên chạy được từng giai đoạn trước khi sang giai đoạn tiếp theo — không thiết kế trước cho tính năng chưa cần
