"""API client cho cổng self-service CPC (cskh.cpc.vn).

Toàn bộ endpoint trong file này được xác nhận từ HAR capture thực tế
(DevTools Network) khi đăng nhập và duyệt cskh.cpc.vn, KHÔNG phải suy
đoán. Xem README.md để biết nguồn từng endpoint.
"""

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://cskh-api.cpc.vn"
LOGIN_URL = f"{BASE_URL}/api/cskh/user/login"
REFERER = "https://cskh.cpc.vn/"

# Công cụ tính hoá đơn điện CHÍNH THỨC của EVN toàn quốc (calc.evn.com.vn),
# domain KHÁC hẳn cskh-api.cpc.vn, KHÔNG cần đăng nhập (public API, không
# có Authorization header trong request thực tế bắt được). Dùng để dự
# tính tiền điện dựa trên số kWh tiêu thụ, áp dụng đúng biểu giá bậc
# thang sinh hoạt hiện hành. Đã đối chiếu khớp chính xác tới từng đồng
# với hoá đơn thật (134 kWh -> 305,230 VNĐ).
BILL_CALC_URL = "https://calc.evn.com.vn/TinhHoaDon/api/Calculate"


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
        self.server_time_header: Optional[str] = None  # header "Date" thô từ response gần nhất

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
            # Header "Date" chuẩn HTTP - cho biết giờ hiện tại theo server
            # EVN (GMT), dùng để đối chiếu khi tính "hôm nay/tháng này" có
            # đúng ngày theo server hay không (tránh lệch múi giờ).
            server_date = resp.headers.get("Date")
            if server_date:
                self.server_time_header = server_date
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

    async def estimate_bill(self, san_luong_kwh: float, ngay_dky: date, ngay_cky: date, so_ho: int = 1) -> Optional[Dict[str, Any]]:
        """Gọi công cụ tính hoá đơn CHÍNH THỨC của EVN (calc.evn.com.vn) để
        tính tiền điện ứng với 1 mức tiêu thụ (kWh) cho trước, theo đúng
        biểu giá bậc thang sinh hoạt hiện hành - KHÔNG cần đăng nhập.

        ngay_dky/ngay_cky: ngày đầu/cuối kỳ (dùng để xác định số ngày
        trong kỳ - biểu giá bậc thang co giãn theo số ngày, ví dụ kỳ 2
        tháng thì các bậc nhân đôi).

        Trả về dict gốc từ EVN (có HDN_HDON[0] chứa SO_TIEN, TIEN_GTGT,
        TONG_TIEN...) hoặc None nếu lỗi.
        """
        payload = {
            "KIMUA_CSPK": "0",
            "LOAI_DDO": "1",
            "SO_HO": so_ho,
            "MA_CAPDAP": "1",
            "NGAY_DKY": ngay_dky.strftime("%d/%m/%Y"),
            "NGAY_CKY": ngay_cky.strftime("%d/%m/%Y"),
            "NGAY_DGIA": "01/01/1900",
            "HDG_BBAN_APGIA": [
                {
                    "LOAI_BCS": "KT",
                    "TGIAN_BANDIEN": "KT",
                    "MA_NHOMNN": "SHBT",
                    "MA_NGIA": "A",
                }
            ],
            "GCS_CHISO": [
                {
                    "BCS": "KT",
                    "SAN_LUONG": str(round(san_luong_kwh, 2)),
                    "LOAI_CHISO": "DDK",
                }
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://calc.evn.com.vn/",
            "Origin": "https://calc.evn.com.vn",
        }
        try:
            async with self._session.post(BILL_CALC_URL, json=payload, headers=headers, ssl=False) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.debug(f"estimate_bill failed HTTP {resp.status}: {text[:300]}")
                    return None
                data = await resp.json()
        except aiohttp.ClientError as err:
            _LOGGER.debug(f"estimate_bill connection error: {err}")
            return None

        bills = (data.get("Data") or {}).get("HDN_HDON") or []
        if not bills:
            return None
        return bills[0]


    def _current_period_bounds(self, bill_history: List[Dict[str, Any]]) -> tuple:
        """Ước tính ngày đầu/cuối kỳ HIỆN TẠI (đang chạy, chưa chốt) dựa
        trên kỳ ĐÃ CHỐT gần nhất: kỳ mới bắt đầu ngay sau ngày chốt kỳ
        trước, độ dài kỳ giả định bằng độ dài kỳ trước (thường ~30 ngày).
        Nếu chưa có kỳ nào (khách hàng mới), fallback về tháng dương lịch
        hiện tại theo giờ server.
        """
        latest = None
        if bill_history:
            latest = sorted(bill_history, key=lambda r: (r.get("NAM", 0), r.get("THANG", 0)))[-1]
        today = date.today()
        if self.server_time_header:
            try:
                from email.utils import parsedate_to_datetime
                today = parsedate_to_datetime(self.server_time_header).date()
            except (TypeError, ValueError):
                pass

        if latest and latest.get("NGAY_CKY"):
            try:
                ngay_dky_truoc = date.fromisoformat(latest["NGAY_DKY"][:10])
                ngay_cky_truoc = date.fromisoformat(latest["NGAY_CKY"][:10])
                do_dai_ky = (ngay_cky_truoc - ngay_dky_truoc).days + 1
                start = ngay_cky_truoc + timedelta(days=1)
                end = start + timedelta(days=do_dai_ky - 1)
                return start, end, today
            except (ValueError, TypeError, KeyError):
                pass

        # Fallback: tháng dương lịch hiện tại
        start = today.replace(day=1)
        if today.month == 12:
            end = date(today.year, 12, 31)
        else:
            end = date(today.year, today.month + 1, 1) - timedelta(days=1)
        return start, end, today

    async def async_fetch_all(self) -> Dict[str, Any]:
        """Lấy toàn bộ dữ liệu 1 lần, dùng cho coordinator."""
        customer_info = await self.get_customer_info()
        index_log = await self.get_index_log()
        bill_history = await self.get_bill_history()
        consumption_summary = await self.get_consumption_summary()

        bill_estimate = None
        period_start, period_end, today_ref = self._current_period_bounds(bill_history)
        kwh_so_far = None
        if consumption_summary:
            kwh_so_far = (consumption_summary.get("electricConsumption") or {}).get("electricConsumptionThisMonth")

        if kwh_so_far is not None and kwh_so_far > 0:
            so_ngay_ky = (period_end - period_start).days + 1
            so_ngay_da_qua = max((today_ref - period_start).days + 1, 1)
            so_ngay_da_qua = min(so_ngay_da_qua, so_ngay_ky)
            kwh_du_tinh_ca_ky = kwh_so_far / so_ngay_da_qua * so_ngay_ky

            bill_result = await self.estimate_bill(kwh_du_tinh_ca_ky, period_start, period_end)
            if bill_result:
                bill_estimate = {
                    "kwh_da_dung": kwh_so_far,
                    "kwh_du_tinh_ca_ky": round(kwh_du_tinh_ca_ky, 2),
                    "so_ngay_da_qua": so_ngay_da_qua,
                    "so_ngay_ky": so_ngay_ky,
                    "ngay_dau_ky": period_start.isoformat(),
                    "ngay_cuoi_ky_du_kien": period_end.isoformat(),
                    "tien_truoc_thue": bill_result.get("SO_TIEN"),
                    "tien_thue": bill_result.get("TIEN_GTGT"),
                    "tong_tien_du_tinh": bill_result.get("TONG_TIEN"),
                }

        return {
            "customer_info": customer_info,
            "index_log": index_log,
            "bill_history": bill_history,
            "consumption_summary": consumption_summary,
            "server_time_header": self.server_time_header,
            "bill_estimate": bill_estimate,
        }
