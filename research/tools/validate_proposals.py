"""Validate Class-B proposals without committing or pushing their protected edits.

For every proposal:
1. create a disposable worktree at origin/main;
2. run git apply --check;
3. ensure the patch does not modify approval/hash artifacts;
4. apply only inside the disposable worktree;
5. run only the proposal's declared targeted commands;
6. remove the worktree.

No approve command is ever invoked.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROPOSALS = ROOT / "research" / "proposals"
FORBIDDEN_PATCH_TARGETS = {
    "engine/approved_build.json",
    "engine/approved_config.sha256",
    "tests/approved_tests.json",
    "infra/approved_infra.json",
    "infra/core_v06.sha256",
}
FORBIDDEN_COMMAND_TOKENS = {
    "approve_build.py",
    "approve_config.py",
    "approve_infra.py",
    "--approve",
}


def run(command: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(command)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def patch_paths(patch: Path) -> list[str]:
    proc = run(["git", "apply", "--numstat", str(patch)], cwd=ROOT)
    paths = []
    for raw in proc.stdout.splitlines():
        parts = raw.split("\t")
        if len(parts) >= 3:
            paths.append(parts[-1])
    return paths


def validate_one(proposal_dir: Path, *, base_ref: str) -> dict:
    meta_path = proposal_dir / "proposal.json"
    patch = proposal_dir / "patch.diff"
    if not meta_path.exists() or not patch.exists():
        raise RuntimeError(f"{proposal_dir.name}: proposal.json or patch.diff missing")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    commands = meta.get("test_commands")
    if not isinstance(commands, list) or not commands:
        raise RuntimeError(f"{proposal_dir.name}: no targeted test_commands declared")

    paths = patch_paths(patch)
    forbidden = sorted(set(paths) & FORBIDDEN_PATCH_TARGETS)
    if forbidden:
        raise RuntimeError(
            f"{proposal_dir.name}: patch illegally contains approval/hash artifacts: {forbidden}"
        )

    for command in commands:
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise RuntimeError(f"{proposal_dir.name}: malformed test command")
        flattened = " ".join(command)
        if any(token in flattened for token in FORBIDDEN_COMMAND_TOKENS):
            raise RuntimeError(f"{proposal_dir.name}: targeted command contains approval token")

    temp_parent = Path(tempfile.mkdtemp(prefix=f"trips-{meta['id'].lower()}-"))
    worktree = temp_parent / "main"
    outputs = []
    try:
        run(["git", "worktree", "add", "--detach", str(worktree), base_ref], cwd=ROOT)
        run(["git", "apply", "--check", str(patch)], cwd=worktree)
        run(["git", "apply", str(patch)], cwd=worktree)

        for command in commands:
            proc = run(command, cwd=worktree)
            outputs.append({
                "command": command,
                "stdout_tail": proc.stdout.splitlines()[-20:],
                "stderr_tail": proc.stderr.splitlines()[-20:],
            })

        status_lines = run(["git", "status", "--porcelain"], cwd=worktree).stdout.splitlines()
        changed = [line[3:] for line in status_lines if len(line) >= 4]
        if sorted(changed) != sorted(paths):
            raise RuntimeError(
                f"{proposal_dir.name}: applied paths differ from patch paths: "
                f"patch={sorted(paths)} worktree={sorted(changed)}"
            )
        return {
            "id": meta["id"],
            "title": meta["title"],
            "apply_check": "PASS",
            "targeted_tests": "PASS",
            "paths": paths,
            "commands": outputs,
        }
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)],
                       cwd=ROOT, text=True, capture_output=True)
        subprocess.run(["git", "worktree", "prune"], cwd=ROOT, text=True, capture_output=True)
        shutil.rmtree(temp_parent, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()

    run(["git", "rev-parse", "--verify", args.base], cwd=ROOT)
    proposal_dirs = sorted(
        path for path in PROPOSALS.iterdir()
        if path.is_dir() and path.name.startswith("P")
    )
    expected = {"P001", "P002", "P003", "P004", "P005"}
    found = {
        json.loads((path / "proposal.json").read_text(encoding="utf-8"))["id"]
        for path in proposal_dirs
        if (path / "proposal.json").exists()
    }
    if found != expected:
        raise RuntimeError(f"proposal inventory mismatch: found={sorted(found)} expected={sorted(expected)}")

    results = [validate_one(path, base_ref=args.base) for path in proposal_dirs]
    print(json.dumps({"ok": True, "base": args.base, "results": results},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
