"""Dependency injection container for workflow intelligence services."""

from __future__ import annotations

from dataclasses import dataclass, field

from workflow_intelligence.services.analytics_service import AnalyticsService
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.services.exception_service import ExceptionService
from workflow_intelligence.services.process_service import ProcessService


@dataclass
class ServiceContainer:
    """Dependency injection container managing service lifecycles."""

    process_service: ProcessService = field(default_factory=ProcessService)
    exception_service: ExceptionService = field(default_factory=ExceptionService)
    analytics_service: AnalyticsService = field(default_factory=AnalyticsService)
    decision_service: DecisionService = field(init=False)

    def __post_init__(self) -> None:
        self.decision_service = DecisionService(analytics=self.analytics_service)


def get_service_container() -> ServiceContainer:
    """Return a newly instantiated default service container."""
    return ServiceContainer()
