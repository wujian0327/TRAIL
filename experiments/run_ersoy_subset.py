#!/usr/bin/env python3
"""Finish an explicitly configured preliminary subset without resuming the full batch."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from run_experiments import ROOT, expand_runs, load_yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-name", required=True)
    args = parser.parse_args()
    config = (ROOT / args.config).resolve()
    path = (ROOT / args.manifest).resolve()
    spec = load_yaml(config)
    runs = expand_runs(spec)
    reused = 0
    for run in runs:
        status = Path(run["output_dir"]) / "runner_status.json"
        if status.exists() and json.loads(status.read_text()).get("status") == "ok":
            reused += 1
    state = dict(status="running", pid=os.getpid(), expected_runs=len(runs),
        reused_runs=reused, new_runs=len(runs)-reused, seeds=spec["seeds"],
        config=str(config), output=str(ROOT / "results/processed" / args.output_name),
        started_at_utc=datetime.now(timezone.utc).isoformat(),
        binary_sha256=hashlib.sha256(Path(spec["binary"]).read_bytes()).hexdigest())

    def save():
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2)+"\n")
        temporary.replace(path)

    save()
    try:
        subprocess.run([sys.executable, "-u", str(ROOT / "experiments/run_experiments.py"),
            "--config", str(config), "--no-build", "--max-parallel", "3"], cwd=ROOT, check=True)
        state["status"] = "validating"
        save()
        subprocess.run([sys.executable, str(ROOT / "experiments/ersoy_participation_report.py"),
            "--config", str(config), "--output-name", args.output_name], cwd=ROOT, check=True)
        state["status"] = "complete"
    except BaseException as exc:
        state.update(status="failed", error=str(exc))
        raise
    finally:
        state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        save()


if __name__ == "__main__":
    main()
