# Hardware runs 5 to 60: 56 repeats of the improved setup, ibm_kingston (2026-10-18 01:03 to 01:05 UTC)

Runs 5 to 60 repeat runs 2 to 4 exactly, to measure how much the result varies from run to run on real hardware.
Together with runs 1 to 4 this makes 60 hardware runs. The summary across all 60 is in `../SUMMARY.md`.

## Result (56 runs, 4,000 shots each)

| Metric                    | Mean   | Sample s.d. | Min    | Max    |
|---------------------------|-------:|------------:|-------:|-------:|
| Share of optimal shots    | 0.778% | 0.145%      | 0.450% | 1.075% |
| Share of valid shots      | 1.71%  | 0.20%       |        |        |
| Optimal among valid shots | 45.5%  | 6.1%        |        |        |
| Found the optimum (-9,000)| 56 of 56 |           |        |        |

- Pooled over the 56 runs: 1,743 optimal shots out of 224,000 (0.778%).
- The spread between runs is what 4,000-shot sampling alone would give. If every run had the same true rate (0.778%),
  the expected s.d. is 0.139%; we measured 0.145%. A chi-square dispersion test gives p = 0.31, so there is no sign of
  extra drift between runs.
- The 7 jobs do not differ from each other (job means 0.73% to 0.80%, one-way ANOVA p = 0.92).
- Runs 2 to 4 (0.692% pooled) are consistent with these runs (two-proportion z = 1.05, p = 0.29). Their very small
  spread (s.d. 0.014%) was luck; with only 3 runs the s.d. is poorly measured.

## Method

- **Same circuit as runs 2 to 4.** Machine ibm_kingston, transpiler seed 172, optimization level 3, angles
  (gamma, beta) = (0.7581, 2.6581), read from run 2's `run.json`. The circuit was compiled once and checked before
  submission: 204 CZ gates, depth 316, and the same 18 physical qubits as run 2 (estimated success probability 0.5456,
  identical to run 2). The script stops if the compiled circuit differs, so these runs are directly comparable.
- **Same options.** SamplerV2, 4,000 shots per run, dynamical decoupling, gate and measurement twirling, and
  max_execution_time 180 s per job.
- **Packing.** Each run is one PUB (one circuit with its own 4,000 shots and its own random twirling instances).
  Eight runs went into each job, giving 7 jobs, all sent in one Batch. The first job was a pilot. Its measured cost
  (12 s for 8 runs) was used to check that the remaining 6 jobs fitted under a 200 s budget before sending them.
- **Queue.** ibm_kingston was operational with 0 jobs waiting at 01:03:20 UTC.
- **Cost.** 84 s of IBM time in total, 12 s per job, or **1.5 s per run**. That is half the 3 s per run of runs 2 to 4,
  because the per-job overhead is shared by 8 runs. Wall time was 128 s including queueing. Fran's account had about
  588 s left before this set, so about 504 s remain this month.

## Assumptions and caveats

- Runs in one job run back to back within seconds, and all 56 runs finished within about 2 minutes. So this set
  measures run-to-run spread under one calibration. It does not measure drift across hours or days. The earlier runs
  2 to 4, about an hour earlier, agree with it.
- "Optimal" means a shot whose QUBO energy equals the proven optimum (-9,000, from an exhaustive search). "Valid"
  means a shot that breaks no constraint, as checked by `ConstraintErrorCorrection.detect_syndrome`. These are the
  same definitions (`score_counts` in `scripts/run_hardware_experiment.py`) used for runs 1 to 4.
- The cost per run is the job's IBM usage divided by its 8 runs. IBM reports usage per job, not per PUB.

## Reproduce

```
pip install qiskit==2.6.0 qiskit-ibm-runtime==0.50.0 dimod scipy matplotlib
set IBM_QUANTUM_TOKEN=<IBM Cloud API key>
set IBM_QUANTUM_CRN=<instance CRN>
python scripts/run_hardware_repeats.py --ref results/hardware/2026-10-18T0005Z_ibm_kingston_T4_w12_improved --plan
python scripts/run_hardware_repeats.py --ref results/hardware/2026-10-18T0005Z_ibm_kingston_T4_w12_improved ^
    --runs 56 --first-run 5 --pubs-per-job 8 --budget-seconds 200
python scripts/summarize_hardware_runs.py results/hardware/<new repeats folder>
```

`--plan` compiles and checks the circuit and the queue without using any QPU time. A rerun on another day may compile
differently if ibm_kingston's calibration has changed. The script then stops rather than run a different circuit.
`summarize_hardware_runs.py` needs no credentials: it reads the saved results and rebuilds `stats.json` and the chart.
Versions: qiskit 2.6.0, qiskit-ibm-runtime 0.50.0, Python 3.13.

IBM job IDs (8 runs each, in order): dba1lmhgv2cc73e67ucg (pilot, runs 5-12), dba1m1424mrc7384ou10, dba1m19gv2cc73e67v3g,
dba1m19gv2cc73e67v40, dba1m1k24mrc7384ou2g, dba1m1gc77vs73826dug, dba1m1s24mrc7384ou4g (runs 53-60).

## Files

- `runs.csv`: one row per run with its job ID, PUB index, IBM seconds, optimal, valid and optimal-among-valid
  shares, optimum found, best and mean energy.
- `counts/run_NN.json`: raw hardware counts for each run (bitstrings in Qiskit order).
- `batch.json`: configuration, circuit check, queue state, per-job usage and every run's metrics.
- `stats.json`: statistics for all 60 runs, the 59 improved runs, runs 2 to 4 and runs 5 to 60, plus the tests above.
- `optimal_share_distribution.png`: histogram of the optimal share over the 59 improved runs, with the spread
  expected from shot noise alone, and run 1 marked.
