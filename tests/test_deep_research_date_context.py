"""Regression tests for issue #1341 — deep research used the model's
training-cutoff year (e.g. "best Python tutorials 2025") because the
query-generation and planning prompts never told the LLM the current date.

The chat/agent path already injects "Today is ..." (src/agent_loop.py); deep
research had no equivalent. These tests pin that the current year now reaches
the LLM at both the planning and query-generation steps, without needing a live
LLM or DB.
"""
import asyncio
import json
import sys
import time
import types
from datetime import datetime

from src.deep_research import (
    DeepResearcher,
    current_date_context,
    RESEARCH_PLAN_PROMPT,
)


def _this_year() -> str:
    return datetime.now().astimezone().strftime("%Y")


def test_current_date_context_names_the_real_year():
    ctx = current_date_context()
    assert _this_year() in ctx
    # It must actively steer the model away from training-data years.
    assert "training data" in ctx.lower()


def test_generate_queries_prompt_carries_the_current_year():
    # Build without the heavy __init__; _generate_queries only needs these.
    r = DeepResearcher.__new__(DeepResearcher)
    r.research_plan = ""
    r.queries_used = set()

    seen = {}

    async def _fake_llm(messages, **kwargs):
        seen["prompt"] = messages[0]["content"]
        seen["kwargs"] = kwargs
        return '["python tutorials", "python guides"]'

    r._llm = _fake_llm

    queries = asyncio.run(r._generate_queries("best python tutorials", "", 1))

    assert queries  # sanity: the JSON array parsed
    # The fix: the real current year is in the prompt the LLM actually sees.
    assert _this_year() in seen["prompt"]
    assert seen["kwargs"]["max_retries"] == 1


def test_generate_queries_falls_back_to_question_when_local_llm_times_out():
    r = DeepResearcher.__new__(DeepResearcher)
    r.research_plan = ""
    r.queries_used = set()
    r._emit = lambda **kwargs: None

    async def _failing_llm(messages, **kwargs):
        raise TimeoutError("local model timed out")

    r._llm = _failing_llm

    queries = asyncio.run(r._generate_queries("Ollama qwen local deep research", "", 1))

    assert queries == ["Ollama qwen local deep research"]
    assert "Ollama qwen local deep research" in r.queries_used


def test_time_limit_zero_disables_deep_research_internal_cap():
    r = DeepResearcher.__new__(DeepResearcher)
    r.max_time = 0
    r._start_time = time.time() - 999999

    assert r._time_exceeded() is False


def test_fetch_and_extract_uses_single_retry_for_local_llm_timeouts(monkeypatch):
    search_mod = types.ModuleType("src.search")

    def fake_fetch_webpage_content(url, timeout):
        return {
            "success": True,
            "content": "useful page content",
            "title": "Page",
            "og_image": "",
        }

    search_mod.fetch_webpage_content = fake_fetch_webpage_content
    monkeypatch.setitem(sys.modules, "src.search", search_mod)

    async def immediate_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)

    r = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        extraction_timeout=123,
    )
    seen = {}

    async def _fake_llm(messages, **kwargs):
        seen.update(kwargs)
        return json.dumps({
            "rational": "relevant",
            "evidence": "evidence",
            "summary": "useful page content",
        })

    r._llm = _fake_llm

    result = asyncio.run(r._fetch_and_extract("https://example.test", "question", "Title"))

    assert result["summary"] == "useful page content"
    assert seen["timeout"] == 123
    assert seen["max_retries"] == 1


def test_plan_prompt_carries_the_current_year():
    r = DeepResearcher.__new__(DeepResearcher)

    seen = {}

    async def _fake_llm(messages, **kwargs):
        seen["prompt"] = messages[0]["content"]
        return "{}"

    r._llm = _fake_llm

    asyncio.run(r._create_plan("what changed this year"))

    assert _this_year() in seen["prompt"]
    # The base template itself stays year-agnostic; the year comes from the
    # prepended context, proving the wiring (not a hard-coded prompt edit).
    assert _this_year() not in RESEARCH_PLAN_PROMPT
