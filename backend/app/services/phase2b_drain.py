from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Phase2BDrainCounts:
    jobs: int
    renditions: int
    assets: int

    @property
    def drained(self) -> bool:
        return self.jobs == 0 and self.renditions == 0 and self.assets == 0


def phase2b_drain_counts(conn: sqlite3.Connection) -> Phase2BDrainCounts:
    jobs = conn.execute(
        """
        SELECT COUNT(*) FROM jobs
        WHERE job_type IN ('preview', 'lut_preview', 'rendition')
          AND status IN ('queued', 'running')
        """
    ).fetchone()[0]
    renditions = conn.execute(
        """
        SELECT COUNT(*) FROM renditions
        WHERE state IN ('queued', 'validating', 'rendering', 'finalizing')
        """
    ).fetchone()[0]
    assets = conn.execute(
        "SELECT COUNT(*) FROM assets WHERE preview_status = 'preview_generating'"
    ).fetchone()[0]
    return Phase2BDrainCounts(
        jobs=int(jobs),
        renditions=int(renditions),
        assets=int(assets),
    )
