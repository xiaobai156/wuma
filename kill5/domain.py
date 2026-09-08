from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResolvedContent:
    name: str
    content: str
    source_kind: str
    rendered: bool = False
    record_id: str | None = None


@dataclass
class CrawlResult:
    url: str
    name: str
    issue: str
    numbers: list[str]
    target_id: str = ""

@dataclass
class CrawlFailure:
    url: str
    name: str
    reason: str
    error_code: str | None = None
    stage: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False


@dataclass
class RunStats:
    total_targets: int
    initial_success: int
    retry_rescued: int
    retry_passes_used: int
    workers: int


@dataclass
class RunOutcome:
    results: list[CrawlResult]
    failures: list[CrawlFailure]
    stats: RunStats
    issues: list[str]
    targets: list[dict[str, Any]]


@dataclass
class RunExecution:
    outcome: RunOutcome
    result_file: str
    failed_file: str
    report_file: str
    cache_updated: bool
    preserved_outputs: bool = False
    cache_error: str | None = None
