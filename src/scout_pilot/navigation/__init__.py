"""Generic semantic navigation layer."""

from scout_pilot.navigation.resolver import SemanticNavigationResolver
from scout_pilot.navigation.types import (
    FormFillPlan,
    FormFillPlanStep,
    NavigationIntent,
    NavigationIntentKind,
    PageTransition,
    SemanticCandidate,
    SemanticResolution,
    SemanticResolutionStatus,
)

__all__ = [
    "FormFillPlan",
    "FormFillPlanStep",
    "NavigationIntent",
    "NavigationIntentKind",
    "PageTransition",
    "SemanticCandidate",
    "SemanticNavigationResolver",
    "SemanticResolution",
    "SemanticResolutionStatus",
]
