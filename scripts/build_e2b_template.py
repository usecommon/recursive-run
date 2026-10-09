"""Build the pinned E2B worker template (build-time only; runtime never uses the SDK).

The template is a digest-pinned Python image. E2B's build layer adds its own tooling
(observed in both variants: git, curl, jq); the "tools" variant also adds ripgrep.
Egress is blocked at run time, so curl cannot reach anything. No repository content or
credentials are baked in; task snapshots are uploaded per lease at run time.
Run with the SDK pinned as a one-off dependency:

    uv run --with e2b==2.50.0 python scripts/build_e2b_template.py [base|tools]

The receipt (template id, build id, base digest, resources) goes to .runtime/.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from e2b import Template

# python:3.12.14-slim-bookworm, published 2026-09-19 (Docker Hub manifest-list digest).
BASE = "python@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e"
CPU, MEMORY_MB = 2, 2048
# Variants: "base" is the v1/v2 smoke template; "tools" adds ripgrep, which the smoke
# agent repeatedly tried to use (protocol v3). jq is already present via E2B's layer. Debian bookworm package versions are
# recorded in the receipt because apt resolves them at build time.
VARIANTS = {
    "base": ("rl-worker-py312", []),
    "tools": ("rl-worker-py312-tools", ["ripgrep", "jq"]),
}


def main():
    import sys

    variant = sys.argv[1] if len(sys.argv) > 1 else "base"
    NAME, packages = VARIANTS[variant]
    template = Template().from_image(BASE)
    if packages:
        template = template.apt_install(packages)
    probe = "python3 --version && bash --version | head -1"
    if packages:
        probe += " && dpkg-query -W " + " ".join(packages)
    template = template.run_cmd(probe)
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
        "apt_packages": packages,
        "sdk": "e2b==2.50.0 (build only)",
    }
    out = Path(
        ".runtime/e2b-template.json"
        if variant == "base"
        else f".runtime/e2b-template-{variant}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
