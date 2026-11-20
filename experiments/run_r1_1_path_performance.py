#!/usr/bin/env python3
"""Isolated, sequential PoS/TRAIL load pilot using optimized per-path certificates."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import time
import traceback
from types import SimpleNamespace

import run_frozen_devnet_experiments as devnet
from run_frozen_devnet_smoke import collect_build_provenance, command_env
from run_trail_per_path_smoke import audit_live_certificates

ROOT = Path(__file__).resolve().parents[1]


class PilotSpec(devnet.RunSpec):
    @property
    def enclave(self):
        return f"r1-path-{self.variant}-n{self.nodes}-l{self.load_tx_per_slot}-s{self.seed}"


class SizedPilotSpec(PilotSpec):
    @property
    def enclave(self):
        return f"r1-540-{self.variant}-n{self.nodes}-l{self.load_tx_per_slot}-s{self.seed}"


def write_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def setup_proxy_bypass():
    bypass = []
    for key in ["NO_PROXY", "no_proxy"]:
        bypass.extend(value.strip() for value in os.environ.get(key, "").split(",") if value.strip())
    bypass.extend(["localhost", "127.0.0.1", "::1"])
    for key in ["NO_PROXY", "no_proxy"]:
        os.environ[key] = ",".join(dict.fromkeys(bypass))


def registry(root, count):
    public, private = (root / f"registry_{label}_n{count}.json" for label in ["public", "private"])
    if not (public.exists() and private.exists()):
        devnet.run([str(ROOT / "target/release/trail_relay_registry"), "--count", str(count),
                    "--public-out", str(public), "--private-out", str(private)])
    return public, private


def save_client_logs(folder):
    samples = folder / "resources.jsonl"
    if not samples.exists():
        return
    lines = samples.read_text().splitlines()
    if not lines:
        return
    latest = {}
    for line in reversed(lines):
        try:
            latest = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    output = folder / "client_logs"
    output.mkdir(exist_ok=True)
    for container, service in latest.get("client_containers", {}).items():
        with (output / (service + ".log")).open("w") as handle:
            subprocess.run(["docker", "logs", "--timestamps", container], stdout=handle,
                           stderr=subprocess.STDOUT, text=True, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "experiments/configs/r1_1_path_performance_pilot.yaml")
    parser.add_argument("--package", type=Path, default=Path("/home/wujian/ethereum-package"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    spec_args = SimpleNamespace(variants=None, seeds=None, nodes=None, loads=None, topologies=None)
    target = config.get("raw_tx_target_bytes")
    spec_class = SizedPilotSpec if target is not None else PilotSpec
    specs = [spec_class(**asdict(spec)) for spec in devnet.specs_from_config(config, spec_args)]
    specs.sort(key=lambda spec: (spec.load_tx_per_slot, spec.seed, spec.variant))
    if args.dry_run:
        print(json.dumps([asdict(spec) | {"run_id": spec.run_id, "enclave": spec.enclave} for spec in specs], indent=2))
        return 0
    setup_proxy_bypass()
    reporter = str(ROOT / config.get("report_script", "experiments/report_r1_1_path_performance.py"))
    if target is not None:
        original_run = devnet.run
        def sized_run(command, *, check=True):
            if len(command) > 2 and command[1:3] == ["experiments/trail_devnet_runner.py", "run"]:
                command = [*command, "--raw-tx-target-bytes", str(target)]
            return original_run(command, check=check)
        devnet.run = sized_run
    def terminate(signum, frame):
        raise KeyboardInterrupt(f"Received signal {signum}")
    signal.signal(signal.SIGTERM, terminate)
    root = ROOT / "results/raw" / config["suite"]
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        raise RuntimeError("This pilot already has a manifest; use a new suite to preserve all attempts")
    running_before = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines()
    (root / "running_containers_before.txt").write_text("\n".join(running_before))
    owns_engine = not any(name.startswith("kurtosis-engine") for name in running_before)
    provenance = collect_build_provenance(args.package)
    provenance["images"] = {}
    for image in [config["geth_image"], config["lighthouse_image"]]:
        value = json.loads(subprocess.check_output(["docker", "image", "inspect", image], text=True))[0]
        provenance["images"][image] = dict(id=value["Id"], created=value["Created"], labels=value["Config"].get("Labels"))
    manifest = dict(status="running", stage="preparing", created_at=time.time(), config=config,
                    build_provenance=provenance, runs=[asdict(spec) | {"run_id": spec.run_id, "status": "pending"} for spec in specs])
    write_json(manifest_path, manifest)
    subprocess.run([sys.executable, reporter, "--root", str(root)], check=True)
    original_generate = devnet.generate_args

    def generate(spec, public, private, output):
        original_generate(spec, public, private, output)
        text = output.read_text()
        assert text.count("el_image: trail/geth:dev") == text.count("cl_image: trail/lighthouse:dev") == 1
        output.write_text(text.replace("el_image: trail/geth:dev", "el_image: " + config["geth_image"])
                         .replace("cl_image: trail/lighthouse:dev", "cl_image: " + config["lighthouse_image"]))

    devnet.generate_args = generate
    namespace = SimpleNamespace(output_root=root, package=args.package, resume=False, keep_enclaves=True)
    active = None
    failures = []
    try:
        if config.get("smoke_first", True):
            smoke = replace(specs[0], variant="trail", nodes=4, topology="ring", load_tx_per_slot=16,
                            measurement_epochs=2, finality_timeout=600, receipt_timeout=180)
            active = smoke
            manifest["stage"] = "optimized_client_smoke"
            write_json(manifest_path, manifest)
            public, private = registry(root, 4)
            status, error = devnet.execute_run(smoke, namespace, provenance, public, private)
            folder = root / smoke.run_id
            save_client_logs(folder)
            if status != "ok":
                raise RuntimeError("Optimized-client smoke failed: " + error)
            summary = json.loads((folder / "summary.json").read_text())
            if target is not None:
                from sized_devnet_audit import audit_sized_run
                audit_sized_run(folder, summary, target)
            audit_live_certificates(smoke, summary, folder, "after")
            manifest["smoke"] = dict(status="passed", summary_path=str(folder / "summary.json"))
            devnet.remove_enclave(smoke.enclave)
            active = None
        public, private = registry(root, specs[0].nodes)
        for index, spec in enumerate(specs):
            active = spec
            entry = manifest["runs"][index]
            entry.update(status="running", started_at=time.time())
            manifest.update(stage="load_comparison", current_run=spec.run_id)
            write_json(manifest_path, manifest)
            print(f"[{index+1}/{len(specs)}] {spec.run_id}", flush=True)
            status, error = devnet.execute_run(spec, namespace, provenance, public, private)
            folder = root / spec.run_id
            try:
                save_client_logs(folder)
                if status == "ok" and target is not None:
                    from sized_devnet_audit import audit_sized_run
                    audit_sized_run(folder, json.loads((folder / "summary.json").read_text()), target)
                    entry["transaction_audit"] = "passed"
                if status == "ok" and spec.variant == "trail":
                    summary = json.loads((folder / "summary.json").read_text())
                    audit_live_certificates(spec, summary, folder, "after")
                    entry["per_path_certificate_audit"] = "passed"
            except Exception:
                status, error = "failed", traceback.format_exc()
            finally:
                devnet.remove_enclave(spec.enclave)
                active = None
            entry.update(status=status, error=error, finished_at=time.time())
            if status != "ok":
                failures.append(spec.run_id)
            write_json(manifest_path, manifest)
            subprocess.run([sys.executable, reporter, "--root", str(root)], check=True)
        manifest.update(status="completed" if not failures else "completed_with_failures", stage="finished", failures=failures, finished_at=time.time())
    except BaseException:
        manifest.update(status="failed", stage="stopped", error=traceback.format_exc(), finished_at=time.time())
        raise
    finally:
        if active is not None:
            try:
                save_client_logs(root / active.run_id)
            finally:
                devnet.remove_enclave(active.enclave)
        if owns_engine and config.get("stop_owned_engine_on_exit", True):
            # Never stop an engine if another experiment appeared while we ran.
            names = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines()
            foreign = [name for name in names if not name.startswith(("kurtosis-engine", "kurtosis-reverse-proxy", "kurtosis-logs-aggregator"))]
            if not foreign:
                stopped = subprocess.run(["kurtosis", "engine", "stop"], env=command_env(), check=False)
                manifest["owned_engine_stopped"] = stopped.returncode == 0
        write_json(manifest_path, manifest)
        subprocess.run([sys.executable, reporter, "--root", str(root)], check=False)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
