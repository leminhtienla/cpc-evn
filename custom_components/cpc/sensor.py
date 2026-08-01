"""Sensors cho CPC."""

import logging
from datetime import date
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
        CPCTodayYesterdaySensor(coordinator, customer_code),
        CPCCurrentPeriodSensor(coordinator, customer_code),
        CPCCurrentMonthRunningSensor(coordinator, customer_code),
        CPCSpiderBreakdownSensor(coordinator, customer_code),
        CPCSpiderCurrentPortionSensor(coordinator, customer_code),
        CPCBillEstimateSensor(coordinator, customer_code),
        CPCLatestBillPeriodSensor(coordinator, customer_code),
        CPCLastYearPeriodSensor(coordinator, customer_code),
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
            "name": self._customer_code,
            "manufacturer": "CPC - Điện lực Miền Trung",
            "model": info.get("meterId", "CPC Meter"),
        }


class CPCRealtimeSensor(CPCBaseSensor):
    """Chỉ số công tơ đọc gần thời gian thực nhất (spider/thongTinChiSo)."""

    _sensor_key = "chi_so_thoi_gian_thuc"
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

    _sensor_key = "thang_truoc"
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


class CPCLastYearPeriodSensor(CPCBaseSensor):
    """Entity RIÊNG chỉ để biết 'cùng kỳ năm trước' (dùng để so sánh với
    'Tháng trước') là tháng/năm nào - cùng tháng với 'Tháng trước' nhưng
    lùi lại đúng 1 năm.
    """

    _sensor_key = "thang_truoc_nam_truoc"
    _attr_name = "Tháng trước năm trước"
    _attr_icon = "mdi:calendar-refresh"

    def _same(self):
        bills = (self.coordinator.data or {}).get("bill_history", [])
        latest = _latest_bill(bills)
        if not latest:
            return None
        return _same_period_last_year(bills, latest.get("THANG"), latest.get("NAM"))

    @property
    def native_value(self):
        same = self._same()
        if not same:
            return None
        return f"Tháng {same.get('THANG')}/{same.get('NAM')}"

    @property
    def extra_state_attributes(self) -> dict:
        same = self._same()
        if not same:
            return {}
        return {"Tháng": same.get("THANG"), "Năm": same.get("NAM")}


class CPCMonthlyConsumptionSensor(CPCBaseSensor):
    """Sản lượng tiêu thụ tháng/kỳ hóa đơn gần nhất (kWh)."""

    _sensor_key = "tieu_thu_thang_truoc"
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

    _sensor_key = "tien_dien_thang_truoc"
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
    """Entity RIÊNG cho biết kỳ hiện tại (đang mở, chưa chốt) tính TỪ
    THÁNG NÀO.

    QUAN TRỌNG: EVN chốt kỳ theo ngày ghi công tơ, KHÔNG theo lịch dương.
    Nếu tháng dương lịch đã qua nhưng EVN chưa chạy job chốt kỳ, thì kỳ
    "đang chạy" thực chất vẫn được cộng dồn TỪ THÁNG TRƯỚC, dù bây giờ đã
    sang tháng mới. Ví dụ: hôm nay 01/08 nhưng EVN chưa chốt kỳ tháng 7,
    nên kỳ đang mở vẫn là "Tháng 7/2026" - không phải "Tháng 8/2026". Vì
    vậy sensor này lấy tháng/năm từ NGÀY BẮT ĐẦU kỳ đang mở (ngay sau
    ngày chốt kỳ trước), KHÔNG lấy theo ngày hôm nay.
    """

    _sensor_key = "thang_hien_tai"
    _attr_name = "Tháng hiện tại"
    _attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self):
        period_start = (self.coordinator.data or {}).get("current_period_start")
        if not period_start:
            return None
        try:
            dt = date.fromisoformat(period_start)
        except (TypeError, ValueError):
            return None
        return f"Tháng {dt.month}/{dt.year}"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "Ngày bắt đầu kỳ (ước tính)": (self.coordinator.data or {}).get("current_period_start"),
            "Lưu ý": "Tháng này = tháng chứa NGÀY BẮT ĐẦU của kỳ đang mở, không phải tháng dương lịch hiện tại. Nếu EVN chưa chốt kỳ tháng trước, kỳ đang mở vẫn thuộc về tháng trước dù đã sang tháng mới.",
        }


