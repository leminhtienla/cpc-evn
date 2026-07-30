"""Sensors cho CPC."""

import logging
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
    """Tiêu thụ theo TỪNG NGÀY - từ API chính thức sl-tieu-thu-view của EVN."""

    _sensor_key = "tieu_thu_theo_ngay"
    _attr_name = "Tiêu thụ theo ngày"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:calendar-today"

    @property
    def native_value(self):
        daily_view = (self.coordinator.data or {}).get("daily_view", [])
        if not daily_view:
            return None
        latest = sorted(daily_view, key=lambda r: r.get("ngay") or "")[-1]
        return latest.get("sanLuongNgay")

    @property
    def extra_state_attributes(self) -> dict:
        daily_view = (self.coordinator.data or {}).get("daily_view", [])
        if not daily_view:
            return {}
        sorted_rows = sorted(daily_view, key=lambda r: r.get("ngay") or "", reverse=True)
        latest = sorted_rows[0]
        return {
            "Ngày gần nhất": latest.get("ngay", "")[:10],
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
        return {
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


class CPCMonthlyConsumptionSensor(CPCBaseSensor):
    """Sản lượng tiêu thụ tháng/kỳ hóa đơn gần nhất (kWh)."""

    _sensor_key = "tieu_thu_thang_nay"
    _attr_name = "Tiêu thụ tháng này"
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
        }


class CPCMonthlyCostSensor(CPCBaseSensor):
    """Tiền điện tháng/kỳ hóa đơn gần nhất (VNĐ)."""

    _sensor_key = "tien_dien_thang_nay"
    _attr_name = "Tiền điện tháng này"
    _attr_native_unit_of_measurement = "VNĐ"
    _attr_icon = "mdi:cash-multiple"

    @property
    def native_value(self):
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        return latest.get("TONG_TIEN") if latest else None


class CPCLastYearConsumptionSensor(CPCBaseSensor):
    """Sản lượng cùng tháng, năm trước (kWh) - để so sánh, tự tính từ bill_history."""

    _sensor_key = "tieu_thu_cung_ky_nam_truoc"
    _attr_name = "Tiêu thụ cùng kỳ năm trước"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:transmission-tower-export"

    @property
    def native_value(self):
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        if not latest:
            return None
        same = _same_period_last_year(bills, latest.get("THANG"), latest.get("NAM"))
        return same.get("DIEN_TTHU") if same else None


class CPCLastYearCostSensor(CPCBaseSensor):
    """Tiền điện cùng tháng, năm trước (VNĐ)."""

    _sensor_key = "tien_dien_cung_ky_nam_truoc"
    _attr_name = "Tiền điện cùng kỳ năm trước"
    _attr_native_unit_of_measurement = "VNĐ"
    _attr_icon = "mdi:cash-multiple"

    @property
    def native_value(self):
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        if not latest:
            return None
        same = _same_period_last_year(bills, latest.get("THANG"), latest.get("NAM"))
        return same.get("TONG_TIEN") if same else None


class CPCBillHistorySensor(CPCBaseSensor):
    """Toàn bộ lịch sử hóa đơn theo tháng (có thể tới hàng chục năm)."""

    _sensor_key = "lich_su_hoa_don"
    _attr_name = "Lịch sử hóa đơn theo tháng"
    _attr_icon = "mdi:calendar-month"

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
    """Lịch sử treo tháo / thay công tơ (biendongtreothao)."""

    _sensor_key = "lich_su_treo_thao_cong_to"
    _attr_name = "Lịch sử treo tháo công tơ"
    _attr_icon = "mdi:electric-switch"

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
