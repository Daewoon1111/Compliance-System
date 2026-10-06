# Hệ thống trích xuất kiểm tra thông tin theo quy định

Hệ thống đọc bộ hồ sơ **đăng ký hợp đồng cung ứng lao động** đưa người Việt Nam đi làm
việc ở nước ngoài, đối chiếu với pháp luật Việt Nam, rồi trả kết luận **kèm căn cứ**.

Đầu vào là các file PDF scan. Đầu ra là một báo cáo nói rõ: trường nào hợp lệ, trường nào
không, **vì sao**, và **điều luật nào** dẫn tới kết luận đó.

Điểm khác biệt: **toàn bộ chạy cục bộ**. Đọc ảnh bằng mô hình Vintern-1B-v3.5 trên máy, mô
hình ngôn ngữ chạy qua Ollama trên máy. Hợp đồng lao động chứa thông tin cá nhân và điều khoản thương mại —
chúng không rời khỏi máy của bạn, và hệ thống không cần API key hay hạn mức gọi.

Kho quy định hiện có: Luật 69/2020/QH14, Nghị định 112/2021/NĐ-CP, Thông tư 02/2024/TT-BLĐTBXH,
Thông tư 21/2021/TT-BLĐTBXH.

---

## Luồng làm việc

```
Tải hồ sơ lên  ->  Soát dữ liệu đã đọc  ->  Kết quả kiểm tra
   (bước 1)            (bước 2)               (bước 3)
```

**Bước 1 — Tải hồ sơ lên.** Chọn khu vực · quốc gia · loại hình lao động, rồi tải lên các
file PDF của bộ hồ sơ (hợp đồng cung ứng, văn bản đăng ký, giấy phép đối tác, thư yêu cầu
tuyển dụng…). Hệ thống đọc **tất cả** các file: giấy tờ phụ được dùng làm nguồn tham khảo
để bù các trường mà tài liệu chính bỏ trống.

Tiến độ hiện theo thời gian thực đến từng trang, ngay trên nút bấm. Chuyển sang trang khác
vẫn chạy tiếp — xong việc thì thông báo ✅ ở góc phải bấm được để đi thẳng tới kết quả.

**Bước 2 — Soát dữ liệu đã đọc.** Bảng trường đã trích xuất đặt cạnh văn bản OCR gốc. Di
chuột vào một giá trị thì đúng vị trí của nó trong văn bản được tô sáng, nên kiểm chứng
được ngay thay vì phải tin. Giá trị nào OCR đọc sai thì bấm ✎ sửa tay tại chỗ rồi kiểm
tra lại — hệ thống coi giá trị người dùng nhập là đúng.

**Bước 3 — Kết quả.** Mỗi trường một thẻ: tên trường ↔ kết quả đánh giá, nội dung chia mục
**1. Giải thích kết quả → 2. Căn cứ pháp lý → 3. Rủi ro** (mục 3 chỉ hiện khi không hợp lệ).
Trích dẫn điều luật được lọc theo đúng trường đang xét, không phải điều luật của trường khác.
Tải báo cáo PDF về nếu cần lưu hồ sơ.

Ba mức kết luận, ưu tiên từ nặng đến nhẹ:

| Kết luận           | Nghĩa                                                                                         |
| ------------------ | --------------------------------------------------------------------------------------------- |
| `FAIL`             | Trái điều kiện bắt buộc, có căn cứ pháp lý rõ ràng                                            |
| `NEEDS_SUPPLEMENT` | Thiếu tài liệu, thiếu điều khoản bắt buộc, dữ liệu đáng ngờ, hoặc không đủ căn cứ để kết luận |
| `PASS`             | Đủ hồ sơ, điều khoản hợp lệ                                                                   |

**Một bộ hồ sơ — một kết luận.** Kết luận chung tính **cả** nhóm _các chi phí_: hồ sơ có khoản
thu ngoài danh mục được phép thì cả hồ sơ không hợp lệ, không còn hai badge tách rời để người
duyệt tự ghép. Các khoản thu / chi phí lạ được liệt kê thành gạch đầu dòng ngay dưới kết luận.

---

## Các chức năng

