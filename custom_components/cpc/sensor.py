"""Sensors cho CPC."""

import logging
from datetime import datetime
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


def _daily_breakdown(index_log: list) -> list:
    """Tính tiêu thụ theo TỪNG NGÀY từ log chỉ số đọc mỗi ~6 tiếng.

    Cách tính: nhóm các lần đọc theo ngày (lấy phần ngày của NGAYGIO),
    với mỗi ngày chỉ giữ lại bản ghi có giờ muộn nhất (chỉ số cuối ngày).
    Tiêu thụ trong ngày = chỉ số cuối ngày N - chỉ số cuối ngày N-1.
    Ngày đầu tiên trong log không tính được tiêu thụ (không có ngày liền
    trước để trừ) nên bị bỏ qua.
    """
    if not index_log:
        return []

    by_date = {}
    for r in index_log:
        ngaygio = r.get("NGAYGIO")
        cs_moi = r.get("CS_MOI")
        if not ngaygio or cs_moi is None:
            continue
        date_part = ngaygio[:10]  # "2026-07-29T13:09:44.38" -> "2026-07-29"
        existing = by_date.get(date_part)
        if existing is None or ngaygio > existing["NGAYGIO"]:
            by_date[date_part] = {"NGAYGIO": ngaygio, "CS_MOI": cs_moi}

    sorted_dates = sorted(by_date.keys())
    result = []
    prev_cs = None
    for date_str in sorted_dates:
        cs = by_date[date_str]["CS_MOI"]
        if prev_cs is not None:
            result.append(
                {
                    "Ngày": datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%Y"),
                    "Chỉ số cuối ngày": cs,
                    "Tiêu thụ (kWh)": round(cs - prev_cs, 3),
                }
            )
        prev_cs = cs
    # Mới nhất lên đầu
    result.reverse()
    return result


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
        CPCMonthlyConsumptionSensor(coordinator, customer_code),
        CPCMonthlyCostSensor(coordinator, customer_code),
        CPCLastYearConsumptionSensor(coordinator, customer_code),
        CPCLastYearCostSensor(coordinator, customer_code),
        CPCBillHistorySensor(coordinator, customer_code),
    ]
    async_add_entities(entities)


class CPCBaseSensor(CoordinatorEntity, SensorEntity):
    """Base entity - chung device_info."""

    _attr_has_entity_name = True

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
    """Tiêu thụ theo TỪNG NGÀY - tự tính từ log chỉ số đọc mỗi ~6 tiếng."""

    _sensor_key = "tieu_thu_theo_ngay"
    _attr_name = "Tiêu thụ theo ngày"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:calendar-today"

    @property
    def native_value(self):
        index_log = (self.coordinator.data or {}).get("index_log", [])
        breakdown = _daily_breakdown(index_log)
        # State = tiêu thụ của ngày gần nhất tính được (không phải hôm nay
        # nếu hôm nay chưa có đủ 2 lần đọc để trừ)
        return breakdown[0]["Tiêu thụ (kWh)"] if breakdown else None

    @property
    def extra_state_attributes(self) -> dict:
        index_log = (self.coordinator.data or {}).get("index_log", [])
        breakdown = _daily_breakdown(index_log)
        return {
            "Ngày gần nhất tính được": breakdown[0]["Ngày"] if breakdown else None,
            "Chi tiết": breakdown,
            "Nguồn": "cskh.cpc.vn (spider/thongTinChiSo) - tự tính chênh lệch chỉ số giữa các ngày",
            "Lưu ý": "Không có tiền điện theo ngày - EVN chỉ tính tiền điện theo bậc thang lũy tiến hàng THÁNG, không có khái niệm tiền điện của riêng 1 ngày.",
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
