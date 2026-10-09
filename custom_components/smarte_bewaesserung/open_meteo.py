"""Schlanker Open-Meteo-Client (kein API-Key nötig)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import aiohttp

API_URL = "https://api.open-meteo.com/v1/forecast"
PAST_DAYS = 7
FORECAST_DAYS = 2
TIMEOUT = aiohttp.ClientTimeout(total=30)


class OpenMeteoError(Exception):
    """Open-Meteo nicht erreichbar oder Antwort unbrauchbar."""


@dataclass(frozen=True)
class DailyWeather:
    """Tageswerte eines Kalendertags (Ortszeit)."""

    et0_mm: float
    rain_mm: float
    temp_min_c: float | None = None
    temp_max_c: float | None = None


@dataclass(frozen=True)
class HourlyWeather:
    """Stundenwerte."""

    time: datetime
    rain_mm: float
    temp_c: float | None
    wind_kmh: float | None


@dataclass
class WeatherData:
    """Aufbereitete Antwort."""

    daily: dict[date, DailyWeather] = field(default_factory=dict)
    hourly: list[HourlyWeather] = field(default_factory=list)

    def next_hours(self, now: datetime, hours: int = 24) -> list[HourlyWeather]:
        """Stundenwerte ab der aktuellen Stunde."""
        start = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        return [h for h in self.hourly if h.time >= start][:hours]

    def current_hour(self, now: datetime) -> HourlyWeather | None:
        """Stundenwert der laufenden Stunde."""
        upcoming = self.next_hours(now, 1)
        return upcoming[0] if upcoming else None


def _num(values: list[Any] | None, index: int) -> float | None:
    if values is None or index >= len(values) or values[index] is None:
        return None
    return float(values[index])


def parse_response(payload: dict[str, Any]) -> WeatherData:
    """JSON-Antwort in WeatherData umwandeln (Zeiten in Ortszeit, ohne tzinfo)."""
    try:
        daily = payload["daily"]
        hourly = payload["hourly"]
        data = WeatherData()
        for i, day in enumerate(daily["time"]):
            et0 = _num(daily.get("et0_fao_evapotranspiration"), i)
            rain = _num(daily.get("precipitation_sum"), i)
            if et0 is None or rain is None:
                continue
            data.daily[date.fromisoformat(day)] = DailyWeather(
                et0_mm=et0,
                rain_mm=rain,
                temp_min_c=_num(daily.get("temperature_2m_min"), i),
                temp_max_c=_num(daily.get("temperature_2m_max"), i),
            )
        for i, ts in enumerate(hourly["time"]):
            data.hourly.append(
                HourlyWeather(
                    time=datetime.fromisoformat(ts),
                    rain_mm=_num(hourly.get("precipitation"), i) or 0.0,
                    temp_c=_num(hourly.get("temperature_2m"), i),
                    wind_kmh=_num(hourly.get("wind_speed_10m"), i),
                )
            )
    except (KeyError, TypeError, ValueError) as err:
        raise OpenMeteoError(f"Unerwartete Antwort: {err}") from err
    return data


async def async_fetch(session: aiohttp.ClientSession, latitude: float, longitude: float, timezone: str) -> WeatherData:
    """Wetter der letzten Tage und Vorhersage laden."""
    params = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "daily": "et0_fao_evapotranspiration,precipitation_sum,temperature_2m_min,temperature_2m_max",
        "hourly": "precipitation,temperature_2m,wind_speed_10m",
        "wind_speed_unit": "kmh",
        "past_days": str(PAST_DAYS),
        "forecast_days": str(FORECAST_DAYS),
        "timezone": timezone,
    }
    try:
        async with session.get(API_URL, params=params, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            payload = await resp.json()
    except (TimeoutError, aiohttp.ClientError) as err:
        raise OpenMeteoError(f"Open-Meteo nicht erreichbar: {err}") from err
    return parse_response(payload)
