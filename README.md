# Tug-Boat-Optimizer

Quantum optimization of tugboat allocation for the Port of Hamburg, built with Qiskit.

A port has a fixed tug fleet. Contract ships must be served; last-minute (spot) ships bring
extra revenue if tugs are free. Large ships can only enter or leave in a short high-tide window,
and stronger wind raises the number of tugs each ship needs. The optimizer chooses which tugs go
to which ship, and which spot ships to accept, so that contracts are honoured with as few tugs as
possible and spot revenue is maximised.

The problem is written as a QUBO (tug uniqueness in the tidal window, wind- and ship-size-dependent
tug demand, crew availability, spot-ship revenue), mapped to an Ising Hamiltonian and solved with
QAOA, with constraint-based error detection and repair of measured bitstrings.

## Status

- Exact solver (dimod) and QAOA agree on small instances (e.g. 4 tugs, 4 ships, 18 qubits).
- QAOA with 2 layers finds the optimum reliably in calm wind; it is weaker in high wind.
- So far run on simulators only, including a simulated noisy IBM backend. Not yet run on a real IBM QPU.

Details and evidence: [docs/FIXES_2026-10-17.md](docs/FIXES_2026-10-17.md),
benchmark output in [results/](results/).

## Run it

Python 3.13+ with `qiskit`, `qiskit-ibm-runtime`, `dimod`, `numpy`, `scipy`, `matplotlib`.

```bash
python scripts/test_qubo.py                                   # exact QUBO solutions (calm, storm, crew fatigue)
python scripts/test_qaoa_sim.py                               # QAOA statevector simulation
python scripts/run_quantum_optimizer.py --tugs 4 --wind 12    # full pipeline, local simulator
python scripts/run_quantum_optimizer.py --tugs 2 --ibm-token <your token>   # IBM Quantum hardware
python scripts/run_qaoa_benchmark.py                          # p=1 vs p=2, calm vs windy
```

`run_quantum_optimizer.py` also takes `--p` (QAOA layers), `--shots`, `--backend` and `--no-qec`.
Never commit an IBM Quantum token to this repository.

## Layout

```
src/tug_optimizer/   qubo_builder.py, qaoa_mapper.py, error_mitigation.py
scripts/             runnable tests, optimizer and benchmark
results/             benchmark results and chart
Data Samples/        Hamburg wind (DWD, Open-Meteo ERA5) and tide (PEGELONLINE St. Pauli) samples
docs/                fix notes and project log
```

## Team

- **Melinoë Elizondo**, Project Lead (PhD Physicist)
- **Rene Borda**, Concept Originator and Market Expert
- **Francesca Dumary**, Software Engineer
- **Ryan Bullock**, Business Strategy

## Acknowledgements

Built at the Florida Qiskit Hackathon: Quantum Horizons, part of Florida Atlantic Hackathon Weekend,
October 17 & 18, 2026. Co-sponsored by the Florida Atlantic University College of Engineering and
Computer Science and the IEEE Palm Beach Section.

Background reading: Jia et al. (2021), "Scheduling Tugboats in a Seaport",
*Transportation Science*, doi:10.1287/trsc.2021.1079.
