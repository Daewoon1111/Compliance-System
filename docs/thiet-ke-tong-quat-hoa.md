# Thiết kế tổng quát hóa hệ thống

Ngày lập: 07/10/2026. **Cập nhật cùng ngày:** Sếp chốt **xóa hẳn** phần hồ sơ cung ứng lao
động (không giữ thành mẫu/gói chuyên ngành) và **tạm dừng bản USB**. Lõi chung đã tách xong
và chạy được với bộ trường + văn bản `.md` — xem **mục 7** cho trạng thái thật và danh sách
việc còn lại. Các mục 1–6 dưới đây giữ làm tài liệu thiết kế; chỗ nào nói tới "mẫu/gói cung
ứng lao động" là phương án cũ, không còn áp dụng.

## 0. Mục tiêu đã chốt

Hệ thống **trích xuất và kiểm tra thông tin theo quy định do người dùng đưa ra**:

- người dùng nạp quy định của họ bằng tệp (nén, PDF, Word…), gom thành **bộ quy định có tên**;
- người dùng nêu **yêu cầu kiểm tra bằng lời**, hệ thống tự chuyển thành bộ trường (JSON),
  người dùng không phải viết JSON;
- hệ thống đọc hồ sơ **thuộc loại bất kỳ**, trích xuất đúng các trường đó và đối chiếu với
  bộ quy định đã chọn.

Không có "chế độ tùy chỉnh" chạy song song: lõi hệ thống là MỘT đường xử lý chung, mọi loại
hồ sơ được mô tả bằng bộ trường và kiểm tra theo quy định người dùng nạp. (Phương án ban đầu
giữ hồ sơ cung ứng lao động thành một mẫu có sẵn; Sếp đã chốt xóa hẳn — xem mục 7.)

## 1. Trả lời từng câu hỏi

### Câu 1 — Không thêm "chế độ tùy chỉnh" mà sửa lõi

Đồng ý. Hiện mã đang trộn hai thứ: (a) đường xử lý chung (đọc PDF/OCR, trích xuất theo danh
sách trường, chống bịa, truy hồi quy định, đối chiếu bằng mô hình, hợp nhất kết luận, báo cáo) và
(b) tri thức riêng của hồ sơ cung ứng lao động (Điều 19/20 Luật 69, khoản thu bị cấm, ký quỹ
theo thị trường, giấy phép đối tác…). Thiết kế đúng là tách (b) ra khỏi lõi thành **gói kiểm tra
chuyên ngành** gắn vào mẫu kiểm tra. Lõi không còn biết "hợp đồng cung ứng" là gì.

### Câu 2 — Yêu cầu bằng lời → tệp JSON tự động

Làm được, theo 4 bước, người dùng chỉ thao tác trên bảng:

1. Người dùng chọn bộ quy định, gõ yêu cầu (mỗi dòng một yêu cầu), có thể tải kèm một hồ sơ
   mẫu.
2. Với từng yêu cầu, hệ thống truy hồi các đoạn quy định liên quan trong bộ đã chọn, rồi gọi mô
   hình kiểm tra **một lần** với lược đồ đầu ra cố định (structured outputs của Ollama — hệ thống
   đang dùng sẵn). Mô hình chỉ điền: tên trường, cách nhận biết giá trị trong hồ sơ, kiểu giá trị,
   tiêu chí kiểm tra, loại kiểm tra, ngưỡng (nếu có).
3. Mã nguồn kiểm lại kết quả: tự sinh `key` từ tên trường (bỏ dấu, không để mô hình đặt khóa),
   kiểu giá trị và loại kiểm tra phải thuộc danh mục cho phép, mỗi yêu cầu phải có ít nhất một
   trường, **ngưỡng số chỉ được giữ nếu con số đó có trong câu yêu cầu hoặc trong đoạn quy định
   được truy hồi** (cùng nguyên tắc chống bịa đang áp cho trích xuất).
4. Hiện bảng trường cho người dùng sửa/xóa/thêm, bấm **Lưu** thì hệ thống ghi JSON.

Ví dụ yêu cầu "Thời hạn hợp đồng không quá 36 tháng" sinh ra:

```json
{
  "thoi_han_hop_dong": {
    "label": "Thời hạn hợp đồng",
    "fill_hint": "Thời gian hợp đồng có hiệu lực, thường ghi 'thời hạn ... năm/tháng'",
    "value_type": "duration",
    "check_type": "rule",
    "rule": {"op": "<=", "value": 36, "unit": "tháng"},
    "check_aspect": "Thời hạn hợp đồng không vượt quá 36 tháng",
    "source_requirement": "Thời hạn hợp đồng không quá 36 tháng"
  }
}
```

