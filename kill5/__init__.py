"""Shared core for the kill-five crawler."""

from .domain import CrawlFailure, CrawlResult, RunStats

__all__ = ["CrawlFailure", "CrawlResult", "RunStats"]
