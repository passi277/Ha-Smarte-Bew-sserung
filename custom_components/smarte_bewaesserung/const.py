"""Konstanten für Smarte Bewässerung."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "smarte_bewaesserung"
SUBENTRY_ZONE: Final = "zone"

UPDATE_INTERVAL: Final = timedelta(hours=1)
# Laufender Wasserbedarf wird ohne neuen Wetterabruf so oft neu gerechnet.
LIVE_INTERVAL: Final = timedelta(minutes=5)
REFRESH_COOLDOWN_SECONDS: Final = 10
STORAGE_VERSION: Final = 1
HISTORY_DAYS: Final = 14

# Hauptentry (Daten/Optionen)
CONF_RAIN_SENSOR: Final = "rain_sensor"
CONF_RAIN_SKIP_MM: Final = "rain_skip_mm"
CONF_FROST_C: Final = "frost_c"
CONF_WIND_KMH: Final = "wind_kmh"
CONF_SOIL_SENSOR_WEIGHT: Final = "soil_sensor_weight"
CONF_RAIN_PROBABILITY: Final = "rain_probability_pct"
CONF_WATER_PRICE: Final = "water_price_eur_m3"

DEFAULT_RAIN_SKIP_MM: Final = 3.0
DEFAULT_FROST_C: Final = 4.0
DEFAULT_WIND_KMH: Final = 30.0
DEFAULT_SOIL_SENSOR_WEIGHT: Final = 0.5
DEFAULT_RAIN_PROBABILITY: Final = 70.0
DEFAULT_WATER_PRICE: Final = 0.0

# Zone (Subentry)
CONF_AREA: Final = "area_m2"
CONF_THROUGHPUT: Final = "throughput_lpm"
CONF_KC: Final = "kc"
CONF_PLANT: Final = "plant"
CONF_KC_FACTOR: Final = "kc_factor"
CONF_SOIL_TYPE: Final = "soil_type"
CONF_ROOT_DEPTH: Final = "root_depth_cm"
CONF_DEPLETION_FRACTION: Final = "depletion_fraction"
CONF_IRRIGATION_TYPE: Final = "irrigation_type"
CONF_MIN_DURATION: Final = "min_duration_min"
CONF_MAX_DURATION: Final = "max_duration_min"
CONF_VALVE: Final = "valve_entity"
CONF_VOLUME_SENSOR: Final = "volume_sensor"
CONF_FLOW_SENSOR: Final = "flow_sensor"
CONF_SOIL_MOISTURE_SENSOR: Final = "soil_moisture_sensor"
CONF_SOIL_DRY_PCT: Final = "soil_dry_pct"
CONF_SOIL_WET_PCT: Final = "soil_wet_pct"
CONF_COMPARE_ENTITY: Final = "compare_entity"

DEFAULT_SOIL_DRY_PCT: Final = 10.0
DEFAULT_SOIL_WET_PCT: Final = 40.0

SOIL_TYPES: Final = ["sand", "loamy_sand", "sandy_loam", "loam", "silt_loam", "clay"]
IRRIGATION_TYPES: Final = ["sprinkler", "drip"]

# Lauferkennung
VOLUME_SETTLE_SECONDS: Final = 20
NO_FLOW_GRACE_SECONDS: Final = 90
THROUGHPUT_DEVIATION: Final = 0.25
THROUGHPUT_SAMPLES: Final = 5
STUCK_OPEN_EXTRA_MIN: Final = 10

# Services
SERVICE_RECALCULATE: Final = "recalculate"
SERVICE_RECORD_IRRIGATION: Final = "record_irrigation"
SERVICE_SET_DEPLETION: Final = "set_depletion"
SERVICE_CALIBRATE_SOIL_SENSOR: Final = "calibrate_soil_sensor"
SERVICE_SET_WINTERIZED: Final = "set_winterized"
SERVICE_WEEKLY_REPORT: Final = "weekly_report"

ATTR_LITERS: Final = "liters"
ATTR_MINUTES: Final = "minutes"
ATTR_DEPLETION_MM: Final = "depletion_mm"
ATTR_SOIL_WATER_PCT: Final = "soil_water_pct"
ATTR_POINT: Final = "point"
ATTR_WINTERIZED: Final = "winterized"

# Saisonstatus der Anlage (Winter-Assistent)
STATUS_SEASON: Final = "season"
STATUS_RESTING: Final = "resting"
STATUS_WINTERIZE: Final = "winterize"
STATUS_WINTERIZED: Final = "winterized"
STATUS_PREPARE_SPRING: Final = "prepare_spring"
SEASON_STATUSES: Final = [STATUS_SEASON, STATUS_RESTING, STATUS_WINTERIZE, STATUS_WINTERIZED, STATUS_PREPARE_SPRING]
# Frost, ab dem Leitungen und Ventile entleert sein sollten (Tiefstwert der nächsten 72 h).
WINTER_FROST_C: Final = 0.0
ENSEMBLE_MAX_AGE: Final = timedelta(hours=6)
