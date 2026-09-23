#!/usr/bin/env python3
"""Task runner for the active TRAIL experiment and figure workflows."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

TRAIL_FIGURE_SCRIPTS = {
    "trail-devnet-figures": (
        "analysis/plot_trail_devnet.py",
        "analysis/plot_trail_devnet_txslot_pilot.py",
    ),
    "trail-fairness-figures": ("analysis/plot_trail_incentive_fairness.py",),
    "trail-outage-figures": ("analysis/plot_trail_outage_resilience.py",),
    "trail-participation-figures": (
        "experiments/trail_relay_participation_report.py",
        "analysis/plot_trail_relay_participation.py",
    ),
    "trail-security-figures": (
        "analysis/plot_trail_proposer_influence.py",
        "analysis/plot_trail_proposer_scaling.py",
        "analysis/plot_trail_path_padding.py",
        "analysis/plot_trail_transaction_flooding.py",
    ),
}


def run(command: list[str], env: dict[str, str] | None = None) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    subprocess.run(command, cwd=ROOT, env=merged_env, check=True)


def task_test(_args: argparse.Namespace) -> None:
    run(["cargo", "check"])
    run(["cargo", "test", "--lib"])
    run(
        [
            PYTHON,
            "-m",
            "unittest",
            "discover",
            "-s",
            "experiments/tests",
            "-p",
            "test_*.py",
        ]
    )


def run_experiment_config(args: argparse.Namespace, config: str) -> None:
    command = [PYTHON, "experiments/run_experiments.py", "--config", config]
    if args.force:
        command.append("--force")
    if args.dry_run:
        command.append("--dry-run")
    run(command)


def task_run_config(args: argparse.Namespace) -> None:
    if not args.config:
        raise SystemExit("run-config requires --config")
    run_experiment_config(args, args.config)


def task_frozen_smoke(args: argparse.Namespace) -> None:
    run_experiment_config(args, "experiments/configs/frozen_v1_smoke.yaml")
    if not args.dry_run:
        run(
            [
                PYTHON,
                "experiments/summarize.py",
                "--config",
                "experiments/configs/frozen_v1_smoke.yaml",
            ]
        )


def task_frozen_security(args: argparse.Namespace, config: str) -> None:
    run_experiment_config(args, config)
    if args.dry_run:
        return
    run(["cargo", "run", "--release", "--bin", "frozen_padding_check"])
    command = [PYTHON, "experiments/frozen_security_report.py", "--config", config]
    if args.allow_incomplete:
        command.append("--allow-incomplete")
    run(command)


def task_frozen_security_pilot(args: argparse.Namespace) -> None:
    task_frozen_security(args, "experiments/configs/frozen_v1_security_pilot.yaml")


def task_frozen_security_main(args: argparse.Namespace) -> None:
    task_frozen_security(args, "experiments/configs/frozen_v1_security_main.yaml")


def task_frozen_security_report(args: argparse.Namespace) -> None:
    config = args.config or "experiments/configs/frozen_v1_security_main.yaml"
    command = [PYTHON, "experiments/frozen_security_report.py", "--config", config]
    if args.allow_incomplete:
        command.append("--allow-incomplete")
    run(command)


def task_frozen_padding_check(_args: argparse.Namespace) -> None:
    run(["cargo", "run", "--release", "--bin", "frozen_padding_check"])


def task_frozen_evidence_bench(_args: argparse.Namespace) -> None:
    run(["cargo", "run", "--release", "--bin", "frozen_evidence_bench"])


def task_trail_transaction_flooding_report(_args: argparse.Namespace) -> None:
    run([PYTHON, "experiments/trail_transaction_flooding_report.py"])


def task_trail_participation_report(_args: argparse.Namespace) -> None:
    run([PYTHON, "experiments/trail_relay_participation_report.py"])


def task_figure_group(target: str) -> Callable[[argparse.Namespace], None]:
    def render(_args: argparse.Namespace) -> None:
        for script in TRAIL_FIGURE_SCRIPTS[target]:
            run([PYTHON, script])

    return render


def task_trail_figures(args: argparse.Namespace) -> None:
    for target in TRAIL_FIGURE_SCRIPTS:
        task_figure_group(target)(args)


def task_frozen_devnet_check(args: argparse.Namespace) -> None:
    command = [PYTHON, "experiments/frozen_devnet_acceptance.py"]
    if args.artifact:
        command.extend(["--artifact", args.artifact, "--mode", args.mode])
    run(command)


def task_frozen_devnet_smoke(args: argparse.Namespace) -> None:
    command = [
        PYTHON,
        "experiments/run_frozen_devnet_smoke.py",
        "--modes",
        args.modes,
    ]
    if args.package:
        command.extend(["--package", args.package])
    if args.skip_existing:
        command.append("--skip-existing")
    if args.keep_enclaves:
        command.append("--keep-enclaves")
    run(command)


def task_frozen_devnet_matrix(args: argparse.Namespace, config: str) -> None:
    command = [
        PYTHON,
        "experiments/run_frozen_devnet_experiments.py",
        "--config",
        config,
    ]
    if args.package:
        command.extend(["--package", args.package])
    for option in ("seeds", "variants", "nodes", "loads", "topologies"):
        value = getattr(args, option)
        if value:
            command.extend([f"--{option}", value])
    if args.resume:
        command.append("--resume")
    if args.keep_enclaves:
        command.append("--keep-enclaves")
    if args.stop_on_failure:
        command.append("--stop-on-failure")
    if args.dry_run:
        command.append("--dry-run")
    run(command)


def task_frozen_devnet_pilot(args: argparse.Namespace) -> None:
    task_frozen_devnet_matrix(args, "experiments/configs/frozen_v1_devnet_pilot.yaml")


def task_frozen_devnet_main(args: argparse.Namespace) -> None:
    task_frozen_devnet_matrix(args, "experiments/configs/frozen_v1_devnet_main.yaml")


def task_summarize(args: argparse.Namespace) -> None:
    command = [PYTHON, "experiments/summarize.py"]
    if args.config:
        command.extend(["--config", args.config])
    run(command)


TASKS: dict[str, Callable[[argparse.Namespace], None]] = {
    "test": task_test,
    "run-config": task_run_config,
    "frozen-smoke": task_frozen_smoke,
    "frozen-security-pilot": task_frozen_security_pilot,
    "frozen-security-main": task_frozen_security_main,
    "frozen-security-report": task_frozen_security_report,
    "frozen-padding-check": task_frozen_padding_check,
    "frozen-evidence-bench": task_frozen_evidence_bench,
    "trail-transaction-flooding-report": task_trail_transaction_flooding_report,
    "trail-participation-report": task_trail_participation_report,
    "trail-figures": task_trail_figures,
    "frozen-devnet-check": task_frozen_devnet_check,
    "frozen-devnet-smoke": task_frozen_devnet_smoke,
    "frozen-devnet-pilot": task_frozen_devnet_pilot,
    "frozen-devnet-main": task_frozen_devnet_main,
    "summarize": task_summarize,
}
for figure_target in TRAIL_FIGURE_SCRIPTS:
    TASKS[figure_target] = task_figure_group(figure_target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=sorted(TASKS))
    parser.add_argument("--config", help="Experiment config override.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--artifact", help="Devnet summary.json to validate.")
    parser.add_argument(
        "--mode",
        choices=["baseline", "pathobs", "fee_only", "bonus_only", "trail"],
        default="trail",
    )
    parser.add_argument("--modes", default="baseline,pathobs,trail")
    parser.add_argument("--package", help="Local ethereum-package checkout.")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--keep-enclaves", action="store_true")
    parser.add_argument("--seeds")
    parser.add_argument("--variants")
    parser.add_argument("--nodes")
    parser.add_argument("--loads")
    parser.add_argument("--topologies")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    args = parser.parse_args()
    TASKS[args.target](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
