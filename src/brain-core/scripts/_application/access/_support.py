"""Shared strict decoding and metadata for authenticated consent controls."""
from collections.abc import Mapping

from ..types import Authority, DependencyTier, EffectClass, InitialAuthorisationClass, Locality, RetryClass


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def identifiers(values, name):
    if not isinstance(values, tuple) or not 1 <= len(values) <= 128:
        raise ValueError(f"{name} requires between 1 and 128 identifiers")
    for value in values:
        nonempty(value, name)
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{name} must be sorted and unique")


def object_fields(value, allowed, required=()):
    if not isinstance(value, Mapping) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError(f"Expected object fields: {', '.join(sorted(allowed))}")
    return value


def control_entry(request_type, executor, *, summary, mutation=False):
    from ..catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry
    return ApplicationEntry(request_type=request_type, executor=executor,
        dependency_tier=DependencyTier.BOOTSTRAP, locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(), optional_providers=(), authority=Authority.READER,
        initial_class=InitialAuthorisationClass.CONTROL,
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION if mutation else EffectClass.NONE,
        retry_class=RetryClass.RECEIPT_REQUIRED if mutation else RetryClass.SAFE,
        projections=ALL_APPLICATION_PROJECTIONS, summary=summary)
