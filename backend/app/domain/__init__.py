"""KIẾN TRÚC DOMAIN — chia theo chức năng nghiệp vụ:

  documents/    ĐỌC HỒ SƠ: đọc ảnh Vintern (ocr/) · trích xuất regex (rules/) · làm giàu bằng
                LLM (enrich.py) · chính tả (spelling.py) ·
                điều phối cả luồng (pipeline.py)
  regulations/  KHO QUY ĐỊNH: dates · embedding · vectorstore · ingest · query · corpus · seed
  compliance/   KIỂM TRA TUÂN THỦ: kiểm tra tất định (factual.py) · chất lượng đầu vào
                (quality.py) · bộ hồ sơ (dossier.py) · dựng payload LLM (payload.py) ·
                hợp nhất kết luận (reconcile.py, validation.py)

Hạ tầng dùng chung nằm ở app/ gốc: core.py (config) + llm.py (Ollama) + store/ (dữ liệu).
Tầng API nằm ở app/routers/.
"""
