# SPDX-License-Identifier: MIT
"""Source-text tracking for evidence_record, claim_record and rob_record.

Stores length and SHA-256 hashes (raw and normalized) of agent-supplied source texts.
Detects when the same record (identified by tool and record_key) is re-recorded
with a different source-text hash, flagging whitespace/formatting-only differences.

Tables owned here: ``source_observations``, ``source_changes``.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from citeproof_mcp import store
from citeproof_mcp.tools.quotes import normalize_text

store.register_schema(
    (
        """CREATE TABLE IF NOT EXISTS source_observations (
            paper_ref TEXT NOT NULL,
            tool TEXT NOT NULL,
            record_key TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            length INTEGER NOT NULL,
            normalized_sha256 TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            PRIMARY KEY (paper_ref, tool, record_key, sha256)
        )""",
        """CREATE TABLE IF NOT EXISTS source_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_ref TEXT NOT NULL,
            tool TEXT NOT NULL,
            record_key TEXT NOT NULL,
            previous_sha256 TEXT NOT NULL,
            current_sha256 TEXT NOT NULL,
            previous_length INTEGER NOT NULL,
            current_length INTEGER NOT NULL,
            formatting_only INTEGER NOT NULL,
            changed_at TEXT NOT NULL
        )""",
    )
)


def record_observation(
    conn: sqlite3.Connection,
    paper_ref: str,
    tool: str,
    record_key: str,
    source_text: str,
) -> dict[str, Any] | None:
    """Record a source-text observation for a tool record inside the caller's write transaction.

    Returns a change details dict if the same record previously had a different raw SHA-256,
    or None if this is the first observation or if the hash is unchanged from the last observation.
    """
    if not source_text:
        return None

    raw_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    raw_length = len(source_text)
    norm_text = normalize_text(source_text)
    norm_sha256 = hashlib.sha256(norm_text.encode("utf-8")).hexdigest()
    now = store.now_iso()

    # Find the most recent observation for this record
    latest_change = conn.execute(
        """SELECT current_sha256, current_length FROM source_changes
           WHERE paper_ref = ? AND tool = ? AND record_key = ?
           ORDER BY id DESC LIMIT 1""",
        (paper_ref, tool, record_key),
    ).fetchone()

    if latest_change:
        prev_sha256 = latest_change["current_sha256"]
        prev_length = latest_change["current_length"]
    else:
        prev_obs = conn.execute(
            """SELECT sha256, length FROM source_observations
               WHERE paper_ref = ? AND tool = ? AND record_key = ?""",
            (paper_ref, tool, record_key),
        ).fetchone()
        if prev_obs:
            prev_sha256 = prev_obs["sha256"]
            prev_length = prev_obs["length"]
        else:
            prev_sha256 = None
            prev_length = None

    change_details: dict[str, Any] | None = None

    if prev_sha256 is not None and prev_sha256 != raw_sha256:
        # Get the normalized sha256 of the previous observation
        prev_norm_row = conn.execute(
            """SELECT normalized_sha256 FROM source_observations
               WHERE paper_ref = ? AND tool = ? AND record_key = ? AND sha256 = ?""",
            (paper_ref, tool, record_key, prev_sha256),
        ).fetchone()

        prev_norm_sha256 = prev_norm_row["normalized_sha256"] if prev_norm_row else None
        formatting_only = prev_norm_sha256 == norm_sha256

        conn.execute(
            """INSERT INTO source_changes
               (paper_ref, tool, record_key, previous_sha256, current_sha256,
                previous_length, current_length, formatting_only, changed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                paper_ref,
                tool,
                record_key,
                prev_sha256,
                raw_sha256,
                prev_length,
                raw_length,
                int(formatting_only),
                now,
            ),
        )

        change_details = {
            "previous_sha256": prev_sha256,
            "current_sha256": raw_sha256,
            "previous_length": prev_length,
            "current_length": raw_length,
            "formatting_only": formatting_only,
        }

    # Record the observation if not already recorded
    conn.execute(
        """INSERT OR IGNORE INTO source_observations
           (paper_ref, tool, record_key, sha256, length, normalized_sha256, first_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (paper_ref, tool, record_key, raw_sha256, raw_length, norm_sha256, now),
    )

    return change_details


def source_changes_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all source_changes rows ordered by id."""
    rows = conn.execute(
        """SELECT id, paper_ref, tool, record_key, previous_sha256, current_sha256,
                  previous_length, current_length, formatting_only, changed_at
           FROM source_changes ORDER BY id"""
    ).fetchall()
    return [dict(r) | {"formatting_only": bool(r["formatting_only"])} for r in rows]


def distinct_source_texts_by_paper(conn: sqlite3.Connection) -> dict[str, int]:
    """Return map of paper_ref -> distinct source text count for papers with > 1 distinct source texts."""
    rows = conn.execute(
        """SELECT paper_ref, COUNT(DISTINCT sha256) as distinct_count
           FROM source_observations
           GROUP BY paper_ref
           HAVING distinct_count > 1
           ORDER BY paper_ref"""
    ).fetchall()
    return {r["paper_ref"]: r["distinct_count"] for r in rows}
