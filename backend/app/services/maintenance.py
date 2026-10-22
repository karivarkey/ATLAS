"""Background maintenance: recurring-task occurrence generation + reminder
evaluation. Runs every 60s inside the server process. Idempotent — safe
across restarts (INSERT OR IGNOREoccurrences, per-task-per-day reminder dedupe)."""
from __future__ import annotations

import asyncio
import logging

from .. import db

log = logging.getLogger("atlas.maintenance")


async def loop() -> None:
    from . import tasks as _tasks

    while True:
        try:
            for r in db.query("SELECT id FROM tasks WHERE recurrence IS NOT NULL"
                              " AND recurrence != '' AND status NOT IN ('archived','cancelled')"):
                try:
                    _tasks.generate_occurrences(r["id"])
                except Exception as exc:
                    log.info("occurrences skip %s: %s", r["id"], exc)
            try:
                made = _tasks.evaluate_reminders()
                if made:
                    log.info("reminders created: %d", len(made))
            except Exception as exc:
                log.info("reminder eval skip: %s", exc)
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("maintenance error: %s", exc)
            await asyncio.sleep(60)
