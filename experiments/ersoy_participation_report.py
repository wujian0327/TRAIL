#!/usr/bin/env python3
"""Validate a configured Ersoy comparison and export reproducible paper figures."""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import statistics
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/trail-matplotlib")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import t as student_t

from adaptive_participation_report import cohort_inclusion_metrics
from run_experiments import ROOT, expand_runs, load_yaml


LABELS = {"pos": "PoS", "trail_eta0": "Fee-only", "trail": "Full TRAIL",
          "trail_no_ema": "TRAIL, beta=1"}
STYLES = {"pos": ("#6B6B6B", "^", ":"), "trail_eta0": ("#0072B2", "s", "--"),
          "ersoy_fee": ("#009E73", "D", "-."), "trail": ("#D55E00", "o", "-"),
          "trail_no_ema": ("#CC79A7", "v", (0, (3, 1, 1, 1)))}


def read_csv(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"non-finite metric: {value}")
    return value


def interval(values):
    values = list(map(finite, values))
    if len(values) < 2:
        raise ValueError("at least two seeds required for a confidence interval")
    return statistics.mean(values), float(student_t.ppf(.975, len(values)-1)) * statistics.stdev(values) / math.sqrt(len(values))


def summarize(run):
    directory = Path(run["output_dir"])
    read_json = lambda name: json.loads((directory / name).read_text())
    status, summary, saved = (read_json(n) for n in
                              ("runner_status.json", "run_summary.json", "run_config.json"))
    if status["status"] != "ok" or int(summary["completed_epochs"]) != run["max_epochs"]:
        raise ValueError(f"incomplete run: {directory}")
    actual = saved["config"]
    trail = actual["trail_config"]
    protocol = run["protocol_label"]
    if protocol == "ersoy_fee":
        assert trail["reward_policy"] == "ersoy" and trail["eta"] == 0
        assert trail["ersoy_c"] == run["ersoy_c"]
    elif protocol != "pos":
        assert trail["reward_policy"] == "trail"
        assert trail["eta"] == run["eta"] and trail["beta"] == run["beta"]
    assert str(actual["consensus"]).lower() == ("pos" if protocol == "pos" else "trail")
    for key in ("graph_seed", "wallet_seed", "workload_seed", "election_seed", "failure_seed"):
        assert actual[key] == run[key]
    for actual_key, run_key in (("node_num", "node_num"), ("gini", "stake_gini"),
            ("trans_num_per_second", "tx_rate"), ("slot_per_epoch", "slot_per_epoch"),
            ("slot_duration", "slot_duration"), ("transaction_fee", "transaction_fee"),
            ("base_reward", "base_reward"), ("max_epochs", "max_epochs"),
            ("warmup_epochs", "warmup_epochs"), ("time_scale", "time_scale"),
            ("reward_reinvestment_rate", "reward_reinvestment_rate")):
        assert actual[actual_key] == run[run_key]
    assert actual["adaptive_relay"]["enabled"]
    for key in ("irrecoverable_cost", "initial_liquid_balance"):
        if key in run:
            assert actual[key] == run[key]
    for key in ("cost_reference", "cost_median_multiplier", "cost_log_sigma"):
        assert actual["adaptive_relay"][key] == run["adaptive_"+key]
    adaptive = read_csv(directory / "adaptive_relay_metrics.csv")
    epochs = read_csv(directory / "epoch_metrics.csv")
    nodes = read_csv(directory / "node_epoch_metrics.csv")
    end, start = int(run["max_epochs"]), int(run["warmup_epochs"])
    assert [int(row["epoch"]) for row in adaptive] == list(range(end))
    assert [int(row["epoch"]) for row in epochs] == list(range(end))
    assert sum(int(row["included_tx"]) for row in epochs) == int(summary["included_tx"])
    # Independent budget check from accepted block count and transaction count,
    # rather than another call to the simulator's reward-plan implementation.
    gross = sum(finite(row[key]) for row in epochs for key in
                ("total_proposer_reward", "total_relay_reward", "burned_relay_fee"))
    budget = int(summary["block_production_success"])*run["base_reward"] + int(summary["included_tx"])*run["transaction_fee"]
    assert abs(gross-budget) <= end*1.5e-6+1e-8, "reward budget is not conserved"
    assert len(nodes) == run["node_num"] * end
    assert len({(r["epoch"], r["validator_id"]) for r in nodes}) == len(nodes)
    assert all(0 <= int(r["epoch"]) < end for r in nodes)
    for row in adaptive:
        assert 0 <= finite(row["active_stake_share"]) <= 1
    for row in nodes:
        for key in ("economic_stake", "relay_reward", "proposer_reward",
                    "relay_cost_per_forward", "normalized_proposer_weight", "fee_spent"):
            finite(row[key])
    initial = sorted((r for r in nodes if int(r["epoch"]) == 0), key=lambda r: int(r["validator_id"]))
    assert len(initial) == run["node_num"]
    assert sum(r["relay_profile"] == "active" for r in initial) == round(run["node_num"]*run["adaptive_initial_active_fraction"])
    initial_design = [(r["validator_id"], r["economic_stake"], r["relay_profile"],
                       r["relay_cost_per_forward"], r["degree"]) for r in initial]
    pair_fingerprint = hashlib.sha256(json.dumps(initial_design).encode()).hexdigest()
    # graph.json stores each undirected edge once; endpoint orientation varies
    # with hash-map iteration order, so normalize both endpoints and edge order.
    graph_edges = json.loads((directory / "graph.json").read_text())
    graph_fingerprint = hashlib.sha256(json.dumps(sorted(tuple(sorted(edge)) for edge in graph_edges)).encode()).hexdigest()
    by_epoch = defaultdict(list)
    for row in nodes:
        by_epoch[int(row["epoch"])].append(row)
    # Node CSV rewards are rounded to six decimals; reconcile with an explicit
    # rounding bound rather than expecting exact floating-point equality.
    tolerance = run["node_num"] * 0.5e-6 + 1e-6
    for row in epochs:
        batch = by_epoch[int(row["epoch"])]
        assert len(batch) == run["node_num"]
        for node_key, epoch_key in (("relay_reward", "total_relay_reward"),
                                    ("proposer_reward", "total_proposer_reward")):
            assert abs(sum(float(r[node_key]) for r in batch)-float(row[epoch_key])) <= tolerance
        if protocol in ("pos", "trail_eta0", "ersoy_fee"):
            stake = sum(float(r["economic_stake"]) for r in batch)
            assert all(abs(float(r["normalized_proposer_weight"])-float(r["economic_stake"])/stake) < 2e-6 for r in batch)
    generated, included = (read_csv(directory / n) for n in ("generation_samples.csv", "inclusion_samples.csv"))
    inclusion = cohort_inclusion_metrics(generated, included, warmup_epochs=start,
        max_epochs=end, slots_per_epoch=run["slot_per_epoch"], slot_duration_s=run["slot_duration"],
        followup_epochs=run["adaptive_followup_epochs"])
    assert inclusion["cohort_generated_tx"] > 0
    steady = [r for r in nodes if start <= int(r["epoch"]) < end]
    relay = sum(float(r["relay_reward"]) for r in steady)
    proposer = sum(float(r["proposer_reward"]) for r in steady)
    cost = sum(int(r["relay_forward_attempts"])*float(r["relay_cost_per_forward"]) for r in steady)
    # fee_spent is cumulative, unlike the epoch reward columns.
    fee_end = sum(float(r["fee_spent"]) for r in by_epoch[end-1])
    fee_start = sum(float(r["fee_spent"]) for r in by_epoch[start-1]) if start else 0.0
    divisor = len(steady)
    mechanism = (f"Ersoy (C={run['ersoy_c']:g})" if protocol == "ersoy_fee" else LABELS[protocol])
    output = dict(run_id=run["run_id"], protocol=protocol, mechanism=mechanism,
        cost_multiplier=run["adaptive_cost_median_multiplier"], seed=run["seed_value"],
        ersoy_c=run["ersoy_c"] if protocol == "ersoy_fee" else "",
        active_stake_pct=100*statistics.mean(float(r["active_stake_share"]) for r in adaptive[start:]),
        restricted_mean_s=inclusion["restricted_mean_inclusion_latency_s"],
        timeout_adjusted_p95_s=inclusion["timeout_adjusted_p95_latency_s"],
        inclusion_rate=inclusion["inclusion_within_horizon_rate"],
        generated_cohort=inclusion["cohort_generated_tx"],
        relay_reward_per_node_epoch=relay/divisor, proposer_reward_per_node_epoch=proposer/divisor,
        forward_cost_per_node_epoch=cost/divisor, relay_net_per_node_epoch=(relay-cost)/divisor,
        total_net_per_node_epoch=(relay+proposer-cost-(fee_end-fee_start))/divisor,
        git_commit_sha=saved["git_commit_sha"], git_source_diff_sha256=saved["git_source_diff_sha256"],
        initial_design_sha256=pair_fingerprint, duration_seconds=status["duration_seconds"],
        graph_sha256=graph_fingerprint,
        background_workload_hash=summary.get("background_workload_hash", ""),
        generated_tx=int(summary["generated_tx"]),
        raw_source=str(directory.relative_to(ROOT)))
    trajectories = [dict(mechanism=mechanism, protocol=protocol, cost_multiplier=output["cost_multiplier"],
        seed=output["seed"], epoch=int(r["epoch"]), active_stake_pct=100*float(r["active_stake_share"])) for r in adaptive]
    return output, trajectories


