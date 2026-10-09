"""Tests für den Open-Meteo-Parser."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from custom_components.smarte_bewaesserung.open_meteo import OpenMeteoError, parse_response

PAYLOAD = {
    "daily": {
        "time": ["2026-10-08", "2026-10-09", "2026-10-10"],
        "et0_fao_evapotranspiration": [1.6, 1.2, None],
        "precipitation_sum": [4.2, 0.0, 1.0],
        "temperature_2m_min": [9.0, 7.6, 6.0],
        "temperature_2m_max": [16.0, 15.0, 14.0],
    },
    "hourly": {
        "time": ["2026-10-09T10:00", "2026-10-09T11:00", "2026-10-09T12:00"],
        "precipitation": [0.0, 0.4, None],
        "temperature_2m": [9.0, 10.0, 11.0],
        "wind_speed_10m": [5.0, 6.0, 7.0],
        "et0_fao_evapotranspiration": [0.1, 0.2, 0.3],
    },
}


def test_parse() -> None:
    data = parse_response(PAYLOAD)
    # Tag ohne ET0 wird ausgelassen
    assert list(data.daily) == [date(2026, 10, 8), date(2026, 10, 9)]
    assert data.daily[date(2026, 10, 8)].rain_mm == 4.2
    # Stundenwerte gelten für die Stunde davor: 11:00 = 10–11 Uhr
    now = datetime(2026, 10, 9, 10, 30)
    assert [h.rain_mm for h in data.next_hours(now, 24)] == [0.4, 0.0]
    assert data.current_hour(now).wind_kmh == 6.0
    assert data.hourly[1].et0_mm == 0.2
    # Halbe Stunde 10:30–11:00 von 0,4 mm, dann 0 mm
    assert data.upcoming(now, 24)[1] == pytest.approx(0.2)
    # Tageswert: Stunden decken den Tag nicht ab → Tageswerte
    assert data.day_totals(date(2026, 10, 8)) == (1.6, 4.2)


def test_parse_broken() -> None:
    with pytest.raises(OpenMeteoError):
        parse_response({"daily": {}})