Yêu cầu không có ngưỡng rõ, ví dụ "Hợp đồng phải có điều khoản bảo hiểm theo quy định", sinh
`"check_type": "regulated"`: giá trị được đối chiếu với bộ quy định bằng mô hình ngôn ngữ như
hiện nay. Trang **Cấu hình** đang bắt người dùng sửa JSON thô sẽ được thay bằng bảng này.

### Câu 3 — Bộ quy định có tên, gồm nhiều loại tệp

Khái niệm mới **Bộ quy định**:

- người dùng đặt tên + mô tả, kéo thả nhiều tệp/tệp nén một lần, thêm hoặc bớt tệp về sau;
- mỗi bộ có kho véc-tơ riêng (một collection ChromaDB), nên chọn bộ nào thì chỉ đối chiếu với
  bộ đó, không lẫn với quy định của bộ khác;
- sau khi nạp, hiện bảng: tên tệp, loại, số trang, cách đọc (lớp chữ hay OCR), số hiệu văn bản,
  ngày hiệu lực (tự nhận, sửa được), số điều khoản cắt được, trạng thái;
- bốn văn bản hiện có (Luật 69/2020, NĐ 112/2021, TT 21/2021, TT 02/2024) chuyển thành bộ quy
  định mặc định "Đưa người lao động đi làm việc ở nước ngoài". Đăng bạ kho luật (hàm băm, phê
  duyệt) giữ nguyên, áp cho từng bộ.

Lưu trữ: `backend/app/data/regulation_sets/<id>/` gồm `set.json` (tên, mô tả, danh sách tệp,
hàm băm, siêu dữ liệu), `source/` (tệp gốc), `text/` (văn bản đã đọc, để người dùng xem lại).

### Câu 4 — Tự giải nén? Đọc được văn bản quy định dạng nào?

**Hiện tại:** không tự giải nén; kho luật chỉ nhận tệp `.md` đặt sẵn trong `backend/app/rules`
rồi chạy `npm run seed`. Hồ sơ tải lên chỉ nhận PDF.

**Sau khi làm:** tự giải nén và đọc được:

| Dạng | Cách đọc | Ghi chú |
|---|---|---|
| `.zip` | thư viện chuẩn `zipfile` | tên tệp tiếng Việt: thử UTF-8 rồi cp1258/cp437 |
| `.7z`, `.rar`, `.tar.gz` | 7-Zip (`7z.exe`, ~1,5 MB) đóng gói kèm bản USB | RAR chỉ giải nén, không tạo |
| Nén lồng nhau | giải tối đa 2 tầng | |
| `.pdf` có lớp chữ | `pypdfium2` (đang có) | đối chứng lớp chữ như hồ sơ |
| `.pdf` bản scan, ảnh `.jpg/.png/.tif` | Vintern (đang có) | chậm trên CPU: 1–3 phút/trang |
| `.docx` | đọc XML của Word, giữ đề mục, đánh số, bảng | |
| `.doc` (Word 97–2003) | chuyển sang `.docx` bằng Word trên máy (nếu có) | **cần Sếp chốt**, xem mục 6 |
| `.rtf`, `.odt` | `striprtf`, `odfpy` | thư viện nhỏ, thuần Python |
| `.html/.htm` | `html.parser` thư viện chuẩn | trang tải từ vbpl.vn, thuvienphapluat |
| `.txt`, `.md` | đọc thẳng | |

An toàn khi giải nén (tệp người dùng đưa vào là dữ liệu không tin cậy): chặn đường dẫn `../` và
đường dẫn tuyệt đối (zip-slip), trần tổng dung lượng sau giải nén (500 MB), trần tỉ lệ nén (chống
bom nén), trần số tệp (500), bỏ qua liên kết tượng trưng và tệp ẩn, tệp không đọc được thì báo tên
trong bảng thay vì làm hỏng cả lượt nạp.

