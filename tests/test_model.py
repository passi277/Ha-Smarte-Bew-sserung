"""Tests für die Wasserbilanz."""

from __future__ import annotations

import pytest

from custom_components.smarte_bewaesserung import model

LAWN = model.ZoneParams(area_m2=120, throughput_lpm=17, kc=0.8, soil_type="loam", root_depth_cm=20)


def test_taw_raw() -> None:
    assert LAWN.taw_mm == pytest.approx(30.0)
    assert LAWN.raw_mm == pytest.approx(15.0)


def test_application_rate() -> None:
    # 17 l/min auf 120 m² mit 75 % Wirkungsgrad
    assert LAWN.application_rate_mm_per_min == pytest.approx(17 / 120 * 0.75)


def test_effective_rain() -> None:
    assert model.effective_rain(0.3) == 0.0
    assert model.effective_rain(10.5) == pytest.approx(9.0)


def test_close_day_adds_etc() -> None:
    result = model.close_day(LAWN, 5.0, et0_mm=4.0, rain_mm=0.0)
    assert result.etc_mm == pytest.approx(3.2)
    assert result.depletion_mm == pytest.approx(8.2)
    assert result.percolation_mm == 0.0


def test_close_day_rain_surplus_percolates() -> None:
    result = model.close_day(LAWN, 2.0, et0_mm=1.0, rain_mm=20.5)
    assert result.depletion_mm == 0.0
    assert result.percolation_mm == pytest.approx(18.0 - 2.0 - 0.8)


def test_close_day_capped_at_taw() -> None:
    assert model.close_day(LAWN, 29.0, et0_mm=8.0, rain_mm=0.0).depletion_mm == LAWN.taw_mm


def test_apply_irrigation() -> None:
    # 340 l auf 120 m² mit 75 % → 2,125 mm
    assert model.apply_irrigation(LAWN, 10.0, 340) == pytest.approx(10.0 - 2.125)
    assert model.apply_irrigation(LAWN, 1.0, 1000) == 0.0


def test_soil_moisture_mapping() -> None:
    assert model.depletion_from_soil_moisture(LAWN, 40, 10, 40) == 0.0
    assert model.depletion_from_soil_moisture(LAWN, 10, 10, 40) == pytest.approx(30.0)
    assert model.depletion_from_soil_moisture(LAWN, 25, 10, 40) == pytest.approx(15.0)
    assert model.depletion_from_soil_moisture(LAWN, 50, 10, 40) == 0.0
    assert model.depletion_from_soil_moisture(LAWN, 25, 40, 10) is None


def test_blend() -> None:
    assert model.blend_depletion(10, None, 0.5) == 10
    assert model.blend_depletion(10, 20, 0.5) == 15
    assert model.blend_depletion(10, 20, 1.0) == 20


def test_recommend_not_needed() -> None:
    rec = model.recommend(LAWN, 10.0, model.Conditions(), model.Thresholds())
    assert not rec.water
    assert rec.minutes == 0
    assert "genug Wasser" in rec.reason


def test_recommend_waters_with_rain_credit() -> None:
    rec = model.recommend(LAWN, 20.0, model.Conditions(rain_forecast_24h_mm=2.5), model.Thresholds())
    assert rec.water
    # 20 mm − 1,8 mm wirksamer Regen = 18,2 mm bei 0,10625 mm/min → 172 min, gekappt auf 30
    assert rec.target_mm == pytest.approx(18.2)
    assert rec.capped
    assert rec.minutes == 30
    assert rec.liters == 510


def test_recommend_drip_short_run() -> None:
    bananas = model.ZoneParams(
        area_m2=15, throughput_lpm=20, kc=1.2, soil_type="loam", root_depth_cm=40, irrigation_type="drip"
    )
    rec = model.recommend(bananas, 35.0, model.Conditions(), model.Thresholds())
    assert rec.water
    # 35 mm / (20/15*0,9 = 1,2 mm/min) = 29,2 → 30 min
    assert rec.minutes == 30
    assert not rec.capped


@pytest.mark.parametrize(
    ("conditions", "text"),
    [
        (model.Conditions(rain_forecast_24h_mm=5), "Regen angesagt"),
        (model.Conditions(min_temp_24h_c=1), "Frostgefahr"),
        (model.Conditions(wind_kmh=45), "Wind"),
        (model.Conditions(raining_now=True), "Regensensor"),
    ],
)
def test_recommend_blocked(conditions: model.Conditions, text: str) -> None:
    rec = model.recommend(LAWN, 25.0, conditions, model.Thresholds())
    assert not rec.water
    assert text in rec.reason


