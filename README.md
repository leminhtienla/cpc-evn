# CPC - Điện lực Miền Trung (Home Assistant custom integration)

Integration **độc lập, xây từ đầu** — không liên quan tới bất kỳ fork EVN
đa vùng miền nào trước đó. Đăng nhập trực tiếp bằng tài khoản cổng
self-service **cskh.cpc.vn**.

## Nguồn dữ liệu

Tất cả endpoint dưới đây được xác nhận bằng cách bắt request thực tế
(HAR capture, DevTools Network) khi đăng nhập và duyệt cskh.cpc.vn:

| Endpoint | Dùng để làm gì |
|---|---|
| `POST /api/cskh/user/login` | Đăng nhập, lấy Bearer token |
| `GET /api/remote/customers/{code}/info` | Thông tin khách hàng, mã công tơ, orgCode |
| `GET /api/remote/spider/thongTinChiSo?customerCode=...&orgCode=...` | Log chỉ số đọc mỗi ~6 tiếng/lần trong kỳ hiện tại — dùng cho chỉ số thời gian thực |
| `GET /api/remote/meter/rf/sl-tieu-thu-view?customerCode=...&orgCode=...` | **Tiêu thụ theo TỪNG NGÀY - API CHÍNH THỨC của EVN đã tính sẵn**, không cần tự tính chênh lệch |
| `GET /api/cskh/power-consumption-alerts/by-customer-code/{code}` | Tóm tắt hôm nay/hôm qua/tháng này/tháng trước + ngưỡng cảnh báo, EVN tính sẵn |
| `GET /api/remote/thongTinHoaDonSpider?customerCode=...&maDonViQuanLy=...` | Lịch sử hóa đơn đầy đủ theo tháng (kWh + tiền + chỉ số đầu/cuối kỳ), phủ nhiều năm |
| `POST https://calc.evn.com.vn/TinhHoaDon/api/Calculate` | **Công cụ tính hoá đơn CHÍNH THỨC của EVN toàn quốc** (domain khác hẳn, KHÔNG cần đăng nhập) - tính tiền điện theo biểu giá bậc thang từ số kWh cho trước. Đã đối chiếu khớp chính xác tới từng đồng với hoá đơn thật (134 kWh → 305.230 VNĐ). |
| `GET /api/remote/customers/{code}/spider/chitiet?maDonViQuanLy=...` | Breakdown "Tiêu thụ tháng hiện tại" theo từng kỳ con (kỳ trước chưa chốt / kỳ hiện tại theo lịch dương) - đúng số liệu popup "(Chi tiết)" trên web |

## Đã xác nhận hoạt động (test với dữ liệu thật)

- Tiêu thụ theo ngày từ `sl-tieu-thu-view` (API chính thức) đã test khớp
  1:1 với `power-consumption-alerts` cho cùng ngày (29/7: cả 2 đều ra
  3.37 kWh) — xác nhận độ tin cậy cao, không cần tự tính chênh lệch chỉ
  số như cách cũ nữa.

## Giới hạn đã biết

- **Không có tiền điện theo từng ngày** — EVN chỉ tính tiền theo bậc
  thang lũy tiến hàng THÁNG, không có khái niệm "tiền điện của 1 ngày".
  Sensor theo ngày chỉ có kWh.
- `power-consumption-alerts` có thể trả về rỗng nếu tài khoản chưa từng
  bật tính năng "Cảnh báo tiêu thụ điện" trên app/web EVN — khi đó sensor
  "Tiêu thụ hôm nay" sẽ là `None`, không phải lỗi.
- Tài khoản có bật captcha khi đăng nhập sẽ **không đăng nhập tự động
  được** qua integration này (EVN yêu cầu giải captcha thủ công).

## Sensors

