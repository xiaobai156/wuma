from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TargetSpec:
    id: str
    name: str
    url: str
    region: str
    count: int
    source: dict[str, Any]
    parse: dict[str, Any]
    network: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True)
class FetchedDocument:
    source_url: str
    document_type: str
    content: str
    content_sha256: str
    record_id: str | None = None


@dataclass(frozen=True)
class DocumentBundle:
    target_id: str
    documents: tuple[FetchedDocument, ...]
    resolved_name: str


@dataclass(frozen=True)
class ResolvedContent:
    name: str
    content: str
    source_kind: str
    rendered: bool = False
    record_id: str | None = None


@dataclass(frozen=True)
class ScopedDocument:
    target_id: str
    source_url: str
    content: str
    document_type: str
    record_id: str | None = None


@dataclass(frozen=True)
class Candidate:
    target_id: str
    issue: str
    numbers: tuple[str, ...]
    source_url: str
    position: int
    segment: str
    record_id: str | None = None


@dataclass
class CrawlResult:
    url: str
    name: str
    issue: str
    numbers: list[str]
    target_id: str = ""


ValidatedResult = CrawlResult


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
