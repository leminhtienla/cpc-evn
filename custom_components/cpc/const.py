"""Constants for CPC (Điện lực Miền Trung) integration.

Component RIÊNG BIỆT, xây từ đầu - KHÔNG liên quan tới bản fork từ npc
trước đó. Đăng nhập trực tiếp bằng tài khoản cổng cskh.cpc.vn (portal
self-service của CPC), không dùng chung API/login với các EVN vùng khác.
"""

DOMAIN = "cpc"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_CUSTOMER_CODE = "customer_code"

SCAN_INTERVAL_SECONDS = 1800  # 30 phút - dữ liệu tháng/ngày không cần quét dày
