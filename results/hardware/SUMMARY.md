# IBM hardware runs: summary (4 runs, 2026-10-17/18)

All runs use the same instance: 4 tugs, 4 ships (2 contract, 2 spot), 12 kn wind, 18 qubits, and QAOA with p = 1.
Each run took 4,000 shots and 3 s of QPU time under a 180 s cap, on Fran's IBM account (open plan).
Run 1 used `scripts/run_hardware_experiment.py`. Runs 2 to 4 used `scripts/run_hardware_improved.py`, which picks the machine and
transpiler seed by estimated success probability. Runs 2 to 4 are repeats of the same configuration. Per-run details are in each folder's `run.json` (and README for runs 1 and 2).

| Run | Folder | Machine | IBM job | CZ gates | Optimal shots | Valid shots | Optimal among valid | Found optimum |
|----:|--------|---------|---------|---------:|--------------:|------------:|--------------------:|:-------------:|
| 1 | `2026-10-17T2350Z_ibm_fez_T4_w12` | ibm_fez | dba0j7vi0auc7391b3tg | 237 | 0.300% | 0.975% | 30.8% | yes |
| 2 | `2026-10-18T0005Z_ibm_kingston_T4_w12_improved` | ibm_kingston | dba0ql8c77vs738253fg | 204 | 0.675% | 1.475% | 45.8% | yes |
| 3 | `2026-10-18T0016Z_ibm_kingston_T4_w12_improved` | ibm_kingston | dba0veni0auc7391blug | 204 | 0.700% | 1.475% | 47.5% | yes |
| 4 | `2026-10-18T0019Z_ibm_kingston_T4_w12_improved` | ibm_kingston | dba1168c77vs73825bsg | 204 | 0.700% | 1.550% | 45.2% | yes |

Reference values:
- Noiseless simulator, same circuit: 3.46% optimal (exact, run-1 angles) and 3.44% (exact, run 2-4 angles). Valid shots are about 7.4 to 8.1% when sampled.
- Uniform random guessing: 24 optimal states out of 2^18 = 0.0092%. Valid: 52 states = 0.0198%.
- Classical baseline: an exhaustive search (`dimod.ExactSolver`) over all 262,144 assignments returns the proven optimum (energy -9,000) on a laptop in seconds.

Statistics:
- Improved setup (runs 2 to 4): optimal 0.692% ± 0.014 (mean ± sample s.d., n = 3). Valid 1.50% ± 0.04. Optimal among valid 46.1% ± 1.2.
- Pooled, the improved setup gave 83 optimal shots out of 12,000 (0.69%), against 12 out of 4,000 (0.30%) for run 1. A two-proportion
  z-test gives z = 2.8 (p ≈ 0.005).
- The improved setup is 2.3 times run 1, about 75 times uniform random, and about one fifth of the noiseless simulator.

Caveat: runs 2 to 4 moved to a different machine (ibm_kingston). Part of the gain is the better machine, not the compilation itself.
The ESP breakdown is in run 2's README.
