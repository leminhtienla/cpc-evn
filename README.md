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
| `GET /api/remote/spider/thongTinChiSo?customerCode=...&orgCode=...` | Log chỉ số đọc mỗi ~6 tiếng/lần trong kỳ hiện tại — dùng để tính chỉ số realtime VÀ tiêu thụ theo từng ngày (tự tính chênh lệch) |
| `GET /api/remote/thongTinHoaDonSpider?customerCode=...&maDonViQuanLy=...` | Lịch sử hóa đơn đầy đủ theo tháng (kWh + tiền + chỉ số đầu/cuối kỳ), phủ nhiều năm |

## Đã xác nhận hoạt động (test với dữ liệu thật)

Tiêu thụ theo ngày tính từ `spider/thongTinChiSo` đã test khớp đúng cho
các ngày mà API tra cứu chính thức (`chisongay`) không có dữ liệu (ví dụ
27, 28, 29/7 khi test) — xem cách tính trong `sensor.py` hàm
`_daily_breakdown()`.

## Giới hạn đã biết

- **Không có tiền điện theo từng ngày** — EVN chỉ tính tiền theo bậc
  thang lũy tiến hàng THÁNG, không có khái niệm "tiền điện của 1 ngày".
  Sensor theo ngày chỉ có kWh.
- Tài khoản có bật captcha khi đăng nhập sẽ **không đăng nhập tự động
  được** qua integration này (EVN yêu cầu giải captcha thủ công).

## Sensors

- `Chỉ số thời gian thực` (kWh) — chỉ số công tơ mới nhất
- `Tiêu thụ theo ngày` (kWh) — tự tính chênh lệch chỉ số giữa các ngày, attribute `Chi tiết` chứa toàn bộ bảng ngày tính được
- `Tiêu thụ tháng này` (kWh)
- `Tiền điện tháng này` (VNĐ)
- `Tiêu thụ cùng kỳ năm trước` (kWh)
- `Tiền điện cùng kỳ năm trước` (VNĐ)
- `Lịch sử hóa đơn theo tháng` — attribute chứa toàn bộ lịch sử (có thể nhiều năm)
