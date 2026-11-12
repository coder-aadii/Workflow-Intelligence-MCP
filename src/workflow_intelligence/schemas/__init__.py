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
    DeviationExplanation,
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
    ExceptionInvestigationResult,
    MatchedEntity,
    ResolutionPlan,
    ResolutionRequest,
    WorkflowInvestigationRequest,
)
from workflow_intelligence.schemas.process import (  # noqa: F401
    AutomationCandidate,
    AutomationScoreReport,
    AutomationScoreRequest,
    BottleneckItem,
    BottleneckReport,
    BottleneckRequest,
    ProcessAnalysisResult,
    ProcessAnalyzeRequest,
    StageMetric,
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
    "StageMetric",
    "ProcessAnalysisResult",
    "BottleneckItem",
    "BottleneckReport",
    "AutomationCandidate",
    "AutomationScoreReport",
    # exception
    "ExceptionCategory",
    "ExceptionInvestigationResult",
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
    "DeviationExplanation",
    "MetricComparison",
    "DimensionAttribution",
    "DecisionBrief",
    "PerformanceAnalysisResult",
]
