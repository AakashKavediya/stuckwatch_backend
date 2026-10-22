"""Print the simulated stuck_doc intervention without using a camera."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stuckwatch.interventions import InterventionEngine


def main() -> None:
    policy = InterventionEngine(sustained_seconds=120)
    for now in (0.0, 60.0, 119.0, 120.0):
        nudge = policy.evaluate(
            score=0.9,
            app_category="editor",
            fullscreen=False,
            paused=False,
            now=now,
            reasons=["the document has been still for a while"],
        )
        if nudge:
            print({"type": "nudge", "v": 1, "ts": now, **nudge})
            response = policy.respond(nudge["id"], "tiny_step", now)
            if response:
                print({"v": 1, "ts": now, **response})


if __name__ == "__main__":
    main()
