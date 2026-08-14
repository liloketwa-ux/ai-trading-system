"""Research CLI.

Every run gets a reproducible id and records the dataset version, feature
versions, label definition, sampling policy, cost model and seed. A result
nobody can re-run is an anecdote.

    python -m ai_trading.research.cli hypothesis:list
    python -m ai_trading.research.cli hypothesis:create --id ICT-007 ...
    python -m ai_trading.research.cli hypothesis:report --id ICT-001
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .hypotheses import STANDARD_FAMILY, HypothesisRegistry

DEFAULT_ROOT = Path("research_artifacts")


def _registry(root: Path) -> HypothesisRegistry:
    return HypothesisRegistry(root / "hypotheses.jsonl")


def cmd_create(args) -> int:
    registry = _registry(Path(args.root))
    hypothesis = registry.register(
        args.id, args.description, tuple(args.features.split(",")),
        label_key=args.label, horizon_seconds=args.horizon,
        research_version=args.research_version, dataset_version=args.dataset_version,
        expected_direction=args.direction,
    )
    print(f"registered {hypothesis.hypothesis_id} (checksum {hypothesis.checksum})")
    return 0


def cmd_seed_family(args) -> int:
    """Register the pre-declared family so the trial denominator is fixed."""
    registry = _registry(Path(args.root))
    for hypothesis_id, description, features in STANDARD_FAMILY:
        registry.register(
            hypothesis_id, description, features,
            label_key=args.label, horizon_seconds=args.horizon,
            research_version=args.research_version,
            dataset_version=args.dataset_version,
        )
    print(f"family size: {registry.family_size('ICT')}")
    return 0


def cmd_list(args) -> int:
    registry = _registry(Path(args.root))
    if not len(registry):
        print("no hypotheses registered")
        return 0
    for hypothesis in registry.all():
        print(f"{hypothesis.hypothesis_id:<10} {hypothesis.description:<48} "
              f"features={','.join(hypothesis.feature_set)}")
    print(f"\ntrials in family: {registry.family_size('ICT')}")
    return 0


def cmd_report(args) -> int:
    path = Path(args.root) / "results" / f"{args.id}.json"
    if not path.exists():
        print(f"no result for {args.id}; run hypothesis:run first", file=sys.stderr)
        return 1
    payload = json.loads(path.read_text())
    print(json.dumps(payload, indent=2) if args.json else payload.get("rendered", payload))
    return 0


def cmd_compare(args) -> int:
    results = Path(args.root) / "results"
    if not results.exists():
        print("no results yet", file=sys.stderr)
        return 1
    rows = []
    for path in sorted(results.glob("*.json")):
        payload = json.loads(path.read_text())
        rows.append((payload["hypothesis_id"], payload["n_events"],
                     payload["net"]["estimate"], payload["conclusion"]))
    print(f"{'id':<10} {'n':>6} {'net':>12}  conclusion")
    for hypothesis_id, n, net, conclusion in rows:
        print(f"{hypothesis_id:<10} {n:>6} {net:>+12.6f}  {conclusion}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research", description="ICT hypothesis research")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("hypothesis:create")
    create.add_argument("--id", required=True)
    create.add_argument("--description", required=True)
    create.add_argument("--features", required=True, help="comma-separated")
    create.add_argument("--label", required=True)
    create.add_argument("--horizon", type=float, required=True)
    create.add_argument("--research-version", default="r1")
    create.add_argument("--dataset-version", default="unset")
    create.add_argument("--direction", default=None, choices=[None, "positive", "negative"])
    create.set_defaults(func=cmd_create)

    seed = sub.add_parser("hypothesis:seed-family")
    seed.add_argument("--label", default="forward_return_1h:v1")
    seed.add_argument("--horizon", type=float, default=3600.0)
    seed.add_argument("--research-version", default="r1")
    seed.add_argument("--dataset-version", default="unset")
    seed.set_defaults(func=cmd_seed_family)

    listing = sub.add_parser("hypothesis:list")
    listing.set_defaults(func=cmd_list)

    report = sub.add_parser("hypothesis:report")
    report.add_argument("--id", required=True)
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=cmd_report)

    compare = sub.add_parser("hypothesis:compare")
    compare.set_defaults(func=cmd_compare)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    Path(args.root).mkdir(parents=True, exist_ok=True)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
