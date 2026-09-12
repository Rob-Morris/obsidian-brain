"""Granular built-in profile projection for the coordinated adapter cutover."""

from __future__ import annotations

from types import MappingProxyType

from _application.catalogue import ApplicationCatalogue
from _application.types import Authority


_PROFILE_AUTHORITY = MappingProxyType(
    {
        "reader": Authority.READER,
        "contributor": Authority.CONTRIBUTOR,
        "maintainer": Authority.MAINTAINER,
        "operator": Authority.OPERATOR,
        "administrator": Authority.ADMINISTRATOR,
    }
)
_AUTHORITY_RANK = {
    Authority.READER: 0,
    Authority.CONTRIBUTOR: 1,
    Authority.MAINTAINER: 2,
    Authority.OPERATOR: 3,
    Authority.ADMINISTRATOR: 4,
}


def builtin_profile_allow_lists(
    catalogue: ApplicationCatalogue,
) -> dict[str, tuple[str, ...]]:
    """Project cumulative exact application commands from authority metadata."""

    result = {}
    for profile, maximum in _PROFILE_AUTHORITY.items():
        tools = tuple(
            entry.command_id
            for entry in catalogue.entries
            if _AUTHORITY_RANK[entry.authority] <= _AUTHORITY_RANK[maximum]
        )
        if tools != tuple(sorted(set(tools))):
            raise RuntimeError(f"{profile} granular profile is not sorted and unique")
        result[profile] = tools
    if not (
        set(result["reader"])
        <= set(result["contributor"])
        <= set(result["maintainer"])
        <= set(result["operator"])
        <= set(result["administrator"])
    ):
        raise RuntimeError("built-in granular profiles must be cumulative")
    return result
