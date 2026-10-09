"""KIẾN TRÚC DOMAIN — chia theo chức năng nghiệp vụ:

  documents/    ĐỌC HỒ SƠ: đọc ảnh Vintern (ocr/) · trích xuất theo bộ trường (rules/) ·
                làm giàu bằng mô hình (enrich.py) · khôi phục dấu (spelling.py) ·
                tiếp nhận (intake.py) · điều phối cả luồng (pipeline.py)
  regulations/  KHO QUY ĐỊNH: dates · embedding · vectorstore · ingest · query · corpus · seed
  compliance/   KIỂM TRA: kiểm tra tất định (factual.py) · chất lượng đầu vào (quality.py) ·
                dựng payload mô hình (payload.py) · hợp nhất kết luận (reconcile.py,
                validation.py) · báo cáo (report.py)

Không module nào biết trước một loại hồ sơ cụ thể: tri thức nghiệp vụ nằm trong BỘ
TRƯỜNG (prompts/field_sets, data/user_config/field_sets) và KHO QUY ĐỊNH (app/rules).

Hạ tầng dùng chung nằm ở app/ gốc: core.py (config) + llm.py (Ollama) + store/ (dữ liệu).
Tầng API nằm ở app/routers/.
"""