- `Chỉ số thời gian thực` (kWh) — chỉ số công tơ mới nhất
- `Tiêu thụ hôm nay` (kWh) — kèm attribute hôm qua, tháng này, tháng trước, ngưỡng cảnh báo
- `Tháng hiện tại` *(entity riêng)* — tháng chứa NGÀY BẮT ĐẦU của kỳ đang mở (không phải tháng dương lịch hôm nay - xem lưu ý bên dưới)
- `Tiêu thụ tháng hiện tại` (kWh) — kỳ đang chạy, chưa chốt kỳ nên **chưa có tiền điện** chính thức
- `Tiêu thụ tháng tạm chốt` (kWh) — CHỈ có giá trị khi EVN đã có số đọc ĐỊNH KỲ thật (LOAI_CHISO=DDK, cùng loại dùng để chốt hóa đơn) cho tháng hiện tại; nếu chưa có (chỉ có số đọc spider thô, real-time) thì = 0, không tự "tạm chốt thay EVN"
- `Tiêu thụ tháng tiếp theo` (kWh) — phần EVN đã bắt đầu tính riêng cho tháng kế tiếp dù tháng hiện tại chưa chốt; khi qua giao thời: "Tiêu thụ tháng hiện tại" = "Tiêu thụ tháng tạm chốt" + "Tiêu thụ tháng tiếp theo"
- `Dự tính tiền điện tháng hiện tại` (VNĐ) — tiền điện tính trên số kWh **ĐÃ DÙNG TỚI HIỆN TẠI** (không ngoại suy/dự đoán cho cả tháng), theo đúng biểu giá bậc thang qua công cụ tính hoá đơn EVN. Ưu tiên dùng số "tạm chốt" thật (LOAI_CHISO=DDK) nếu có, tự fallback qua "Tiêu thụ tháng hiện tại" nếu chưa có DDK. Hiểu đơn giản: "nếu EVN chốt sổ ngay bây giờ thì tiền điện là bao nhiêu" - số này tăng dần theo từng chu kỳ cập nhật khi dùng thêm điện
- `Đơn giá điện hiện tại` (VNĐ/kWh) — đơn giá của **bậc cao nhất** đã chạm tới với mức tiêu thụ hiện tại (biểu giá Sinh hoạt), hoặc mức giá cố định đang áp dụng (biểu giá Kinh doanh). Lấy TRỰC TIẾP từ response thật của EVN (`HDN_HDONCTIET`), không tự lưu bảng giá cứng trong code nên không lo bị lỗi thời khi EVN điều chỉnh giá
- `Tháng trước` *(entity riêng)* — state dạng "Tháng 6/2026" (thực chất là kỳ hóa đơn **ĐÃ CHỐT** gần nhất - có thể không đúng nghĩa đen "tháng dương lịch trước" nếu ngày chốt sổ lệch)
- `Tiêu thụ tháng trước` (kWh)
- `Tiền điện tháng trước` (VNĐ)
- `Tiêu thụ tháng trước năm trước` (kWh) — so với **Tháng trước**, KHÔNG PHẢI Tháng hiện tại
- `Tiền điện tháng trước năm trước` (VNĐ) — tương tự

## ⚠️ Lưu ý quan trọng: "Tháng hiện tại" KHÔNG phải tháng dương lịch

EVN chốt kỳ hóa đơn theo **ngày ghi công tơ**, không tự động reset theo
lịch dương. Nếu EVN chưa chạy job chốt kỳ tháng trước, kỳ "đang chạy" vẫn
tiếp tục cộng dồn từ tháng trước dù thực tế đã sang tháng mới. Ví dụ đã
gặp thực tế: hôm nay 01/08/2026 nhưng EVN chưa chốt kỳ tháng 7 → "Tháng
hiện tại" vẫn hiện **"Tháng 7/2026"**, "Tiêu thụ tháng hiện tại" vẫn cộng
dồn từ 01/07 (136.35 kWh), KHÔNG reset về 0 lúc sang tháng 8. Đã đối
chiếu và khớp chính xác với dữ liệu thật trên `cskh.cpc.vn`.

## ⚠️ Lưu ý quan trọng: phân loại "tháng hiện tại" / "tháng tiếp theo" theo NGÀY, không theo tên EVN đặt

