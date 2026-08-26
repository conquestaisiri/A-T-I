# backend/application/research/__init__.py
"""Research factory: dataset versioning, labelling, evaluation, experiments.

Phase P1 builds the causally correct research and validation system that turns
captured market data into provable edges. The modules in this package are the
application-side owners of research truth; they depend only on the domain
models and the persistence ports.
"""

from backend.application.research.alt_data_service import AltDataService
from backend.application.research.analog_retrieval import (
    AnalogRetrievalConfig,
    AnalogRetrievalEngine,
    analog_retrieval_result,
    feature_similarity,
    make_state,
)
from backend.application.research.autonomy_program import (
    AutonomyProgram,
    ProgramConfig,
    run_autonomy_program,
)
from backend.application.research.baseline_evaluation import (
    AlwaysFlatBaseline,
    BaselineEvaluator,
    BuyAndHoldBaseline,
    EvaluationCosts,
    MomentumBaseline,
    MovingAverageCrossoverBaseline,
    compare_strategies,
)
from backend.application.research.canary_harness import (
    CanaryHarness,
    CanaryHarnessConfig,
    run_canary_campaign,
)
from backend.application.research.dataset_service import DatasetService
from backend.application.research.experiment_registry import ExperimentRegistry
from backend.application.research.feature_attribution import AblationRunner, ThresholdScorer
from backend.application.research.gradual_scaling import (
    GradualScalingRunner,
    ScalingConfig,
    run_gradual_scaling,
)
from backend.application.research.label_engine import LabelEngine
from backend.application.research.paper_autonomy import (
    PaperAutonomyRunner,
    PaperCampaignConfig,
    run_paper_campaign,
)
from backend.application.research.promotion_engine import (
    PromotionEngine,
    promote,
    promotion_chain,
    rollback_required,
)
from backend.application.research.regime_evaluation import (
    RegimeEvaluator,
    VolatilityRegimeClassifier,
)
from backend.application.research.research_loop import (
    HypothesisGenerator,
    ResearchLoop,
    ResearchLoopConfig,
    generate_hypotheses,
    run_research_cycle,
)
from backend.application.research.robustness import RobustnessRunner
from backend.application.research.scenario_engine import (
    CalibrationBucket,
    ScenarioEngine,
    build_scenario_set,
    round_trip_cost_pct,
)
from backend.application.research.strategy_allocator import AllocationConfig, allocate_strategies

__all__ = [
    "AblationRunner",
    "AllocationConfig",
    "AltDataService",
    "AlwaysFlatBaseline",
    "AnalogRetrievalConfig",
    "AnalogRetrievalEngine",
    "AutonomyProgram",
    "BaselineEvaluator",
    "BuyAndHoldBaseline",
    "CalibrationBucket",
    "CanaryHarness",
    "CanaryHarnessConfig",
    "DatasetService",
    "EvaluationCosts",
    "ExperimentRegistry",
    "GradualScalingRunner",
    "HypothesisGenerator",
    "LabelEngine",
    "MomentumBaseline",
    "MovingAverageCrossoverBaseline",
    "PaperAutonomyRunner",
    "PaperCampaignConfig",
    "ProgramConfig",
    "PromotionEngine",
    "ResearchLoop",
    "ResearchLoopConfig",
    "RegimeEvaluator",
    "RobustnessRunner",
    "ScenarioEngine",
    "ScalingConfig",
    "ThresholdScorer",
    "VolatilityRegimeClassifier",
    "allocate_strategies",
    "analog_retrieval_result",
    "build_scenario_set",
    "compare_strategies",
    "feature_similarity",
    "generate_hypotheses",
    "make_state",
    "promote",
    "promotion_chain",
    "rollback_required",
    "round_trip_cost_pct",
    "run_canary_campaign",
    "run_gradual_scaling",
    "run_paper_campaign",
    "run_autonomy_program",
    "run_research_cycle",
]