class CPCCurrentMonthRunningSensor(CPCBaseSensor):
    """Tiêu thụ tháng dương lịch HIỆN TẠI, đang chạy - chưa chốt hóa đơn nên
    chưa có tiền điện (EVN chỉ tính tiền khi chốt kỳ). Nguồn:
    power-consumption-alerts.electricConsumptionThisMonth.
    """

    _sensor_key = "tieu_thu_thang_hien_tai"
    _attr_name = "Tiêu thụ tháng hiện tại"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:calendar-clock"

    @property
    def _period_month_year(self):
        period_start = (self.coordinator.data or {}).get("current_period_start")
        if period_start:
            try:
                dt = date.fromisoformat(period_start)
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
        thang, nam = self._period_month_year
        return {
            "Tháng": thang,
            "Năm": nam,
            "Nguồn": "cskh.cpc.vn (power-consumption-alerts)",
            "Lưu ý": "Tháng đang chạy, chưa chốt kỳ nên CHƯA CÓ tiền điện. Xem sensor 'Tiêu thụ tháng trước' để biết tiền điện của kỳ đã chốt gần nhất. 'Tháng' ở đây là tháng chứa NGÀY BẮT ĐẦU kỳ đang mở - nếu EVN chưa chốt kỳ trước, có thể vẫn là tháng trước dù đã sang tháng mới.",
        }


class CPCSpiderBreakdownSensor(CPCBaseSensor):
    """Số liệu TẠM CHỐT của tháng hiện tại (từ breakdown spider/chitiet).

    Đây gần như là số cuối cùng của kỳ hóa đơn hiện tại (ví dụ tháng 7) -
    chỉ còn thiếu bước EVN CHÍNH THỨC chốt sổ để phát hành hóa đơn. Nếu
    chưa qua tháng dương lịch mới, số này TRÙNG với 'Tiêu thụ tháng hiện
    tại'. Khi đã qua tháng mới mà EVN chưa kịp chốt, 'Tiêu thụ tháng hiện
    tại' sẽ CỘNG THÊM phần của 'Tiêu thụ tháng tiếp theo' vào - lúc đó sensor này
    mới là số ĐÚNG đại diện cho tháng hiện tại, không bị lẫn tháng sau.
    """

    _sensor_key = "tieu_thu_thang_hien_tai_tam_chot"
    _attr_name = "Tiêu thụ tháng hiện tại tạm chốt"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:calendar-alert"

    @property
    def _rows(self) -> list:
        return (self.coordinator.data or {}).get("spider_detail") or []

    @property
    def native_value(self):
        for r in self._rows:
            if r.get("KY_HDON") != "Kỳ hiện tại":
                return r.get("SAN_LUONG")
        return 0

    @property
    def extra_state_attributes(self) -> dict:
        rows = self._rows
        if not rows:
            return {}
        chua_chot = next((r for r in rows if r.get("KY_HDON") != "Kỳ hiện tại"), None)
        hien_tai = next((r for r in rows if r.get("KY_HDON") == "Kỳ hiện tại"), None)
        return {
            "Tên kỳ (EVN)": chua_chot.get("KY_HDON") if chua_chot else None,
            "Ngày đầu kỳ": chua_chot.get("NGAY_DKY_FORMAT") if chua_chot else None,
            "Ngày cuối kỳ (lần đọc gần nhất)": chua_chot.get("NGAY_CKY_FORMAT") if chua_chot else None,
            "Chỉ số đầu kỳ": chua_chot.get("CHISO_CU") if chua_chot else None,
            "Chỉ số cuối (lần đọc gần nhất)": chua_chot.get("CHISO_MOI") if chua_chot else None,
            "Tiêu thụ tháng tiếp theo (kWh, entity riêng)": hien_tai.get("SAN_LUONG") if hien_tai else None,
            "Nguồn": "cskh.cpc.vn (spider/chitiet)",
            "Lưu ý": "Số gần như cuối cùng của tháng hiện tại, chỉ còn chờ EVN chốt sổ chính thức. Sensor 'Dự tính tiền điện tháng hiện tại' dùng đúng số này để tính, không dùng số gộp của 'Tiêu thụ tháng hiện tại'.",
        }


