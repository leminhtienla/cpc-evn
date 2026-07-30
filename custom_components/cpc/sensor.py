"""Sensors cho CPC."""

import logging
from email.utils import parsedate_to_datetime
from typing import Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CUSTOMER_CODE, DOMAIN
from .coordinator import CPCDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _latest_index(index_log: list) -> Optional[dict]:
    """Bản ghi đọc chỉ số mới nhất (NGAYGIO lớn nhất)."""
    if not index_log:
        return None
    return sorted(index_log, key=lambda r: r.get("NGAYGIO") or "")[-1]


def _latest_bill(bill_history: list) -> Optional[dict]:
    """Bản ghi hóa đơn mới nhất (NAM, THANG lớn nhất)."""
    if not bill_history:
        return None
    return sorted(bill_history, key=lambda r: (r.get("NAM", 0), r.get("THANG", 0)))[-1]


def _same_period_last_year(bill_history: list, thang, nam) -> Optional[dict]:
    """Tìm hóa đơn cùng tháng, năm trước - để so sánh."""
    for r in bill_history:
        if r.get("THANG") == thang and r.get("NAM") == nam - 1:
            return r
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator: CPCDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    customer_code = entry.data[CONF_CUSTOMER_CODE]

    entities = [
        CPCRealtimeSensor(coordinator, customer_code),
        CPCDailyBreakdownSensor(coordinator, customer_code),
        CPCTodayYesterdaySensor(coordinator, customer_code),
        CPCCurrentPeriodSensor(coordinator, customer_code),
        CPCCurrentMonthRunningSensor(coordinator, customer_code),
        CPCBillEstimateSensor(coordinator, customer_code),
        CPCLatestBillPeriodSensor(coordinator, customer_code),
        CPCMonthlyConsumptionSensor(coordinator, customer_code),
        CPCMonthlyCostSensor(coordinator, customer_code),
        CPCLastYearConsumptionSensor(coordinator, customer_code),
        CPCLastYearCostSensor(coordinator, customer_code),
    ]
    async_add_entities(entities)


class CPCBaseSensor(CoordinatorEntity, SensorEntity):
    """Base entity - chung device_info."""

    _attr_has_entity_name = True
    # "Chi tiết" ở nhiều sensor chứa danh sách dài (hàng chục-hàng trăm bản
    # ghi), dễ vượt giới hạn 16KB mà recorder cho phép lưu vào DB lịch sử.
    # Loại các attribute này khỏi recorder - vẫn hiển thị bình thường trên
    # sensor, chỉ là không lưu lịch sử thay đổi của riêng attribute đó.
    _unrecorded_attributes = frozenset({"Chi tiết"})

    def __init__(self, coordinator: CPCDataUpdateCoordinator, customer_code: str):
        super().__init__(coordinator)
        self._customer_code = customer_code
        self._attr_unique_id = f"{customer_code}_{self._sensor_key}"

    @property
    def device_info(self):
        info = (self.coordinator.data or {}).get("customer_info", {})
        return {
            "identifiers": {(DOMAIN, self._customer_code)},
            "name": info.get("customerName") or f"CPC {self._customer_code}",
            "manufacturer": "CPC - Điện lực Miền Trung",
            "model": info.get("meterId", "CPC Meter"),
        }


class CPCRealtimeSensor(CPCBaseSensor):
    """Chỉ số công tơ đọc gần thời gian thực nhất (spider/thongTinChiSo)."""

    _sensor_key = "chi_so_realtime"
    _attr_name = "Chỉ số thời gian thực"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:gauge"

    @property
    def native_value(self):
        index_log = (self.coordinator.data or {}).get("index_log", [])
        latest = _latest_index(index_log)
        return latest.get("CS_MOI") if latest else None

    @property
    def extra_state_attributes(self) -> dict:
        index_log = (self.coordinator.data or {}).get("index_log", [])
        latest = _latest_index(index_log)
        if not latest:
            return {}
        return {
            "Sản lượng từ đầu kỳ tới lúc đọc (kWh)": latest.get("SL_MOI"),
            "Thời điểm đọc": latest.get("NGAYGIO"),
            "Số công tơ": latest.get("SO_CTO"),
            "Nguồn": "cskh.cpc.vn (spider/thongTinChiSo)",
        }


