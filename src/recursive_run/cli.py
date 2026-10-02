import argparse
import json
from pathlib import Path
from recursive_run.contracts import Manifest


def main():
    parser = argparse.ArgumentParser(description="RL0–RL1 development and guarded validation")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("manifest", type=Path)
    validate.add_argument(
        "--operation", choices=["offline", "smoke", "learning", "final"], default="offline"
    )
    args = parser.parse_args()
    manifest = Manifest.model_validate_json(args.manifest.read_text())
    blockers = manifest.blockers(args.operation)
    print(
        json.dumps(
            {
                "schema_version": 1,
                "operation": args.operation,
                "configuration_valid": True,
                "live_ready": not manifest.blockers("smoke"),
                "blockers": blockers,
            },
            indent=2,
        )
    )
    if blockers:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
