# Hardware run 2: leaner circuit + noise-aware angles, ibm_kingston (2026-10-18 00:05 UTC)

Same 4-tug instance as run 1 (`../2026-10-17T2350Z_ibm_fez_T4_w12/`). This run tests whether we can raise the share of
optimal shots on real hardware without changing the problem or the QAOA circuit shape (p = 1, 18 qubits).

## Result

| Metric (4,000 shots each)      | Simulator (run-2 angles) | Hardware run 1 (ibm_fez) | Hardware run 2 (ibm_kingston) |
|--------------------------------|-------------------------:|-------------------------:|------------------------------:|
| Share of optimal shots         | 3.55%                    | 0.30% (12)               | **0.68% (27)**                |
| Share of valid shots           | 7.42%                    | 0.97%                    | **1.47%**                     |
| Optimal among valid shots      | 47.8%                    | 30.8%                    | **45.8%**                     |
| Found the optimum (-9,000)     | yes                      | yes                      | yes                           |
| Mean shot energy               | 30,304                   | 105,097                  | 85,252                        |
| Two-qubit (CZ) gates           | -                        | 237                      | 204                           |
| Transpiled depth               | -                        | 308                      | 316                           |
| QPU time                       | -                        | 3 s                      | 3 s                           |

- The optimal share went up 2.25 times (0.30% to 0.68%). A two-proportion z-test on 12 vs 27 optimal shots out of 4,000
  gives z = 2.4 (p of about 0.016), so the gain is unlikely to be chance. It is still a single run on each side.
- Among valid shots, the hardware now matches the noiseless simulator closely (45.8% vs 47.8%). The remaining gap is mostly
  shots that break a rule.
- Uniform random guessing gives 0.0092% optimal, so run 2 is about 74 times better than random.

## What changed, and what each change contributed

1. **Machine and transpiler seed chosen by estimated success probability (ESP).** ESP is the product of (1 - error) over every
   gate and measurement, using each machine's live calibration. We tried 300 seeds on each of the 3 machines on Fran's account:

   | Machine       | Best seed | CZ  | Depth | ESP    |
   |---------------|----------:|----:|------:|-------:|
   | ibm_fez (run 1 as compiled, seed 1) | 1 | 237 | 308 | 0.4085 |
   | ibm_fez       | 172       | 204 | 318   | 0.4465 |
   | ibm_kingston  | 172       | 204 | 317   | 0.5457 |
   | ibm_marrakesh | 272       | 225 | 300   | 0.3396 |

   Most of the gain comes from the machine: the seed alone lifts ESP by 9% on ibm_fez, and moving to ibm_kingston adds 22%
   more. The theoretical floor is 112 CZ (56 couplings x 2), so qubit routing (SWAPs) still accounts for about 90 of the 204 CZ gates.
2. **Noise-aware angles.** Angles were re-tuned to maximise P(optimal) under a model of the chosen qubits' noise: readout
   error plus T1 decay over the circuit's 13 us, followed by global depolarising at fidelity ESP. The result was
   (gamma, beta) = (0.7581, 2.6581), against (0.7748, 2.6446) in run 1. The model predicted only a 1.36% vs 1.35% gain, so
   **this change made no real difference**. The script uses the new angles only when the model predicts any gain, which it
   narrowly did. The finding is that the hardware noise is close to unbiased, so the noiseless-optimal angles are already right.

Because the machine changed, run 2 also differs from run 1 in calibration and in when it ran. The ESP numbers above are the
best available split of the gain.

## Model check (honest calibration)

The noise model is used to choose between options, not to report results. It predicted 0.82% optimal for run 1, against
0.30% measured, and 1.36% for run 2, against 0.68% measured. So it overestimates by 2 to 3 times, which is typical
because ESP leaves out crosstalk and idle errors. It does rank the two runs in the right order, which is all the selection needs.

## Reproduce

```
pip install qiskit qiskit-ibm-runtime dimod scipy matplotlib
set IBM_QUANTUM_TOKEN=<IBM Cloud API key>
set IBM_QUANTUM_CRN=<instance CRN>
python scripts/run_hardware_improved.py --run1 results/hardware/2026-10-17T2350Z_ibm_fez_T4_w12 --no-submit   # plan only, no QPU time
python scripts/run_hardware_improved.py --run1 results/hardware/2026-10-17T2350Z_ibm_fez_T4_w12
```

The options are `--seeds` (default 300 per machine), `--backends` (default ibm_fez,ibm_kingston,ibm_marrakesh) and `--grid`
(default 48 x 48 for the angle scan). The choices depend on live calibration, so a rerun on another day may pick another
machine or seed. Everything chosen is recorded in `run.json` (`seed_sweep`, `chosen_backend`, `chosen_seed`,
`noise_model_qubits`, `predictions`). IBM job ID: `dba0ql8c77vs738253fg`. Run 1 job ID: `dba0j7vi0auc7391b3tg`.
Versions: qiskit 2.6.0, qiskit-ibm-runtime 0.50.0, Python 3.13.

## Files

- `run.json`: all choices, predictions and metrics for the run-2 simulator, both hardware runs and the run-1 simulator.
- `counts_hardware.json`, `counts_simulator.json`: raw counts for run 2.
- `outcomes.png`: share of shots that are optimal / valid / break a rule, for both simulators and both hardware runs.
- `energy_cdf.png`: cumulative distribution of shot energies for the same four series.

## Next levers, not tried here

- Reduce routing SWAPs with a qubit ordering that follows the chip's heavy-hex lattice. That would save up to about 90 CZ gates.
- Constraint-preserving (XY) mixer, so that shots cannot break the one-tug-per-ship rules.
- Repeated runs to measure the spread between runs.