Văn bản đọc xong được **cắt theo cấu trúc pháp lý** (Chương/Mục/Điều/Khoản/Điểm) thay vì theo đoạn
trống như `chunk_markdown` hiện nay, nên trích dẫn ra đúng "Điều 5 khoản 2". Hệ thống tự nhận số
hiệu văn bản, ngày ban hành, ngày có hiệu lực bằng biểu thức chính quy ("có hiệu lực thi hành từ
ngày…"). Quy định nội bộ không có cấu trúc Điều/Khoản thì cắt theo đề mục rồi theo đoạn.

### Câu 5 — Chạy linh hoạt nhiều loại hồ sơ: sửa những chỗ nào

Làm được. Rà mã cho thấy các chỗ gắn chặt và cách xử lý:

| Chỗ gắn chặt | Nằm ở | Xử lý |
|---|---|---|
| Chọn khu vực · quốc gia · loại hình lao động ở bước tải lên | `intake.resolve_selection`, `markets.json`, trang tải lên | Thay bằng **chọn mẫu kiểm tra**. Ba tầng lựa chọn trở thành "thuộc tính lựa chọn" do riêng mẫu cung ứng lao động khai |
| Bộ 50 trường mặc định + 3 tầng chồng cấu hình | `prompts/jobs/regions/**` | Thành bộ trường của mẫu cung ứng lao động, giữ nguyên nội dung |
| Luật regex theo tên trường (tiền lương, ký quỹ, 17 khoản chi phí, vùng chi phí…) | `extraction.json`, `rules/extract.py`, `rules/sections.py` | Thành "gói trích xuất" của mẫu. Lõi chỉ giữ luật chung theo **kiểu giá trị** (ngày, số, tiền, thời hạn) + quét nhãn chung |
| Cổng chất lượng: biên độ lương theo thị trường, khoản thu bị cấm, giữ giấy tờ, đối chiếu tên nước | `compliance/quality.py`, `checks.json > input_quality` | Tách: cổng OCR + ngày ký + đơn vị tiền ở lõi; phần còn lại vào gói cung ứng lao động |
| Phân tích bộ hồ sơ: vai trò tài liệu, đủ thành phần Điều 20, giấy phép đối tác | `compliance/dossier.py`, `checks.json > dossier` | Vào gói. Lõi giữ khung chung "vai trò tài liệu do mẫu khai" |
| Ký quỹ theo thị trường, danh mục khoản được thu, bất thường chi phí, Điều 19 bắt buộc | `factual.py`, `reconcile.py`, `markets.json > deposit_policy` | Vào gói |
| Gộp tài liệu theo thứ tự "văn bản đăng ký trước, hợp đồng cung ứng sau", đối chiếu chéo | `report.py` | Thứ tự ưu tiên do mẫu khai; lõi gộp theo thứ tự đó |
| Câu lệnh hệ thống "Bạn là hệ thống kiểm tra hợp đồng cung ứng lao động…" | `extraction_llm.json`, `validation_prompt.json` | Tham số hóa bằng `document_kind` và `domain_context` của mẫu |
| Truy vấn dự phòng "quy định liên quan hợp đồng cung ứng lao động" | `validation.py:88` | Lấy từ mô tả của bộ quy định |
| Neo OCR `ocr_start_anchor = "HỢP ĐỒNG CUNG ỨNG LAO ĐỘNG"` | `core.py` | Thành thuộc tính của mẫu (trống = không neo) |
| Nhãn thị trường của đoạn luật (Nhật Bản, Đài Loan, Hàn Quốc, Macao) | `regulations/ingest.py` | Thành siêu dữ liệu tùy chọn của bộ quy định mặc định |
| Tên ứng dụng, mô tả trang, nhãn giao diện | `core.app_name`, `index.html`, `i18n.ts` | Đổi sang tên chung |

**Gói kiểm tra chuyên ngành** là một giao diện nhỏ trong mã:

```python
class DomainPack(Protocol):
    id: str
    def extraction_rules(self) -> dict: ...                        # luật regex riêng
    def input_flags(self, contract, ctx) -> list[dict]: ...        # cổng chất lượng riêng
    def dossier_analysis(self, documents, ctx) -> dict: ...        # phân tích cả bộ hồ sơ
    def deterministic_check(self, field_key, value, ctx): ...      # kiểm tất định riêng
    def report_extras(self, result, ctx) -> dict: ...              # phần riêng trong báo cáo
```

Mẫu kiểm tra khai `"pack": "cung_ung_lao_dong"` hoặc `null`. Mẫu do người dùng tạo không có gói
và chạy hoàn toàn trên lõi chung.

## 2. Mô hình dữ liệu đích

```
Bộ quy định (regulation set)        Mẫu kiểm tra (check profile)
  id, tên, mô tả                      id, tên, loại hồ sơ (document_kind)
  tệp[]: gốc, văn bản, hàm băm        bộ quy định[]  ──────────────► dùng để truy hồi
  collection ChromaDB riêng           bộ trường (fields_catalog, sinh từ yêu cầu)
  siêu dữ liệu: số hiệu, hiệu lực     yêu cầu gốc của người dùng (giữ để sửa lại)
                                      gói chuyên ngành (tùy chọn)
                                      thuộc tính lựa chọn (tùy chọn)
                                      vai trò tài liệu + thứ tự ưu tiên (tùy chọn)

Lượt kiểm tra = mẫu kiểm tra + các tệp hồ sơ  ──►  đọc → trích xuất → soát → đối chiếu → báo cáo
```

Định dạng bộ trường giữ tương thích với `fields_catalog` hiện tại (`label`, `fill_hint`,
`check_aspect`, `check_type`, `field_group`), chỉ thêm `value_type`, `rule`,
`source_requirement`. Nhờ vậy mẫu cung ứng lao động chuyển sang không phải viết lại bộ trường.

## 3. Thay đổi trong đường xử lý chung

- **Trích xuất hồ sơ dài:** hồ sơ loại bất kỳ có thể dài hơn cửa sổ ngữ cảnh. Cắt hồ sơ thành
  đoạn, véc-tơ hóa, mỗi trường chỉ gửi mô hình các đoạn liên quan nhất (truy hồi phía hồ sơ),
  thay vì gửi cả văn bản như hiện nay.
- **Kiểm tất định chung:** phép so sánh `>=, <=, between, in_list, required, regex` theo
  `rule` của trường, chạy bằng mã, không qua mô hình.
- **Truy hồi quy định:** truy vấn trong collection của các bộ quy định thuộc mẫu; lọc theo
  ngày hiệu lực chỉ khi văn bản có khai ngày.
- **Báo cáo:** khung chung (kết luận từng trường + kết luận chung + căn cứ); phần riêng do gói.

## 4. Kế hoạch theo giai đoạn

| Giai đoạn | Nội dung | Nghiệm thu |
|---|---|---|
| 1. Bộ quy định | Lưu trữ + API + trang "Bộ quy định"; giải nén an toàn; đọc pdf/docx/html/txt/ảnh; cắt theo Điều/Khoản; collection riêng; chuyển 4 văn bản thành bộ mặc định | Nạp một tệp zip gồm pdf + docx + scan; kết quả đối chiếu hồ sơ cung ứng lao động **không đổi** so với trước |
| 2. Tách gói chuyên ngành | `DomainPack`; chuyển mã cung ứng lao động vào `app/packs/cung_ung_lao_dong/`; trang tải lên chọn mẫu kiểm tra | Toàn bộ kiểm thử hiện có (`test_eval_golden`, `test_audit_regression`…) đạt mà không sửa kỳ vọng |
| 3. Yêu cầu → bộ trường | Bộ biên dịch yêu cầu (mô hình + kiểm bằng mã); bảng sửa trường thay JSON thô ở trang Cấu hình | 20 yêu cầu mẫu: mọi yêu cầu có trường, không có ngưỡng nào không có nguồn |
| 4. Lõi cho hồ sơ không có gói | Truy hồi phía hồ sơ, kiểm tất định theo `rule`, báo cáo chung | Chạy trọn luồng với 2 loại hồ sơ khác (ví dụ hợp đồng lao động trong nước, quy chế nội bộ) |
| 5. Đo chất lượng | Bộ hồ sơ có nhãn cho từng mẫu; mở rộng `app/eval.py` theo mẫu | Có số đo độ đúng theo từng mẫu |

Giai đoạn 1 và 3 không phụ thuộc nhau; giai đoạn 2 là điều kiện của giai đoạn 4.

## 5. Rủi ro

- **Độ chính xác của mẫu do người dùng tạo thấp hơn mẫu cung ứng lao động:** mẫu đó có luật regex
  và các cổng chuyên ngành tích lũy qua nhiều vòng sửa lỗi. Giảm rủi ro: bước soát giữ nguyên,
  thiếu căn cứ thì hạ về `NEEDS_SUPPLEMENT`, hiện rõ trên báo cáo "mẫu chưa có bộ hồ sơ đo chất
  lượng".
- **Mô hình 7B sinh bộ trường chưa ổn định:** lược đồ đầu ra cố định + kiểm bằng mã + người dùng
  duyệt bảng trước khi lưu.
- **Quy định bản scan dài nạp rất chậm trên CPU:** hiện tiến độ từng trang, khuyên dùng bản có
  lớp chữ hoặc Word; nạp chạy nền, đóng cửa sổ vẫn giữ việc đang chạy.
- **Tách gói làm hỏng hồ sơ cung ứng lao động:** nghiệm thu giai đoạn 2 bắt buộc toàn bộ kiểm thử
  hồi quy hiện có đạt nguyên trạng.

## 6. Điểm cần Sếp chốt trước khi làm

1. **Tệp `.doc` (Word 97–2003)** — văn bản tải từ cổng văn bản chính phủ hay ở dạng này:
   (a) dùng Microsoft Word có sẵn trên máy để chuyển sang `.docx`, máy không có Word thì báo người
   dùng tự lưu lại thành `.docx`; hoặc (b) đóng gói LibreOffice vào bản USB (~350 MB) để đọc được
   trên mọi máy. Đề xuất (a).
2. **Đóng gói 7-Zip vào bản USB** để đọc `.rar/.7z`. Đề xuất: có.
3. **Thứ tự làm:** đề xuất 1 → 2 → 3 → 4 → 5 như bảng trên.

## 7. Trạng thái sau đợt tách (07/10/2026) và việc còn lại

### 7.1. Đã làm

- **Xóa sạch phần cung ứng lao động:** 4 văn bản luật trong `app/rules`, bộ trường 3 tầng
  (`prompts/jobs`), danh mục thị trường, ngân hàng cụm đáp án, phân tích bộ hồ sơ
  (`dossier.py`), luật regex theo mục (`sections.py`), kiểm tất định chuyên ngành (ký quỹ,
  khoản thu, biên độ lương, thời hạn), nhắc hạn hợp đồng, phân loại vai trò tệp; giao diện bỏ
  khu vực/quốc gia/loại hình, bảng chi phí, khoản thu lạ.
- **Lõi chung chạy bằng bộ trường:** bộ trường (JSON) khai `value_type`, `check_type`,
  `check_aspect`, `signed_date_field`, `field_check_mode`; trích xuất theo kiểu giá trị;
  truy vấn quy định = `document_kind` + nhãn + tiêu chí; lọc hiệu lực theo ngày ký chỉ khi bộ
  trường khai trường ngày ký. Có bộ mẫu `hop_dong_mau`.
- **Trang Cấu hình** quản lý bộ trường (soạn JSON, kiểm hợp lệ ngay khi gõ, sao chép bộ mặc
  định); **Quản trị** sửa/tạo bộ trường mặc định và văn bản quy định `.md`.
- **Chuyển Word sang Markdown:** `npm run docx2md -- <tệp.docx> [--rules]` (thư viện chuẩn, giữ
  đề mục, Chương/Mục/Điều, danh sách đánh số, bảng).
- Sửa lỗi phát hiện khi viết lại kiểm thử: nhãn chịu lỗi OCR khớp nhầm ("Thời hạn" khớp
  "thanh"), giá trị nuốt tiêu đề "Điều N." kế tiếp, mô hình không bù được trường ngày, khôi
  phục dấu làm hỏng từ đã có dấu, "2.5" bị đọc thành số nguyên 25, truy vấn quy định thừa khi
  không có trường cần đối chiếu. Kiểm thử: 574 ca đạt, ruff/eslint sạch.

### 7.1b. Đợt 08/10/2026 — trang Kiểm tra, bộ quy định, vùng kiểm tra, chỉ số chất lượng

- **Trang Kiểm tra bỏ ô chọn loại hồ sơ.** Thay bằng dòng "Bộ kiểm tra đang dùng" và hai nút:
  - **Tạo bộ kiểm tra**: hộp thoại có bảng soạn (mỗi dòng một thông tin: nhãn, tên gọi khác,
    kiểu giá trị, cách kiểm tra, tiêu chí, bắt buộc, trong bảng), chọn bộ quy định đối chiếu,
    chọn trường ngày ký, bắt đầu từ bộ có sẵn; tab "Dùng bộ có sẵn" để đổi bộ. Bộ vừa lưu
    thành bộ đang dùng, nhớ ở `data/user_config/active.json`
    (`GET/POST /api/v1/settings/active-field-set`). Lượt tải lên không gửi bộ trường thì
    backend dùng bộ đang dùng.
  - **Thêm bộ quy định**: đặt tên + tải `.md/.txt/.docx/.pdf/.zip`
    (`POST /api/v1/config/regulation-sets`). Giải nén an toàn (chỉ lấy tên tệp, trần 50 tệp /
    200 MB), chuyển sang Markdown, ghi `app/rules/`, khai đăng bạ với khóa `set` (chưa phê
    duyệt), nạp lại kho. Mỗi đoạn mang metadata `reg_set`; bộ trường khai `regulation_sets`
    thì truy vấn chỉ lấy đoạn của các bộ đó (không khai = toàn kho).
- **PDF sang Markdown** (`pdf2md.py`, `npm run pdf2md -- <tệp.pdf> [--rules] [--ocr]`): đọc lớp
  chữ bằng `textlayer`, trang rác/quét đọc bằng Vintern khi bật OCR, nối dòng thành đoạn, nhận
  Chương/Mục/Điều làm tiêu đề, bỏ dòng số trang.
- **Chọn vùng kiểm tra**: nút bật/tắt cạnh "File hồ sơ" (chỉ bật được khi đã có file); cửa sổ
  90% trang (pdf.js 4.10) vẽ hình chữ nhật trên trang, sidebar phải mỗi trang có ô "Quét trang"
  + "Trang N" + chữ "Đã chọn"; nút Hủy bỏ / Xác nhận sát phải. Gửi `regions` (JSON theo thứ tự
  file: `skip`, `rects` chuẩn hóa 0..1). Backend (`intake.parse_regions` → `pipeline`): bỏ trang
  không tick, trang có lớp chữ chỉ giữ ký tự có tâm trong vùng, trang OCR cắt ảnh trước khi đọc;
  vùng đi vào khóa đệm phiên.
- **Chỉ số chất lượng** (`compliance/accuracy.py`): sau mỗi lượt kiểm tra ghi vào nhật ký CER,
  WER, OCR Accuracy (1 − CER), Field-level, Table (trường `in_table`), Number (số + tiền), Date
  Accuracy. Đáp án = giá trị sau soát; giá trị máy lưu ở `machine` khi người duyệt sửa lần đầu.
  Trang Thống kê hiện trung bình chung, theo bộ kiểm tra và từng bộ hồ sơ.
- Phục vụ giao diện: ghim kiểu nội dung `.js/.mjs/.css` (worker pdf.js) thay vì để `mimetypes`
  của Windows đoán.
- Kiểm thử: 609 ca đạt, độ phủ 85%, ruff/eslint/tsc sạch; luồng giao diện kiểm bằng Playwright
  với OCR/LLM giả lập.

### 7.2. Việc còn lại để hoàn tất (bản chạy trên máy tính)

| # | Việc | Ghi chú |
|---|---|---|
| 1 | ~~Nạp quy định từ giao diện~~ — **đã làm** (mục 7.1b); còn lại: Word chỉ chứa ảnh | Các tệp Word luật 69, NĐ 112, TT 21 trên máy Sếp chỉ chứa ẢNH quét — in/lưu ra PDF rồi nạp với "Đọc ảnh (OCR)" |
| 2 | ~~Gắn bộ trường với bộ quy định~~ — **đã làm** (`regulation_sets`, lọc `reg_set`) | Lọc theo siêu dữ liệu trong một collection |
| 3 | **Yêu cầu bằng lời → bộ trường** (bảng soạn thay ô JSON đã có ở hộp thoại Tạo bộ kiểm tra) | Mục 1 câu 2 |
| 4 | **Kiểm tất định theo `rule`** (>=, <=, between, in_list, regex) | Mục 3 |
| 5 | **Cắt quy định theo Điều/Khoản** để trích dẫn ra đúng "Điều 5 khoản 2" | Thay `chunk_markdown` theo đoạn trống |
| 6 | **Bộ hồ sơ có nhãn cho từng loại hồ sơ** (CER/WER theo bản đánh máy chuẩn cả trang) | Hiện chỉ số đo theo bản soát; `app/eval.py`, `data/golden/` |
| 7 | **Đóng gói cài đặt Windows** cho bản máy tính (lối tắt Start menu, kiểm Ollama/mô hình lần đầu) | Thay cho bản USB đang tạm dừng |
| 8 | Dọn dữ liệu cũ trên máy: `npm run seed` (xóa đoạn luật cũ trong ChromaDB), `npm run clear` (nhật ký cũ còn trường thị trường) | Thao tác một lần |

**Tạm dừng:** bản USB (`desktop/build_portable.py`) — giữ mã, không phát triển tiếp.
