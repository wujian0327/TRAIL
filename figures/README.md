# TRAIL paper figures

```bash
# All maintained figure groups
python scripts/task.py trail-figures

# Individual groups
python scripts/task.py trail-security-figures
python scripts/task.py trail-fairness-figures
python scripts/task.py trail-participation-figures
python scripts/task.py trail-outage-figures
python scripts/task.py trail-devnet-figures
```

The maintained outputs are stored only in `trail_security`, `trail_fairness`,
`trail_participation`, `trail_outage`, and `trail_devnet`. Raw and processed
experiment artifacts remain under `results/` and are not modified by plotting.
