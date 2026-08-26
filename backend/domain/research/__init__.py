# backend/domain/research/__init__.py
"""Domain models for the research factory (phase P1).

Immutable contracts for datasets, labels, experiments, regime reports and
evaluation results. These models are the research analogue of the
decision-domain models: they make causal correctness (point-in-time data, no
leakage, honest validation) enforceable by construction rather than by
convention.
"""

from backend.domain.research.allocation import AllocationResult, StrategyAllocation, StrategyProfile
from backend.domain.research.alt_data import AltDataEvent, AltDataKind, AltDataSnapshot
from backend.domain.research.analog import (
    AnalogEvidence,
    AnalogRetrievalResult,
    HistoricalAnalog,
    MarketState,
)
from backend.domain.research.attribution import AttributionReport, FeatureAttribution, Metrics
from backend.domain.research.autonomy_program import (
    AutonomyProgramResult,
    ProgramStage,
    StageResult,
    StageVerdict,
)
from backend.domain.research.canary import (
    CanaryAction,
    CanaryNotAuthorized,
    CanaryPeriod,
    CanaryProgramResult,
)
from backend.domain.research.dataset import (
    DatasetKind,
    DatasetRecord,
    DatasetVersion,
    compute_content_hash,
)
from backend.domain.research.experiment import (
    ExperimentGroup,
    ExperimentRecord,
    ExperimentStatus,
)
from backend.domain.research.hypothesis import (
    CandidateInsight,
    CycleReport,
    EvidenceSummary,
    EvidenceVerdict,
    ExperimentOutcome,
    Hypothesis,
    HypothesisSource,
)
from backend.domain.research.label import (
    LabelDefinition,
    LabeledSample,
    LabelKind,
)
from backend.domain.research.paper_campaign import (
    PaperCampaignAction,
    PaperCampaignResult,
    PaperDay,
    PaperDayAction,
    PaperDayOutcome,
)
from backend.domain.research.promotion import (
    CandidateEvidence,
    DeploymentMonitor,
    GateDecision,
    ModelEnvironment,
    PromotionConfig,
    PromotionRequest,
    RollbackDecision,
)
from backend.domain.research.records import (
    CampaignRunRecord,
    CampaignStatus,
    DayOutcomeRecord,
    ProgramRunRecord,
    PromotionAction,
    PromotionDecisionRecord,
    RollbackRecord,
    StageSnapshot,
)
from backend.domain.research.regime_evaluation import (
    RegimeEvaluatedResult,
    RegimePerformance,
)
from backend.domain.research.robustness import (
    ExpenseStressReport,
    PerturbationOutcome,
    PerturbationReport,
    SelectionBiasReport,
)
from backend.domain.research.scaling import (
    ScaleTier,
    ScalingAction,
    ScalingBoundary,
    ScalingProgramResult,
)
from backend.domain.research.scenario import (
    Scenario,
    ScenarioAction,
    ScenarioDecision,
    ScenarioEvaluation,
    ScenarioSet,
)

__all__ = [
    "AllocationResult",
    "AltDataEvent",
    "AltDataKind",
    "AltDataSnapshot",
    "AnalogEvidence",
    "AnalogRetrievalResult",
    "AttributionReport",
    "AutonomyProgramResult",
    "CampaignRunRecord",
    "CampaignStatus",
    "CanaryAction",
    "CanaryNotAuthorized",
    "CanaryPeriod",
    "CanaryProgramResult",
    "CandidateEvidence",
    "CandidateInsight",
    "CycleReport",
    "DatasetKind",
    "DatasetRecord",
    "DatasetVersion",
    "DeploymentMonitor",
    "DayOutcomeRecord",
    "EvidenceSummary",
    "EvidenceVerdict",
    "ExperimentGroup",
    "ExperimentOutcome",
    "ExperimentRecord",
    "ExperimentStatus",
    "ExpenseStressReport",
    "FeatureAttribution",
    "GateDecision",
    "HistoricalAnalog",
    "Hypothesis",
    "HypothesisSource",
    "LabelDefinition",
    "LabelKind",
    "LabeledSample",
    "MarketState",
    "Metrics",
    "ModelEnvironment",
    "PaperCampaignAction",
    "PaperCampaignResult",
    "PaperDay",
    "PaperDayAction",
    "PaperDayOutcome",
    "PerturbationOutcome",
    "PerturbationReport",
    "PromotionConfig",
    "PromotionRequest",
    "ProgramStage",
    "ProgramRunRecord",
    "PromotionAction",
    "PromotionConfig",
    "PromotionDecisionRecord",
    "PromotionRequest",
    "RegimeEvaluatedResult",
    "RegimePerformance",
    "RollbackDecision",
    "RollbackRecord",
    "ScaleTier",
    "Scenario",
    "ScalingAction",
    "ScalingBoundary",
    "ScalingProgramResult",
    "ScenarioAction",
    "ScenarioDecision",
    "ScenarioEvaluation",
    "ScenarioSet",
    "SelectionBiasReport",
    "StageResult",
    "StageSnapshot",
    "StageVerdict",
    "StrategyAllocation",
    "StrategyProfile",
    "compute_content_hash",
]