class CPCSpiderCurrentPortionSensor(CPCBaseSensor):
    """Phần sản lượng đã bắt đầu tính cho THÁNG TIẾP THEO (từ breakdown
    spider/chitiet), dù tháng hiện tại còn chưa được EVN chốt sổ. Ví dụ
    tháng 7 chưa chốt nhưng EVN đã bắt đầu 1 bucket riêng từ 01/08 - đó
    chính là sensor này. Sẽ tăng dần tới khi tháng 7 chính thức chốt sổ,
    sau đó "tháng tiếp theo" sẽ trở thành "tháng hiện tại" mới.
    """

    _sensor_key = "tieu_thu_thang_tiep_theo"
    _attr_name = "Tiêu thụ tháng tiếp theo"
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:calendar-arrow-right"

    @property
    def native_value(self):
        rows = (self.coordinator.data or {}).get("spider_detail") or []
        row = next((r for r in rows if r.get("KY_HDON") == "Kỳ hiện tại"), None)
        return row.get("SAN_LUONG") if row else 0

    @property
    def extra_state_attributes(self) -> dict:
        rows = (self.coordinator.data or {}).get("spider_detail") or []
        row = next((r for r in rows if r.get("KY_HDON") == "Kỳ hiện tại"), None)
        if not row:
            return {}
        return {
            "Ngày đầu": row.get("NGAY_DKY_FORMAT"),
            "Lần đọc gần nhất": row.get("NGAY_CKY_FORMAT"),
            "Chỉ số đầu": row.get("CHISO_CU"),
            "Chỉ số hiện tại": row.get("CHISO_MOI"),
            "Nguồn": "cskh.cpc.vn (spider/chitiet)",
        }


class CPCBillEstimateSensor(CPCBaseSensor):
    """Dự tính tiền điện của kỳ hóa đơn đang mở, tính theo đúng biểu giá
    bậc thang qua công cụ tính hoá đơn CHÍNH THỨC của EVN (calc.evn.com.vn).

    Luôn ngoại suy tuyến tính theo số ngày dữ liệu THỰC đã capture được
    (xác định bằng lần đọc chỉ số mới nhất thật, KHÔNG đoán theo ngày
    hôm nay) - đúng cả khi mới giữa kỳ, không chỉ đúng lúc gần cuối kỳ.
    Ưu tiên lấy period_start/end và sản lượng từ "kỳ chưa chốt" thật của
    EVN (spider/chitiet) nếu có, fallback về power-consumption-alerts +
    tự đoán độ dài kỳ nếu chưa có.
    """

    _sensor_key = "du_tinh_tien_dien_thang_hien_tai"
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
            "Chế độ tính": est.get("che_do"),
            "Đã dùng tới hiện tại (kWh)": est.get("kwh_da_dung"),
            "Sản lượng dùng để tính - dự tính cả kỳ (kWh)": est.get("kwh_du_tinh_ca_ky"),
            "Số ngày đã capture / tổng số ngày kỳ": f"{est.get('so_ngay_da_qua')}/{est.get('so_ngay_ky')}",
            "Ngày đầu kỳ": est.get("ngay_dau_ky"),
            "Ngày cuối kỳ": est.get("ngay_cuoi_ky_du_kien"),
            "Tiền điện trước thuế (VNĐ)": est.get("tien_truoc_thue"),
            "Thuế GTGT (VNĐ)": est.get("tien_thue"),
            "Nguồn": "calc.evn.com.vn (công cụ tính hoá đơn chính thức của EVN)",
            "Lưu ý": "Đây là ƯỚC TÍNH ngoại suy tuyến tính - giả định mức dùng điện trung bình/ngày (tính tới lần đọc chỉ số mới nhất thật) không đổi tới hết kỳ. Có thể lệch nếu thói quen dùng điện thay đổi (vd dùng điều hoà nhiều hơn cuối tháng nóng hơn).",
        }


class CPCLastYearConsumptionSensor(CPCBaseSensor):
    """Sản lượng cùng THÁNG với 'Tháng trước' (kỳ hóa đơn đã chốt gần
    nhất), nhưng của NĂM TRƯỚC. Ví dụ 'Tháng trước' đang là tháng 6/2026
    thì sensor này là tháng 6/2025 - KHÔNG PHẢI so với tháng hiện tại.
    """

    _sensor_key = "tieu_thu_thang_truoc_nam_truoc"
    _attr_name = "Tiêu thụ tháng trước năm trước"
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

    _sensor_key = "tien_dien_thang_truoc_nam_truoc"
    _attr_name = "Tiền điện tháng trước năm trước"
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


