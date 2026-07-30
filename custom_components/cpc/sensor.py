"""Sensors cho CPC."""

import logging
from email.utils import parsedate_to_datetime
from typing import Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
        CPCLatestBillPeriodSensor(coordinator, customer_code),
        CPCMonthlyConsumptionSensor(coordinator, customer_code),
        CPCMonthlyCostSensor(coordinator, customer_code),
        CPCLastYearConsumptionSensor(coordinator, customer_code),
        CPCLastYearCostSensor(coordinator, customer_code),
        CPCBillHistorySensor(coordinator, customer_code),
        CPCMeterChangeHistorySensor(coordinator, customer_code),
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
    _attr_name = "Kỳ hóa đơn gần nhất"
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
    _attr_name = "Tiêu thụ kỳ hóa đơn gần nhất"
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
            "Lưu ý": "Đây là kỳ hóa đơn ĐÃ CHỐT gần nhất (đã có tiền điện chính thức), không phải tháng dương lịch hiện tại đang chạy. Xem sensor 'Tiêu thụ tháng này' để có số liệu tháng hiện tại (chưa chốt, chưa có tiền điện).",
        }


class CPCMonthlyCostSensor(CPCBaseSensor):
    """Tiền điện kỳ hóa đơn ĐÃ CHỐT gần nhất (VNĐ)."""

    _sensor_key = "tien_dien_ky_hoa_don_gan_nhat"
    _attr_name = "Tiền điện kỳ hóa đơn gần nhất"
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
    _attr_name = "Tháng hiện tại (đang chạy)"
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
    _attr_name = "Tiêu thụ tháng này"
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
            "Lưu ý": "Tháng đang chạy, chưa chốt kỳ nên CHƯA CÓ tiền điện. Xem sensor 'Tiêu thụ kỳ hóa đơn gần nhất' để biết tiền điện của kỳ đã chốt gần nhất.",
        }


class CPCLastYearConsumptionSensor(CPCBaseSensor):
    """Sản lượng cùng tháng, năm trước (kWh) - so sánh với kỳ hóa đơn đã chốt gần nhất."""

    _sensor_key = "tieu_thu_cung_ky_nam_truoc"
    _attr_name = "Tiêu thụ cùng kỳ năm trước"
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
        return {"Tháng": same.get("THANG"), "Năm": same.get("NAM")}


class CPCLastYearCostSensor(CPCBaseSensor):
    """Tiền điện cùng tháng, năm trước (VNĐ)."""

    _sensor_key = "tien_dien_cung_ky_nam_truoc"
    _attr_name = "Tiền điện cùng kỳ năm trước"
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
        return {"Tháng": same.get("THANG"), "Năm": same.get("NAM")}


class CPCBillHistorySensor(CPCBaseSensor):
    """Toàn bộ lịch sử hóa đơn theo tháng (có thể tới hàng chục năm).

    State chỉ là SỐ LƯỢNG bản ghi (để biết có dữ liệu hay không) - dữ liệu
    thật sự nằm trong attribute "Chi tiết". Đánh dấu DIAGNOSTIC để HA gom
    riêng, không lẫn với các sensor số liệu chính trên trang thiết bị.
    """

    _sensor_key = "lich_su_hoa_don"
    _attr_name = "Lịch sử hóa đơn theo tháng"
    _attr_icon = "mdi:calendar-month"
    _attr_native_unit_of_measurement = "bản ghi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        bills = (self.coordinator.data or {}).get("bill_history", [])
        return len(bills)

    @property
    def extra_state_attributes(self) -> dict:
        bills = (self.coordinator.data or {}).get("bill_history", [])
        return {
            "Chi tiết": [
                {
                    "Tháng": r.get("THANG"),
                    "Năm": r.get("NAM"),
                    "Tiêu thụ (kWh)": r.get("DIEN_TTHU"),
                    "Tiền điện (VNĐ)": r.get("TONG_TIEN"),
                    "Chỉ số đầu kỳ": r.get("CHISO_CU"),
                    "Chỉ số cuối kỳ": r.get("CHISO_MOI"),
                }
                for r in sorted(bills, key=lambda r: (r.get("NAM", 0), r.get("THANG", 0)), reverse=True)
            ]
        }


class CPCMeterChangeHistorySensor(CPCBaseSensor):
    """Lịch sử treo tháo / thay công tơ (biendongtreothao).

    State chỉ là SỐ LƯỢNG lần treo tháo - dữ liệu thật nằm trong attribute
    "Chi tiết". Đánh dấu DIAGNOSTIC vì lý do tương tự sensor lịch sử hóa đơn.
    """

    _sensor_key = "lich_su_treo_thao_cong_to"
    _attr_name = "Lịch sử treo tháo công tơ"
    _attr_icon = "mdi:electric-switch"
    _attr_native_unit_of_measurement = "lần"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        changes = (self.coordinator.data or {}).get("meter_changes", [])
        return len(changes)

    @property
    def extra_state_attributes(self) -> dict:
        changes = (self.coordinator.data or {}).get("meter_changes", [])
        return {
            "Chi tiết": [
                {
                    "Ngày": r.get("NGAY_BDONG", "")[:10],
                    "Số công tơ": r.get("SO_CTO"),
                    "Loại biến động": "Lắp mới" if r.get("MA_BDONG") == "B" else "Tháo" if r.get("MA_BDONG") == "E" else r.get("MA_BDONG"),
                    "Lý do": r.get("TEN_LDO"),
                }
                for r in sorted(changes, key=lambda r: r.get("NGAY_BDONG") or "", reverse=True)
            ]
        }
