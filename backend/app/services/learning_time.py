"""Learning time (cost-tracking model, ADR-0019): what ``Session.duration_seconds`` counts.

Only *genuine* learning time enters the cost-per-learning-hour rate. Per turn the session gains

- the learner's **audio input seconds** (they were speaking),
- the **processing time** between request start and response end, capped at
  ``MAX_PROCESSING_SECONDS`` (the learner is waiting for / listening to the reply; a hung provider
  must not inflate the rate),
- the **gap since the previous turn**, but only when it is at most ``MAX_GAP_SECONDS`` (reading the
  last reply, thinking, typing). A longer gap means the learner walked away — idle time is excluded
  entirely, not capped.

Ending a session counts the final gap under the same rule. The helper is shared by the tutor and the
roleplay hub so both session kinds report comparable learning time.
"""

from __future__ import annotations

from datetime import datetime

MAX_GAP_SECONDS = 300.0  # gaps up to 5 minutes are reading/thinking time; longer gaps are idle → 0
MAX_PROCESSING_SECONDS = 120.0  # request → response time counts at most 2 minutes per turn


def gap_seconds(last_turn_at: datetime | None, started_at: datetime) -> float:
    """Seconds between the previous turn and this request when ≤ ``MAX_GAP_SECONDS``, else 0."""
    if last_turn_at is None:
        return 0.0
    gap = (started_at - last_turn_at).total_seconds()
    return gap if 0.0 <= gap <= MAX_GAP_SECONDS else 0.0


def processing_seconds(started_at: datetime, ended_at: datetime) -> float:
    return min(max((ended_at - started_at).total_seconds(), 0.0), MAX_PROCESSING_SECONDS)


def learning_increment(
    last_turn_at: datetime | None,
    started_at: datetime,
    ended_at: datetime,
    audio_seconds: float = 0.0,
) -> float:
    """Learning seconds one turn adds: audio input + capped processing + short gap since the previous turn."""
    # Recording precedes upload and overlaps the inter-turn gap. Count its union,
    # rather than adding the same speaking interval twice.
    return max(gap_seconds(last_turn_at, started_at), max(audio_seconds, 0.0)) + processing_seconds(
        started_at, ended_at
    )


def parse_last_turn(state: dict[str, object] | None) -> datetime | None:
    raw = (state or {}).get("last_turn_at")
    return datetime.fromisoformat(str(raw)) if raw else None