METRICS = ("active_stake_pct", "restricted_mean_s", "timeout_adjusted_p95_s", "inclusion_rate",
           "relay_reward_per_node_epoch", "proposer_reward_per_node_epoch", "forward_cost_per_node_epoch",
           "relay_net_per_node_epoch", "total_net_per_node_epoch")


def aggregate(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["mechanism"], row["cost_multiplier"])].append(row)
    summaries = []
    contrasts = []
    for (name, cost), batch in groups.items():
        record = dict(mechanism=name, cost_multiplier=cost, n=len(batch))
        assert len({r["seed"] for r in batch}) == len(batch)
        for metric in METRICS:
            mean, ci = interval(r[metric] for r in batch)
            record[metric+"_mean"], record[metric+"_ci95"] = mean, ci
        summaries.append(record)
    for cost in sorted({r["cost_multiplier"] for r in rows}):
        samples = {name: {r["seed"]: r for r in batch} for (name, c), batch in groups.items() if c == cost}
        if "Full TRAIL" not in samples:
            continue
        for name in samples:
            if name == "Full TRAIL":
                continue
            first, second = samples["Full TRAIL"], samples[name]
            assert set(first) == set(second)
            for metric in METRICS:
                differences = [first[s][metric]-second[s][metric] for s in sorted(first)]
                mean, ci = interval(differences)
                contrasts.append(dict(comparison="Full TRAIL minus "+name, cost_multiplier=cost,
                    metric=metric, n_pairs=len(differences), mean_difference=mean, ci95=ci))
    return summaries, contrasts


