"""Run-scoped circuit breaker for SerpApi (serpapi_docs rule 15).

Counts consecutive failures across one research run's searches. Once the count
reaches `threshold` the circuit opens for the REST of the run: remaining live
searches are skipped and the caller completes the step from recent evidence
within its freshness window (labeled `recent_evidence` / cached), or proceeds
honestly with whatever exists (downstream tolerates zero new claims -> UNKNOWN).

A success closes the circuit again (half-open probe) and resets the counter.
This class is intentionally stateless beyond the run it belongs to: the
orchestrator creates one instance per plan execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CircuitBreaker:
    threshold: int = 5
    consecutive_failures: int = 0
    opened: bool = False
    failures: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.opened:
            return "open"
        if self.consecutive_failures > 0:
            return "half_open"
        return "closed"

    def record_success(self) -> None:
        """Any success closes the circuit and resets the failure streak."""
        self.consecutive_failures = 0
        self.opened = False

    def record_failure(self, code: str = "PROVIDER_ERROR") -> None:
        self.failures.append(code)
        self.consecutive_failures += 1
        if self.consecutive_failures >= max(1, self.threshold):
            self.opened = True

    def allow(self) -> bool:
        """False once open: callers must skip the live search."""
        return not self.opened
