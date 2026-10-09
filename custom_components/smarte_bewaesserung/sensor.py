"""Sensoren."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfPrecipitationDepth,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmarteBewaesserungConfigEntry
from .const import CONF_COMPARE_ENTITY
from .coordinator import Snapshot, ZoneSnapshot
from .entity import GlobalEntity, ZoneEntity
from .plants import PHASE_DORMANT, PHASE_GROWTH, PHASE_PEAK, PHASE_RIPENING, PHASE_SPROUTING

MM = UnitOfPrecipitationDepth.MILLIMETERS
PHASES = [PHASE_DORMANT, PHASE_SPROUTING, PHASE_GROWTH, PHASE_PEAK, PHASE_RIPENING]


@dataclass(frozen=True, kw_only=True)
class GlobalSensorDescription(SensorEntityDescription):
    """Beschreibung eines globalen Sensors."""

    value_fn: Callable[[Snapshot], Any]
    attrs_fn: Callable[[Snapshot], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class ZoneSensorDescription(SensorEntityDescription):
    """Beschreibung eines Zonen-Sensors."""

    value_fn: Callable[[ZoneSnapshot], Any]
    attrs_fn: Callable[[ZoneSnapshot], dict[str, Any]] | None = None
    requires: str | None = None


GLOBAL_SENSORS: tuple[GlobalSensorDescription, ...] = (
    GlobalSensorDescription(
        key="gts",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda s: s.gts,
    ),
    GlobalSensorDescription(
        key="et0_so_far",
        native_unit_of_measurement=MM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda s: s.et0_so_far_mm,
    ),
    GlobalSensorDescription(
        key="et0_today",
        native_unit_of_measurement=MM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s: s.et0_today_mm,
    ),
    GlobalSensorDescription(
        key="et0_yesterday",
        native_unit_of_measurement=MM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s: s.et0_yesterday_mm,
    ),
    GlobalSensorDescription(
        key="rain_today",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=MM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s: s.rain_today_mm,
        attrs_fn=lambda s: {"source": s.rain_source},
    ),
    GlobalSensorDescription(
        key="rain_yesterday",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=MM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s: s.rain_yesterday_mm,
    ),
    GlobalSensorDescription(
        key="rain_forecast_24h",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=MM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s: s.rain_forecast_24h_mm,
    ),
    GlobalSensorDescription(
        key="min_temp_24h",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s: s.min_temp_24h_c,
        attrs_fn=lambda s: {
            "wind_kmh": s.wind_kmh,
            "last_closed_day": s.last_closed.isoformat() if s.last_closed else None,
            "weather_updated": s.weather_updated.isoformat() if s.weather_updated else None,
        },
    ),
)

ZONE_SENSORS: tuple[ZoneSensorDescription, ...] = (
    ZoneSensorDescription(
        key="depletion",
        native_unit_of_measurement=MM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda z: z.depletion_mm,
        attrs_fn=lambda z: {
            "depletion_at_day_start_mm": z.committed_depletion_mm,
            "taw_mm": z.taw_mm,
            "raw_mm": z.raw_mm,
            "soil_sensor_depletion_mm": z.sensor_depletion_mm,
            "soil_moisture_pct": z.soil_moisture_pct,
            "history": z.history,
        },
    ),
    ZoneSensorDescription(
        key="water_demand",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda z: z.demand_liters,
        attrs_fn=lambda z: {"depletion_mm": z.depletion_mm},
    ),
    ZoneSensorDescription(
        key="etc_so_far",
        native_unit_of_measurement=MM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda z: z.etc_so_far_mm,
    ),
    ZoneSensorDescription(
        key="etc_today",
        native_unit_of_measurement=MM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda z: z.etc_today_mm,
    ),
    ZoneSensorDescription(
        key="etc_tomorrow",
        native_unit_of_measurement=MM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda z: z.etc_tomorrow_mm,
    ),
    ZoneSensorDescription(
        key="etc_7d",
        native_unit_of_measurement=MM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda z: z.etc_7d_mm,
    ),
    ZoneSensorDescription(
        key="kc",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda z: z.kc,
        attrs_fn=lambda z: {"plant": z.plant},
    ),
    ZoneSensorDescription(
        key="season_phase",
        device_class=SensorDeviceClass.ENUM,
        options=PHASES,
        value_fn=lambda z: z.phase,
        attrs_fn=lambda z: {"source": z.phase_source, "spring_gts_threshold": z.spring_gts},
    ),
    ZoneSensorDescription(
        key="current_run_volume",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda z: z.live_run_liters,
        attrs_fn=lambda z: {"flow_lpm": z.current_flow_lpm},
    ),
    ZoneSensorDescription(
        key="soil_water",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda z: z.soil_water_pct,
    ),
    ZoneSensorDescription(
        key="recommended_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: z.recommendation.minutes,
        attrs_fn=lambda z: {
            "target_mm": round(z.recommendation.target_mm, 1),
            "capped": z.recommendation.capped,
            "blocked_by": z.recommendation.blocked_by,
            "reason": z.recommendation.reason,
        },
    ),
    ZoneSensorDescription(
        key="recommended_volume",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: z.recommendation.liters,
    ),
    ZoneSensorDescription(
        key="reason",
        value_fn=lambda z: z.recommendation.reason[:255],
    ),
    ZoneSensorDescription(
        key="last_run_volume",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=0,
        value_fn=lambda z: z.last_run["liters"] if z.last_run else None,
        attrs_fn=lambda z: dict(z.last_run) if z.last_run else {},
    ),
    ZoneSensorDescription(
        key="measured_throughput",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda z: z.measured_throughput_lpm,
    ),
    ZoneSensorDescription(
        key="water_total",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda z: z.total_liters,
    ),
    ZoneSensorDescription(
        key="compare_smart_irrigation",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        requires=CONF_COMPARE_ENTITY,
        value_fn=lambda z: (
            round(z.recommendation.minutes - z.compare_minutes, 1) if z.compare_minutes is not None else None
        ),
        attrs_fn=lambda z: {
            "smart_irrigation_minutes": z.compare_minutes,
            "smarte_bewaesserung_minutes": z.recommendation.minutes,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmarteBewaesserungConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Sensoren anlegen."""
    coordinator = entry.runtime_data
    async_add_entities(GlobalSensor(coordinator, d) for d in GLOBAL_SENSORS)
    for subentry_id, zone in coordinator.zones.items():
        async_add_entities(
            (
                ZoneSensor(coordinator, subentry_id, d)
                for d in ZONE_SENSORS
                if d.requires is None or zone.data.get(d.requires)
            ),
            config_subentry_id=subentry_id,
        )


class GlobalSensor(GlobalEntity, SensorEntity):
    """Globaler Sensor."""

    entity_description: GlobalSensorDescription

    def __init__(self, coordinator, description: GlobalSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None


class ZoneSensor(ZoneEntity, SensorEntity):
    """Sensor einer Zone."""

    entity_description: ZoneSensorDescription

    def __init__(self, coordinator, subentry_id: str, description: ZoneSensorDescription) -> None:
        super().__init__(coordinator, subentry_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        zone = self.zone_data
        return self.entity_description.value_fn(zone) if zone else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        zone = self.zone_data
        return fn(zone) if fn and zone else None
