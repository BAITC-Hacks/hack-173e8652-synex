from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request

from moneygraph.api.settings import APISettings
from moneygraph.repository.database import Database
from moneygraph.repository.repositories import MoneyGraphRepository
from moneygraph.services.agentic_loop import AgenticLoopService
from moneygraph.services.artifacts import ArtifactStore
from moneygraph.services.auto_monitor import AgenticAutoMonitor
from moneygraph.services.graph_queries import GraphQueryService


@dataclass(slots=True)
class AppServices:
    settings: APISettings
    database: Database
    repository: MoneyGraphRepository
    artifacts: ArtifactStore
    graph: GraphQueryService
    agentic: AgenticLoopService
    auto_monitor: AgenticAutoMonitor


def build_services(settings: APISettings) -> AppServices:
    database = Database(settings.database_url)
    database.create_schema()
    repository = MoneyGraphRepository(database.session_factory)
    artifacts = ArtifactStore(settings.out_dir, settings.artifacts_dir, settings.data_dir)
    agentic = AgenticLoopService(
        repository=repository,
        data_dir=settings.data_dir,
        actor=settings.analyst_name,
    )
    return AppServices(
        settings=settings,
        database=database,
        repository=repository,
        artifacts=artifacts,
        graph=GraphQueryService(artifacts),
        agentic=agentic,
        auto_monitor=AgenticAutoMonitor(
            service=agentic,
            repository=repository,
            enabled=settings.agentic_auto_monitor_enabled,
            cadence_seconds=settings.agentic_auto_monitor_cadence_seconds,
        ),
    )


def get_services(request: Request) -> AppServices:
    return cast(AppServices, request.app.state.services)


ServicesDependency = Annotated[AppServices, Depends(get_services)]
