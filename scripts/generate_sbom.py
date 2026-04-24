from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "sbom" / "driftguard.cyclonedx.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a CycloneDX SBOM from the current Python environment.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path for the generated SBOM. Defaults to {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--format",
        choices=("JSON", "XML"),
        default="JSON",
        help="CycloneDX output format.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scripts_dir = Path(sys.executable).resolve().parent
    candidate_names = ["cyclonedx-py.exe", "cyclonedx-py"] if sys.platform.startswith("win") else ["cyclonedx-py"]
    cyclonedx = next((str(scripts_dir / name) for name in candidate_names if (scripts_dir / name).exists()), None)
    if cyclonedx is None:
        cyclonedx = shutil.which("cyclonedx-py")
    if cyclonedx is None:
        print(
            "cyclonedx-py is not installed. Install SBOM tooling with: "
            f"{sys.executable} -m pip install -r requirements-sbom.txt",
            file=sys.stderr,
        )
        return 1

    output_path = args.output if args.output.is_absolute() else (REPO_ROOT / args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        cyclonedx,
        "environment",
        "--output-reproducible",
        "--of",
        args.format,
        "-o",
        str(output_path),
    ]
    subprocess.run(command, cwd=REPO_ROOT, check=True)
    print(f"Generated SBOM at {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())