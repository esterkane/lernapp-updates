"""lernapp MCP server (ADR-0014).

Thin, typed client over the FastAPI backend. No business logic here — every tool maps to one
HTTP endpoint (docs/api.md) so behaviour is identical to the UI. Used by Claude Code during
implementation and later by Claude Desktop for the learner ("frag den Tutor nach meinen Dokumenten").

Every tool returns the ``ToolResult`` envelope (learnings-udacity §3): ``isError``, ``data`` or
``errorCategory`` (transient | validation | business | permission) + ``isRetryable`` + ``message``.
Only ``transient`` is retryable.

Run: `uv run python -m lernapp_mcp.server` (stdio). Requires the API at LERNAPP_API_URL; sends
``Authorization: Bearer $LERNAPP_API_TOKEN`` when set and ``X-Lernapp-Caller: mcp`` always.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
import yaml
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

API = os.environ.get("LERNAPP_API_URL", "http://localhost:8000")
TOKEN = os.environ.get("LERNAPP_API_TOKEN", "")
CALLER = "mcp"
DEFAULT_LEARNER = "default"  # docs/api.md: learner_id defaults to "default" everywhere


class ErrorCategory(StrEnum):
    """Categories an agent can act on (cd15828 pattern). Only TRANSIENT is retryable."""

    TRANSIENT = "transient"
    VALIDATION = "validation"
    BUSINESS = "business"
    PERMISSION = "permission"


class ToolResult(BaseModel):
    """Uniform envelope returned by every tool. Wire format uses MCP camelCase."""

    model_config = ConfigDict(populate_by_name=True)
    is_error: bool = Field(serialization_alias="isError")
    data: Any = None
    error_category: ErrorCategory | None = Field(default=None, serialization_alias="errorCategory")
    is_retryable: bool | None = Field(default=None, serialization_alias="isRetryable")
    message: str | None = None

    @classmethod
    def ok(cls, data: Any) -> dict[str, Any]:
        return cls(is_error=False, data=data).model_dump(by_alias=True, exclude_none=True)

    @classmethod
    def fail(cls, category: ErrorCategory, message: str) -> dict[str, Any]:
        return cls(
            is_error=True,
            error_category=category,
            is_retryable=category is ErrorCategory.TRANSIENT,
            message=message,
        ).model_dump(by_alias=True, exclude_none=True)


@dataclass(frozen=True)
class ToolContract:
    """Purpose / inputs / outputs / explicit boundary / example / edge case → tool description."""

    purpose: str
    inputs: str
    outputs: str
    boundaries: str
    example: str
    edge_case: str

    def render(self) -> str:
        return (
            f"Purpose: {self.purpose}\nInputs: {self.inputs}\nOutputs: {self.outputs}\n"
            f"Do NOT use for: {self.boundaries}\nExample query: {self.example}\nEdge case: {self.edge_case}"
        )


CONTRACTS: dict[str, ToolContract] = {
    "list_blueprints": ToolContract(
        purpose="List the hand-maintained exam blueprints (task types, timings, item counts) with verification status.",
        inputs="none.",
        outputs="List of {id, title, skill, version, is_verified, task_types, …} straight from blueprints/*.yaml.",
        boundaries="creating or editing blueprints — they are never generated (product rule 2); edit the YAML by hand.",
        example="Welche Blueprints gibt es für Schreiben?",
        edge_case="is_verified=false means verified_on is unset — content from such a blueprint is practice only.",
    ),
    "generate_task": ToolContract(
        purpose="Generate ONE task for a blueprint slot: generator → independent validator (other vendor) → consistency check.",
        inputs="blueprint_id, task_type (must exist in that blueprint), level (default C1), optional topic, learner_id.",
        outputs="{task_id, status ok|rejected, task (answers withheld), validation{ok, issues, agreement}, models, cost_eur}.",
        boundaries="inventing exam parameters, scoring answers (use the app's /tasks/{id}/score), or free chat.",
        example="Erzeuge eine Leseaufgabe (task_type 'lesen_1') für testdaf_digital_lesen zum Thema Energie.",
        edge_case="status='rejected' returns the validator issues instead of a task — regenerate with another topic.",
    ),
    "assess_writing": ToolContract(
        purpose="Rubric-based feedback (internal 0–4 rubric, 7 criteria) on a learner's written text.",
        inputs="learner_text; either task_id (persisted task) or task_text (ad-hoc prompt); learner_id; is_progress_point.",
        outputs="{result_id, rubric: RubricResult (scores, evidence, error_tags, better_formulations), total/28, "
        "internal_score_20, needs_review, label 'interne Übungsbewertung', cost_eur}.",
        boundaries="spoken transcripts (use assess_speaking_transcript) or producing an official TestDaF/TDN grade — it never does.",
        example="Bewerte diesen Text für Aufgabe t_123. / Bewerte diesen Text zur Aufgabenstellung 'Homeoffice: pro oder contra?'",
        edge_case="is_progress_point=true triggers the independent validator; a criterion diff ≥ 2 sets needs_review=true.",
    ),
    "assess_speaking_transcript": ToolContract(
        purpose="Rubric feedback on a speaking transcript plus optional prosody report.",
        inputs="transcript; either task_id or task_text; learner_id; optional prosody (ProsodyReport dict); is_progress_point.",
        outputs="Same as assess_writing plus pronunciation_tip; pronunciation feedback is qualitative flags only.",
        boundaries="raw audio (the app transcribes via /assess/speaking/audio) or written texts.",
        example="Bewerte mein Transkript der Sprechaufgabe s_7.",
        edge_case="Without prosody, fluessigkeit_verstaendlichkeit is scored from text only and flagged as such.",
    ),
    "start_roleplay": ToolContract(
        purpose="Start a negotiation roleplay session with a scenario's counterpart (Verhandlungsdeutsch).",
        inputs="scenario_id (from GET /roleplay/scenarios), learner_id, voice (TTS for replies).",
        outputs="{session_id, scenario (public part), opening_line, turn 0, max_turns}.",
        boundaries="grammar correction during the roleplay — coaching starts only after 'ROLLENSPIEL ENDE' (product rule 7).",
        example="Starte das Rollenspiel 'gehaltsverhandlung_1' für mich.",
        edge_case="An unknown scenario_id returns a business error; hidden_targets are never included while running.",
    ),
    "roleplay_turn": ToolContract(
        purpose="Send one learner turn into a running roleplay and get the counterpart's reply.",
        inputs="session_id, text. Say 'ROLLENSPIEL ENDE' to end early.",
        outputs="{learner_text, reply_text, turn, max_turns, ended, coach: CoachReport|null, hidden_targets when ended, cost_eur_turn}.",
        boundaries="sessions that already ended (start a new one) or tutor sessions (use the app's /sessions).",
        example="Ich schlage 68.000 € Jahresgehalt vor.",
        edge_case="On the last allowed turn (turn == max_turns) the roleplay ends automatically and the coach report is attached.",
    ),
    "ingest_document": ToolContract(
        purpose="Upload ONE local file (pdf/docx/txt/md/csv) into the learner's own document store for RAG/vocab.",
        inputs="path (local file), learner_id, optional tags list.",
        outputs="{document_id, title, status, n_chunks, n_vocab_items, cost_eur}.",
        boundaries="official TestDaF/Goethe material (never stored — product rule 10) or files of another learner.",
        example="Lade meine Notizen ~/Downloads/redemittel.md als Dokument mit Tag 'verhandlung' hoch.",
        edge_case="CSV with columns wort,bedeutung[,beispiel] becomes vocab items (no embedding, n_chunks=0).",
    ),
    "search_documents": ToolContract(
        purpose="Hybrid lexical+vector search over ONE learner's uploaded documents.",
        inputs="query, learner_id, optional tags list, k (default 8).",
        outputs="List of chunks {chunk_id, document_id, document_title, ord, heading, text, score} (RRF-fused).",
        boundaries="web search, official TestDaF material, or documents of another learner (owner scope is enforced).",
        example="Finde in meinen Dokumenten Redemittel für Gehaltsverhandlungen.",
        edge_case="An empty list is a valid result (no matching chunks), not an error.",
    ),
    "cost_summary": ToolContract(
        purpose="Exact cost overview from the ledger (actual consumption, priced from config/pricing.yaml).",
        inputs="from_date, to_date (ISO, optional; default last 30 days), group_by stage|model|learner|day|session|provider.",
        outputs="{groups:[{key, rows, quantity, cost_usd, cost_eur, unpriced_rows}], total_eur, total_usd, learning_seconds, "
        "eur_per_learning_minute, pricing_version, fx_rate}.",
        boundaries="estimates or projections (use cost_counterfactual for what-if variants).",
        example="Was hat die App im August gekostet, nach Stage?",
        edge_case="unpriced_rows > 0 means a model id is missing from pricing.yaml — fix before trusting totals.",
    ),
    "cost_counterfactual": ToolContract(
        purpose="What the SAME recorded usage would have cost if local stages had run in the cloud.",
        inputs="variant all_cloud_openai|cheapest_cloud, from_date, to_date (ISO, optional).",
        outputs="{actual_eur, counterfactual_eur, saving_eur, by_stage{stage:{actual_usd, counterfactual_usd}}, mapping}.",
        boundaries="actual spend reporting (use cost_summary) or pricing changes — prices come only from pricing.yaml.",
        example="Was hätte der letzte Monat all-cloud bei OpenAI gekostet?",
        edge_case="With no local rows in range counterfactual_eur == actual_eur and saving_eur == 0.",
    ),
    "run_evals": ToolContract(
        purpose="Run the eval harness (golden sets + evaluators with partial credit) and return the metric table.",
        inputs="suite all|writing|rag|tasks.",
        outputs="Report dict {run_id, git_sha, llm_backend, models, suites{name:{n, metrics…}}, cost_eur}; also written to evals/reports/.",
        boundaries="grading a learner (use assess_*) — evals run against golden sets only, and cost real money with a live backend.",
        example="Führe die Writing-Evals aus.",
        edge_case="Regression rule: a new prompt version must not worsen MAE by > 0.15 on any criterion without a note.",
    ),
    "learner_progress": ToolContract(
        purpose="Progress points of one learner grouped by rubric_version/prompt_version, with version breaks.",
        inputs="learner_id (default 'default'), optional skill filter (schreiben|sprechen|lesen|hoeren).",
        outputs="{points:[{result_id, created_at, skill, score, score_max, internal_score_20, rubric_version, needs_review, …}], "
        "weekly_focus, top_error_tags, activity_status, version_breaks}.",
        boundaries="any TDN/official-grade mapping (never exists) or comparing learners (single-learner scope).",
        example="Wie hat sich mein Schreiben in den letzten Wochen entwickelt?",
        edge_case="Points across a version break are not comparable — the response lists the break timestamps.",
    ),
}

ROOT = Path(__file__).resolve().parents[2]  # repo root when run from ./mcp

mcp = FastMCP("lernapp", instructions="Tools for the TestDaF/Verhandlungsdeutsch learning app.")


def _client() -> httpx.Client:
    return httpx.Client(base_url=API, timeout=120)


def _categorize(code: int) -> ErrorCategory:
    """HTTP status → error category the agent can act on (only transient is worth a retry)."""
    if code in (401, 403):
        return ErrorCategory.PERMISSION
    if code == 422:
        return ErrorCategory.VALIDATION
    if code in (400, 404, 409, 410):
        return ErrorCategory.BUSINESS
    return ErrorCategory.TRANSIENT  # 408, 429, 5xx …


def _wrap(fn: Any) -> dict[str, Any]:
    """Map HTTP outcomes onto the ToolResult envelope so the agent can decide retry/stop/escalate."""
    try:
        return ToolResult.ok(fn())
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        return ToolResult.fail(_categorize(code), f"HTTP {code}: {e.response.text[:300]}")
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        return ToolResult.fail(ErrorCategory.TRANSIENT, f"{type(e).__name__}: API unreachable at {API}")
    except OSError as e:  # e.g. local file for ingest_document missing
        return ToolResult.fail(ErrorCategory.VALIDATION, f"{type(e).__name__}: {e}")


def _headers() -> dict[str, str]:
    from urllib.parse import urlparse

    from platformdirs import user_data_dir

    token = TOKEN
    endpoint = urlparse(API)
    if not token and endpoint.scheme == "http" and endpoint.hostname in {"127.0.0.1", "localhost"}:
        directory = Path(os.environ.get("LERNAPP_DATA_DIR") or user_data_dir("Lernapp", appauthor=False))
        try:
            state = json.loads((directory / "run" / "state.json").read_text(encoding="utf-8"))
            if state.get("api_port") == endpoint.port:
                token = (directory / "run" / "api-token").read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            pass
    headers = {"X-Lernapp-Caller": "mcp"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get(path: str, **params: Any) -> dict[str, Any]:
    def go() -> Any:
        with _client() as c:
            r = c.get(path, params={k: v for k, v in params.items() if v is not None}, headers=_headers())
            r.raise_for_status()
            return r.json()

    return _wrap(go)


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    def go() -> Any:
        with _client() as c:
            r = c.post(path, json={k: v for k, v in body.items() if v is not None}, headers=_headers())
            r.raise_for_status()
            return r.json()

    return _wrap(go)


# ---------- resources (static config; no API needed) ----------
@mcp.resource("lernapp://pricing")
def pricing() -> str:
    """The versioned price table used by the cost ledger."""
    return (ROOT / "config" / "pricing.yaml").read_text()


@mcp.resource("lernapp://blueprints/{blueprint_id}")
def blueprint(blueprint_id: str) -> str:
    """A static exam blueprint (never LLM-generated)."""
    for p in (ROOT / "blueprints").rglob("*.yaml"):
        if yaml.safe_load(p.read_text()).get("id") == blueprint_id:
            return p.read_text()
    raise ValueError(f"unknown blueprint {blueprint_id}")


@mcp.resource("lernapp://prompts/{name}")
def prompt(name: str) -> str:
    """Highest version of a prompt file."""
    files = sorted((ROOT / "prompts").glob(f"{name}.v*.md"))
    if not files:
        raise ValueError(f"unknown prompt {name}")
    return files[-1].read_text()


# ---------- tools (Phase 0/1) ----------
@mcp.tool(description=CONTRACTS["list_blueprints"].render())
def list_blueprints() -> dict[str, Any]:
    """List loaded exam blueprints with verification status (GET /blueprints)."""
    return _get("/blueprints")


@mcp.tool(description=CONTRACTS["generate_task"].render())
def generate_task(
    blueprint_id: str,
    task_type: str,
    level: str = "C1",
    topic: str | None = None,
    learner_id: str = DEFAULT_LEARNER,
) -> dict[str, Any]:
    """Generate a task via generator → validator → consistency check (POST /tasks/generate)."""
    return _post(
        "/tasks/generate",
        {
            "blueprint_id": blueprint_id,
            "task_type": task_type,
            "level": level,
            "topic": topic,
            "learner_id": learner_id,
        },
    )


@mcp.tool(description=CONTRACTS["assess_writing"].render())
def assess_writing(
    learner_text: str,
    task_id: str | None = None,
    task_text: str | None = None,
    learner_id: str = DEFAULT_LEARNER,
    is_progress_point: bool = False,
) -> dict[str, Any]:
    """Rubric-based writing feedback (internal rubric, no TDN) for a stored task or an ad-hoc task_text (POST /assess/writing)."""
    return _post(
        "/assess/writing",
        {
            "task_id": task_id,
            "task_text": task_text,
            "learner_text": learner_text,
            "learner_id": learner_id,
            "is_progress_point": is_progress_point,
        },
    )


@mcp.tool(description=CONTRACTS["assess_speaking_transcript"].render())
def assess_speaking_transcript(
    transcript: str,
    task_id: str | None = None,
    task_text: str | None = None,
    learner_id: str = DEFAULT_LEARNER,
    prosody: dict[str, Any] | None = None,
    is_progress_point: bool = False,
) -> dict[str, Any]:
    """Rubric-based speaking feedback from a transcript (+ optional prosody report) (POST /assess/speaking)."""
    return _post(
        "/assess/speaking",
        {
            "task_id": task_id,
            "task_text": task_text,
            "transcript": transcript,
            "learner_id": learner_id,
            "prosody": prosody,
            "is_progress_point": is_progress_point,
        },
    )


@mcp.tool(description=CONTRACTS["cost_summary"].render())
def cost_summary(
    from_date: str | None = None,
    to_date: str | None = None,
    group_by: str = "stage",
    learner_id: str | None = None,
) -> dict[str, Any]:
    """Exact cost overview from the ledger (GET /costs/summary). group_by: stage|model|learner|day|session|provider."""
    return _get("/costs/summary", **{"from": from_date, "to": to_date, "group_by": group_by, "learner_id": learner_id})


@mcp.tool(description=CONTRACTS["cost_counterfactual"].render())
def cost_counterfactual(
    variant: str = "all_cloud_openai", from_date: str | None = None, to_date: str | None = None
) -> dict[str, Any]:
    """What the same usage would have cost in a cloud variant (GET /costs/counterfactual)."""
    return _get("/costs/counterfactual", **{"variant": variant, "from": from_date, "to": to_date})


# ---------- tools (Phase 2) ----------
@mcp.tool(description=CONTRACTS["start_roleplay"].render())
def start_roleplay(scenario_id: str, learner_id: str = DEFAULT_LEARNER, voice: bool = False) -> dict[str, Any]:
    """Start a negotiation roleplay session (POST /roleplay/start)."""
    return _post("/roleplay/start", {"scenario_id": scenario_id, "learner_id": learner_id, "voice": voice})


@mcp.tool(description=CONTRACTS["roleplay_turn"].render())
def roleplay_turn(session_id: str, text: str) -> dict[str, Any]:
    """Send a learner turn (POST /roleplay/{session_id}/turn). 'ROLLENSPIEL ENDE' ends it and returns the coach report."""
    return _post(f"/roleplay/{session_id}/turn", {"text": text})


@mcp.tool(description=CONTRACTS["ingest_document"].render())
def ingest_document(path: str, learner_id: str = DEFAULT_LEARNER, tags: list[str] | None = None) -> dict[str, Any]:
    """Upload a local file (pdf/docx/txt/md/csv) into the learner's document store (POST /documents, multipart)."""

    def go() -> Any:
        with _client() as c, open(path, "rb") as f:
            data: dict[str, str] = {"learner_id": learner_id}
            if tags:
                data["tags"] = ",".join(tags)
            r = c.post("/documents", data=data, files={"file": (Path(path).name, f)}, headers=_headers())
            r.raise_for_status()
            return r.json()

    return _wrap(go)


@mcp.tool(description=CONTRACTS["search_documents"].render())
def search_documents(
    query: str, learner_id: str = DEFAULT_LEARNER, tags: list[str] | None = None, k: int = 8
) -> dict[str, Any]:
    """Hybrid (lexical + vector, RRF) search over the learner's documents (GET /documents/search?q&learner_id&tags&k)."""
    return _get("/documents/search", q=query, learner_id=learner_id, tags=",".join(tags) if tags else None, k=k)


@mcp.tool(description=CONTRACTS["learner_progress"].render())
def learner_progress(learner_id: str = DEFAULT_LEARNER, skill: str | None = None) -> dict[str, Any]:
    """Progress points grouped by rubric_version/prompt_version (GET /learners/{id}/progress)."""
    return _get(f"/learners/{learner_id}/progress", skill=skill)


@mcp.tool(description=CONTRACTS["run_evals"].render())
def run_evals(suite: str = "all") -> dict[str, Any]:
    """Run the eval harness and return the metric table (POST /evals/run {suite})."""
    return _post("/evals/run", {"suite": suite})


if __name__ == "__main__":
    mcp.run()
