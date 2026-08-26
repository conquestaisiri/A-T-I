# backend/domain/context/feature_registry.py
"""Feature registry for context features.

Provides deterministic registration order, duplicate protection, and basic dependency handling.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from backend.domain.context.errors import DuplicateFeatureError, FeatureRegistrationError
from backend.domain.context.features import FeatureCls


class FeatureRegistry:
    """Registry for ContextFeature implementations.

    Features are registered by calling :meth:`register`. The registry maintains
    insertion order to guarantee deterministic discovery. Duplicate feature names
    raise :class:`DuplicateFeatureError`.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # OrderedDict preserves registration order
        self._features: OrderedDict[str, type[FeatureCls]] = OrderedDict()

    def register(self, feature_cls: type[FeatureCls]) -> None:
        """Register a feature class.

        Parameters
        ----------
        feature_cls: Type[ContextFeature]
            The concrete feature class to register. It must define a ``name``
            attribute (string) and optionally a ``depends_on`` attribute – a list
            of other feature names this feature depends on.
        """
        with self._lock:
            # Ensure the class provides a name
            feature_name = getattr(feature_cls, "name", None)
            if not isinstance(feature_name, str) or not feature_name:
                raise FeatureRegistrationError(
                    f"Feature class {feature_cls.__name__} must define a non‑empty ``name`` string."
                )
            if feature_name in self._features:
                raise DuplicateFeatureError(f"Feature '{feature_name}' already registered.")
            # Basic dependency validation – all declared dependencies must already be registered
            depends = getattr(feature_cls, "depends_on", [])
            if not isinstance(depends, list):
                raise FeatureRegistrationError(
                    f"Feature '{feature_name}' attribute 'depends_on' must be "
                    "a list of feature names."
                )
            for dep_name in depends:
                if dep_name not in self._features:
                    raise FeatureRegistrationError(
                        f"Feature '{feature_name}' depends on unknown feature '{dep_name}'."
                    )
            self._features[feature_name] = feature_cls

    def get_all(self) -> list[type[FeatureCls]]:
        """Return a list of all registered feature classes in registration order."""
        with self._lock:
            return list(self._features.values())

    def get_by_name(self, name: str) -> type[FeatureCls] | None:
        """Retrieve a feature class by its name, or ``None`` if not registered."""
        with self._lock:
            return self._features.get(name)

    def clear(self) -> None:
        """Remove all registered features – useful for test isolation."""
        with self._lock:
            self._features.clear()
