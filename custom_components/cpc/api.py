"""API client cho cổng self-service CPC (cskh.cpc.vn).

Toàn bộ endpoint trong file này được xác nhận từ HAR capture thực tế
(DevTools Network) khi đăng nhập và duyệt cskh.cpc.vn, KHÔNG phải suy
đoán. Xem README.md để biết nguồn từng endpoint.
"""

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import aiohttp

from .const import TARIFF_DEFAULT, TARIFF_KINH_DOANH_1_GIA, TARIFF_KINH_DOANH_3_GIA, TARIFF_SINH_HOAT

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

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str, customer_code: str, tariff: str = TARIFF_DEFAULT):
        self._session = session
        self._username = username
        self._password = password
        self.customer_code = customer_code
        self.tariff = tariff
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

    async def get_spider_detail(self) -> List[Dict[str, Any]]:
        """Chi tiết breakdown sản lượng theo TỪNG KỲ con (spider/chitiet).

        Khi EVN CHƯA chốt kỳ trước, endpoint này trả về 2 (hoặc nhiều)
        phần tử tách biệt thay vì gộp chung 1 số như
        power-consumption-alerts, ví dụ:
        - "Kỳ 1 - 7/2026": SAN_LUONG=120 (từ 01/07 tới lúc EVN đọc chỉ số
          định kỳ cuối cùng, LOAI_CHISO="DDK")
        - "Kỳ hiện tại": SAN_LUONG=16.37 (từ đầu tháng dương lịch hiện tại
          tới giờ, đọc bằng spider, LOAI_CHISO="")
        Tổng 2 số này = đúng bằng electricConsumptionThisMonth của
        power-consumption-alerts (120 + 16.37 ≈ 136.35).
        """
        if not self.org_code:
            await self.get_customer_info()
        data = await self._get(
            f"/api/remote/customers/{self.customer_code}/spider/chitiet",
            params={"maDonViQuanLy": self.org_code},
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

    def _bill_calc_payload(self, san_luong_kwh: float, ngay_dky: date, ngay_cky: date, so_ho: int) -> Dict[str, Any]:
        """Build payload theo đúng biểu giá đã chọn (self.tariff).

        3 biểu giá được xác nhận từ HAR capture thực tế trên
        calc.evn.com.vn (không suy đoán):
        - Sinh hoạt bậc thang (mặc định): MA_CAPDAP=1, MA_NHOMNN=SHBT,
          1 dòng giá "KT". Đã đối chiếu khớp chính xác tới từng đồng với
          hoá đơn thật (134 kWh -> 305.230 VNĐ).
        - Kinh doanh dịch vụ 1 giá: MA_CAPDAP=2, MA_NHOMNN=KDDV, 1 dòng
          giá "BT" (Bình Thường).
        - Kinh doanh dịch vụ 3 giá: giống 1 giá nhưng có thêm 2 dòng giá
          CD (Cao Điểm) và TD (Thấp Điểm). LƯU Ý: vì không có dữ liệu
          tách theo khung giờ (Cao Điểm/Bình Thường/Thấp Điểm) từ phía
          CPC, toàn bộ sản lượng được tính vào khung "Bình Thường" - đây
          là ƯỚC TÍNH GẦN ĐÚNG, không phản ánh đúng cơ cấu giờ dùng điện
          thực tế của khách hàng 3 giá.
        """
        if self.tariff == TARIFF_KINH_DOANH_1_GIA:
            ma_capdap = "2"
            ma_nhomnn = "KDDV"
            hdg_bban_apgia = [
                {"LOAI_BCS": "BT", "TGIAN_BANDIEN": "BT", "MA_NHOMNN": ma_nhomnn, "MA_NGIA": "A"},
            ]
            gcs_chiso = [
                {"BCS": "BT", "SAN_LUONG": 0, "LOAI_CHISO": "CCS"},
                {"BCS": "CD", "SAN_LUONG": 0, "LOAI_CHISO": "CCS"},
                {"BCS": "TD", "SAN_LUONG": 0, "LOAI_CHISO": "CCS"},
                {"BCS": "VC", "SAN_LUONG": 0, "LOAI_CHISO": "CCS"},
                {"BCS": "BT", "SAN_LUONG": str(round(san_luong_kwh, 2)), "LOAI_CHISO": "DDK"},
            ]
            so_ho_val = "0"
        elif self.tariff == TARIFF_KINH_DOANH_3_GIA:
            ma_capdap = "2"
            ma_nhomnn = "KDDV"
            hdg_bban_apgia = [
                {"LOAI_BCS": "BT", "TGIAN_BANDIEN": "BT", "MA_NHOMNN": ma_nhomnn, "MA_NGIA": "A"},
                {"LOAI_BCS": "CD", "TGIAN_BANDIEN": "CD", "MA_NHOMNN": ma_nhomnn, "MA_NGIA": "A"},
                {"LOAI_BCS": "TD", "TGIAN_BANDIEN": "TD", "MA_NHOMNN": ma_nhomnn, "MA_NGIA": "A"},
            ]
            # Không có dữ liệu tách theo khung giờ - dồn hết vào "Bình
            # Thường" (xem lưu ý trong docstring).
            gcs_chiso = [
                {"BCS": "BT", "SAN_LUONG": 0, "LOAI_CHISO": "CCS"},
                {"BCS": "CD", "SAN_LUONG": 0, "LOAI_CHISO": "CCS"},
                {"BCS": "TD", "SAN_LUONG": 0, "LOAI_CHISO": "CCS"},
                {"BCS": "VC", "SAN_LUONG": 0, "LOAI_CHISO": "CCS"},
                {"BCS": "BT", "SAN_LUONG": str(round(san_luong_kwh, 2)), "LOAI_CHISO": "DDK"},
            ]
            so_ho_val = "0"
        else:  # TARIFF_SINH_HOAT (mặc định)
            ma_capdap = "1"
            hdg_bban_apgia = [
                {"LOAI_BCS": "KT", "TGIAN_BANDIEN": "KT", "MA_NHOMNN": "SHBT", "MA_NGIA": "A"},
            ]
            gcs_chiso = [
                {"BCS": "KT", "SAN_LUONG": str(round(san_luong_kwh, 2)), "LOAI_CHISO": "DDK"},
            ]
            so_ho_val = str(so_ho)

        return {
            "KIMUA_CSPK": "0",
            "LOAI_DDO": "3" if self.tariff == TARIFF_KINH_DOANH_3_GIA else "1",
            "SO_HO": so_ho_val,
            "MA_CAPDAP": ma_capdap,
            "NGAY_DKY": ngay_dky.strftime("%d/%m/%Y"),
            "NGAY_CKY": ngay_cky.strftime("%d/%m/%Y"),
            "NGAY_DGIA": "01/01/1900",
            "HDG_BBAN_APGIA": hdg_bban_apgia,
            "GCS_CHISO": gcs_chiso,
        }

    async def estimate_bill(self, san_luong_kwh: float, ngay_dky: date, ngay_cky: date, so_ho: int = 1) -> Optional[Dict[str, Any]]:
        """Gọi công cụ tính hoá đơn CHÍNH THỨC của EVN (calc.evn.com.vn) để
        tính tiền điện ứng với 1 mức tiêu thụ (kWh) cho trước, theo đúng
        biểu giá đã chọn (self.tariff) - KHÔNG cần đăng nhập.

        ngay_dky/ngay_cky: ngày đầu/cuối kỳ (dùng để xác định số ngày
        trong kỳ - biểu giá bậc thang co giãn theo số ngày, ví dụ kỳ 2
        tháng thì các bậc nhân đôi).

        Trả về dict gốc từ EVN (có HDN_HDON[0] chứa SO_TIEN, TIEN_GTGT,
        TONG_TIEN...) hoặc None nếu lỗi.
        """
        payload = self._bill_calc_payload(san_luong_kwh, ngay_dky, ngay_cky, so_ho)
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
                ngay_cky_truoc = date.fromisoformat(latest["NGAY_CKY"][:10])
                start = ngay_cky_truoc + timedelta(days=1)
            except (ValueError, TypeError, KeyError):
                start = today.replace(day=1)
        else:
            start = today.replace(day=1)

        # period_end LUÔN là ngày cuối cùng của THÁNG DƯƠNG LỊCH chứa
        # period_start - dùng số ngày THẬT của tháng đó (28/29/30/31),
        # KHÔNG đoán/copy độ dài kỳ trước (đơn giản hơn, và chính xác hơn
        # vì độ dài kỳ trước có thể khác tháng hiện tại, ví dụ 30 vs 31).
        if start.month == 12:
            end = date(start.year, 12, 31)
        else:
            end = date(start.year, start.month + 1, 1) - timedelta(days=1)
        return start, end, today

    async def async_fetch_all(self) -> Dict[str, Any]:
        """Lấy toàn bộ dữ liệu 1 lần, dùng cho coordinator."""
        customer_info = await self.get_customer_info()
        index_log = await self.get_index_log()
        bill_history = await self.get_bill_history()
        consumption_summary = await self.get_consumption_summary()
        spider_detail = await self.get_spider_detail()

        # Xác định THÁNG/NĂM hiện tại một cách ĐỘC LẬP, dựa vào hóa đơn ĐÃ
        # CHỐT gần nhất - KHÔNG dựa vào tên "Kỳ hiện tại" của EVN trong
        # spider_detail, vì ý nghĩa tên đó THAY ĐỔI tùy EVN đã chốt kỳ
        # trước hay chưa (trước khi chốt: "Kỳ hiện tại" = tháng SAU; sau
        # khi chốt: "Kỳ hiện tại" = chính tháng hiện tại).
        period_start, period_end, _ = self._current_period_bounds(bill_history)
        thang_hien_tai_ym = (period_start.year, period_start.month)

        def _row_ym(row):
            ngay_dky = row.get("NGAY_DKY")
            if not ngay_dky:
                return None
            try:
                d = date.fromisoformat(ngay_dky[:10])
                return (d.year, d.month)
            except ValueError:
                return None

        # Dòng thuộc ĐÚNG tháng hiện tại (so theo ngày thật, không theo tên)
        # - dùng để xác định "Tháng hiện tại" (mục 3) là tháng nào.
        row_thang_hien_tai = next((r for r in spider_detail if _row_ym(r) == thang_hien_tai_ym), None)

        # Dòng "TẠM CHỐT" THẬT của EVN - PHẢI đúng LOAI_CHISO="DDK" (chỉ
        # số ĐỊNH KỲ, cùng loại EVN dùng để chốt hóa đơn thật). Dòng
        # LOAI_CHISO="" chỉ là số đọc spider thô (real-time), CHƯA phải
        # số EVN tạm chốt - không tự ý coi đó là "tạm chốt" thay EVN.
        row_tam_chot = next(
            (r for r in spider_detail if _row_ym(r) == thang_hien_tai_ym and r.get("LOAI_CHISO") == "DDK"),
            None,
        )

        kwh_so_far = None
        nguon_kwh = None

        if row_tam_chot:
            kwh_so_far = row_tam_chot.get("SAN_LUONG")
            nguon_kwh = f"spider/chitiet - tạm chốt EVN ({row_tam_chot.get('KY_HDON')})"

        if kwh_so_far is None and consumption_summary:
            kwh_so_far = (consumption_summary.get("electricConsumption") or {}).get("electricConsumptionThisMonth")
            nguon_kwh = nguon_kwh or "power-consumption-alerts (tổng tháng hiện tại)"

        # KHÔNG ngoại suy/dự đoán cả tháng - chỉ tính tiền cho ĐÚNG số kWh
        # đã tiêu thụ tới hiện tại, áp theo biểu giá bậc thang của tháng
        # (period_start/period_end dùng để biểu giá tính đúng số ngày
        # trong tháng, không phải để nhân kWh lên).
        bill_estimate = None
        if kwh_so_far is not None and kwh_so_far > 0 and period_start and period_end:
            bill_result = await self.estimate_bill(kwh_so_far, period_start, period_end)
            if bill_result:
                bill_estimate = {
                    "che_do": f"tính trực tiếp trên số đã dùng (không ngoại suy) - nguồn: {nguon_kwh}",
                    "kwh_da_dung": kwh_so_far,
                    "ngay_dau_ky": period_start.isoformat(),
                    "ngay_cuoi_ky_du_kien": period_end.isoformat(),
                    "tien_truoc_thue": bill_result.get("SO_TIEN"),
                    "tien_thue": bill_result.get("TIEN_GTGT"),
                    "tong_tien_du_tinh": bill_result.get("TONG_TIEN"),
                }

        # current_period_start dùng cho các sensor khác (Tháng hiện tại,
        # mục 5 "Tiêu thụ tháng tạm chốt", mục 6 "Tiêu thụ tháng tiếp
        # theo"...) - ưu tiên ngày thật từ EVN (row_thang_hien_tai đã xác
        # định ở trên) nếu có, fallback về ước tính theo lịch.
        cp_start, cp_end = period_start, period_end
        if row_thang_hien_tai and row_thang_hien_tai.get("NGAY_DKY"):
            try:
                cp_start = date.fromisoformat(row_thang_hien_tai["NGAY_DKY"][:10])
                if row_thang_hien_tai.get("NGAY_CKY"):
                    cp_end = date.fromisoformat(row_thang_hien_tai["NGAY_CKY"][:10])
            except (ValueError, TypeError):
                pass

        return {
            "customer_info": customer_info,
            "index_log": index_log,
            "bill_history": bill_history,
            "consumption_summary": consumption_summary,
            "server_time_header": self.server_time_header,
            "bill_estimate": bill_estimate,
            "current_period_start": cp_start.isoformat(),
            "current_period_end_estimate": cp_end.isoformat(),
            "spider_detail": spider_detail,
        }
