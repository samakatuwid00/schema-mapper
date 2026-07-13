"""Preview or apply the one-time entity fingerprint scope migration."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.services.ops import rebaseline_entity_fingerprints


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="write scoped fingerprints and re-enable legacy drift pauses")
    parser.add_argument("--by", required=True, help="administrator recorded in the audit log")
    args = parser.parse_args()
    result = rebaseline_entity_fingerprints(args.by, apply=args.apply)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
