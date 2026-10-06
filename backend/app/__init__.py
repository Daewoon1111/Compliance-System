"""Backend hệ thống kiểm tra hợp đồng cung ứng lao động — phân tầng:

  HẠ TẦNG    core.py (cấu hình + schemas) · llm.py (client Ollama) ·
             store/ (paths · sessions+cache · config · audit) · progress.py (SSE tiến độ)
  NGHIỆP VỤ  domain/ (documents: OCR + trích xuất · regulations: RAG ·
             compliance: kiểm tra tuân thủ)
  TẦNG API   routers/ (sessions · meta · admin · stats)
  KHỞI ĐỘNG  main.py (FastAPI app + CORS + include routers)
"""
