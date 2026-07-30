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
| `GET /api/remote/biendongtreothao?customerCode=...` | Lịch sử treo tháo / thay công tơ |

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
- `Tổng tiêu thụ dồn kỳ này (theo ngày)` (kWh) — tổng cộng dồn từ đầu kỳ hóa đơn hiện tại tới nay, attribute `Chi tiết` chứa bảng đầy đủ từng ngày
- `Tiêu thụ hôm nay` (kWh) — kèm attribute hôm qua, tháng này, tháng trước, ngưỡng cảnh báo
- `Kỳ hóa đơn gần nhất` *(entity riêng)* — state dạng "Tháng 6/2026", cho biết `Tiêu thụ/Tiền điện kỳ hóa đơn gần nhất` đang là của tháng nào
- `Tiêu thụ kỳ hóa đơn gần nhất` (kWh)
- `Tiền điện kỳ hóa đơn gần nhất` (VNĐ)
- `Tháng hiện tại (đang chạy)` *(entity riêng)* — state dạng "Tháng 7/2026", theo giờ server EVN
- `Tiêu thụ tháng này` (kWh) — tháng dương lịch đang chạy, chưa chốt kỳ nên **chưa có tiền điện**
- `Tiêu thụ cùng kỳ năm trước` / `Tiền điện cùng kỳ năm trước` — so với cùng THÁNG của kỳ hóa đơn gần nhất (attribute có Tháng/Năm chính xác)
- `Lịch sử hóa đơn theo tháng` — attribute chứa toàn bộ lịch sử (có thể nhiều năm)
- `Lịch sử treo tháo công tơ` — attribute chứa lịch sử thay/lắp công tơ

Tên các sensor số liệu (tiêu thụ/tiền điện) là **tên tĩnh, cố định** —
muốn biết đang thuộc tháng nào thì xem 2 entity riêng `Kỳ hóa đơn gần
nhất` và `Tháng hiện tại (đang chạy)` ở trên, hoặc xem attribute
"Tháng"/"Năm" của từng sensor.

### ⚠️ Lưu ý quan trọng: "tháng này" vs "kỳ hóa đơn gần nhất"

EVN chốt hóa đơn theo **ngày ghi công tơ**, không phải cuối tháng dương
lịch. Ví dụ hôm nay 30/7 nhưng kỳ hóa đơn tháng 6 mới vừa chốt xong (kết
thúc 30/6) — tháng 7 vẫn đang chạy, **chưa có hóa đơn, chưa có tiền
điện**. Vì vậy 2 khái niệm này KHÁC NHAU và không thể gộp làm một:

- **"Tiêu thụ tháng này"**: số kWh tháng dương lịch hiện tại tính tới
  thời điểm hiện tại (chưa đầy đủ cả tháng, tăng dần mỗi ngày).
- **"Kỳ hóa đơn gần nhất"**: kỳ ĐÃ CHỐT gần nhất - đầy đủ, có tiền điện
  chính thức, nhưng có thể là tháng trước chứ không phải tháng hiện tại.
