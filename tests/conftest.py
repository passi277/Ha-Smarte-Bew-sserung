"""Gemeinsame Fixtures."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from custom_components.smarte_bewaesserung.open_meteo import DailyWeather, HourlyWeather, WeatherData


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Custom Integrations in allen Tests erlauben."""
    return


def make_weather(
    today: date,
    *,
    et0: float = 4.0,
    rain: float = 0.0,
    forecast_rain_per_hour: float = 0.0,
    temp: float = 15.0,
    wind: float = 5.0,
) -> WeatherData:
    """Wetter von 7 Tagen zurück bis 3 Tage voraus, gleichmäßig über die Stunden verteilt."""
    data = WeatherData()
    for offset in range(-7, 3):
        data.daily[today + timedelta(days=offset)] = DailyWeather(et0_mm=et0, rain_mm=rain, temp_min_c=temp)
    start = datetime.combine(today - timedelta(days=7), datetime.min.time())
    for hour in range(1, 24 * 10 + 1):
        data.hourly.append(
            HourlyWeather(
                time=start + timedelta(hours=hour),
                rain_mm=rain / 24 + forecast_rain_per_hour,
                temp_c=temp,
                wind_kmh=wind,
                et0_mm=et0 / 24,
            )
        )
    return data


@pytest.fixture
def mock_fetch():
    """Open-Meteo-Abruf ersetzen; Rückgabewert über mock_fetch.return_value setzen."""
    with patch("custom_components.smarte_bewaesserung.coordinator.async_fetch") as mock:
        yield mock


@pytest.fixture(autouse=True)
def mock_daily_means():
    """Archivabruf der Tagesmittel ersetzen (Standard: keine Daten → Kalender)."""
    with patch("custom_components.smarte_bewaesserung.coordinator.async_fetch_daily_means", return_value={}) as mock:
        yield mock


@pytest.fixture(autouse=True)
def mock_ensemble():
    """Ensemble-Abruf ersetzen (Standard: nicht erreichbar → normale Vorhersage)."""
    from custom_components.smarte_bewaesserung.open_meteo import OpenMeteoError

    with patch(
        "custom_components.smarte_bewaesserung.coordinator.async_fetch_ensemble",
        side_effect=OpenMeteoError("kein Netz im Test"),
    ) as mock:
        yield mock


def make_ensemble(today: date, member_rain_per_hour: list[float]):
    """Ensemble mit einem Lauf je Eintrag (konstanter Regen pro Stunde)."""
    from custom_components.smarte_bewaesserung.open_meteo import EnsembleRain

    start = datetime.combine(today, datetime.min.time())
    times = [start + timedelta(hours=h) for h in range(1, 73)]
    return EnsembleRain(times, [[r] * len(times) for r in member_rain_per_hour])
