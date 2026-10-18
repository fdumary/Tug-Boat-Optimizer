# Hardware run: 4 tugs, 12 kn wind, ibm_fez (2026-10-17 23:50 UTC)

The first real IBM quantum hardware run of the tug-allocation QAOA, compared shot for shot with the
noiseless simulator on the same circuit.

## Result

| Metric (4,000 shots each)        | Simulator (noiseless) | ibm_fez (hardware) | Uniform random guess |
|----------------------------------|----------------------:|-------------------:|---------------------:|
| Share of optimal shots           | 3.33% (133)           | 0.30% (12)         | 0.0092%              |
| Share of valid shots             | 8.12%                 | 0.97%              | 0.0198%              |
| Found the optimum                | yes                   | yes                | -                    |
| Best energy (optimum = -9,000)   | -9,000                | -9,000             | -                    |
| Mean shot energy                 | 32,236                | 105,097            | -                    |
| Distinct bitstrings              | 1,830                 | 3,748              | -                    |

- The hardware found the optimal schedule: 3 tugs used, both spot ships accepted, and the contract container served.
- Noise reduces the optimal share about 11 times (3.33% to 0.30%). Even so, the hardware lands on an optimal schedule about 33 times
  more often than uniform random guessing (24 optimal states out of 2^18 = 262,144), and on a valid one about 49 times more often.
- The exact simulator probability of an optimal shot is 3.46%. The 3.33% sampled value agrees within sampling error.

Graphs: `outcomes.png` (share of shots that are optimal / valid but not optimal / break a rule) and
`energy_cdf.png` (cumulative distribution of shot energies, simulator vs hardware).

## What ran

- Instance: 4 tugs, 4 ships (2 contract, 2 spot), wind 12 kn, 18 qubits (4 per tug + 2). Demands: container 1,
  feeder 0, bulk carrier 1, tanker 1. Built by `build_instance()` in `scripts/run_hardware_experiment.py`.
- QAOA p = 1. Angles tuned locally in the simulator with CVaR(alpha = 0.1), 8 starts and 100 COBYLA iterations (20 s):
  gamma = 0.7748, beta = 2.6446. The hardware ran the same angles, so the only difference between the two columns is the device.
- Circuit: 25 layers deep before transpiling. Transpiled for ibm_fez (156-qubit Heron, CZ gates) at optimization level 3
  with seed 1: depth 308, 237 CZ gates, 488 SX, 297 RZ.
- Sampler: Qiskit Runtime SamplerV2 with dynamical decoupling plus gate and measurement twirling, and 4,000 shots.
  `max_execution_time = 180 s` was the project's 3-minute cap.
- Backend: picked by `least_busy` among machines with at least 18 qubits. IBM job ID `dba0j7vi0auc7391b3tg`.
- Cost: 3 s of QPU time (IBM's usage figure) and 10 s wall time including the queue. The account was Fran's (IBM open plan, 600 s/month).

## How metrics are computed

`score_counts()` scores every shot with the same code for both runs. Energy is the QUBO energy (`build_bqm()`).
A shot is optimal if its energy is no more than the exact optimum from `dimod.ExactSolver`. It is valid if
`ConstraintErrorCorrection.detect_syndrome` reports no broken constraint. The random-guess column counts the optimal and
valid states among all 2^18 bitstrings, enumerated with ExactSolver.

## Files

- `run.json`: all metrics, angles, circuit stats, job ID and QPU usage.
- `counts_hardware.json`, `counts_simulator.json`: raw counts, as Qiskit bitstrings (variable i = character n-1-i).
- `outcomes.png`, `energy_cdf.png`: the two comparison graphs.

## Reproduce

```
pip install qiskit qiskit-ibm-runtime dimod scipy matplotlib
set IBM_QUANTUM_TOKEN=<IBM Cloud API key>     (PowerShell: $env:IBM_QUANTUM_TOKEN="...")
set IBM_QUANTUM_CRN=<instance CRN>
python scripts/run_hardware_experiment.py --tugs 4 --wind 12
```

Versions used: qiskit 2.6.0, qiskit-ibm-runtime 0.50.0, Python 3.13 (Linux).
A rerun will tune the same angles, because the tuning is deterministic. The hardware counts will differ from run to run
because of noise and calibration drift, and `least_busy` may pick another machine (use `--backend ibm_fez` to fix it).
Credentials are read only from the environment and are never written to any output file.
The network needs to reach quantum.cloud.ibm.com, iam.cloud.ibm.com, api.global-search-tagging.cloud.ibm.com and
globalcatalog.cloud.ibm.com.

## Caveats

- This is one run. The spread between hardware runs is not measured yet.
- SamplerV2 printed a deprecation warning in qiskit-ibm-runtime 0.50.0. It still works, but a future version will need the new
  client-side Sampler.
