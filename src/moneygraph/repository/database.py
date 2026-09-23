from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    """Declarative base shared by the persistence models."""


class Database:
    """Own an SQLAlchemy engine and a short-lived session factory."""

    def __init__(self, database_url: str) -> None:
        if not database_url.strip():
            raise ValueError("database_url must not be empty")

        engine_options: dict[str, object] = {"pool_pre_ping": True}
        if database_url.startswith("sqlite"):
            engine_options["connect_args"] = {"check_same_thread": False}
            if database_url in {"sqlite://", "sqlite:///:memory:"}:
                engine_options["poolclass"] = StaticPool

        self.engine: Engine = create_engine(database_url, **engine_options)
        if database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self.session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    def create_schema(self) -> None:
        # Import registers every mapped class before metadata creation.
        from moneygraph.repository import models  # noqa: F401

        Base.metadata.create_all(self.engine)
        self._backfill_auto_monitor_claims()

    def _backfill_auto_monitor_claims(self) -> None:
        """Upgrade pre-claim databases without deleting duplicate historical scans."""

        from moneygraph.repository.models import AutoMonitorClaim, MonitoringScan

        with self.engine.begin() as connection:
            claimed = set(connection.execute(select(AutoMonitorClaim.replay_date)).scalars())
            scans = connection.execute(
                select(MonitoringScan.replay_date, MonitoringScan.id)
                .where(MonitoringScan.created_by == "auto-monitor")
                .order_by(MonitoringScan.replay_date, MonitoringScan.created_at, MonitoringScan.id)
            ).all()
            seen: set[object] = set()
            for replay_date, scan_id in scans:
                if replay_date in seen or replay_date in claimed:
                    continue
                seen.add(replay_date)
                try:
                    with connection.begin_nested():
                        connection.execute(
                            insert(AutoMonitorClaim).values(
                                replay_date=replay_date,
                                scan_id=scan_id,
                            )
                        )
                except IntegrityError:
                    # Another worker or a previous boot already claimed this date.
                    pass

    def session(self) -> Iterator[Session]:
        """Yield a transactional session for framework dependency injection."""

        with self.session_factory() as session:
            yield session

    def dispose(self) -> None:
        self.engine.dispose()
