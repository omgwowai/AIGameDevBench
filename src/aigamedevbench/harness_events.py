from __future__ import annotations

"""Parse a CLI harness's streamed stdout into structured per-turn events.

A command-driver run only produces raw stdout lines; that is enough to eyeball a
failure but not to *analyse* what the harness did (which tools it called, how
many turns, how many tokens). This module turns that stream into the same
`ai_turns` / `total_tokens` shape the dashboard already renders for survey runs,
so the activity view lights up for our own runs with no front-end change.

Two strategies, chosen per `fmt`:
  - "stream-json": each line is one JSON event. Recognises the Claude Code
    `--output-format stream-json` schema (type=assistant/user/result, tool_use
    blocks, a `usage` token block) and Codex `--json` events. Most precise.
  - "text": no JSON; a light heuristic pulls tool invocations out of plain text
    (e.g. a line beginning with a known tool name, or a "● Tool(args)" marker)
    and otherwise accumulates the text as the turn's output.
  - "auto" (default): try JSON per line, fall back to the heuristic for any line
    that is not a recognised JSON event. A harness that streams JSON gets precise
    events; a plain-text harness still gets a best-effort record.

The parser is fed one line at a time (via `feed`) as the driver emits them, then
`finalize()` returns {"turns": [...], "total_tokens": int|None}. A turn is
{"turn": int, "agent_output": str, "tool_calls": [str, ...]} — matching the keys
the dashboard's renderActivity() expects.
"""

import json
import re
import time
from dataclasses import dataclass, field

# Tool-name markers for the text heuristic. Deliberately small and conservative:
# these are the common Claude Code / Codex tool names. An unrecognised line is
# treated as output text, never a spurious tool call.
_TEXT_TOOL_RE = re.compile(
    r"^\s*(?:[●▶*-]\s*)?"
    r"(Read|Edit|Write|Bash|Grep|Glob|Search|Task|WebFetch|WebSearch|"
    r"MultiEdit|NotebookEdit|Update|Create|Delete|Move|Run)"
    r"\b[\s(:]",
    re.IGNORECASE,
)


@dataclass
class _Turn:
    turn: int
    agent_output_parts: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    # Wall-clock (ms) relative to the parser's run start, stamped when the turn
    # first gets content; duration_ms is filled in when the turn closes. Both are
    # None until measured so a caller can tell "not timed" from "0 ms".
    start_ms: float | None = None
    duration_ms: float | None = None

    def to_dict(self) -> dict:
        return {
            "turn": self.turn,
            "agent_output": "\n".join(p for p in self.agent_output_parts if p).strip(),
            "tool_calls": self.tool_calls,
            "duration_ms": self.duration_ms,
        }

    def is_empty(self) -> bool:
        return not self.tool_calls and not any(self.agent_output_parts)