class CPCDailyBreakdownSensor(CPCBaseSensor):
    """Tổng tiêu thụ dồn từ đầu kỳ hóa đơn hiện tại tới nay - cộng dồn từ
    bảng theo ngày chính thức của EVN (sl-tieu-thu-view).

    State KHÔNG phải "hôm nay" (xem sensor 'Tiêu thụ hôm nay' cho số đó) -
    đây là tổng của TẤT CẢ các ngày có trong kỳ hiện tại, để tránh trùng
    số với sensor hôm nay. Bảng chi tiết từng ngày nằm trong attribute
    "Chi tiết".
    """

    _sensor_key = "tieu_thu_theo_ngay"
    _attr_name = "Tổng tiêu thụ dồn kỳ này (theo ngày)"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:calendar-today"

    @property
    def native_value(self):
        daily_view = (self.coordinator.data or {}).get("daily_view", [])
        if not daily_view:
            return None
        return round(sum(r.get("sanLuongNgay") or 0 for r in daily_view), 2)

    @property
    def extra_state_attributes(self) -> dict:
        daily_view = (self.coordinator.data or {}).get("daily_view", [])
        if not daily_view:
            return {}
        sorted_rows = sorted(daily_view, key=lambda r: r.get("ngay") or "", reverse=True)
        latest = sorted_rows[0]
        return {
            "Số ngày có dữ liệu": len(sorted_rows),
            "Ngày gần nhất": latest.get("ngay", "")[:10],
            "Tiêu thụ ngày gần nhất (kWh)": latest.get("sanLuongNgay"),
            "Chi tiết": [
                {
                    "Ngày": r.get("ngay", "")[:10],
                    "Tiêu thụ (kWh)": r.get("sanLuongNgay"),
                    "Trung bình cộng dồn từ đầu kỳ (kWh)": r.get("sanLuongTrungBinh"),
                }
                for r in sorted_rows
            ],
            "Nguồn": "cskh.cpc.vn (meter/rf/sl-tieu-thu-view) - API chính thức của EVN, không phải tự tính",
            "Ghi chú của EVN": latest.get("message"),
        }


class CPCTodayYesterdaySensor(CPCBaseSensor):
    """Tiêu thụ hôm nay/hôm qua + ngưỡng cảnh báo - từ power-consumption-alerts."""

    _sensor_key = "tieu_thu_hom_nay"
    _attr_name = "Tiêu thụ hôm nay"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:flash"

    @property
    def _ec(self) -> dict:
        summary = (self.coordinator.data or {}).get("consumption_summary")
        if not summary:
            return {}
        return summary.get("electricConsumption") or {}

    @property
    def native_value(self):
        return self._ec.get("electricConsumptionToday")

    @property
    def extra_state_attributes(self) -> dict:
        ec = self._ec
        if not ec:
            return {}
        ngay_hom_nay = None
        server_header = (self.coordinator.data or {}).get("server_time_header")
        if server_header:
            try:
                ngay_hom_nay = parsedate_to_datetime(server_header).date().isoformat()
            except (TypeError, ValueError):
                pass
        return {
            "Ngày (hôm nay, theo server)": ngay_hom_nay,
            "Tiêu thụ hôm qua (kWh)": ec.get("electricConsumptionYesterday"),
            "Tiêu thụ tháng này (kWh)": ec.get("electricConsumptionThisMonth"),
            "Tiêu thụ tháng trước (kWh)": ec.get("electricConsumptionLastMonth"),
            "Ngưỡng cảnh báo theo ngày (kWh)": ec.get("electricConsumptionThresholdDay"),
            "Ngưỡng cảnh báo theo tháng (kWh)": ec.get("electricConsumptionThresholdMonth"),
            "Vượt ngưỡng ngày (kWh)": ec.get("electricConsumptionExceededThresholdDay"),
            "Vượt ngưỡng tháng (kWh)": ec.get("electricConsumptionExceededThresholdMonth"),
            "Cảnh báo đang bật": (self.coordinator.data or {}).get("consumption_summary", {}).get("isActive"),
            "Nguồn": "cskh.cpc.vn (power-consumption-alerts)",
        }


class CPCLatestBillPeriodSensor(CPCBaseSensor):
    """Entity RIÊNG chỉ để biết kỳ hóa đơn ĐÃ CHỐT gần nhất là tháng nào -
    tách khỏi tên/giá trị của sensor tiêu thụ & tiền điện, để không phải
    đoán qua attribute.
    """

    _sensor_key = "ky_hoa_don_gan_nhat"
    _attr_name = "Tháng trước"
    _attr_icon = "mdi:calendar-check"

    @property
    def native_value(self):
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        if not latest:
            return None
        return f"Tháng {latest.get('THANG')}/{latest.get('NAM')}"

    @property
    def extra_state_attributes(self) -> dict:
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        if not latest:
            return {}
        return {"Tháng": latest.get("THANG"), "Năm": latest.get("NAM")}


