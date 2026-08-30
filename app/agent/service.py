"""The agent: Claude orchestrating deterministic tools.

Flow, end to end::

    user question
        -> Claude (chooses tools)
        -> app.agent.tools -> app.analytics (real pandas maths)
        -> structured JSON back to Claude
        -> Claude writes the explanation

A manual tool-use loop is used rather than the SDK's beta ``tool_runner``
because this app needs every tool call and raw tool result surfaced back to the
UI (that transparency is the product), and because a manual loop keeps the
service off a beta dependency.

If no API key is configured, or the Anthropic API errors, the request falls
through to the deterministic renderer in :mod:`app.agent.fallback` so the app
still answers correctly — clearly flagged as a non-AI answer.
"""

from __future__ import annotations

import functools
import json
from typing import Any

import anthropic

from app.agent import fallback
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import anthropic_tool_definitions, run_tool
from app.analytics.ranking import dataset_overview
from app.config import Settings, get_settings
from app.data.dataset import AirportDataset
from app.data.repository import DataRepository, get_repository
from app.logging_config import get_logger

log = get_logger(__name__)

#: Enables server-side refusal fallbacks so a declined request is re-routed
#: rather than returned empty. Dropped automatically if the API rejects it.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

MAX_HISTORY_MESSAGES = 20


