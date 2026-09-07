"""Explicit CP11 live-acceptance entry point; never imported by pytest."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from universal_video_ai.provider_runtime import ProviderMode, get_cost_report


def main() -> int:
    if os.getenv("RUN_LIVE_TESTS") != "1":
        print("LIVE_CP11_ACCEPTANCE_NOT_RUN")
        print("Set RUN_LIVE_TESTS=1 and PROVIDER_EXECUTION_MODE=LIVE explicitly.")
        return 0
    if os.getenv("PROVIDER_EXECUTION_MODE", "").upper() != ProviderMode.LIVE.value:
        print("LIVE_CP11_ACCEPTANCE_NOT_RUN")
        print("PROVIDER_EXECUTION_MODE must be LIVE.")
        return 0
    if os.getenv("CP11_LIVE_CONFIRM") != "I_OWN_THE_TARGETS":
        print("LIVE_CP11_ACCEPTANCE_NOT_RUN")
        print("Set CP11_LIVE_CONFIRM=I_OWN_THE_TARGETS after reviewing private/test destinations.")
        return 0
    # A deployment-specific caller invokes the authenticated start-cycle API;
    # this script only enforces the opt-in boundary and emits the shared cost
    # report. It deliberately never guesses credentials, page IDs, or privacy.
    print("CP11 live boundary enabled; invoke one owner-scoped controlled cycle via the API.")
    print(json.dumps(get_cost_report().to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