class CPCMonthlyConsumptionSensor(CPCBaseSensor):
    """Sản lượng tiêu thụ tháng/kỳ hóa đơn gần nhất (kWh)."""

    _sensor_key = "tieu_thu_ky_hoa_don_gan_nhat"
    _attr_name = "Tiêu thụ tháng trước"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:transmission-tower-export"

    @property
    def native_value(self):
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        return latest.get("DIEN_TTHU") if latest else None

    @property
    def extra_state_attributes(self) -> dict:
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        if not latest:
            return {}
        return {
            "Tháng": latest.get("THANG"),
            "Năm": latest.get("NAM"),
            "Ngày đầu kỳ": latest.get("NGAY_DKY"),
            "Ngày cuối kỳ": latest.get("NGAY_CKY"),
            "Chỉ số đầu kỳ": latest.get("CHISO_CU"),
            "Chỉ số cuối kỳ": latest.get("CHISO_MOI"),
            "Lưu ý": "Đây là kỳ hóa đơn ĐÃ CHỐT gần nhất (đã có tiền điện chính thức) - xem entity 'Tháng trước' để biết chính xác là tháng/năm nào.",
        }


class CPCMonthlyCostSensor(CPCBaseSensor):
    """Tiền điện kỳ hóa đơn ĐÃ CHỐT gần nhất (VNĐ)."""

    _sensor_key = "tien_dien_ky_hoa_don_gan_nhat"
    _attr_name = "Tiền điện tháng trước"
    _attr_native_unit_of_measurement = "VNĐ"
    _attr_icon = "mdi:cash-multiple"

    @property
    def native_value(self):
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        return latest.get("TONG_TIEN") if latest else None

    @property
    def extra_state_attributes(self) -> dict:
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        if not latest:
            return {}
        return {
            "Tháng": latest.get("THANG"),
            "Năm": latest.get("NAM"),
            "Ngày đầu kỳ": latest.get("NGAY_DKY"),
            "Ngày cuối kỳ": latest.get("NGAY_CKY"),
        }


class CPCCurrentPeriodSensor(CPCBaseSensor):
    """Entity RIÊNG chỉ để biết tháng dương lịch hiện tại (đang chạy, chưa
    chốt kỳ) là tháng nào, theo giờ server EVN.
    """

    _sensor_key = "thang_hien_tai"
    _attr_name = "Tháng hiện tại"
    _attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self):
        server_header = (self.coordinator.data or {}).get("server_time_header")
        if not server_header:
            return None
        try:
            dt = parsedate_to_datetime(server_header)
        except (TypeError, ValueError):
            return None
        return f"Tháng {dt.month}/{dt.year}"


class CPCCurrentMonthRunningSensor(CPCBaseSensor):
    """Tiêu thụ tháng dương lịch HIỆN TẠI, đang chạy - chưa chốt hóa đơn nên
    chưa có tiền điện (EVN chỉ tính tiền khi chốt kỳ). Nguồn:
    power-consumption-alerts.electricConsumptionThisMonth.
    """

    _sensor_key = "tieu_thu_thang_nay"
    _attr_name = "Tiêu thụ tháng hiện tại"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:calendar-clock"

    @property
    def _server_month_year(self):
        server_header = (self.coordinator.data or {}).get("server_time_header")
        if server_header:
            try:
                dt = parsedate_to_datetime(server_header)
                return dt.month, dt.year
            except (TypeError, ValueError):
                pass
        return None, None

    @property
    def _ec(self) -> dict:
        summary = (self.coordinator.data or {}).get("consumption_summary")
        if not summary:
            return {}
        return summary.get("electricConsumption") or {}

    @property
    def native_value(self):
        return self._ec.get("electricConsumptionThisMonth")

    @property
    def extra_state_attributes(self) -> dict:
        if not self._ec:
            return {}
        thang, nam = self._server_month_year
        return {
            "Tháng": thang,
            "Năm": nam,
            "Nguồn": "cskh.cpc.vn (power-consumption-alerts)",
            "Lưu ý": "Tháng đang chạy, chưa chốt kỳ nên CHƯA CÓ tiền điện. Xem sensor 'Tiêu thụ tháng trước' để biết tiền điện của kỳ đã chốt gần nhất.",
        }


