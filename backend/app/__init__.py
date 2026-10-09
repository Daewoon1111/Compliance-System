"""Backend hệ thống trích xuất và kiểm tra thông tin theo quy định — phân tầng:

  HẠ TẦNG    core.py (cấu hình + schemas) · llm.py (client Ollama) ·
             store/ (paths · sessions+cache · config · audit) · progress.py (SSE tiến độ)
  NGHIỆP VỤ  domain/ (documents: OCR + trích xuất · regulations: RAG ·
             compliance: kiểm tra tuân thủ)
  TẦNG API   routers/ (sessions · meta · config · admin · stats · export · desktop)
  KHỞI ĐỘNG  main.py (FastAPI app + CORS + include routers)
"""
