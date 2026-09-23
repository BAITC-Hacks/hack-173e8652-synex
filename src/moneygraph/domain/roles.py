"""Canonical, deliberately small role vocabulary for MoneyGraph."""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType


class Role(StrEnum):
    """Functional hypotheses derived only from the observed transfer graph."""

    CONSOLIDATOR = "consolidator"
    TRANSIT = "transit"
    DISTRIBUTOR = "distributor"
    TERMINAL = "terminal"
    COORDINATOR = "coordinator"
    PERIPHERAL = "peripheral"


CORE_ROLES: tuple[Role, ...] = (
    Role.CONSOLIDATOR,
    Role.TRANSIT,
    Role.DISTRIBUTOR,
    Role.TERMINAL,
    Role.COORDINATOR,
)
ROLE_VALUES: frozenset[str] = frozenset(role.value for role in Role)
ROLE_LABELS_RU = MappingProxyType(
    {
        Role.CONSOLIDATOR: "консолидация",
        Role.TRANSIT: "транзит",
        Role.DISTRIBUTOR: "распределение",
        Role.TERMINAL: "конечный получатель",
        Role.COORDINATOR: "структурное посредничество",
        Role.PERIPHERAL: "периферийный узел",
    }
)
