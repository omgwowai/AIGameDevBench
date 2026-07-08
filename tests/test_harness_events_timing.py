from __future__ import annotations

import json

from aigamedevbench.harness_events import EventParser


class _FakeClock:
    """Deterministic monotonic clock: each call returns the next scripted value
    (seconds). The parser samples the clock on turn start and turn close, so a
    two-turn transcript consumes four samples in order."""

    def __init__(self, samples: list[float]):
        self._samples = list(samples)
        self._i = 0

    def __call__(self) -> float:
        val = self._samples[min(self._i, len(self._samples) - 1)]
        self._i += 1
        return val


def _assistant(text: str | None, tool: str | None) -> str:
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "name": tool, "input": {"file_path": "a.gd"}})
    return json.dumps({"type": "assistant", "message": {"content": content}})


_USER = json.dumps({"type": "user", "message": {"content": [{"type": "tool_result"}]}})


def test_per_turn_duration_and_bottleneck():
    # Clock samples (s): run-start is stamped on the first sample.
    #   turn1 start=0.0  -> ensure_turn (relative 0ms)
    #   turn1 close=0.5  -> duration 500ms
    #   turn2 start=0.5  -> relative 500ms
    #   turn2 close=3.0  -> duration 2500ms  (the bottleneck)
    clock = _FakeClock([0.0, 0.5, 0.5, 3.0])
    parser = EventParser("stream-json", clock=clock)
    for ln in [_assistant("read", "Read"), _USER, _assistant("edit", "Edit"), _USER]:
        parser.feed(ln)
    out = parser.finalize()

    assert len(out["turns"]) == 2
    assert out["turns"][0]["duration_ms"] == 500.0
    assert out["turns"][1]["duration_ms"] == 2500.0
    # total_turn_ms sums measured turns.
    assert out["total_turn_ms"] == 3000.0
    # slowest_turn identifies turn 2 as the bottleneck with its tool calls.
    assert out["slowest_turn"]["turn"] == 2
    assert out["slowest_turn"]["duration_ms"] == 2500.0
    assert out["slowest_turn"]["tool_calls"] == ["Edit(a.gd)"]


def test_single_turn_timing():
    clock = _FakeClock([10.0, 11.25])  # start rel 0, close rel 1250ms
    parser = EventParser("stream-json", clock=clock)
    parser.feed(_assistant("only", "Read"))
    out = parser.finalize()  # finalize closes the open turn
    assert len(out["turns"]) == 1
    assert out["turns"][0]["duration_ms"] == 1250.0
    assert out["total_turn_ms"] == 1250.0
    assert out["slowest_turn"]["turn"] == 1


def test_no_turns_has_null_bottleneck():
    parser = EventParser("stream-json", clock=_FakeClock([0.0]))
    out = parser.finalize()
    assert out["turns"] == []
    assert out["slowest_turn"] is None
    assert out["total_turn_ms"] is None


def test_default_clock_produces_nonnegative_durations():
    # With the real clock, durations are measured and non-negative (smoke check
    # that the default wiring works without an injected clock).
    parser = EventParser("stream-json")
    parser.feed(_assistant("x", "Read"))
    parser.feed(_USER)
    out = parser.finalize()
    assert out["turns"][0]["duration_ms"] is not None
    assert out["turns"][0]["duration_ms"] >= 0.0
