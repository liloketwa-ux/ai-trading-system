"""``system:status``, ``system:audit`` and the gated campaign entry point.

    python -m ai_trading.project.cli system:status
    python -m ai_trading.project.cli system:status --json
    python -m ai_trading.project.cli system:audit
    python -m ai_trading.project.cli research:ict:run

The status output is **deterministic**: no timestamps, no elapsed times, no
iteration order that depends on a dict built at import. Two runs at the same
commit produce identical bytes, so the report can be diffed and committed.

``research:ict:run`` exists so the refusal has an address. Someone looking for
"how do I run the ICT research" finds a command, and the command tells them
exactly what is missing instead of leaving them to assemble an ungated run.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .audit import run_integrity_audit
from .gate import RealDataPending, run_ict_family_campaign
from .status import resolve_status

__all__ = ["build_parser", "main", "cmd_status", "cmd_audit", "cmd_ict_run",
           "render_status"]


def render_status(status) -> str:
    """Fixed-width key/value lines, in declared order."""
    payload = status.to_dict()
    holdout = ("UNSPENT (no research version has been evaluated)"
               if payload["market_claim_status"] == "BLOCKED"
               else "see the holdout ledger")
    rows = [
        ("project_status", payload["project_status"]),
        ("research_protocol_version", payload["research_protocol_version"]),
        ("ict_family", f"{payload['ict_family_label']} "
                       f"({payload['ict_family_version']})"),
        ("ict_family_fingerprint", payload["ict_family_fingerprint"]),
        ("ict_family_locked", str(payload["ict_family_locked"]).lower()),
        ("declared_trials", str(payload["declared_trials"])),
        ("test_count", "unavailable" if payload["test_count"] is None
                       else str(payload["test_count"])),
        ("code_commit", payload["code_commit"][:12] or "unknown"),
        ("real_data_status", payload["real_data_status"]),
        ("registered_providers",
         ", ".join(payload["registered_providers"]) or "none"),
        ("market_claim_status", payload["market_claim_status"]),
        ("holdout_status", holdout),
        ("prop_firm_readiness",
         f"rules modelled for {payload['primary_prop_target']}; "
         "no funded account, no credentials"),
        ("live_execution_status", payload["live_execution_status"]),
        ("primary_prop_target", payload["primary_prop_target"]),
        ("next_required_external_action",
         payload["next_required_external_action"]),
        ("next_permitted_research_action",
         payload["next_permitted_research_action"]),
    ]
    width = max(len(key) for key, _ in rows)
    lines = [f"{key.ljust(width)}  {value}" for key, value in rows]

    blockers = _blockers(payload)
    lines += ["", "outstanding blockers:"]
    lines += [f"  - {blocker}" for blocker in blockers] or ["  none"]
    return "\n".join(lines)


def _blockers(payload: dict) -> list[str]:
    """Derived from the status fields, not from a maintained list."""
    blockers: list[str] = []
    if payload["real_data_status"] != "APPROVED":
        blockers.append(
            "no approved real NQ dataset: network egress to a contract-level "
            "provider is refused at this environment's proxy, and no provider "
            "adapter is registered")
    if payload["market_claim_status"] == "BLOCKED":
        blockers.append(
            "MARKET_CLAIM_ALLOWED not granted, so no statement about NQ may be "
            "made from anything computed here")
    if payload["project_status"] == "EVIDENCE_PENDING":
        blockers.append(
            f"{payload['ict_family_label']} has never been run; the research "
            "engine is implemented and unexercised on market data")
    return blockers


def cmd_status(args) -> int:
    status = resolve_status(include_test_count=not args.no_tests)
    if args.json:
        print(json.dumps(status.to_dict(), indent=2, sort_keys=False))
    else:
        print(render_status(status))
    return 0


def cmd_audit(args) -> int:
    report = run_integrity_audit()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())
    return 0 if report.passed else 1


def cmd_ict_run(args) -> int:
    """The gated campaign entry point. Refuses, loudly, with exit code 3."""
    try:
        run_ict_family_campaign(None)
    except RealDataPending as error:
        print(str(error), file=sys.stderr)
        return 3
    return 0                                   # pragma: no cover - unreachable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="system", description="Project status, integrity audit and the "
                                   "gated ICT campaign entry point")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("system:status",
                            help="deterministic project status report")
    status.add_argument("--json", action="store_true")
    status.add_argument("--no-tests", action="store_true",
                        help="skip pytest collection; test_count reports null")
    status.set_defaults(func=cmd_status)

    audit = sub.add_parser("system:audit",
                           help="read-only integrity audit; exit 1 on a "
                                "critical failure")
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(func=cmd_audit)

    ict = sub.add_parser("research:ict:run",
                         help="run ICT-FAMILY-V1; refuses until a dataset "
                              "reaches MARKET_CLAIM_ALLOWED")
    ict.set_defaults(func=cmd_ict_run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