`spider/chitiet` trả về tên kỳ (`KY_HDON`) do EVN tự đặt, ví dụ "Kỳ 1 -
7/2026" hoặc "Kỳ hiện tại" - nhưng ý nghĩa của tên **"Kỳ hiện tại" THAY
ĐỔI** tùy theo EVN đã chốt kỳ trước hay chưa:
- **Trước khi chốt kỳ trước**: "Kỳ hiện tại" = dữ liệu của tháng SAU
  (đúng nghĩa "tháng tiếp theo").
- **Sau khi chốt kỳ trước**: "Kỳ hiện tại" = chính THÁNG HIỆN TẠI (không
  còn là "tháng sau" nữa).

Vì vậy code **không dựa vào chuỗi tên `KY_HDON`** để phân loại, mà **so
sánh tháng/năm thật** (`NGAY_DKY` của từng dòng) với entity `Tháng hiện
tại` (vốn đã tính đúng dựa trên hóa đơn đã chốt gần nhất) để xác định
dòng nào thuộc tháng hiện tại, dòng nào thuộc tháng sau.

## Biểu giá điện (chọn khi cấu hình integration, đổi được sau qua Options)

Dùng để tính sensor "Dự tính tiền điện tháng hiện tại", xác nhận từ HAR capture thật trên `calc.evn.com.vn`:

- **Sinh hoạt (bậc thang)** — mặc định, dành cho hộ gia đình. Đã đối chiếu khớp chính xác tới từng đồng với hoá đơn thật (134 kWh → 305.230 VNĐ).
- **Kinh doanh dịch vụ - 1 giá** — 1 mức giá cố định.
- **Kinh doanh dịch vụ - 3 giá** — có 3 mức giá theo khung giờ (Cao điểm/Bình thường/Thấp điểm). **Lưu ý:** vì không có dữ liệu tách theo khung giờ từ phía CPC, toàn bộ sản lượng được tính vào khung "Bình Thường" - đây là ước tính gần đúng, không phản ánh đúng cơ cấu giờ dùng điện thực tế.

**Đổi biểu giá sau khi đã add:** Settings → Devices & Services → tìm entry CPC → nút **Configure/Cấu hình** → chọn biểu giá mới → Submit. Integration tự reload, không cần xoá/tạo lại entry.

## Đã bỏ

- Sensor "Lịch sử hóa đơn theo tháng" và "Lịch sử treo tháo công tơ" đã bị
  xoá khỏi danh sách entity (không cần thiết cho mục đích theo dõi hàng
  ngày). Dữ liệu API `thongTinHoaDonSpider` vẫn được coordinator lấy về
  bình thường để phục vụ tính "Tháng trước"/"cùng kỳ năm trước", chỉ là
  không còn lộ ra thành entity riêng để xem toàn bộ lịch sử nữa.
- API `biendongtreothao` (treo tháo công tơ) không còn được gọi mỗi chu
  kỳ nữa, giảm 1 request không cần thiết.
- Sensor "Giờ server EVN" cũng đã bỏ (đã bỏ ở bản trước) - vẫn dùng ngầm
  bên trong để tính "Tháng hiện tại"/"Tiêu thụ tháng hiện tại" chính xác.

### ⚠️ Lưu ý quan trọng: "tháng này" vs "kỳ hóa đơn gần nhất"

EVN chốt hóa đơn theo **ngày ghi công tơ**, không phải cuối tháng dương
lịch. Ví dụ hôm nay 30/7 nhưng kỳ hóa đơn tháng 6 mới vừa chốt xong (kết
thúc 30/6) — tháng 7 vẫn đang chạy, **chưa có hóa đơn, chưa có tiền
điện**. Vì vậy 2 khái niệm này KHÁC NHAU và không thể gộp làm một:

- **"Tiêu thụ tháng này"**: số kWh tháng dương lịch hiện tại tính tới
  thời điểm hiện tại (chưa đầy đủ cả tháng, tăng dần mỗi ngày).
- **"Kỳ hóa đơn gần nhất"**: kỳ ĐÃ CHỐT gần nhất - đầy đủ, có tiền điện
  chính thức, nhưng có thể là tháng trước chứ không phải tháng hiện tại.