class CPCBillEstimateSensor(CPCBaseSensor):
    """Dự tính tiền điện CẢ THÁNG hiện tại - ngoại suy từ kWh đã dùng tới
    nay ra hết kỳ, rồi tính theo đúng biểu giá bậc thang sinh hoạt hiện
    hành qua công cụ tính hoá đơn CHÍNH THỨC của EVN (calc.evn.com.vn).

    Đây là ƯỚC TÍNH dựa trên giả định mức tiêu thụ trung bình mỗi ngày
    không đổi tới hết kỳ - số thực tế có thể khác nếu thói quen dùng điện
    thay đổi (ví dụ dùng điều hoà nhiều hơn vào cuối tháng nóng hơn).
    """

    _sensor_key = "du_tinh_tien_dien_thang_nay"
    _attr_name = "Dự tính tiền điện tháng hiện tại"
    _attr_native_unit_of_measurement = "VNĐ"
    _attr_icon = "mdi:calculator"

    @property
    def _est(self) -> dict:
        return (self.coordinator.data or {}).get("bill_estimate") or {}

    @property
    def native_value(self):
        est = self._est
        tong = est.get("tong_tien_du_tinh")
        return round(tong) if tong is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        est = self._est
        if not est:
            return {}
        return {
            "Đã dùng tới hiện tại (kWh)": est.get("kwh_da_dung"),
            "Dự tính cả kỳ (kWh)": est.get("kwh_du_tinh_ca_ky"),
            "Số ngày đã qua / tổng số ngày kỳ": f"{est.get('so_ngay_da_qua')}/{est.get('so_ngay_ky')}",
            "Ngày đầu kỳ (ước tính)": est.get("ngay_dau_ky"),
            "Ngày cuối kỳ (ước tính)": est.get("ngay_cuoi_ky_du_kien"),
            "Tiền điện trước thuế (VNĐ)": est.get("tien_truoc_thue"),
            "Thuế GTGT (VNĐ)": est.get("tien_thue"),
            "Nguồn": "calc.evn.com.vn (công cụ tính hoá đơn chính thức của EVN)",
            "Lưu ý": "Đây là ƯỚC TÍNH - giả định mức dùng điện trung bình/ngày không đổi tới hết kỳ. Ngày đầu/cuối kỳ cũng là ước tính dựa trên độ dài kỳ trước, có thể lệch vài ngày so với ngày EVN thực sự ghi công tơ.",
        }


class CPCLastYearConsumptionSensor(CPCBaseSensor):
    """Sản lượng cùng THÁNG với 'Tháng trước' (kỳ hóa đơn đã chốt gần
    nhất), nhưng của NĂM TRƯỚC. Ví dụ 'Tháng trước' đang là tháng 6/2026
    thì sensor này là tháng 6/2025 - KHÔNG PHẢI so với tháng hiện tại.
    """

    _sensor_key = "tieu_thu_cung_ky_nam_truoc"
    _attr_name = "Tiêu thụ cùng kỳ năm trước (so với tháng trước)"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:transmission-tower-export"

    def _same(self):
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        if not latest:
            return None
        return _same_period_last_year(bills, latest.get("THANG"), latest.get("NAM"))

    @property
    def native_value(self):
        same = self._same()
        return same.get("DIEN_TTHU") if same else None

    @property
    def extra_state_attributes(self) -> dict:
        same = self._same()
        if not same:
            return {}
        return {
            "Tháng": same.get("THANG"),
            "Năm": same.get("NAM"),
            "Lưu ý": "So sánh với 'Tháng trước' (kỳ hóa đơn đã chốt gần nhất), KHÔNG PHẢI với 'Tháng hiện tại' đang chạy.",
        }


class CPCLastYearCostSensor(CPCBaseSensor):
    """Tiền điện cùng THÁNG với 'Tháng trước', nhưng của NĂM TRƯỚC."""

    _sensor_key = "tien_dien_cung_ky_nam_truoc"
    _attr_name = "Tiền điện cùng kỳ năm trước (so với tháng trước)"
    _attr_native_unit_of_measurement = "VNĐ"
    _attr_icon = "mdi:cash-multiple"

    def _same(self):
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        if not latest:
            return None
        return _same_period_last_year(bills, latest.get("THANG"), latest.get("NAM"))

    @property
    def native_value(self):
        same = self._same()
        return same.get("TONG_TIEN") if same else None

    @property
    def extra_state_attributes(self) -> dict:
        same = self._same()
        if not same:
            return {}
        return {
            "Tháng": same.get("THANG"),
            "Năm": same.get("NAM"),
            "Lưu ý": "So sánh với 'Tháng trước' (kỳ hóa đơn đã chốt gần nhất), KHÔNG PHẢI với 'Tháng hiện tại' đang chạy.",
        }


