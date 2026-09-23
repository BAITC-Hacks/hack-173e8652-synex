from __future__ import annotations

import argparse
from pathlib import Path

from moneygraph.verification import verify_outputs

__all__ = ["verify_outputs"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify MoneyGraph AML output contracts")
    parser.add_argument("--out", type=Path, default=Path("out"))
    parser.add_argument("--expected-nodes", type=int)
    args = parser.parse_args()
    errors = verify_outputs(args.out, args.expected_nodes)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: mandatory outputs in {args.out} satisfy the contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