def test_recommend_below_minimum() -> None:
    small = model.ZoneParams(area_m2=1, throughput_lpm=20, kc=1, root_depth_cm=10, min_duration_min=3)
    rec = model.recommend(small, small.raw_mm, model.Conditions(), model.Thresholds())
    assert not rec.water
    assert "unter Minimum" in rec.reason


def test_seasonal_kc() -> None:
    from datetime import date

    from custom_components.smarte_bewaesserung.plants import phase, seasonal_kc

    assert seasonal_kc("lawn", date(2026, 4, 15)) == pytest.approx(1.0)
    assert seasonal_kc("lawn", date(2026, 1, 1)) == pytest.approx(0.6)
    # 1. März liegt zwischen Februar (0,64) und März (0,75)
    assert seasonal_kc("lawn", date(2026, 3, 1)) == pytest.approx(0.64 + 0.11 * 14 / 28, abs=0.001)
    # Frühjahrswachstum braucht mehr als Hochsommer
    assert seasonal_kc("lawn", date(2026, 4, 20)) > seasonal_kc("lawn", date(2026, 7, 20))
    assert seasonal_kc("banana", date(2026, 7, 15)) == pytest.approx(1.2)
    assert seasonal_kc("banana", date(2026, 1, 15)) == pytest.approx(0.2)
    assert seasonal_kc("custom", date(2026, 7, 15), 0.7) == 0.7
    assert phase("banana", date(2026, 12, 1)) == "dormant"
    assert phase("custom", date(2026, 12, 1)) is None


def test_grassland_temperature_sum() -> None:
    from datetime import date, timedelta

    from custom_components.smarte_bewaesserung.plants import grassland_temperature_sum

    means = {date(2026, 1, 1) + timedelta(days=i): 4.0 for i in range(70)}
    means[date(2026, 1, 5)] = -3.0  # Frosttage zählen 0
    # Januar: 30 Tage × 4 × 0,5 = 60; Februar: 28 × 4 × 0,75 = 84; März 1.–10.: 10 × 4 = 40
    assert grassland_temperature_sum(means, date(2026, 3, 11)) == pytest.approx(184.0)
    # Lücken über 3 Tage → unbekannt
    assert grassland_temperature_sum(means, date(2026, 4, 30)) is None


def test_weather_overrides_calendar() -> None:
    from datetime import date, timedelta

    from custom_components.smarte_bewaesserung.plants import adjust_for_weather, weather_dormant

    # Milder Winter: GTS 200 schon am 25. Februar erreicht → Rasen treibt aus, obwohl Kalender „Ruhe“ sagt
    warm = {date(2026, 1, 1) + timedelta(days=i): 6.0 for i in range(60)}
    day = date(2026, 2, 25)
    assert weather_dormant("lawn", warm, day) is False
    kc, phase, source = adjust_for_weather("lawn", day, False)
    assert (phase, source) == ("sprouting", "weather")
    assert kc == pytest.approx(0.75)
    # Bananen brauchen GTS 500 → ruhen noch
    assert weather_dormant("banana", warm, day) is True
    kc, phase, _ = adjust_for_weather("banana", day, True)
    assert (kc, phase) == (0.2, "dormant")

    # Herbst: fünf Tage unter 10 °C beenden die Bananen-Saison, Rasen (5 °C) wächst weiter
    autumn = {date(2026, 8, 1) + timedelta(days=i): 15.0 for i in range(70)}
    for i in range(5):
        autumn[date(2026, 10, 1) + timedelta(days=i)] = 8.0
    day = date(2026, 10, 9)
    assert weather_dormant("banana", autumn, day) is True
    assert weather_dormant("lawn", autumn, day) is False
    # Warmer November: Rasen reift noch ab statt Winterruhe
    kc, phase, _ = adjust_for_weather("lawn", date(2026, 11, 20), False)
    assert phase == "ripening"
    assert kc == pytest.approx(0.72)
    # Ohne Daten gilt der Kalender
    assert adjust_for_weather("lawn", date(2026, 11, 20), None)[1:] == ("dormant", "calendar")
