"""Pydantic V2 schema exports for Workflow Intelligence MCP."""

from workflow_intelligence.schemas.common import (  # noqa: F401
    AuditRecord,
    BaseMCPResponse,
    ExecutionStatus,
    PaginationParams,
    SeverityLevel,
)
from workflow_intelligence.schemas.decision import (  # noqa: F401
    DecisionBrief,
    DecisionBriefRequest,
    DimensionAttribution,
    KPICalculationRequest,
    KPIExplanationRequest,
    MetricComparison,
    PerformanceAnalysisResult,
)
from workflow_intelligence.schemas.exception import (  # noqa: F401
    EscalationRequest,
    EscalationResult,
    ExceptionCategory,
    MatchedEntity,
    ResolutionPlan,
    ResolutionRequest,
    WorkflowInvestigationRequest,
)
from workflow_intelligence.schemas.process import (  # noqa: F401
    AutomationCandidate,
    AutomationScoreReport,
    AutomationScoreRequest,
    BottleneckReport,
    BottleneckRequest,
    ProcessAnalysisResult,
    ProcessAnalyzeRequest,
    TransitionMetric,
)

__all__ = [
    # common
    "SeverityLevel",
    "ExecutionStatus",
    "BaseMCPResponse",
    "PaginationParams",
    "AuditRecord",
    # process
    "ProcessAnalyzeRequest",
    "BottleneckRequest",
    "AutomationScoreRequest",
    "TransitionMetric",
    "ProcessAnalysisResult",
    "BottleneckReport",
    "AutomationCandidate",
    "AutomationScoreReport",
    # exception
    "ExceptionCategory",
    "WorkflowInvestigationRequest",
    "ResolutionRequest",
    "EscalationRequest",
    "MatchedEntity",
    "ResolutionPlan",
    "EscalationResult",
    # decision
    "KPICalculationRequest",
    "KPIExplanationRequest",
    "DecisionBriefRequest",
    "MetricComparison",
    "DimensionAttribution",
    "DecisionBrief",
    "PerformanceAnalysisResult",
]
