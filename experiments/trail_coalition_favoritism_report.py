#!/usr/bin/env python3
"""Validate Fig. 4(b) evidence and pair actual modeled income by seed.

Payment totals come from successful deductions, NOT the legacy fee/net-income
columns. Rewards use accrual at inclusion; pending settlement is not added a
second time. Operating/verification costs are not modeled by this study.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from run_experiments import ROOT, expand_runs, load_yaml


def json_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def graph_identity(graph: list) -> tuple:
    # graph.json emits one arbitrary orientation per undirected edge.
    return tuple(sorted({tuple(sorted(edge)) for edge in graph}))


def validate_observed_receipts(audits: list[dict]) -> int:
    """Reject different SIGNED incoming prefixes observed within one run.

    The legacy conflict counter counts attempts rejected before signing. It is
    not a count of conflicting proofs. Terminal receiving signatures are part
    of the aggregate certificate already verified by the Rust builder; this
    additional check covers explicit receiving proofs in retained prefixes.
    """
    commitments = {}
    for audit in audits:
        for prefix in audit["candidate_prefixes"]:
            for hop in prefix["paths"]:
                if hop["receiver_signature"] is None:
                    continue
                key = (prefix["transaction"]["hash"], prefix["chain_id"], prefix["epoch"], hop["to"])
                commitment = (hop["from"], hop["to"], hop["prefix"])
                if key in commitments and commitments[key] != commitment:
                    raise ValueError(f"conflicting signed incoming prefixes in recorded evidence: {key}")
                commitments[key] = commitment
    return len(commitments)


def aggregate_run(run: dict) -> dict:
    directory = Path(run["output_dir"])
    summary = json.loads((directory / "run_summary.json").read_text())
    if summary["completed_epochs"] != run["max_epochs"]:
        raise ValueError(f"incomplete run: {directory}")
    if summary.get("block_production_failed", 0):
        raise ValueError(f"failed block production in online-only study: {directory}")
    rows = json_lines(directory / "coalition_epoch_economics.jsonl")
    if [r["epoch"] for r in rows] != list(range(run["max_epochs"])):
        raise ValueError(f"missing or duplicated epoch economics: {directory}")
    if any(not math.isfinite(value) for r in rows for value in r.values()
           if isinstance(value, (int, float))):
        raise ValueError(f"nonfinite economic measurement: {directory}")
    payments = json.loads((directory / "coalition_actual_payments.json").read_text())
    if any(not math.isfinite(value) or value < 0 for value in payments.values()):
        raise ValueError(f"invalid actual payment: {directory}")
    if any(int(epoch) not in range(run["max_epochs"]) for epoch in payments):
        raise ValueError(f"payment outside measurement timeline: {directory}")
    audits = json_lines(directory / "coalition_path_selections.jsonl")
    seen = set()
    for a in audits:
        key = a["tx_hash"]
        candidates = a["candidate_prefixes"]
        if key in seen or a["candidate_count"] != len(candidates):
            raise ValueError(f"duplicate transaction or incorrect candidate count: {directory}")
        seen.add(key)
        paths = []
        for p in candidates:
            sequence = [p["transaction"]["from"]] + [h["to"] for h in p["paths"]]
            if (p["transaction"]["hash"] != key or p["epoch"] != a["epoch"]
                    or len(sequence) != len(set(sequence)) or sequence[-1] != a["proposer"]
                    or p["paths"][-1]["receiver_signature"] is not None
                    or any(not h["sender_signature"] for h in p["paths"])
                    or any(not h["receiver_signature"] for h in p["paths"][:-1])):
                raise ValueError(f"invalid candidate structure: {directory}")
            paths.append(tuple(sequence))
        if len(paths) != len(set(paths)) or tuple(a["selected_path"]) not in paths:
            raise ValueError(f"duplicate or invented selected path: {directory}")
        certificate = a["selected_certificate"]
        if (certificate["epoch"] != a["epoch"] or not certificate["signature"]
                or certificate["paths"] + [a["proposer"]] != a["selected_path"]):
            raise ValueError(f"selected certificate does not match the audited path: {directory}")
        expected = a["favoritism_path"] if a["applied_rule"] == "favoritism" else a["honest_path"]
        if (a["selected_path"] != expected
                or a["effective_change"] != (a["selected_path"] != a["honest_path"])
                or a["would_change"] != (a["honest_path"] != a["favoritism_path"])):
            raise ValueError(f"inconsistent selector outcome: {directory}")
        if a["eligible"] != (a["coalition_proposer"] and len(paths) >= 2):
            raise ValueError(f"incorrect eligibility flag: {directory}")
        if a["effective_change"] and not (a["phase"] == "attack" and a["coalition_proposer"]
                                             and a["applied_rule"] == "favoritism"):
            raise ValueError(f"favoritism outside allowed phase/role: {directory}")
        if a["favoritism_coalition_relay_reward"] + 1e-15 < a["honest_coalition_relay_reward"]:
            raise ValueError(f"selector does not maximize local coalition reward: {directory}")
    with (directory / "epoch_metrics.csv").open() as handle:
        epoch_metrics = list(csv.DictReader(handle))
    observed_receipts = validate_observed_receipts(audits)
    rejected_receipt_attempts = sum(int(r["conflicting_receipt_count"]) for r in epoch_metrics)
    if any(r["proposer_weight_share"] > r["theoretical_weight_bound"] + 1e-9 for r in rows):
        raise ValueError(f"proposer probability exceeds stake envelope: {directory}")
    result = {k: run[k] for k in ["run_id", "seed_value", "adversary_stake_fraction",
                                 "adversary_placement", "coalition_path_selection"]}
    result["coalition_identity_hash"] = summary["coalition_identity_hash"]
    result["background_workload_hash"] = summary["background_workload_hash"]
    result["graph_identity"] = graph_identity(json.loads((directory / "graph.json").read_text()))
    recorded_config = json.loads((directory / "run_config.json").read_text())
    result["config"] = recorded_config["config"]
    result["source_identity"] = (recorded_config["git_commit_sha"], recorded_config["git_source_diff_sha256"])
    with (directory / "proposer_duties.csv").open() as handle:
        first_duty = next(csv.DictReader(handle), None)
    if first_duty is None:
        raise ValueError(f"no proposer duties: {directory}")
    result["initial_proposer_address"] = first_duty["proposer_address"]
    stake_path = directory / "initial_stakes.json"
    if stake_path.exists():
        initial_stakes = json.loads(stake_path.read_text())
    else:
        # Compatibility with the first pilot, which retained only the first
        # epoch's unchanged economic stake snapshot (six decimal places).
        with (directory / "node_epoch_metrics.csv").open() as handle:
            initial_stakes = {"validator-index:" + row["validator_id"]: float(row["economic_stake"])
                              for row in csv.DictReader(handle) if int(row["epoch"]) == 0}
    if not initial_stakes or any(not math.isfinite(stake) or stake < 0 for stake in initial_stakes.values()):
        raise ValueError(f"invalid initial stake snapshot: {directory}")
    result["initial_stake_identity"] = tuple(sorted(initial_stakes.items()))
    result["actual_stake_share"] = rows[0]["actual_stake_share"]
    result["rejected_receipt_attempts"] = rejected_receipt_attempts
    result["observed_consistent_signed_receipts"] = observed_receipts
    result["audited_transactions"] = len(audits)
    result["multi_candidate_transactions"] = sum(a["candidate_count"] >= 2 for a in audits)
    result["potential_selection_changes_all_roles_phases"] = sum(a["would_change"] for a in audits)
    result["rejected_candidates"] = sum(r["rejected_candidates"] for r in rows)
    result["capped_candidates"] = sum(r["capped_candidates"] for r in rows)
    window_path = directory / "coalition_windows.jsonl"
    windows = json_lines(window_path) if window_path.exists() else []
    for window in windows:
        if (window.get("deadline_ms") is not None and window.get("collection_started_ms") is not None
                and not math.isclose(window["deadline_ms"] - window["collection_started_ms"],
                                     window["collection_ms"], rel_tol=0, abs_tol=1e-6)):
            raise ValueError(f"collection window was consumed by preparation: {directory}")
        batch = window["generation"]
        if batch["generated"] > batch["enqueued"] or batch["rejected"] != batch["sampled"] - batch["generated"]:
            raise ValueError(f"generation acknowledgement mismatch: {directory}")
    result["generation_sampled"] = sum(w["generation"]["sampled"] for w in windows)
    result["generation_acknowledged"] = sum(w["generation"]["generated"] for w in windows)
    result["generation_rejected"] = sum(w["generation"]["rejected"] for w in windows)
    arrivals_path = directory / "coalition_candidate_arrivals.jsonl"
    arrivals = json_lines(arrivals_path) if arrivals_path.exists() else []
    for disposition in ["accepted", "duplicate", "late_before_verification", "verification_deadline", "late_after_close"]:
        result["arrivals_" + disposition] = sum(a["disposition"] == disposition for a in arrivals)
    # The terminal ledger also captures payments processed after an epoch's
    # intermediate snapshot. Never infer paid costs from submitted requests.
    for phase, phases in [("warmup", {"warmup"}), ("attack", {"attack"}),
                          ("post", {"post"}), ("total", {"attack", "post"})]:
        selected = [r for r in rows if r["phase"] in phases]
        cost = sum(payments.get(str(r["epoch"]), 0.0) for r in selected)
        for field in ["relay_reward", "proposer_reward", "raw_contribution", "honest_relay_reward"]:
            result[f"{phase}_{field}"] = sum(r[field] for r in selected)
        result[f"{phase}_transaction_cost"] = cost
        result[f"{phase}_modeled_net_income"] = (result[f"{phase}_relay_reward"]
                                                  + result[f"{phase}_proposer_reward"] - cost)
        result[f"{phase}_proposer_weight_share"] = statistics.mean(r["proposer_weight_share"] for r in selected)
        result[f"{phase}_next_epoch_expected_base_proposer_reward"] = sum(
            r["next_epoch_expected_base_proposer_reward"] for r in selected)
        relevant = [a for a in audits if a["phase"] in phases and a["coalition_proposer"]]
        result[f"{phase}_eligible"] = sum(a["eligible"] for a in relevant)
        result[f"{phase}_effective"] = sum(a["effective_change"] for a in relevant)
        result[f"{phase}_local_relay_gain"] = sum(a["favoritism_coalition_relay_reward"]
                                                   - a["honest_coalition_relay_reward"] for a in relevant)
        result[f"{phase}_local_raw_gain"] = sum(a["favoritism_coalition_raw_contribution"]
                                                 - a["honest_coalition_raw_contribution"] for a in relevant)
        result[f"{phase}_local_honest_relay_loss"] = sum(a["local_honest_relay_reward_loss"] for a in relevant)
    return result


def pair_input_mismatches(honest: dict, favoritism: dict) -> list[str]:
    fields = ["coalition_identity_hash", "graph_identity", "source_identity"]
    # Individual realizations and election draws need not match. The user
    # requires the same topology and each validator's initial economic stake.
    mismatches = [field for field in fields if honest[field] != favoritism[field]]
    h_stake, f_stake = dict(honest["initial_stake_identity"]), dict(favoritism["initial_stake_identity"])
    if h_stake.keys() != f_stake.keys() or any(
            not math.isclose(h_stake[key], f_stake[key], rel_tol=0, abs_tol=1e-12) for key in h_stake):
        mismatches.append("initial_stake_identity")
    # Identical coalition members/stakes can sum in different HashSet orders.
    # Preserve exact identity checks above; tolerate only floating-point noise.
    if not math.isclose(honest["actual_stake_share"], favoritism["actual_stake_share"],
                        rel_tol=0, abs_tol=1e-12):
        mismatches.append("actual_stake_share")
    def external_config(run):
        return {k: v for k, v in run["config"].items()
                if k not in {"run_id", "output_dir", "metrics_prefix", "coalition_path_selection"}
                and not k.endswith("_seed")}
    if external_config(honest) != external_config(favoritism):
        mismatches.append("protocol_configuration")
    return mismatches


def pair_runs(runs: list[dict]) -> list[dict]:
    groups = defaultdict(dict)
    for run in runs:
        key = (run["seed_value"], run["adversary_stake_fraction"], run["adversary_placement"])
        mode = run["coalition_path_selection"]
        if mode in groups[key]:
            raise ValueError(f"duplicate paired arm: {key}")
        groups[key][mode] = run
    pairs = []
    for (seed, stake, placement), arms in sorted(groups.items()):
        if set(arms) != {"honest", "favoritism"}:
            raise ValueError("each seed/scenario requires both selection arms")
        h, f = arms["honest"], arms["favoritism"]
        mismatches = pair_input_mismatches(h, f)
        if mismatches:
            raise ValueError(f"pair mismatch in {', '.join(mismatches)}: "
                             f"seed={seed}, stake={stake}, placement={placement}")
        if not f["attack_eligible"]:
            raise ValueError(f"no genuine multi-path attack opportunity: seed={seed}, stake={stake}, placement={placement}")
        row = dict(seed_value=seed, target_stake=stake, placement=placement,
                   actual_stake_share=f["actual_stake_share"], eligible=f["attack_eligible"],
                   effective=f["attack_effective"],
                   local_relay_gain=f["attack_local_relay_gain"],
                   local_raw_gain=f["attack_local_raw_gain"],
                   local_honest_relay_loss=f["attack_local_honest_relay_loss"],
                   rejected_candidates=h["rejected_candidates"] + f["rejected_candidates"],
                   capped_candidates=h["capped_candidates"] + f["capped_candidates"])
        row["initial_proposer_matched"] = h["initial_proposer_address"] == f["initial_proposer_address"]
        row["background_workload_matched"] = h["background_workload_hash"] == f["background_workload_hash"]
        row["effective_fraction"] = row["effective"] / row["eligible"]
        for phase in ["warmup", "attack", "post", "total"]:
            for metric in ["relay_reward", "proposer_reward", "transaction_cost", "modeled_net_income",
                           "raw_contribution", "proposer_weight_share", "next_epoch_expected_base_proposer_reward"]:
                row[f"{phase}_delta_{metric}"] = f[f"{phase}_{metric}"] - h[f"{phase}_{metric}"]
            row[f"{phase}_honest_relay_loss"] = h[f"{phase}_honest_relay_reward"] - f[f"{phase}_honest_relay_reward"]
        pairs.append(row)
    return pairs


def confidence_interval(values: list[float]) -> tuple[float, float]:
    if len(values) not in (3, 20):
        raise ValueError("the defined pilot/main confidence intervals require 3 or 20 paired seeds")
    critical = {3: 4.302652729696142, 20: 2.093024054408263}[len(values)]
    return statistics.mean(values), critical * statistics.stdev(values) / math.sqrt(len(values))


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    spec = load_yaml(args.config)
    runs = [aggregate_run(run) for run in expand_runs(spec)]
    pairs = pair_runs(runs)
    grouped = defaultdict(list)
    for pair in pairs:
        grouped[(pair["target_stake"], pair["placement"])].append(pair)
    points = []
    for (stake, placement), group in sorted(grouped.items()):
        if {r["seed_value"] for r in group} != set(spec["seeds"]):
            raise ValueError("missing paired seeds")
        if not sum(r["effective"] for r in group):
            raise ValueError(f"no effective favoritism at stake={stake}, placement={placement}; diagnose before plotting")
        mean, half_width = confidence_interval([r["total_delta_modeled_net_income"] for r in group])
        points.append(dict(target_stake=stake, placement=placement, n=len(group),
                           actual_stake_mean=statistics.mean(r["actual_stake_share"] for r in group),
                           mean=mean, ci95=half_width, eligible=sum(r["eligible"] for r in group),
                           effective=sum(r["effective"] for r in group)))
    directory = args.output_dir or ROOT / "results" / "processed" / spec["suite"]
    directory.mkdir(parents=True, exist_ok=True)
    write_csv(directory / "paired.csv", pairs)
    write_csv(directory / "points.csv", points)
    exported_runs = [{k: v for k, v in r.items() if k not in {"config", "graph_identity", "initial_stake_identity"}} for r in runs]
    write_csv(directory / "runs.csv", exported_runs)
    (directory / "report.md").write_text(
        "# Coalition path favoritism\n\n"
        "Income is modeled incremental net income over attack + post epochs (10–79). "
        "Rewards accrue at inclusion; transaction costs come from actual deductions. "
        "Extra verification/operating costs are not modeled. Expected base proposer "
        "rewards are diagnostics based on next-epoch weights, and are never added to actual income.\n\n"
        "Seed derivation and transaction identity logic are unchanged. Pairing checks "
        "topology, per-validator initial stake, coalition identity and protocol parameters; "
        "background/election realizations may differ and are diagnostic only. "
        "Asynchronous delivery remains a source of variation. Candidate signatures and "
        "selected certificates are verified in Rust before inclusion.\n\n"
        "The collection window is an opt-in terminal-receiver extension shared by both arms. "
        "This result covers favoritism under this extension; it does not establish "
        "selective-withholding resistance or all-cost attack unprofitability.\n\n"
        + "\n".join(f"- {p['placement']}, target stake {p['target_stake']:.0%}: "
                    f"Δincome={p['mean']:.9g} ± {p['ci95']:.9g} token (95% CI), "
                    f"n={p['n']}, eligible={p['eligible']}, changed={p['effective']}"
                    for p in points) + "\n")
    print(directory)


if __name__ == "__main__":
    main()