class EventParser:
    """Incremental parser. Call feed(line) per stdout line, then finalize()."""

    def __init__(self, fmt: str = "auto", clock=time.perf_counter):
        if fmt not in ("auto", "stream-json", "text"):
            fmt = "auto"
        self.fmt = fmt
        self._turns: list[_Turn] = []
        self._cur: _Turn | None = None
        self._total_tokens: int | None = None
        # Injectable monotonic clock (seconds) so tests can drive deterministic
        # per-turn durations. The run start is stamped lazily on the first turn
        # so idle time before any harness output isn't attributed to a turn.
        self._clock = clock
        self._run_start: float | None = None

    # --- turn bookkeeping ---

    def _now_ms(self) -> float:
        now = self._clock()
        if self._run_start is None:
            self._run_start = now
        return (now - self._run_start) * 1000.0

    def _ensure_turn(self) -> _Turn:
        if self._cur is None:
            self._cur = _Turn(turn=len(self._turns) + 1, start_ms=self._now_ms())
        return self._cur

    def _close_turn(self) -> None:
        if self._cur is not None and not self._cur.is_empty():
            if self._cur.start_ms is not None:
                self._cur.duration_ms = round(self._now_ms() - self._cur.start_ms, 1)
            self._turns.append(self._cur)
        self._cur = None

    def _add_tokens(self, n: int | None) -> None:
        if n is None:
            return
        self._total_tokens = (self._total_tokens or 0) + int(n)

    # --- feed ---

    def feed(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        if self.fmt in ("auto", "stream-json"):
            event = _try_parse_json(stripped)
            if event is not None:
                self._handle_json_event(event)
                return
            if self.fmt == "stream-json":
                # Strict JSON mode: a non-JSON line is noise (banner, warning).
                return
        # text mode, or auto-fallback for a non-JSON line.
        self._handle_text_line(stripped)

    def _handle_json_event(self, event: dict) -> None:
        etype = event.get("type")
        # Claude Code stream-json: an "assistant" event wraps a message whose
        # content is a list of blocks (text / tool_use); "result" carries usage.
        if etype == "assistant":
            turn = self._ensure_turn()
            msg = event.get("message") or {}
            for block in msg.get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    turn.agent_output_parts.append(str(block["text"]))
                elif block.get("type") == "tool_use":
                    turn.tool_calls.append(_summarize_tool_use(block))
            self._add_tokens(_usage_tokens(msg.get("usage")))
            # A tool_use block ends the assistant's turn; the tool result comes
            # back as a "user" event and the next assistant reply is a new turn.
            self._close_turn()
            return
        if etype == "user":
            # Tool results / user messages delimit turns; nothing to record.
            self._close_turn()
            return
        if etype == "result":
            self._add_tokens(_usage_tokens(event.get("usage")))
            if isinstance(event.get("total_tokens"), (int, float)):
                self._total_tokens = int(event["total_tokens"])
            return
        # Codex-style JSON events: {"type":"tool_call","name":...} etc.
        if etype in ("tool_call", "function_call", "tool_use"):
            self._ensure_turn().tool_calls.append(_summarize_tool_use(event))
            return
        if etype in ("message", "agent_message", "text") and event.get("text"):
            self._ensure_turn().agent_output_parts.append(str(event["text"]))
            return
        # Unknown JSON event: harvest a token count if present, else ignore.
        self._add_tokens(_usage_tokens(event.get("usage")))

    def _handle_text_line(self, line: str) -> None:
        turn = self._ensure_turn()
        if _TEXT_TOOL_RE.match(line):
            turn.tool_calls.append(line[:200])
        else:
            turn.agent_output_parts.append(line)

    # --- output ---

    def finalize(self) -> dict:
        self._close_turn()
        # Text-mode with no recognisable structure collapses into one big turn;
        # that is fine (the dashboard shows it as turn 1). Trim per-turn output so
        # the report does not balloon with a full transcript.
        turns = []
        for t in self._turns:
            d = t.to_dict()
            if len(d["agent_output"]) > 4000:
                d["agent_output"] = d["agent_output"][:4000] + "\n...(truncated)"
            turns.append(d)
        # Bottleneck analysis: which single turn cost the most wall time, and the
        # sum of all measured turn durations. Both derived from timed turns only
        # (duration_ms is None for a turn the clock never measured).
        timed = [t for t in self._turns if t.duration_ms is not None]
        slowest_turn = None
        total_turn_ms = None
        if timed:
            total_turn_ms = round(sum(t.duration_ms for t in timed), 1)
            worst = max(timed, key=lambda t: t.duration_ms)
            slowest_turn = {
                "turn": worst.turn,
                "duration_ms": worst.duration_ms,
                "tool_calls": list(worst.tool_calls),
            }
        return {
            "turns": turns,
            "total_tokens": self._total_tokens,
            "slowest_turn": slowest_turn,
            "total_turn_ms": total_turn_ms,
        }


def _try_parse_json(line: str) -> dict | None:
    if not (line.startswith("{") and line.endswith("}")):
        return None
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _summarize_tool_use(block: dict) -> str:
    """A compact one-line label for a tool call: name + the most identifying arg
    (file path or command), so the dashboard list is scannable, not a JSON dump."""
    name = block.get("name") or block.get("tool") or block.get("function") or "tool"
    inp = block.get("input")
    if not isinstance(inp, dict):
        inp = block.get("arguments") if isinstance(block.get("arguments"), dict) else {}
    for key in ("file_path", "path", "command", "pattern", "query", "url", "cmd"):
        val = inp.get(key)
        if isinstance(val, str) and val:
            return f"{name}({val[:160]})"
    return str(name)


def _usage_tokens(usage) -> int | None:
    """Total tokens from an Anthropic/OpenAI-style usage block. Sums input +
    output (+ cache) when present, tolerating either naming convention."""
    if not isinstance(usage, dict):
        return None
    total = 0
    found = False
    for key in ("input_tokens", "output_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens",
                "prompt_tokens", "completion_tokens"):
        val = usage.get(key)
        if isinstance(val, (int, float)):
            total += int(val)
            found = True
    if isinstance(usage.get("total_tokens"), (int, float)):
        return int(usage["total_tokens"])
    return total if found else None
