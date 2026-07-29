"""API client cho cổng self-service CPC (cskh.cpc.vn).

Toàn bộ endpoint trong file này được xác nhận từ HAR capture thực tế
(DevTools Network) khi đăng nhập và duyệt cskh.cpc.vn, KHÔNG phải suy
đoán. Xem README.md để biết nguồn từng endpoint.
"""

import logging
from typing import Any, Dict, List, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://cskh-api.cpc.vn"
LOGIN_URL = f"{BASE_URL}/api/cskh/user/login"
REFERER = "https://cskh.cpc.vn/"


class CPCAuthError(Exception):
    """Sai tài khoản/mật khẩu hoặc token hết hạn không refresh được."""


class CPCApiError(Exception):
    """Lỗi khác khi gọi API (mạng, HTTP status, response không hợp lệ)."""


class CPCApi:
    """Client gọi API cổng self-service CPC."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str, customer_code: str):
        self._session = session
        self._username = username
        self._password = password
        self.customer_code = customer_code
        self.token: Optional[str] = None
        self.org_code: Optional[str] = None  # vd "PP0700", lấy từ customer info

    def _headers(self, with_auth: bool = True) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Referer": REFERER,
        }
        if with_auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def login(self) -> None:
        """Đăng nhập, lưu token vào self.token. Raise CPCAuthError nếu sai tài khoản."""
        payload = {
            "username": self._username,
            "password": self._password,
            "grant_type": "password",
            "scope": "CSKH",
            "ThongTinCaptcha": {"captcha": "undefined", "token": "undefined"},
        }
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
            "Referer": REFERER,
        }
        try:
            async with self._session.post(LOGIN_URL, json=payload, headers=headers, ssl=False) as resp:
                if resp.status in (400, 401):
                    raise CPCAuthError("Sai tên đăng nhập hoặc mật khẩu")
                if resp.status != 200:
                    text = await resp.text()
                    raise CPCApiError(f"Login thất bại, HTTP {resp.status}: {text[:300]}")
                data = await resp.json()
        except aiohttp.ClientError as err:
            raise CPCApiError(f"Lỗi kết nối khi đăng nhập: {err}") from err

        token = data.get("access_token")
        if not token:
            # Một số trường hợp EVN yêu cầu captcha (isShowCaptcha=true) - login
            # bằng script sẽ luôn fail trong trường hợp này, cần báo rõ cho user.
            if data.get("isShowCaptcha"):
                raise CPCAuthError(
                    "Tài khoản yêu cầu nhập captcha khi đăng nhập - "
                    "không thể đăng nhập tự động. Thử đăng nhập thủ công trên "
                    "cskh.cpc.vn một lần rồi thử lại."
                )
            raise CPCAuthError(f"Login không trả về access_token: {data}")
        self.token = token

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None, retry: bool = True) -> Any:
        """GET có tự động re-login 1 lần nếu token hết hạn (401)."""
        if not self.token:
            await self.login()

        url = f"{BASE_URL}{path}"
        async with self._session.get(url, params=params, headers=self._headers(), ssl=False) as resp:
            if resp.status == 401 and retry:
                await self.login()
                return await self._get(path, params=params, retry=False)
            if resp.status != 200:
                text = await resp.text()
                raise CPCApiError(f"GET {path} thất bại, HTTP {resp.status}: {text[:300]}")
            return await resp.json()

    async def get_customer_info(self) -> Dict[str, Any]:
        """Thông tin khách hàng: tên, địa chỉ, mã công tơ, orgCode..."""
        data = await self._get(f"/api/remote/customers/{self.customer_code}/info")
        self.org_code = data.get("orgCode")
        return data

    async def get_daily_view(self) -> List[Dict[str, Any]]:
        """Tiêu thụ theo TỪNG NGÀY - API CHÍNH THỨC của EVN đã tính sẵn
        (sl-tieu-thu-view), không cần tự tính chênh lệch chỉ số nữa.

        Mỗi phần tử: ngay (ISO date), sanLuongNgay (kWh tiêu thụ ngày đó,
        đã tính sẵn), sanLuongTrungBinh (trung bình cộng dồn từ đầu kỳ),
        chiSoCongTo (chi tiết chỉ số đầu/cuối ngày), message (ghi chú của
        EVN - ví dụ ngày thiếu dữ liệu chỉ số sẽ dồn sang ngày sau).
        """
        if not self.org_code:
            await self.get_customer_info()
        data = await self._get(
            "/api/remote/meter/rf/sl-tieu-thu-view",
            params={"customerCode": self.customer_code, "orgCode": self.org_code},
        )
        return data if isinstance(data, list) else []

    async def get_consumption_summary(self) -> Optional[Dict[str, Any]]:
        """Tóm tắt tiêu thụ hôm nay/hôm qua/tháng này/tháng trước + ngưỡng
        cảnh báo, đã được EVN tính sẵn (power-consumption-alerts).

        Trả về dict với electricConsumption = {electricConsumptionToday,
        electricConsumptionYesterday, electricConsumptionThisMonth,
        electricConsumptionLastMonth, electricConsumptionThresholdDay,
        electricConsumptionThresholdMonth, ...}. Có thể là None nếu
        khách hàng chưa từng bật tính năng cảnh báo tiêu thụ trên app EVN.
        """
        try:
            data = await self._get(
                f"/api/cskh/power-consumption-alerts/by-customer-code/{self.customer_code}"
            )
        except CPCApiError:
            return None
        return data if isinstance(data, dict) else None

    async def get_meter_change_history(self) -> List[Dict[str, Any]]:
        """Lịch sử treo tháo / thay công tơ (biendongtreothao)."""
        data = await self._get(
            "/api/remote/biendongtreothao",
            params={"customerCode": self.customer_code, "appType": "Web", "isLoad": 0},
        )
        result = data.get("result") or []
        return result

    async def get_index_log(self) -> List[Dict[str, Any]]:
        """Log chỉ số công tơ đọc mỗi ~6 tiếng/lần (spider/thongTinChiSo).

        Trả về danh sách các bản ghi (thường phủ trọn kỳ hóa đơn hiện tại,
        ~5-6 lần đọc/ngày), mỗi bản ghi có CS_MOI (chỉ số tuyệt đối) và
        NGAYGIO (thời điểm đọc, ISO datetime). Dùng để suy ra:
        - Chỉ số/tiêu thụ thời gian thực (bản ghi có NGAYGIO lớn nhất).
        - Tiêu thụ theo TỪNG NGÀY (lấy bản ghi cuối mỗi ngày, trừ chỉ số
          giữa 2 ngày liên tiếp).
        """
        if not self.org_code:
            await self.get_customer_info()
        data = await self._get(
            "/api/remote/spider/thongTinChiSo",
            params={"customerCode": self.customer_code, "orgCode": self.org_code},
        )
        result = data.get("chiSoGiao") or []
        return result

    async def get_bill_history(self) -> List[Dict[str, Any]]:
        """Lịch sử hóa đơn đầy đủ theo tháng (thongTinHoaDonSpider).

        Mỗi phần tử: THANG/NAM, DIEN_TTHU (kWh tiêu thụ trong kỳ),
        TONG_TIEN (VNĐ), CHISO_CU/CHISO_MOI (chỉ số đầu/cuối kỳ),
        NGAY_DKY/NGAY_CKY (ngày đầu/cuối kỳ). Phủ toàn bộ lịch sử có
        trên hệ thống (có thể tới hàng chục năm), sắp mới nhất trước.
        """
        if not self.org_code:
            await self.get_customer_info()
        data = await self._get(
            "/api/remote/thongTinHoaDonSpider",
            params={"customerCode": self.customer_code, "maDonViQuanLy": self.org_code},
        )
        result = data.get("result") or []
        return result

    async def async_fetch_all(self) -> Dict[str, Any]:
        """Lấy toàn bộ dữ liệu 1 lần, dùng cho coordinator."""
        customer_info = await self.get_customer_info()
        index_log = await self.get_index_log()
        bill_history = await self.get_bill_history()
        daily_view = await self.get_daily_view()
        consumption_summary = await self.get_consumption_summary()
        meter_changes = await self.get_meter_change_history()
        return {
            "customer_info": customer_info,
            "index_log": index_log,
            "bill_history": bill_history,
            "daily_view": daily_view,
            "consumption_summary": consumption_summary,
            "meter_changes": meter_changes,
        }
