"""Schlanker Open-Meteo-Client (kein API-Key nötig)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

API_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
PAST_DAYS = 7
FORECAST_DAYS = 3
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
    temp_mean_c: float | None = None


@dataclass(frozen=True)
class HourlyWeather:
    """Stundenwerte. Mengen (Regen, ET0) gelten für die Stunde *vor* `time`."""

    time: datetime
    rain_mm: float
    temp_c: float | None
    wind_kmh: float | None
    et0_mm: float | None = None


HOUR = timedelta(hours=1)


@dataclass
class WeatherData:
    """Aufbereitete Antwort (Zeiten in Ortszeit, ohne tzinfo)."""

    daily: dict[date, DailyWeather] = field(default_factory=dict)
    hourly: list[HourlyWeather] = field(default_factory=list)

    def _sum(self, attr: str, start: datetime, end: datetime) -> tuple[float, float]:
        """Summe eines Stundenwerts im Zeitraum, anteilig für angeschnittene Stunden.

        Gibt (Summe, abgedeckte Stunden) zurück.
        """
        total = 0.0
        covered = 0.0
        for h in self.hourly:
            value = getattr(h, attr)
            if value is None:
                continue
            overlap = (min(h.time, end) - max(h.time - HOUR, start)).total_seconds() / 3600
            if overlap > 0:
                total += value * overlap
                covered += overlap
        return total, covered

    def day_totals(self, day: date) -> tuple[float, float] | None:
        """(ET0, Regen) eines Kalendertags, bevorzugt aus Stundenwerten.

        So passt der Tagesabschluss exakt zum laufenden Stand des Tages.
        """
        start = datetime.combine(day, datetime.min.time())
        et0, covered_et0 = self._sum("et0_mm", start, start + timedelta(days=1))
        rain, covered_rain = self._sum("rain_mm", start, start + timedelta(days=1))
        if covered_et0 >= 23.5 and covered_rain >= 23.5:
            return et0, rain
        daily = self.daily.get(day)
        return (daily.et0_mm, daily.rain_mm) if daily else None

    def so_far_today(self, now: datetime) -> tuple[float, float]:
        """(ET0, Regen) seit Mitternacht bis jetzt."""
        now = now.replace(tzinfo=None)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self._sum("et0_mm", midnight, now)[0], self._sum("rain_mm", midnight, now)[0]

    def upcoming(self, now: datetime, hours: float = 24) -> tuple[float, float]:
        """(ET0, Regen) der nächsten Stunden."""
        now = now.replace(tzinfo=None)
        end = now + timedelta(hours=hours)
        return self._sum("et0_mm", now, end)[0], self._sum("rain_mm", now, end)[0]

    def next_hours(self, now: datetime, hours: int = 24) -> list[HourlyWeather]:
        """Stundenwerte ab der laufenden Stunde."""
        now = now.replace(tzinfo=None)
        return [h for h in self.hourly if h.time > now][:hours]

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
                temp_mean_c=_num(daily.get("temperature_2m_mean"), i),
            )
        for i, ts in enumerate(hourly["time"]):
            data.hourly.append(
                HourlyWeather(
                    time=datetime.fromisoformat(ts),
                    rain_mm=_num(hourly.get("precipitation"), i) or 0.0,
                    temp_c=_num(hourly.get("temperature_2m"), i),
                    wind_kmh=_num(hourly.get("wind_speed_10m"), i),
                    et0_mm=_num(hourly.get("et0_fao_evapotranspiration"), i),
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
        "daily": "et0_fao_evapotranspiration,precipitation_sum,"
        "temperature_2m_min,temperature_2m_max,temperature_2m_mean",
        "hourly": "precipitation,temperature_2m,wind_speed_10m,et0_fao_evapotranspiration",
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


async def async_fetch_daily_means(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    timezone: str,
    start: date,
    end: date,
) -> dict[date, float]:
    """Tagesmitteltemperaturen aus dem Open-Meteo-Archiv (einige Tage Verzug)."""
    params = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "daily": "temperature_2m_mean",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": timezone,
    }
    try:
        async with session.get(ARCHIVE_URL, params=params, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            payload = await resp.json()
        daily = payload["daily"]
        return {
            date.fromisoformat(day): float(mean)
            for day, mean in zip(daily["time"], daily["temperature_2m_mean"], strict=True)
            if mean is not None
        }
    except (TimeoutError, aiohttp.ClientError, KeyError, TypeError, ValueError) as err:
        raise OpenMeteoError(f"Open-Meteo-Archiv nicht erreichbar: {err}") from err
