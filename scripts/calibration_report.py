#!/usr/bin/env python3
"""Run every calibration dataset and print the recovery table.

Usage:  PYTHONPATH=src python scripts/calibration_report.py
"""
from ai_trading.calibration import (
    ALL_GENERATORS, CalibrationRun, detect_by_regime, detect_mean_reversion,
    detect_momentum, false_discovery_stress, generate_momentum,
    generate_regime_dependent,
)
from ai_trading.research.costs import REALISTIC

COSTS = REALISTIC


def main() -> int:
    print("=" * 92)
    print(f"RESEARCH CALIBRATION -- cost model '{COSTS.name}', "
          f"{COSTS.round_trip_bps:.1f} bps round trip (one model, every dataset)")
    print("=" * 92)
    print(f"{'dataset':<18}{'n':>7}{'gross bps':>11}{'net bps':>10}{'p':>9}"
          f"  {'statistical':<18}{'economic':<28}{'ok'}")

    all_correct = True
    for name in ("null", "momentum", "mean_reversion", "regime_dependent", "sub_cost"):
        dataset = ALL_GENERATORS[name]()
        detector = detect_mean_reversion if name == "mean_reversion" else detect_momentum
        run = CalibrationRun(dataset, COSTS)
        d = run.run(detector)
        score = run.score()
        all_correct &= score.correct
        print(f"{name:<18}{d.samples:>7}{d.gross_mean_bps:>11.3f}"
              f"{d.net_mean_bps:>10.3f}{d.p_value:>9.4f}  "
              f"{d.statistical.value:<18}{d.economic.value:<28}"
              f"{'PASS' if score.correct else 'FAIL'}")

    print("\nOUT-OF-SAMPLE RECOVERY (chronological 50/50 split)")
    for name in ("momentum", "mean_reversion"):
        _is, oos = ALL_GENERATORS[name]().split(0.5)
        detector = detect_mean_reversion if name == "mean_reversion" else detect_momentum
        d = detector(oos, costs=COSTS, seed=7)
        print(f"  {name:<18} n={d.samples:<6} gross={d.gross_mean_bps:>8.3f} bps  "
              f"p={d.p_value:.4f}  {d.statistical.value}")

    print("\nKNOWN NEGATIVE EDGE (phi = -0.25)")
    neg = detect_momentum(generate_momentum(phi=-0.25, seed=61), costs=COSTS, seed=7)
    print(f"  gross={neg.gross_mean_bps:.3f} bps  p={neg.p_value:.4f}  "
          f"{neg.statistical.value} / {neg.economic.value}")

    print("\nREGIME BREAKDOWN")
    rb = detect_by_regime(generate_regime_dependent(), costs=COSTS, seed=7)
    for label, d in rb.detections.items():
        print(f"  regime {label}: n={d.samples:<6} gross={d.gross_mean_bps:>8.3f} bps  "
              f"CI[{d.ci_low_bps:.3f}, {d.ci_high_bps:.3f}]  {d.statistical.value}")
    print(f"  spread={rb.spread_bps:.3f} bps  intervals "
          f"{'disjoint' if rb.separated else 'overlapping'}")

    print("\nFALSE-DISCOVERY STRESS (pure null, pre-declared family)")
    for trials in (50, 200, 400, 800):
        fd = false_discovery_stress(trials=trials, seed=13)
        print(f"  trials={trials:<5} raw={fd.raw_discoveries:<4}"
              f"({fd.observed_false_positive_rate:>6.2%})  BH={fd.bh_discoveries:<3} "
              f"bonferroni={fd.bonferroni_discoveries:<3} "
              f"best_sharpe={fd.best_sharpe:.4f}  DSR={fd.deflated_sharpe:.4f}")

    print("\n" + "=" * 92)
    print(f"All five calibration objectives: {'PASS' if all_correct else 'FAIL'}")
    print("Synthetic throughout. No market claim is made by any result above.")
    print("=" * 92)
    return 0 if all_correct else 1


if __name__ == "__main__":
    raise SystemExit(main())
