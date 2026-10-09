"""Tests für Intervalle, Regenwahrscheinlichkeit, Selbstkalibrierung und Lernen."""

from __future__ import annotations

import pytest

from custom_components.smarte_bewaesserung import model
from custom_components.smarte_bewaesserung.learning import DemandLearner, SoilCalibrator

BANANAS = model.ZoneParams(area_m2=15, throughput_lpm=20, kc=1.2, root_depth_cm=40, irrigation_type="drip")
LAWN = model.ZoneParams(area_m2=120, throughput_lpm=17, kc=0.8, root_depth_cm=20)
HOUR = 3600


def test_cycle_plan_for_fast_zone() -> None:
    # 80 mm/h auf Lehm (12 mm/h): nach gut 2 min stünde Wasser → 2-min-Intervalle, 15 min Pause
    plan = model.cycle_plan(BANANAS, 10)
    assert plan == model.CyclePlan(cycles=5, on_min=2, soak_min=15)
    assert plan.describe() == "5 × 2 min, je 15 min Pause"
    # Rasen mit 8,5 mm/h versickert sofort
    assert model.cycle_plan(LAWN, 30) is None
    # Kurzer Lauf passt in ein Intervall
    assert model.cycle_plan(BANANAS, 2) is None


def test_recommendation_contains_cycles() -> None:
    rec = model.recommend(BANANAS, 30.0, model.Conditions(), model.Thresholds())
    assert rec.cycles is not None
    assert "in Intervallen" in rec.reason


def test_rain_probability_blocks_or_credits() -> None:
    thresholds = model.Thresholds(rain_skip_mm=3, rain_probability=0.7)
    likely = model.Conditions(rain_forecast_24h_mm=1, rain_probability=0.8, rain_expected_effective_mm=4)
    rec = model.recommend(LAWN, 20, likely, thresholds)
    assert not rec.water
    assert "80 % Chance auf ≥ 3,0 mm Regen" in rec.reason

    # 40 %: nicht sperren, aber den erwarteten Regen anrechnen – auch wenn die Vorhersage 5 mm sagt
    maybe = model.Conditions(rain_forecast_24h_mm=5, rain_probability=0.4, rain_expected_effective_mm=2)
    rec = model.recommend(LAWN, 20, maybe, thresholds)
    assert rec.water
    assert rec.target_mm == pytest.approx(18)
    assert "40 % Regenchance" in rec.reason


def test_winterized_blocks() -> None:
    rec = model.recommend(LAWN, 25, model.Conditions(winterized=True), model.Thresholds())
    assert not rec.water
    assert "winterfest" in rec.reason


def test_soil_calibrator_learns_field_capacity() -> None:
    cal = SoilCalibrator({})
    t = 0.0
    for value in (22, 21.8, 21.6):  # trocknet langsam ab
        cal.feed(t, value)
        t += HOUR
    for value in (26, 31, 36):  # Gießen: steigt
        cal.feed(t, value)
        t += 600
    peak = t
    # Sickerwasser fließt ab, dann Plateau
    for hours, value in ((2, 34), (6, 32), (12, 30.5), (20, 30.2)):
        assert not cal.feed(peak + hours * HOUR, value)
    assert cal.wet_pct is None
    assert cal.feed(peak + 24 * HOUR, 30.0)
    assert cal.wet_pct == 30.0
    # Trockenwert: eingestellt 10 %, gemessen nie darunter → 10 %
    assert cal.dry_pct(10, cal.wet_pct) == 10
    # Gemessen schon trockener → niedriger
    cal.feed(peak + 40 * HOUR, 8.0)
    assert cal.dry_pct(10, 30) == 8.0
    # Funkaussetzer mit 0 % verfälscht den Trockenwert nicht
    cal.feed(peak + 41 * HOUR, 0.0)
    assert cal.dry_pct(10, 30) == 8.0


def test_soil_calibrator_ignores_slow_decline() -> None:
    cal = SoilCalibrator({})
    for i in range(48):
        assert not cal.feed(i * HOUR, 35 - i * 0.2)
    assert cal.state["event_peak"] is None


def test_demand_learner() -> None:
    learner = DemandLearner({})
    # Sensor im Grenzbereich: nicht lernen
    assert learner.learn("2026-07-01", 2.0, 4.0, 0.5, 2.5, taw_mm=30) is None
    # Zu wenig Verbrauch: nicht lernen
    assert learner.learn("2026-07-01", 0.5, 0.8, 10, 10.5, taw_mm=30) is None
    for day in range(3):
        assert learner.learn(f"2026-07-0{day + 2}", 2.0, 4.0, 10, 12, taw_mm=30) == pytest.approx(0.5)
    # Nach 3 Lerntagen aktiv und langsam gesunken: 0,925³
    assert learner.factor == pytest.approx(0.925**3, abs=0.002)
    assert learner.state["samples"] == 3
