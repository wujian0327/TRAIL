#!/usr/bin/env python3
"""Run and validate formal suites using an archived binary and configurations."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from run_experiments import ROOT, expand_runs, load_yaml


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--max-parallel", type=int, default=3)
    parser.add_argument("--suites", nargs="+", choices=("main", "sensitivity", "no_ema"),
                        default=["main", "sensitivity", "no_ema"])
    args = parser.parse_args()
    batch = (ROOT / args.batch_dir).resolve()
    batch.mkdir(parents=True, exist_ok=False)
    binary = batch / "trail"
    shutil.copy2(ROOT / "target/release/trail", binary)
    shutil.copytree(ROOT / "src", batch / "source/src")
    for name in ("Cargo.toml", "Cargo.lock"):
        shutil.copy2(ROOT / name, batch / "source" / name)
    shutil.copytree(ROOT / "experiments/configs", batch / "source/experiments/configs")
    shutil.copytree(ROOT / "experiments/golden", batch / "source/experiments/golden")
    for name in ("run_experiments.py", "ersoy_participation_report.py",
                 "adaptive_participation_report.py", "trail_compat.py", "run_ersoy_comparison.py"):
        shutil.copy2(ROOT / "experiments" / name, batch / "source/experiments" / name)
    source_diff = subprocess.check_output(["git", "diff", "--binary", "HEAD", "--", "src"], cwd=ROOT)
    (batch / "source.patch").write_bytes(source_diff)
    specs = {}
    for name in args.suites:
        spec = load_yaml(ROOT / f"experiments/configs/trail_ersoy_participation_{name}.yaml")
        spec["binary"] = str(binary)
        spec["max_parallel"] = args.max_parallel
        path = batch / f"config_{name}.yaml"
        path.write_text(json.dumps(spec, indent=2)+"\n")
        specs[name] = (path, len(expand_runs(spec)))
    manifest = dict(started_at_utc=timestamp(), pid=os.getpid(), status="running",
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        source_patch_sha256=hashlib.sha256(source_diff).hexdigest(),
        git_commit_sha=subprocess.check_output(["git", "rev-parse", "HEAD"],cwd=ROOT,text=True).strip(),
        max_parallel=args.max_parallel, suites={name:dict(expected_runs=n,status="queued") for name,(_,n) in specs.items()})

    def save():
        temporary = batch / "manifest.tmp"
        temporary.write_text(json.dumps(manifest,indent=2)+"\n")
        temporary.replace(batch / "manifest.json")

    save()
    try:
        for name, (config, _) in specs.items():
            current = subprocess.check_output(["git", "diff", "--binary", "HEAD", "--", "src"],cwd=ROOT)
            if current != source_diff:
                raise RuntimeError("simulator source changed since batch snapshot; refusing to mix versions")
            manifest["current_suite"] = name
            manifest["suites"][name].update(status="running",started_at_utc=timestamp())
            save()
            print(f"Starting {name}: {config}",flush=True)
            subprocess.run([sys.executable,"-u",str(ROOT / "experiments/run_experiments.py"),
                "--config",str(config),"--no-build","--max-parallel",str(args.max_parallel)],cwd=ROOT,check=True)
            subprocess.run([sys.executable,str(ROOT / "experiments/ersoy_participation_report.py"),
                "--config",str(config)],cwd=ROOT,check=True)
            manifest["suites"][name].update(status="validated",finished_at_utc=timestamp())
            save()
        if len(specs) > 1:
            command = [sys.executable,str(ROOT / "experiments/ersoy_participation_report.py")]
            for config, _ in specs.values():
                command.extend(["--config",str(config)])
            command.extend(["--output-name","trail_ersoy_participation_complete"])
            subprocess.run(command,cwd=ROOT,check=True)
        manifest["status"] = "complete"
    except BaseException as exc:
        manifest.update(status="failed",error=str(exc))
        raise
    finally:
        manifest["updated_at_utc"] = timestamp()
        save()


if __name__ == "__main__":
    main()
