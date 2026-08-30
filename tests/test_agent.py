"""Agent behaviour: tool routing, follow-up context, and the Claude loop."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent import fallback
from app.agent.service import AgentService
from app.agent.tools import TOOL_REGISTRY, anthropic_tool_definitions, run_tool
from app.data.repository import DataRepository

EXAMPLES = [
    ("Which airports in New England are strong candidates for terminal expansion?",
     "rank_airports"),
    ("Compare LAX and SNA congestion levels.", "compare_congestion"),
    ("What percentage of flights from ANC are long-haul?", "get_long_haul_share"),
    ("What is the unmet flight demand at SFO and why?", "get_unmet_demand_proxy"),
]


# --- tool registry ---------------------------------------------------------
def test_tool_definitions_are_well_formed():
    definitions = anthropic_tool_definitions()
    assert {d["name"] for d in definitions} == set(TOOL_REGISTRY)
    for definition in definitions:
        assert definition["description"]
        assert definition["input_schema"]["type"] == "object"


def test_tool_errors_are_returned_not_raised(dataset):
    assert "error" in run_tool("get_airport_metrics", {"iata": "ZZZ"}, dataset, 2500.0)
    assert "error" in run_tool("nope", {}, dataset, 2500.0)
    assert "error" in run_tool("compare_airports", {"iatas": ["LAX"]}, dataset, 2500.0)


def test_tool_output_is_json_serialisable(dataset):
    for name in TOOL_REGISTRY:
        args = {}
        if "iata" in str(TOOL_REGISTRY[name].__code__.co_varnames):
            args = {"iata": "SFO"}
        if name in {"compare_airports", "compare_congestion"}:
            args = {"iatas": ["LAX", "SNA"]}
        output = run_tool(name, args, dataset, 2500.0)
        json.dumps(output, default=str)  # must not raise


# --- deterministic routing -------------------------------------------------
@pytest.mark.parametrize("question,expected_tool", EXAMPLES)
def test_example_questions_route_to_the_right_tool(dataset, question, expected_tool):
    result = fallback.answer(question, dataset, 2500.0)
    assert [c["tool"] for c in result["tool_calls"]] == [expected_tool]
    assert result["answer"]
    assert result["used_llm"] is False


def test_new_england_question_only_returns_new_england(dataset):
    result = fallback.answer(EXAMPLES[0][0], dataset, 2500.0)
    ranked = result["tool_calls"][0]["output"]["results"]
    assert {r["region"] for r in ranked} == {"New England"}


def test_long_haul_answer_states_the_threshold(dataset):
    answer = fallback.answer(EXAMPLES[2][0], dataset, 2500.0)["answer"]
    assert "2,500" in answer
    assert "%" in answer


def test_unmet_demand_answer_carries_the_proxy_caveat(dataset):
    answer = fallback.answer(EXAMPLES[3][0], dataset, 2500.0)["answer"]
    assert "PROXY" in answer or "proxy" in answer


def test_code_extraction_ignores_lowercase_words(dataset):
    """'sat' and 'boi' are real IATA codes; lowercase prose must not match them."""
    assert fallback.extract_codes("we sat and boi led the way", dataset) == []
    assert fallback.extract_codes("Compare LAX and SNA", dataset) == ["LAX", "SNA"]
    assert fallback.extract_codes("How is Boston doing?", dataset) == ["BOS"]


def test_follow_up_reuses_context_from_history(dataset):
    history = [
        {"role": "user", "content": "Compare LAX and SNA."},
        {"role": "assistant", "content": "LAX scores higher."},
    ]
    result = fallback.answer("Which one is a better expansion candidate?",
                             dataset, 2500.0, history)
    assert result["tool_calls"]
    codes = result["tool_calls"][0]["input"].get("iatas", [])
    assert set(codes) == {"LAX", "SNA"}


def test_bare_question_falls_back_to_the_overview(dataset):
    result = fallback.answer("What can you do?", dataset, 2500.0)
    assert result["tool_calls"][0]["tool"] == "get_dataset_overview"


# --- service without a key -------------------------------------------------
def test_service_degrades_without_an_api_key(settings):
    service = AgentService(settings=settings,
                           repository=DataRepository(settings=settings))
    assert service.llm_available is False
    result = service.answer("Compare LAX and SNA congestion levels.")
    assert result["degraded"] is True
    assert result["used_llm"] is False
    assert "ANTHROPIC_API_KEY" in result["degraded_reason"]
    assert result["data_status"] == "demo"


def test_empty_question_is_rejected(settings):
    service = AgentService(settings=settings,
                           repository=DataRepository(settings=settings))
    with pytest.raises(ValueError):
        service.answer("   ")


# --- Claude loop with a stubbed client --------------------------------------
class _Block(SimpleNamespace):
    pass


class StubMessages:
    """Replays a scripted sequence of Messages API responses."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


