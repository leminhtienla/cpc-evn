"""Constants for CPC (Điện lực Miền Trung) integration.

Component RIÊNG BIỆT, xây từ đầu - KHÔNG liên quan tới bản fork từ npc
trước đó. Đăng nhập trực tiếp bằng tài khoản cổng cskh.cpc.vn (portal
self-service của CPC), không dùng chung API/login với các EVN vùng khác.
"""

DOMAIN = "cpc"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_CUSTOMER_CODE = "customer_code"
CONF_TARIFF = "tariff"

# Biểu giá dùng để tính "Dự tính tiền điện" qua calc.evn.com.vn
TARIFF_SINH_HOAT = "sinh_hoat"
TARIFF_KINH_DOANH_1_GIA = "kinh_doanh_1_gia"
TARIFF_KINH_DOANH_3_GIA = "kinh_doanh_3_gia"
TARIFF_OPTIONS = [TARIFF_SINH_HOAT, TARIFF_KINH_DOANH_1_GIA, TARIFF_KINH_DOANH_3_GIA]
TARIFF_DEFAULT = TARIFF_SINH_HOAT

SCAN_INTERVAL_SECONDS = 1800  # 30 phút - dữ liệu tháng/ngày không cần quét dày
