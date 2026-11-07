"""Phase 1 wire results. No verification verdict is inferred from a query."""
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class QueryResult:
    status: Literal['ok', 'error', 'unsupported']
    data: Any = None
    evidence: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def failure(code, message, *, unsupported=False):
    return QueryResult('unsupported' if unsupported else 'error',
                       errors=[{'code': code, 'message': str(message)}])


@dataclass(frozen=True)
class CheckResult:
    requirement_id: str
    status: Literal['PASS', 'FAIL', 'UNKNOWN', 'NOT_APPLICABLE']
    kind: str
    expected: dict = field(default_factory=dict)
    observed: dict = field(default_factory=dict)
    violations: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    reason: str = ''
    reason_kind: str = ''
    check_id: str = ''
    proof_level: str = ''
    counts: dict = field(default_factory=dict)
    identity: dict = field(default_factory=dict)
    source_refs: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def aggregate(results):
    statuses = {r.status for r in results}
    if 'FAIL' in statuses:
        return 'FAIL'
    if not statuses or 'UNKNOWN' in statuses:
        return 'UNKNOWN'
    return 'PASS' if 'PASS' in statuses else 'NOT_APPLICABLE'
