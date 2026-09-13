import argparse
import json
from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sync_version(root: Path, *, check: bool = False) -> bool:
    version = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
    if not isinstance(version, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", version
    ):
        raise ValueError(f"Invalid package.json version: {version!r}")

    targets = {
        "pyproject.toml": r'(\[tool\.poetry\][^\[]*?^version\s*=\s*)"[^"\r\n]*"',
        "backend/__init__.py": r'(^__version__\s*=\s*)"[^"\r\n]*"',
    }
    updates = []
    for relative_path, pattern in targets.items():
        path = root / relative_path
        # Preserve existing line endings and unrelated metadata.
        original = path.read_bytes().decode("utf-8")
        updated, count = re.subn(
            pattern, lambda match: match[1] + json.dumps(version), original, flags=re.M
        )
        if count != 1:
            raise ValueError(
                f"Expected one version field in {relative_path}, found {count}"
            )
        if original != updated:
            updates.append((path, updated))

    for path, updated in updates:
        if check:
            print(f"Version mismatch: {path.relative_to(root)} (expected {version})")
        else:
            path.write_bytes(updated.encode("utf-8"))
            print(f"Updated {path.relative_to(root)} to {version}")
    if check and updates:
        print("Run pnpm version:sync to update generated version fields.")
        return False
    print(f"Application version: {version}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Check without changing files"
    )
    args = parser.parse_args()
    return 0 if sync_version(PROJECT_ROOT, check=args.check) else 1


if __name__ == "__main__":
    sys.exit(main())