**Đọc hồ sơ.** Trang PDF có lớp chữ nhúng sạch được đọc thẳng, nhưng trước đó hệ thống đọc
đối chứng một trang bằng ảnh: lớp chữ khác nội dung in trên trang (chữ ẩn) thì bỏ lớp chữ,
đọc ảnh cả tệp và gắn cờ `TEXT_LAYER_MISMATCH`. Trang scan được đọc bằng **Vintern-1B-v3.5**
(mô hình thị giác-ngôn ngữ tiếng Việt): mô hình tự chép theo thứ tự đọc, độ tin cậy từng
dòng tính từ xác suất token. Tự xoay trang nằm ngang hoặc lộn ngược; xóa dấu mộc đỏ đè chữ
trước khi đọc; trang quá dày thì chia đôi rồi đọc từng nửa; dòng lặp vô hạn bị cắt.

**Trích xuất trường.** Ưu tiên regex và heuristic (rẻ, ổn định, giải thích được); mô hình
ngôn ngữ chỉ được gọi để bù trường còn thiếu và sửa chính tả — không đổi nội dung. Bộ trường
dùng để trích xuất là bộ đã dựng đủ 3 tầng khu vực · quốc gia · loại hình của lượt tải lên.

**Chống bịa nhiều lớp.** Giá trị do mô hình sinh phải neo vào một đoạn có thật trong văn bản
OCR và được đoạn đó phủ gần trọn; số tiền, số lượng phải có đúng chữ số trong đoạn bằng
chứng; độ tin cậy bị chặn trần. Ngân hàng cụm đáp án không thay giá trị khi hai bên khác nhau
ở chữ phủ định, thứ tự chủ thể hay con số. Không chứng minh được xuất
xứ thì trường để trống — trống là `NEEDS_SUPPLEMENT`, còn hơn một con số không có gốc.

**Kiểm soát chất lượng đầu vào.** Nhiều lớp cổng trước khi kết luận: chất lượng OCR, ngày ký,
đơn vị tiền tệ, biên độ lương so với thị trường, đối chiếu chéo tên nước với thị trường đã
chọn, khoản thu bị cấm, khoản chi phí / ký quỹ / lương lệch giữa văn bản đăng ký và hợp đồng
cung ứng. Trường nào bị gắn cờ nghiêm trọng thì hạ về `NEEDS_SUPPLEMENT`. Kết luận PASS hoặc
FAIL do mô hình đưa ra mà không gắn được điều luật nào cũng bị hạ về `NEEDS_SUPPLEMENT`; căn
cứ do hệ thống tự gắn (mô hình không chỉ ra) được đánh dấu "tự gắn — cần đối chiếu".

**Kiểm tra tuân thủ theo thị trường.** Bộ trường bắt buộc theo Điều 19 khoản 2 Luật 69/2020,
cấu hình riêng cho từng khu vực · quốc gia · loại hình lao động. Kiểm tra tất định (số dương,
định dạng địa điểm, mức ký quỹ, danh mục khoản thu được phép) chạy bằng code thuần; mô hình
ngôn ngữ chỉ được gọi một lần cho các trường cần đối chiếu quy định.

**Phân tích cả bộ hồ sơ.** Không chỉ xét từng file: kiểm đủ thành phần theo Điều 20, nhận
diện vai trò từng tài liệu, phát hiện đơn đề nghị bị nhầm thành giấy phép, kiểm hiệu lực và
phạm vi giấy phép đối tác.

**Thống kê và kho hồ sơ.** Tỉ lệ hợp lệ theo thị trường; tìm kiếm toàn văn trong lịch sử
kiểm tra (bỏ dấu vẫn tìm được), lọc theo thị trường và kết quả; nhắc hạn hợp đồng sắp hết
hiệu lực.

**Tự tạo cấu hình.** Trang **Cấu hình** cho phép tự khai bộ trường công việc hoặc thị trường
mới rồi bấm **Áp dụng** — không cần lập trình viên, không cần sửa file trong mã nguồn. Gỡ áp
dụng là hệ thống quay về mặc định ngay. Mỗi nhóm giữ tối đa 200 cấu hình do người dùng tạo;
sửa lại một cấu hình đã có thì không tính vào trần này.

**Giao diện.** Song ngữ Việt/Anh, nền sáng/tối, bảng thông báo gom mọi việc đã chạy xong dù
đang ở trang nào.

---