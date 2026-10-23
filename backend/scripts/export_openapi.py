"""Write the API contract used by the web and mobile (Flutter) clients.

    cd backend && python scripts/export_openapi.py          # rewrite contracts/openapi.json
    cd backend && python scripts/export_openapi.py --check  # exit 1 if it is out of date
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CONTRACT = BACKEND.parent / "contracts" / "openapi.json"


def render() -> str:
    sys.path.insert(0, str(BACKEND))
    import main

    return json.dumps(main.app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main_cli(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if not CONTRACT.is_file() or CONTRACT.read_text(encoding="utf-8") != text:
            print(f"{CONTRACT} is out of date; run: python scripts/export_openapi.py", file=sys.stderr)
            return 1
        return 0
    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT.write_text(text, encoding="utf-8")
    print(f"Wrote {CONTRACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv[1:]))
