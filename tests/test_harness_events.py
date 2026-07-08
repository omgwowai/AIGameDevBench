from __future__ import annotations

import json

from aigamedevbench.harness_events import EventParser


def _feed(parser: EventParser, lines: list[str]) -> dict:
    for ln in lines:
        parser.feed(ln)
    return parser.finalize()


def test_stream_json_parses_tool_use_and_tokens():
    # A minimal Claude Code stream-json transcript: one assistant text+tool_use
    # turn, a tool result (user), a second assistant turn, then a result event.
    lines = [
        json.dumps({"type": "assistant", "message": {
            "content": [
                {"type": "text", "text": "Reading the file."},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "player.gd"}},
            ],
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }}),
        json.dumps({"type": "user", "message": {"content": [{"type": "tool_result"}]}}),
        json.dumps({"type": "assistant", "message": {
            "content": [{"type": "tool_use", "name": "Edit",
                         "input": {"file_path": "player.gd"}}],
            "usage": {"input_tokens": 130, "output_tokens": 15},
        }}),
        json.dumps({"type": "result", "usage": {"input_tokens": 0, "output_tokens": 0}}),
    ]
    out = _feed(EventParser("stream-json"), lines)
    assert len(out["turns"]) == 2
    assert out["turns"][0]["turn"] == 1
    assert "Reading the file." in out["turns"][0]["agent_output"]
    assert out["turns"][0]["tool_calls"] == ["Read(player.gd)"]
    assert out["turns"][1]["tool_calls"] == ["Edit(player.gd)"]
    # 100+20 + 130+15 tokens summed across the assistant turns.
    assert out["total_tokens"] == 265


def test_stream_json_ignores_non_json_noise():
    lines = [
        "Some banner line printed before JSON starts",
        json.dumps({"type": "assistant", "message": {
            "content": [{"type": "tool_use", "name": "Bash",
                         "input": {"command": "godot --headless"}}]}}),
    ]
    out = _feed(EventParser("stream-json"), lines)
    assert len(out["turns"]) == 1
    assert out["turns"][0]["tool_calls"] == ["Bash(godot --headless)"]


def test_text_heuristic_extracts_tool_lines():
    lines = [
        "Let me look at the scene.",
        "Read player.tscn",
        "Editing the script now",
        "Edit: player.gd",
        "Bash(godot --headless --path . --import)",
    ]
    out = _feed(EventParser("text"), lines)
    # One text turn accumulating output + recognised tool lines.
    assert len(out["turns"]) == 1
    tools = out["turns"][0]["tool_calls"]
    assert any(t.startswith("Read") for t in tools)
    assert any(t.startswith("Edit") for t in tools)
    assert any(t.startswith("Bash") for t in tools)
    assert "Let me look at the scene." in out["turns"][0]["agent_output"]
    assert out["total_tokens"] is None  # text mode has no token info


def test_auto_falls_back_from_json_to_text():
    lines = [
        json.dumps({"type": "assistant", "message": {
            "content": [{"type": "text", "text": "hi"}]}}),
        "Read some_file.gd",  # not JSON -> heuristic
    ]
    out = _feed(EventParser("auto"), lines)
    # The JSON assistant turn, then a text turn with the Read tool.
    all_tools = [t for turn in out["turns"] for t in turn["tool_calls"]]
    assert any(t.startswith("Read") for t in all_tools)


def test_empty_stream_yields_no_turns():
    out = _feed(EventParser("auto"), [])
    assert out["turns"] == []
    assert out["total_tokens"] is None


def test_codex_style_tool_call_event():
    lines = [
        json.dumps({"type": "tool_call", "name": "apply_patch",
                    "arguments": {"path": "src/main.gd"}}),
    ]
    out = _feed(EventParser("stream-json"), lines)
    assert out["turns"][0]["tool_calls"] == ["apply_patch(src/main.gd)"]