class AgentService:
    """Answers analyst questions with Claude on top of deterministic tools."""

    def __init__(
        self,
        settings: Settings | None = None,
        repository: DataRepository | None = None,
        client: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.repository = repository or get_repository()
        self._client = client
        self._client_ready = client is not None
        self._use_fallback_beta = True

    # -- client ------------------------------------------------------------
    @property
    def client(self) -> Any | None:
        """Lazily build the Anthropic client; ``None`` when unconfigured."""
        if self._client_ready:
            return self._client
        self._client_ready = True
        if not self.settings.has_anthropic_key:
            log.warning("ANTHROPIC_API_KEY is not set; chat will use the "
                        "deterministic fallback renderer")
            self._client = None
            return None
        try:
            self._client = anthropic.Anthropic(
                api_key=self.settings.anthropic_api_key,
                timeout=self.settings.anthropic_timeout_seconds,
                max_retries=2,
            )
        except Exception as exc:  # noqa: BLE001 - never fail app start on this
            log.exception("could not construct the Anthropic client: %s", exc)
            self._client = None
        return self._client

    @property
    def llm_available(self) -> bool:
        return self.client is not None

    # -- public API --------------------------------------------------------
    def answer(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Answer one question, with conversation history for follow-ups."""
        question = (question or "").strip()
        if not question:
            raise ValueError("Question must not be empty.")

        dataset = self.repository.get_dataset()
        threshold = self.settings.long_haul_miles
        provenance = dataset.provenance.to_dict()

        if not self.llm_available:
            result = fallback.answer(question, dataset, threshold, history)
            return self._envelope(result, provenance, degraded=True,
                                  reason="No ANTHROPIC_API_KEY configured; "
                                         "answered directly from the analytics engine.")
        try:
            result = self._answer_with_claude(question, history or [], dataset, threshold)
            return self._envelope(result, provenance, degraded=False)
        except anthropic.APIError as exc:
            log.warning("Anthropic API error (%s); falling back", exc)
            reason = f"Anthropic API unavailable ({type(exc).__name__}); " \
                     "answered directly from the analytics engine."
        except Exception as exc:  # noqa: BLE001 - chat must always answer
            log.exception("unexpected agent failure: %s", exc)
            reason = "The AI narration step failed; answered directly from the " \
                     "analytics engine."
        result = fallback.answer(question, dataset, threshold, history)
        return self._envelope(result, provenance, degraded=True, reason=reason)

    # -- Claude loop -------------------------------------------------------
    def _answer_with_claude(
        self,
        question: str,
        history: list[dict[str, str]],
        dataset: AirportDataset,
        threshold: float,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = self._history_messages(history)
        messages.append({"role": "user", "content": question})

        tools = anthropic_tool_definitions()
        system = SYSTEM_PROMPT + "\n\n" + self._dataset_context(dataset, threshold)
        tool_calls: list[dict[str, Any]] = []
        response = None

        for round_index in range(self.settings.agent_max_tool_rounds):
            response = self._create_message(system, messages, tools)

            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                log.warning("model declined the request: %s", detail)
                raise RuntimeError("The model declined to answer this request.")

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                break

            messages.append({"role": "assistant", "content": response.content})

            # All results for one assistant turn go back in a single user
            # message, otherwise the model stops issuing parallel tool calls.
            results: list[dict[str, Any]] = []
            for block in tool_uses:
                output = run_tool(block.name, dict(block.input or {}), dataset, threshold)
                tool_calls.append({"tool": block.name,
                                   "input": dict(block.input or {}),
                                   "output": output})
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output, default=str),
                    "is_error": "error" in output,
                })
            messages.append({"role": "user", "content": results})
        else:
            log.warning("hit the %d-round tool limit; answering with what we have",
                        self.settings.agent_max_tool_rounds)

        text = "\n\n".join(
            block.text for block in (response.content if response else [])
            if block.type == "text"
        ).strip()
        if not text:
            text = ("The analysis ran but produced no narrative. The tool results "
                    "below hold the underlying figures.")
        return {"answer": text, "tool_calls": tool_calls, "used_llm": True}

    def _create_message(self, system: str, messages: list[dict[str, Any]],
                        tools: list[dict[str, Any]]) -> Any:
        """One Messages API call, with server-side refusal fallbacks when allowed."""
        kwargs: dict[str, Any] = {
            "model": self.settings.anthropic_model,
            "max_tokens": self.settings.anthropic_max_tokens,
            "system": system,
            "tools": tools,
            "messages": messages,
        }
        if self._use_fallback_beta:
            try:
                return self.client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **kwargs
                )
            except (anthropic.BadRequestError, TypeError) as exc:
                # The beta is not available for this account/SDK: stop asking.
                log.info("server-side fallbacks unavailable (%s); using the "
                         "standard Messages endpoint", exc)
                self._use_fallback_beta = False
        return self.client.messages.create(**kwargs)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _history_messages(history: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Normalize prior turns into Messages API shape.

        Only plain text is replayed: the tool_use/tool_result blocks from
        earlier turns are dropped, which keeps the request small while still
        letting Claude resolve references like "which one is better?".
        """
        cleaned: list[dict[str, Any]] = []
        for message in (history or [])[-MAX_HISTORY_MESSAGES:]:
            role = message.get("role")
            content = (message.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                cleaned.append({"role": role, "content": content})
        # The API requires the first message to be from the user.
        while cleaned and cleaned[0]["role"] != "user":
            cleaned.pop(0)
        return cleaned

    def _dataset_context(self, dataset: AirportDataset, threshold: float) -> str:
        overview = dataset_overview(dataset, threshold)
        prov = overview["provenance"]
        return (
            "## Active dataset\n"
            f"- Status: {prov['status'].upper()} ({prov['source_name']})\n"
            f"- Coverage: {overview['airport_count']} US airports, "
            f"{overview['route_count']} non-stop segments, years "
            f"{overview['years'][0]}-{overview['years'][-1]}\n"
            f"- Regions available: {', '.join(overview['regions'])}\n"
            f"- Long-haul threshold in force: "
            f"{overview['long_haul_threshold_miles']:,.0f} statute miles\n"
            + ("- This is BUNDLED DEMO DATA, not live BTS output. Say so if the "
               "user might act on a number.\n" if prov["is_demo"] else "")
        )

    def _envelope(self, result: dict[str, Any], provenance: dict[str, Any],
                  degraded: bool, reason: str = "") -> dict[str, Any]:
        return {
            "answer": result["answer"],
            "tool_calls": result["tool_calls"],
            "used_llm": result["used_llm"],
            "model": self.settings.anthropic_model if result["used_llm"] else None,
            "degraded": degraded,
            "degraded_reason": reason,
            "data_status": provenance["status"],
            "data_source": provenance["source_name"],
            "data_label": provenance["label"],
        }


@functools.lru_cache(maxsize=1)
def get_agent_service() -> AgentService:
    """Process-wide agent singleton (FastAPI dependency)."""
    return AgentService()