def plot(rows, epochs, destination, main_c):
    primary = [r for r in rows if r["protocol"] != "ersoy_fee" or r["ersoy_c"] == main_c]
    if not primary:
        primary = rows
    labels = list(dict.fromkeys(r["mechanism"] for r in primary))
    selected_cost = 2.0 if any(r["cost_multiplier"] == 2 for r in primary) else primary[0]["cost_multiplier"]
    fig, axes = plt.subplots(1, 3, figsize=(12.6, 3.6), constrained_layout=True)
    clipped_active_intervals = False
    for name in labels:
        batch = [r for r in primary if r["mechanism"] == name]
        color, marker, line = STYLES[batch[0]["protocol"]]
        trajectory = defaultdict(list)
        for row in epochs:
            if row["mechanism"] == name and row["cost_multiplier"] == selected_cost:
                trajectory[row["epoch"]].append(row["active_stake_pct"])
        x = sorted(trajectory)
        points = [interval(trajectory[e]) for e in x]
        y, ci = [p[0] for p in points], [p[1] for p in points]
        clipped_active_intervals |= any(mean-half < 0 or mean+half > 100 for mean,half in points)
        axes[0].plot(x,y,color=color,linestyle=line,label=name)
        axes[0].fill_between(x,[a-b for a,b in zip(y,ci)],[a+b for a,b in zip(y,ci)],color=color,alpha=.08)
        for ax, metric in zip(axes[1:], ("active_stake_pct", "restricted_mean_s")):
            costs = sorted({r["cost_multiplier"] for r in batch})
            points = [interval(r[metric] for r in batch if r["cost_multiplier"] == c) for c in costs]
            if metric == "active_stake_pct":
                clipped_active_intervals |= any(mean-half < 0 or mean+half > 100 for mean,half in points)
            offset = (labels.index(name)-(len(labels)-1)/2)*.035
            ax.errorbar([c+offset for c in costs],[p[0] for p in points],yerr=[p[1] for p in points],color=color,
                        marker=marker,linestyle=line,label=name,capsize=3,markerfacecolor="white")
            ax.set_xticks(costs)
    axes[0].set(xlabel="Epoch",ylabel="Active relay stake (%)",title=f"Relay participation, cost {selected_cost:g}x")
    axes[1].set(xlabel="Median relay cost / C_ref",ylabel="Active relay stake (%)",title="Post-warm-up participation")
    axes[2].set(xlabel="Median relay cost / C_ref",ylabel="Restricted mean inclusion time (s)",title="Transaction inclusion")
    for ax in axes:
        ax.grid(axis="y",alpha=.2)
        ax.spines[["top","right"]].set_visible(False)
        ax.legend(fontsize=7)
        ax.set_ylim(bottom=0)
    for ax in axes[:2]:
        ax.set_ylim(0,100)
    n = len({r["seed"] for r in rows})
    fig.suptitle(f"{'Pilot' if n < 20 else 'Comparison'}: {n} seeds per condition; 95% t intervals",fontsize=11)
    if clipped_active_intervals:
        fig.supxlabel("Active-stake intervals outside 0-100% are clipped; exact intervals are in groups.csv.",fontsize=8)
    for extension in ("png","pdf"):
        fig.savefig(destination / f"participation.{extension}",dpi=180)
    plt.close(fig)
    ersoy = [r for r in rows if r["protocol"] == "ersoy_fee" and r["cost_multiplier"] == selected_cost]
    cs = sorted({r["ersoy_c"] for r in ersoy})
    if len(cs) > 1:
        fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2), constrained_layout=True)
        for ax, metric, ylabel in zip(axes, ("active_stake_pct", "restricted_mean_s"),
                                      ("Active relay stake (%)", "Restricted mean inclusion time (s)")):
            points = [interval(r[metric] for r in ersoy if r["ersoy_c"] == c) for c in cs]
            ax.errorbar(cs, [p[0] for p in points], yerr=[p[1] for p in points],
                        color="#009E73",marker="D",capsize=3,markerfacecolor="white")
            ax.set(xlabel="Ersoy C",ylabel=ylabel,xticks=cs)
            ax.set_ylim(bottom=0)
            ax.grid(axis="y",alpha=.2)
        axes[0].set_ylim(0,100)
        fig.suptitle(f"Ersoy sensitivity, cost {selected_cost:g}x; {n} seeds; 95% t intervals",fontsize=10)
        for extension in ("png","pdf"):
            fig.savefig(destination / f"sensitivity.{extension}",dpi=180)
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,required=True,action="append")
    parser.add_argument("--output-name")
    args = parser.parse_args()
    specs = [load_yaml(path) for path in args.config]
    spec = specs[0]
    runs = [run for item in specs for run in expand_runs(item)]
    rows, epochs = [], []
    for run in runs:
        result, trajectory = summarize(run)
        rows.append(result)
        epochs.extend(trajectory)
    assert len({r["run_id"] for r in rows}) == len(rows)
    assert len({(r["git_commit_sha"],r["git_source_diff_sha256"]) for r in rows}) == 1
    workload_variants = {}
    for key, batch in itertools.groupby(sorted(rows,key=lambda r:(r["seed"],r["cost_multiplier"])),key=lambda r:(r["seed"],r["cost_multiplier"])):
        batch = list(batch)
        assert len({r["initial_design_sha256"] for r in batch}) == 1, "paired designs differ"
        assert len({r["graph_sha256"] for r in batch}) == 1, "paired network topologies differ"
        workload_variants[f"seed={key[0]},cost={key[1]}"] = len({r["background_workload_hash"] for r in batch})
    summaries, contrasts = aggregate(rows)
    output_name = args.output_name or (spec["suite"] if len(specs) == 1 else spec["suite"]+"_combined")
    destination = ROOT / "results/processed" / output_name
    destination.mkdir(parents=True,exist_ok=True)
    write_csv(destination / "runs.csv", rows)
    write_csv(destination / "groups.csv", summaries)
    write_csv(destination / "epochs.csv", epochs)
    if contrasts:
        write_csv(destination / "paired_contrasts.csv", contrasts)
    (destination / "audit.json").write_text(json.dumps(dict(expected_runs=len(runs),valid_runs=len(rows),
        git_commit_sha=rows[0]["git_commit_sha"],git_source_diff_sha256=rows[0]["git_source_diff_sha256"],
        config_sha256={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in args.config},
        report_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        workload_hash_variants_per_pair=workload_variants,
        estimator="Common history-only mean-field proposer reward; path-dependent Ersoy fees are averaged",
        adaptation="Shared first-arrival PoS propagation and TRAIL path evidence; Ersoy fee rule only",
        units="Rewards and costs: token per validator per epoch; inclusion: logical seconds",
        rounding_note="Node rewards have 6 decimal places; reconciled within explicit rounding bounds"),indent=2)+"\n")
    plot(rows, epochs, destination, spec["defaults"]["ersoy_c"])
    for row in summaries:
        print(f"{row['mechanism']:20s} cost={row['cost_multiplier']:g} n={row['n']} "
              f"active={row['active_stake_pct_mean']:.2f}% "
              f"inclusion={row['restricted_mean_s_mean']:.3f}s "
              f"relay_net={row['relay_net_per_node_epoch_mean']:.8f}")
    print(destination)


if __name__ == "__main__":
    main()
