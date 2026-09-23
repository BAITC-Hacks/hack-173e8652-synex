from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import date
from threading import RLock
from typing import Any

from sqlalchemy.exc import IntegrityError

from moneygraph.repository.repositories import MoneyGraphRepository
from moneygraph.services.agentic_loop import AgenticLoopService, AgenticValidationError

LOGGER = logging.getLogger("moneygraph.auto_monitor")


class AgenticAutoMonitor:
    """Advance a day-level replay without granting the rules engine decision authority."""

    def __init__(
        self,
        service: AgenticLoopService,
        repository: MoneyGraphRepository,
        *,
        enabled: bool,
        cadence_seconds: float,
    ) -> None:
        self._service = service
        self._repository = repository
        self._enabled = enabled
        self._cadence_seconds = cadence_seconds
        self._lock = RLock()
        self._stop = asyncio.Event()
        self._state = "starting" if enabled else "disabled"
        self._last_error: str | None = None
        self._total_days = 0
        self._transactions_in_source = 0

    def tick_once(self) -> dict[str, Any] | None:
        """Process the earliest unscanned source date, including its safe proposals."""

        if not self._enabled:
            return None
        with self._lock:
            available_days, transaction_count = self._service.available_days()
            self._total_days = len(available_days)
            self._transactions_in_source = transaction_count
            scans = self._repository.list_auto_monitoring_scans()
            for scan in scans:
                for alert in scan["alerts"]:
                    if len(alert["actions"]) != 3:
                        self._service.propose_actions(str(alert["id"]))

            completed = {date.fromisoformat(str(scan["replay_date"])) for scan in scans}
            next_day = next((day for day in available_days if day not in completed), None)
            if next_day is None:
                self._state = "caught_up"
                self._last_error = None
                return None

            self._state = "scanning"
            try:
                scan = self._service.run_scan(next_day, actor="auto-monitor")
            except IntegrityError:
                # A different API worker committed the durable day claim first.
                winner = next(
                    (
                        item
                        for item in self._repository.list_auto_monitoring_scans()
                        if item["replay_date"] == next_day.isoformat()
                    ),
                    None,
                )
                if winner is None:
                    raise
                self._state = "running"
                self._last_error = None
                return winner
            for alert in scan["alerts"]:
                self._service.propose_actions(str(alert["id"]))
            result = self._repository.get_monitoring_scan(str(scan["id"]))
            self._state = "caught_up" if len(completed) + 1 == len(available_days) else "running"
            self._last_error = None
            return result

    def status(self) -> dict[str, Any]:
        with self._lock:
            scans = self._repository.list_auto_monitoring_scans()
            latest_scan = scans[-1] if scans else None
            recent_alerts = [
                {**alert, "replay_date": scan["replay_date"]}
                for scan in reversed(scans)
                for alert in sorted(
                    scan["alerts"],
                    key=lambda item: (-float(item["priority_score"]), str(item["gid"])),
                )
            ][:30]
            queued_alerts = sorted(
                (
                    {**alert, "replay_date": scan["replay_date"]}
                    for scan in scans
                    for alert in scan["alerts"]
                    if any(action["status"] == "proposed" for action in alert["actions"])
                    and not any(action["status"] == "executed" for action in alert["actions"])
                ),
                key=lambda item: (
                    float(item["priority_score"]),
                    str(item["replay_date"]),
                    str(item["created_at"]),
                ),
                reverse=True,
            )
            return {
                "enabled": self._enabled,
                "state": self._state,
                "cadence_seconds": self._cadence_seconds,
                "processed_days": len({scan["replay_date"] for scan in scans}),
                "total_days": self._total_days,
                "latest_scan": latest_scan,
                "recent_alerts": recent_alerts,
                "priority_queue": queued_alerts[:30],
                "queue_size": len(queued_alerts),
                "transactions_in_source": self._transactions_in_source,
                "last_error": self._last_error,
                "simulation": True,
                "source_time_granularity": "day",
            }

    async def run(self) -> None:
        if not self._enabled:
            return
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.tick_once)
            except Exception as exc:
                LOGGER.exception("Automatic day-level replay failed")
                with self._lock:
                    message = (
                        str(exc)
                        if isinstance(exc, AgenticValidationError)
                        else "Monitoring scan failed; see server logs"
                    )
                    if message != self._last_error:
                        try:
                            self._repository.record_monitoring_failure(message)
                        except Exception:
                            LOGGER.exception("Unable to journal automatic scan failure")
                    self._state = "error"
                    self._last_error = message
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._cadence_seconds)

    def request_stop(self) -> None:
        self._stop.set()
