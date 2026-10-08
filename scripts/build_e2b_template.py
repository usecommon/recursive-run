"""Build the pinned E2B worker template (build-time only; runtime never uses the SDK).

The template is a digest-pinned Python image and nothing else: no network tools, git,
repository content or credentials. Task snapshots are uploaded per lease at run time.
Run with the SDK pinned as a one-off dependency:

    uv run --with e2b==2.50.0 python scripts/build_e2b_template.py

The receipt (template id, build id, base digest, resources) goes to .runtime/.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from e2b import Template

# python:3.12.14-slim-bookworm, published 2026-09-19 (Docker Hub manifest-list digest).
BASE = "python@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e"
NAME = "rl-worker-py312"
CPU, MEMORY_MB = 2, 2048


def main():
    template = Template().from_image(BASE).run_cmd("python3 --version && bash --version | head -1")
    info = Template.build(template, NAME, cpu_count=CPU, memory_mb=MEMORY_MB)
    receipt = {
        "schema_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "name": NAME,
        "template_id": info.template_id,
        "build_id": info.build_id,
        "base_image": BASE,
        "cpu_count": CPU,
        "memory_mb": MEMORY_MB,
        "sdk": "e2b==2.50.0 (build only)",
    }
    out = Path(".runtime/e2b-template.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
