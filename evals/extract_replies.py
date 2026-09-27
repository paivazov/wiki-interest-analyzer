#!/usr/bin/env python3
"""Write reply.md for every run from the agent transcript (Claude Code sub-agent JSONL).

The reply is what the user would see in the graded turn (turn 1 for eval 1, turn 2 for its follow-up, eval 4):
the SubagentHandback message if it is written to the user (mostly Cyrillic, like the prompts), otherwise the
last Cyrillic assistant text of that turn. Some agents hand back an English note to the operator and put the
real reply in a text block.

Usage: uv run evals/extract_replies.py <runs-dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FOLLOW_UP = "А додай ще словацьку"  # first words of eval 4's prompt: starts turn 2 in the eval-1 transcript


def messages_by_turn(transcript: Path) -> list[list[tuple[str, str]]]:
    turns, cur = [], []
    for line in transcript.read_text(encoding="utf-8").splitlines():
        o = json.loads(line)
        content = o.get("message", {}).get("content")
        if o.get("type") == "user" and FOLLOW_UP in json.dumps(content, ensure_ascii=False):
            turns.append(cur)
            cur = []
        if o.get("type") == "assistant":
            for b in content or []:
                if b.get("type") == "text" and b["text"].strip():
                    cur.append(("text", b["text"].strip()))
                elif b.get("type") == "tool_use" and b["name"] == "SubagentHandback":
                    cur.append(("handback", b["input"]["message"].strip()))
    turns.append(cur)
    return turns


def cyrillic(text: str) -> bool:
    letters = [ch.lower() for ch in text if ch.isalpha()]
    return sum("а" <= ch <= "я" or ch in "іїєґ" for ch in letters) > 0.5 * max(1, len(letters))


def main() -> None:
    for meta_path in sorted(Path(sys.argv[1]).rglob("meta.json")):
        meta = json.loads(meta_path.read_text())
        turns = messages_by_turn(Path(meta["transcript"]))
        turn = turns[1 if meta["eval_id"] == 4 else 0] if len(turns) > (meta["eval_id"] == 4) else []
        if not turn:
            print(f"{meta_path.parent}: no reply yet")
            continue
        handbacks = [t for kind, t in turn if kind == "handback" and cyrillic(t)]
        texts = [t for _, t in turn if cyrillic(t)]
        reply = (handbacks or texts or [turn[-1][1]])[-1]
        (meta_path.parent / "reply.md").write_text(reply + "\n", encoding="utf-8")
        print(f"eval {meta['eval_id']}: {len(reply)} chars: {reply[:80]!r}")


if __name__ == "__main__":
    main()