class StubClient:
    def __init__(self, script):
        self.messages = StubMessages(script)
        # Force the service onto the standard endpoint in these tests.
        self.beta = SimpleNamespace(messages=SimpleNamespace(
            create=lambda **kwargs: (_ for _ in ()).throw(TypeError("no beta"))))


def _tool_turn(name, args):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[_Block(type="tool_use", id="tu_1", name=name, input=args)],
    )


def _text_turn(text):
    return SimpleNamespace(stop_reason="end_turn",
                           content=[_Block(type="text", text=text)])


def test_claude_loop_runs_tools_and_returns_narration(settings):
    client = StubClient([
        _tool_turn("get_long_haul_share", {"iata": "ANC"}),
        _text_turn("9.4% of ANC departures are long-haul."),
    ])
    service = AgentService(settings=settings,
                           repository=DataRepository(settings=settings),
                           client=client)
    result = service.answer("What percentage of flights from ANC are long-haul?")

    assert result["used_llm"] is True
    assert result["degraded"] is False
    assert result["answer"].startswith("9.4%")
    assert [c["tool"] for c in result["tool_calls"]] == ["get_long_haul_share"]
    # The real tool output — not the model — supplied the number.
    assert result["tool_calls"][0]["output"]["long_haul_departure_share"] > 0

    # The second request must carry the tool result back to the model.
    second = client.messages.calls[1]["messages"]
    assert second[-1]["role"] == "user"
    assert second[-1]["content"][0]["type"] == "tool_result"
    payload = json.loads(second[-1]["content"][0]["content"])
    assert payload["iata"] == "ANC"


def test_history_is_replayed_for_follow_ups(settings):
    client = StubClient([_text_turn("LAX.")])
    service = AgentService(settings=settings,
                           repository=DataRepository(settings=settings),
                           client=client)
    service.answer("Which one is better?", history=[
        {"role": "assistant", "content": "dropped: history must start with a user turn"},
        {"role": "user", "content": "Compare LAX and SNA."},
        {"role": "assistant", "content": "LAX scores higher."},
    ])
    messages = client.messages.calls[0]["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Compare LAX and SNA."
    assert messages[-1]["content"] == "Which one is better?"


def test_api_failure_falls_back_to_the_analytics_engine(settings):
    import anthropic

    class Boom:
        def create(self, **kwargs):
            raise anthropic.APIConnectionError(request=None)

    client = SimpleNamespace(
        messages=Boom(),
        beta=SimpleNamespace(messages=SimpleNamespace(
            create=lambda **kwargs: (_ for _ in ()).throw(TypeError("no beta")))),
    )
    service = AgentService(settings=settings,
                           repository=DataRepository(settings=settings),
                           client=client)
    result = service.answer("Compare LAX and SNA congestion levels.")
    assert result["degraded"] is True
    assert result["used_llm"] is False
    assert "unavailable" in result["degraded_reason"].lower()
    assert result["answer"]


def test_tool_round_limit_is_enforced(settings):
    capped = settings.model_copy(update={"agent_max_tool_rounds": 2})
    client = StubClient([_tool_turn("get_dataset_overview", {})] * 2)
    service = AgentService(settings=capped,
                           repository=DataRepository(settings=capped),
                           client=client)
    result = service.answer("Loop forever please")
    assert len(client.messages.calls) == 2
    assert len(result["tool_calls"]) == 2
    assert result["answer"]


def test_system_prompt_forbids_model_arithmetic():
    from app.agent.prompts import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "you do not compute statistics" in lowered
    assert "must come from a tool result" in lowered
    assert "proxy" in lowered
    assert "not a financial valuation model" in lowered
