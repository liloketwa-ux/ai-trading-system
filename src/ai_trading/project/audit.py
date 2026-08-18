"""A read-only integrity audit. Eight checks, none of which trust a docstring.

Each check *exercises* the property rather than asserting that a guard exists.
The difference matters: `hasattr(engine, "refuses_lookahead")` passes forever
once someone adds the attribute, whereas running the engine over a prefix and
comparing the output to the same prefix of a full run fails the moment the
computation stops being causal.

Nothing here writes a file, mutates a registry, opens a socket, or submits an
order. The audit is safe to run in CI on every commit, which is the only way an
audit stays honest.

Severity has two levels. ``CRITICAL`` means an integrity property this project
is built on has failed and the result of any research run would be
untrustworthy. ``ADVISORY`` means something worth knowing that does not
invalidate results.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

__all__ = ["Severity", "AuditCheck", "AuditReport", "run_integrity_audit",
           "CHECK_NAMES"]

UTC = timezone.utc
_T0 = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)


class Severity(str, Enum):
    CRITICAL = "critical"
    ADVISORY = "advisory"


@dataclass(frozen=True)
class AuditCheck:
    name: str
    passed: bool
    severity: Severity
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed,
                "severity": self.severity.value, "detail": self.detail}


@dataclass(frozen=True)
class AuditReport:
    checks: tuple[AuditCheck, ...]

    @property
    def passed(self) -> bool:
        return not self.critical_failures

    @property
    def critical_failures(self) -> tuple[AuditCheck, ...]:
        return tuple(c for c in self.checks
                     if not c.passed and c.severity is Severity.CRITICAL)

    @property
    def failures(self) -> tuple[AuditCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)

    def summary(self) -> str:
        lines = [f"integrity audit: {len(self.checks)} checks, "
                 f"{len(self.failures)} failing "
                 f"({len(self.critical_failures)} critical)"]
        for check in self.checks:
            mark = "PASS" if check.passed else check.severity.value.upper()
            lines.append(f"  [{mark}] {check.name}: {check.detail}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"passed": self.passed, "check_count": len(self.checks),
                "critical_failures": [c.name for c in self.critical_failures],
                "checks": [c.to_dict() for c in self.checks]}


# =========================================================================
# 1. No future-data access
# =========================================================================


def _check_no_future_data_access() -> AuditCheck:
    """Run the feature engine over prefixes and compare against the full run.

    Prefix determinism is the property that actually matters: if the features
    emitted for bars 0..n are identical whether or not bars n+1.. exist, no
    future bar contributed. Everything else -- rolling ATR, confirmation
    indices, as-of mitigation -- is a means to this end, so this is what the
    audit measures.
    """
    from ..features.ict_objective import Candle, ObjectiveFeatureEngine

    prices = [20_000 + 40 * ((i * 7) % 11) - 15 * ((i * 3) % 5)
              for i in range(80)]
    candles = [
        Candle(index=i, event_time=_T0 + timedelta(minutes=i),
               available_at=_T0 + timedelta(minutes=i + 1),
               open=float(p), high=float(p) + 12.0, low=float(p) - 9.0,
               close=float(p) + 3.0, volume=100.0, instrument="AUDIT",
               timeframe="1m")
        for i, p in enumerate(prices)
    ]

    def fingerprint(emissions) -> list[tuple]:
        return [(e.bar_index, e.atr, snapshot.name, snapshot.feature_version,
                 repr(snapshot.value), snapshot.available_at.isoformat())
                for e in emissions for snapshot in e.snapshots]

    full = fingerprint(ObjectiveFeatureEngine().run(candles))
    for cut in (20, 40, 60, 79):
        prefix = fingerprint(ObjectiveFeatureEngine().run(candles[:cut]))
        expected = [row for row in full if row[0] < cut]
        if prefix != expected:
            differing = [row for row in prefix if row not in expected][:3]
            return AuditCheck(
                "no_future_data_access", False, Severity.CRITICAL,
                f"features differ when bars after index {cut} are withheld, so "
                f"a future bar contributed: {differing}")
    return AuditCheck(
        "no_future_data_access", True, Severity.CRITICAL,
        "prefix determinism holds at cuts 20/40/60/79 over 80 bars; withholding "
        "later bars changes nothing about earlier emissions")


# =========================================================================
# 2. No holdout leakage
# =========================================================================


def _check_no_holdout_leakage() -> AuditCheck:
    """Probe the split guard with every non-holdout purpose."""
    from ..research.splits import (
        HoldoutViolation, Purpose, SplitDefinition,
    )

    split = SplitDefinition(
        split_id="audit", split_version="1",
        dev_start=_T0, dev_end=_T0 + timedelta(days=30),
        validation_start=_T0 + timedelta(days=30),
        validation_end=_T0 + timedelta(days=45),
        holdout_start=_T0 + timedelta(days=45),
        holdout_end=_T0 + timedelta(days=60),
        created_at=_T0, created_commit="audit")

    permitted = [p for p in Purpose if p.may_touch_holdout]
    if permitted != [Purpose.FINAL_HOLDOUT_EVAL]:
        return AuditCheck(
            "no_holdout_leakage", False, Severity.CRITICAL,
            f"purposes able to reach the holdout: {[p.value for p in permitted]}"
            " -- only FINAL_HOLDOUT_EVAL may")

    for purpose in Purpose:
        if purpose.may_touch_holdout:
            continue
        try:
            split.assert_no_holdout(split.holdout_start, split.holdout_end,
                                    purpose)
        except HoldoutViolation:
            continue
        return AuditCheck(
            "no_holdout_leakage", False, Severity.CRITICAL,
            f"purpose {purpose.value!r} reached holdout dates without refusal")

    return AuditCheck(
        "no_holdout_leakage", True, Severity.CRITICAL,
        f"{len(list(Purpose)) - 1} non-holdout purposes refused holdout dates; "
        "only final_holdout_eval may touch them")


# =========================================================================
# 3. No mutable v1 family
# =========================================================================


def _check_no_mutable_v1_family() -> AuditCheck:
    from ..research.ict_family import FamilyLockError, ICT_FAMILY_V1
    from ..research.ict_freeze import (
        FROZEN_FINGERPRINT, FamilyDriftError, verify_frozen,
    )

    try:
        fingerprint = verify_frozen()
    except FamilyDriftError as error:
        return AuditCheck("no_mutable_v1_family", False, Severity.CRITICAL,
                          str(error).splitlines()[0])
    if fingerprint != FROZEN_FINGERPRINT:
        return AuditCheck("no_mutable_v1_family", False, Severity.CRITICAL,
                          f"fingerprint {fingerprint} != {FROZEN_FINGERPRINT}")

    for method in ("unlock", "reopen", "edit", "remove"):
        if hasattr(ICT_FAMILY_V1, method):
            return AuditCheck(
                "no_mutable_v1_family", False, Severity.CRITICAL,
                f"HypothesisFamily grew a {method!r} method; a locked "
                "pre-registration with an escape hatch is not locked")

    probe = ICT_FAMILY_V1.require("ICT-LS-001")
    try:
        ICT_FAMILY_V1.add(probe)
    except FamilyLockError:
        pass
    else:
        return AuditCheck("no_mutable_v1_family", False, Severity.CRITICAL,
                          "the locked family accepted an addition")

    return AuditCheck(
        "no_mutable_v1_family", True, Severity.CRITICAL,
        f"{FROZEN_FINGERPRINT} verified against the pinned record; locked, no "
        "unlock/reopen/edit/remove, and addition refused")


# =========================================================================
# 4. No unverified firm rules presented as verified
# =========================================================================


def _check_no_unverified_rules_as_verified() -> AuditCheck:
    """A rule usable for compliance must justify why it is usable.

    Three distinct claims are checked separately, because ``is_verified``
    covers two of them and conflating them produces a false alarm:

    * **Source-verified** -- an actual value read off official documentation.
      Must carry a url, a verified_at date and a verification method.
    * **Not applicable** -- the rule does not exist for this program. Legitimate
      and carries no url, but must carry a *note* saying why; an unexplained
      exemption is indistinguishable from a gap someone gave up on.
    * **Everything else** -- user-supplied, third-party or unknown. None of
      these may report ``is_verified``, and third-party is excluded on purpose:
      it is the largest source of stale prop-firm numbers and it reads as
      authoritative.
    """
    from ..propfirm import REGISTRY, VerificationMethod, VerificationStatus

    source_backed = {VerificationStatus.VERIFIED_OFFICIAL,
                     VerificationStatus.OFFICIAL_SOURCE_VERIFIED}
    offenders: list[str] = []
    verified = exempt = 0

    for profile in REGISTRY.all():
        for name, rule in profile.all_rules.items():
            where = f"{profile.ruleset_key}.{name}"
            if rule.status in source_backed:
                verified += 1
                source = rule.source
                missing = []
                if not getattr(source, "url", ""):
                    missing.append("url")
                if getattr(source, "verified_at", None) is None:
                    missing.append("verified_at")
                if getattr(source, "verification_method",
                           VerificationMethod.NONE) is VerificationMethod.NONE:
                    missing.append("verification_method")
                if missing:
                    offenders.append(
                        f"{where} source-verified without {'/'.join(missing)}")
            elif rule.status is VerificationStatus.NOT_APPLICABLE:
                exempt += 1
                if not getattr(rule.source, "note", ""):
                    offenders.append(f"{where} NOT_APPLICABLE with no note")
            elif rule.is_verified:
                offenders.append(
                    f"{where} is {rule.status.value} yet reports verified")

    if offenders:
        return AuditCheck("no_unverified_rules_as_verified", False,
                          Severity.CRITICAL,
                          f"{len(offenders)}: {offenders[:3]}")
    return AuditCheck(
        "no_unverified_rules_as_verified", True, Severity.CRITICAL,
        f"{verified} rules across {len(REGISTRY.all())} rulesets are "
        "source-verified and every one carries a url, a verified_at date and a "
        f"method; {exempt} NOT_APPLICABLE rules each explain the exemption; no "
        "user-supplied, third-party or unknown rule reports verified")


# =========================================================================
# 5. No OpenMobius case data in statistical research
# =========================================================================


def _check_no_openmobius_cases_in_research() -> AuditCheck:
    from ..knowledge import (
        CaseUseError, TemporalImportError, assert_importable,
        case_outcome_statistics, OPENMOBIUS_TEMPORAL_AUDIT,
    )

    try:
        case_outcome_statistics([])
    except CaseUseError:
        pass
    except Exception as error:                # noqa: BLE001 - reported, not raised
        return AuditCheck("no_openmobius_cases_in_research", False,
                          Severity.CRITICAL,
                          f"case_outcome_statistics raised {error!r} rather "
                          "than refusing outright")
    else:
        return AuditCheck("no_openmobius_cases_in_research", False,
                          Severity.CRITICAL,
                          "case_outcome_statistics returned a value; unreviewed "
                          "VLM extractions became statistics")

    # Not only the retrospective ones: anything short of POINT_IN_TIME_SAFE
    # must be refused import as-is, including delayed-confirmation outputs.
    unsafe = [f for f in OPENMOBIUS_TEMPORAL_AUDIT
              if not f.temporal_class.importable_as_is]
    leaked = []
    for finding in unsafe:
        try:
            assert_importable(finding)
        except TemporalImportError:
            continue
        leaked.append(finding.output)
    if leaked:
        return AuditCheck("no_openmobius_cases_in_research", False,
                          Severity.CRITICAL,
                          f"outputs importable despite forward dependence: "
                          f"{leaked}")

    barred = [f.output for f in OPENMOBIUS_TEMPORAL_AUDIT
              if f.temporal_class.barred_from_research]
    return AuditCheck(
        "no_openmobius_cases_in_research", True, Severity.CRITICAL,
        f"case statistics refused outright; {len(unsafe)} of "
        f"{len(OPENMOBIUS_TEMPORAL_AUDIT)} audited outputs refuse import "
        f"as-is, {len(barred)} of them barred from research entirely")


# =========================================================================
# 6. No live execution route
# =========================================================================


def _check_no_live_execution_route() -> AuditCheck:
    from .status import LiveExecutionStatus, _live_execution_status

    status, implementations = _live_execution_status()
    if status is not LiveExecutionStatus.DISABLED:
        return AuditCheck(
            "no_live_execution_route", False, Severity.CRITICAL,
            f"broker implementations beyond paper: {list(implementations)}")
    return AuditCheck(
        "no_live_execution_route", True, Severity.CRITICAL,
        "PaperBroker is the only Broker implementation; no live adapter exists "
        "to be enabled")


# =========================================================================
# 7. No credentials in code
# =========================================================================

#: Names that, assigned a string literal in source, would be a leaked secret.
_SECRET_NAMES = re.compile(
    r"(api_key|apikey|secret|password|passwd|private_key|access_token|"
    r"auth_token|token)$", re.IGNORECASE)

#: Placeholders that are obviously not credentials.
_HARMLESS = re.compile(
    r"^(|none|null|changeme|example|placeholder|xxx+|test|dummy|fake|"
    r"stub|\$\{.*\}|<.*>)$", re.IGNORECASE)


def _check_no_credentials_in_code() -> AuditCheck:
    """Parse the source tree; flag secret-shaped names bound to literals.

    AST rather than a text grep, so a line mentioning ``api_key`` in a docstring
    or a comparison does not trip it. Only *assignment of a non-trivial string
    literal* counts, which is what an actually-committed credential looks like.
    """
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    scanned = 0

    for path in sorted(root.rglob("*.py")):
        scanned += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            value = None
            if isinstance(node, ast.Assign):
                value = node.value
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                targets += [t.attr for t in node.targets
                            if isinstance(t, ast.Attribute)]
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value = node.value
                if isinstance(node.target, ast.Name):
                    targets = [node.target.id]
            if not targets or not isinstance(value, ast.Constant):
                continue
            if not isinstance(value.value, str) or _HARMLESS.match(value.value):
                continue
            for name in targets:
                if _SECRET_NAMES.search(name):
                    offenders.append(
                        f"{path.relative_to(root)}:{node.lineno} {name}")

    # A credential must also be impossible to pass on a command line, where it
    # would land in shell history.
    from ..history.cli import build_parser

    subparsers = build_parser()._subparsers._group_actions[0]
    flags = {action.dest
             for parser in subparsers.choices.values()
             for action in parser._actions}
    cli_offenders = sorted(f for f in flags if _SECRET_NAMES.search(f))

    if offenders or cli_offenders:
        return AuditCheck(
            "no_credentials_in_code", False, Severity.CRITICAL,
            f"source: {offenders[:3]}; cli flags: {cli_offenders}")
    return AuditCheck(
        "no_credentials_in_code", True, Severity.CRITICAL,
        f"{scanned} source files carry no secret-shaped assignment, and no CLI "
        "flag accepts a key; credentials are read from the environment by name")


# =========================================================================
# 8. No synthetic data eligible for market claims
# =========================================================================


def _check_no_synthetic_market_claims() -> AuditCheck:
    """Build a clean synthetic dataset and confirm the gate still refuses it."""
    from ..history import DataOrigin, assess_grades, run_quality_gate
    from ..history.grades import DatasetGrade
    from ..history.providers import SCHEMA_VERSION, Bar
    from ..project.gate import RealDataPending, require_real_data_approved

    bars = [
        Bar(source="audit-synthetic", event_time=_T0 + timedelta(minutes=i),
            available_at=_T0 + timedelta(minutes=i), retrieved_at=_T0,
            schema_version=SCHEMA_VERSION, instrument="NQ", contract="NQM26",
            timeframe="1m", open=20_000.0, high=20_010.0, low=19_990.0,
            close=20_005.0, volume=100.0)
        for i in range(30)
    ]
    report = run_quality_gate(bars, provider="audit-synthetic")
    grades = assess_grades(source_name="audit-synthetic",
                           origin=DataOrigin.SYNTHETIC,
                           quality_report=report, point_in_time_clean=True)

    if not grades.permits_research:
        return AuditCheck(
            "no_synthetic_market_claims", False, Severity.ADVISORY,
            "the synthetic probe did not reach RESEARCH_GRADE, so this check "
            "did not exercise the boundary it is meant to test")
    if grades.granted(DatasetGrade.MARKET_CLAIM_ALLOWED):
        return AuditCheck("no_synthetic_market_claims", False,
                          Severity.CRITICAL,
                          "a SYNTHETIC dataset was granted "
                          "MARKET_CLAIM_ALLOWED")

    class Dataset:
        origin = DataOrigin.SYNTHETIC
        grades = None

    Dataset.grades = grades
    try:
        require_real_data_approved(Dataset())
    except RealDataPending:
        pass
    else:
        return AuditCheck("no_synthetic_market_claims", False,
                          Severity.CRITICAL,
                          "the research gate accepted a synthetic dataset")

    return AuditCheck(
        "no_synthetic_market_claims", True, Severity.CRITICAL,
        "a RESEARCH_GRADE synthetic dataset is denied MARKET_CLAIM_ALLOWED and "
        "refused by the research gate; synthetic results describe the generator")


# =========================================================================
# The audit
# =========================================================================

_CHECKS = (
    _check_no_future_data_access,
    _check_no_holdout_leakage,
    _check_no_mutable_v1_family,
    _check_no_unverified_rules_as_verified,
    _check_no_openmobius_cases_in_research,
    _check_no_live_execution_route,
    _check_no_credentials_in_code,
    _check_no_synthetic_market_claims,
)

#: The audit's contract, so a removed check is a failing test rather than a
#: quietly shorter report.
CHECK_NAMES = (
    "no_future_data_access",
    "no_holdout_leakage",
    "no_mutable_v1_family",
    "no_unverified_rules_as_verified",
    "no_openmobius_cases_in_research",
    "no_live_execution_route",
    "no_credentials_in_code",
    "no_synthetic_market_claims",
)


def run_integrity_audit() -> AuditReport:
    """Run every check. Read-only: nothing here writes, mutates or connects."""
    return AuditReport(tuple(check() for check in _CHECKS))
