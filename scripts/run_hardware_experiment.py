"""
Hardware vs Simulator Experiment
================================
One QAOA run on an IBM quantum computer, compared with the noiseless simulator on the
exact same circuit (same instance, same tuned angles, same number of shots).

  1. Builds the tug instance and tunes (gamma, beta) in the local simulator (CVaR + multi-start).
  2. Simulator reference: exact probabilities plus `shots` sampled shots.
  3. Transpiles for the IBM backend and submits ONE SamplerV2 job, capped by
     max_execution_time (default 180 s of IBM execution time; queue time is not counted).
  4. Scores every shot from both runs with the same metrics and writes a results folder:
       results/hardware/<UTC time>_<backend>_T<tugs>_w<wind>/
         run.json            metrics for simulator and hardware, circuit stats, job id, QPU usage
         counts_hardware.json, counts_simulator.json
         outcomes.png        share of shots: optimal / valid but not optimal / invalid
         energy_cdf.png      cumulative distribution of shot energies, simulator vs hardware

Credentials are read from environment variables only and are never written anywhere:
  IBM_QUANTUM_TOKEN   IBM Cloud API key
  IBM_QUANTUM_CRN     instance CRN

Usage:
  python scripts/run_hardware_experiment.py --tugs 4 --wind 12            # real IBM backend
  python scripts/run_hardware_experiment.py --tugs 4 --wind 12 --fake     # dry run on FakeBrisbane (no account)
"""

import sys
import os
import json
import time
import argparse
from datetime import datetime, timezone

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import dimod
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from tug_optimizer import Ship, Tug, TugOptimizationQUBO, QAOASimulator, ConstraintErrorCorrection


def build_instance(T: int, wind: float) -> TugOptimizationQUBO:
    # Same fleet and ships as run_quantum_optimizer.py and the simulator benchmark
    tugs = [Tug(id=i, name=f"Tug-{i+1:02d}") for i in range(T)]
    ships = [
        Ship(id=0, name="Contract Container (250m)", loa_meters=250.0, is_contract=True),
        Ship(id=1, name="Contract Feeder (180m)", loa_meters=180.0, is_contract=True),
        Ship(id=2, name="Spot Bulk Carrier (220m)", loa_meters=220.0, is_contract=False, spot_revenue=5000.0),
        Ship(id=3, name="Spot Tanker (280m)", loa_meters=280.0, is_contract=False, spot_revenue=5500.0),
    ]
    return TugOptimizationQUBO(tugs, ships, wind_speed_knots=wind)


def score_counts(optimizer: TugOptimizationQUBO, counts: dict, optimum: float) -> dict:
    """
    Same metrics for any set of shots. Bitstrings are Qiskit order (variable i = char n-1-i).
    """
    bqm = optimizer.build_bqm()
    checker = ConstraintErrorCorrection(optimizer)
    n = optimizer.num_variables
    shots = sum(counts.values())
    energies, weights, valid = [], [], []
    for bitstring, c in counts.items():
        sample = {v: int(bitstring[n - 1 - i]) for i, v in enumerate(optimizer.var_names)}
        energies.append(bqm.energy(sample))
        weights.append(c)
        valid.append(checker.detect_syndrome(sample)["is_valid"])
    energies, weights, valid = np.array(energies), np.array(weights), np.array(valid)
    is_opt = energies <= optimum + 1e-6
    best = int(np.argmin(energies))
    best_bits = list(counts.keys())[best]
    best_sample = {v: int(best_bits[n - 1 - i]) for i, v in enumerate(optimizer.var_names)}
    return {
        "shots": int(shots),
        "distinct_bitstrings": int(len(counts)),
        "share_optimal": float(weights[is_opt].sum() / shots),
        "share_valid": float(weights[valid].sum() / shots),
        "found_optimum": bool(is_opt.any()),
        "best_energy": float(energies[best]),
        "mean_energy": float((energies * weights).sum() / shots),
        "best_schedule": optimizer.decode_solution(best_sample),
        "_energies": energies.tolist(),
        "_weights": weights.tolist(),
        "_valid": valid.tolist(),
    }


def make_plots(out_dir: str, sim: dict, hw: dict, optimum: float, title: str, hw_label: str):
    # 1. Share of shots by outcome class
    cats = ["Optimal schedule", "Valid, not optimal", "Breaks a rule"]
    def shares(m):
        return [100 * m["share_optimal"], 100 * (m["share_valid"] - m["share_optimal"]), 100 * (1 - m["share_valid"])]
    x = np.arange(len(cats))
    fig, ax = plt.subplots(figsize=(8, 4.5))
    b1 = ax.bar(x - 0.2, shares(sim), 0.4, label="Simulator (noiseless)")
    b2 = ax.bar(x + 0.2, shares(hw), 0.4, label=hw_label)
    ax.bar_label(b1, fmt="%.2f%%", fontsize=8)
    ax.bar_label(b2, fmt="%.2f%%", fontsize=8)
    ax.set_xticks(x, cats)
    ax.set_ylabel("Share of shots (%)")
    ax.set_title(title)
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.6)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "outcomes.png"), dpi=150)
    plt.close()

    # 2. Cumulative distribution of shot energies
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for m, label in ((sim, "Simulator (noiseless)"), (hw, hw_label)):
        e = np.array(m["_energies"]); w = np.array(m["_weights"], dtype=float)
        order = np.argsort(e)
        ax.step(e[order], np.cumsum(w[order]) / w.sum() * 100, where="post", label=label)
    ax.axvline(optimum, color="gray", linestyle=":", label=f"Optimum ({optimum:,.0f})")
    ax.set_xscale("symlog", linthresh=10000)
    ax.set_xlabel("QUBO energy of shot (lower is better)")
    ax.set_ylabel("Shots at or below this energy (%)")
    ax.set_title(title)
    ax.legend()
    ax.grid(linestyle="--", alpha=0.6)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "energy_cdf.png"), dpi=150)
    plt.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tugs", type=int, default=4)
    ap.add_argument("--wind", type=float, default=12.0)
    ap.add_argument("--p", type=int, default=1, help="QAOA layers (default 1: shallowest circuit)")
    ap.add_argument("--shots", type=int, default=4000)
    ap.add_argument("--backend", type=str, default=None, help="IBM backend name (default: least busy)")
    ap.add_argument("--max-seconds", type=int, default=180, help="Cap on IBM execution time (default 180)")
    ap.add_argument("--fake", action="store_true", help="Dry run on FakeBrisbane, no account needed")
    args = ap.parse_args()

    optimizer = build_instance(args.tugs, args.wind)
    n = optimizer.num_variables
    optimum = float(dimod.ExactSolver().sample(optimizer.build_bqm()).first.energy) if n <= 22 else None
    print(f"Instance: {args.tugs} tugs, wind {args.wind} kn, {n} qubits, optimum energy {optimum}")

    # 1. Tune angles locally
    t0 = time.time()
    sim = QAOASimulator(optimizer, p_layers=args.p, cvar_alpha=0.1)
    params, _ = sim.optimize_parameters(max_iter=100, n_starts=8)
    tune_s = time.time() - t0
    print(f"Tuned angles in {tune_s:.1f}s: {np.round(params, 4).tolist()}")

    # 2. Simulator reference on the same circuit
    probs = np.abs(sim.statevector_array(params)) ** 2
    sim_counts = sim.sample_counts(params, shots=args.shots, seed=7)
    sim_m = score_counts(optimizer, sim_counts, optimum)
    sim_m["exact_p_optimal_per_shot"] = float(probs[sim.diag_E <= sim.diag_E.min() + 1e-9].sum())

    # 3. Hardware
    from qiskit_ibm_runtime import SamplerV2 as Sampler
    if args.fake:
        from qiskit_ibm_runtime.fake_provider import FakeBrisbane
        backend = FakeBrisbane()
    else:
        from qiskit_ibm_runtime import QiskitRuntimeService
        token, crn = os.environ.get("IBM_QUANTUM_TOKEN"), os.environ.get("IBM_QUANTUM_CRN")
        if not token or not crn:
            sys.exit("Set IBM_QUANTUM_TOKEN and IBM_QUANTUM_CRN (or use --fake).")
        service = QiskitRuntimeService(channel="ibm_quantum_platform", token=token, instance=crn)
        backend = service.backend(args.backend) if args.backend else service.least_busy(
            operational=True, simulator=False, min_num_qubits=n)
    print(f"Backend: {backend.name}")

    qc = sim.bind_parameters(params)
    qc.measure_all()
    tqc = generate_preset_pass_manager(backend=backend, optimization_level=3, seed_transpiler=1).run(qc)
    ops = tqc.count_ops()
    two_q = sum(v for k, v in ops.items() if k in ("ecr", "cz", "cx", "rzz"))
    print(f"Transpiled: depth {tqc.depth()}, two-qubit gates {two_q}")

    sampler = Sampler(mode=backend)
    sampler.options.default_shots = args.shots
    sampler.options.dynamical_decoupling.enable = True
    sampler.options.twirling.enable_gates = True
    sampler.options.twirling.enable_measure = True
    if not args.fake:
        sampler.options.max_execution_time = args.max_seconds
    t0 = time.time()
    job = sampler.run([tqc])
    job_id = job.job_id()
    print(f"Submitted job {job_id}, waiting...")
    result = job.result()
    wall_s = time.time() - t0
    hw_counts = result[0].data.meas.get_counts()

    usage_s = None
    if not args.fake:
        try:
            usage_s = job.usage()
        except Exception:
            try:
                usage_s = job.metrics()["usage"]["quantum_seconds"]
            except Exception:
                pass
    hw_m = score_counts(optimizer, hw_counts, optimum)

    # 4. Save
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "results", "hardware",
                                           f"{stamp}_{backend.name}_T{args.tugs}_w{int(args.wind)}"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "counts_hardware.json"), "w") as f:
        json.dump(hw_counts, f)
    with open(os.path.join(out_dir, "counts_simulator.json"), "w") as f:
        json.dump(sim_counts, f)
    strip = lambda m: {k: v for k, v in m.items() if not k.startswith("_")}
    run = {
        "instance": {"tugs": args.tugs, "wind_knots": args.wind, "qubits": n, "optimum_energy": optimum,
                     "demands": {s.name: optimizer.demands[s.id] for s in optimizer.ships}},
        "qaoa": {"p_layers": args.p, "tuning": "CVaR(0.1), 8 starts, 100 COBYLA iterations",
                 "params": [float(x) for x in params], "tuning_seconds_local": tune_s},
        "circuit": {"virtual_depth": qc.depth(), "transpiled_depth": tqc.depth(),
                    "two_qubit_gates": int(two_q), "ops": {k: int(v) for k, v in ops.items()}},
        "hardware": {"backend": backend.name, "fake": args.fake, "job_id": job_id,
                     "max_execution_time_s": None if args.fake else args.max_seconds,
                     "qpu_usage_seconds": usage_s, "wall_seconds_incl_queue": wall_s,
                     "options": "dynamical decoupling + gate and measurement twirling"},
        "metrics_simulator": strip(sim_m),
        "metrics_hardware": strip(hw_m),
    }
    with open(os.path.join(out_dir, "run.json"), "w") as f:
        json.dump(run, f, indent=2)
    hw_label = f"{'Fake ' if args.fake else ''}{backend.name} (hardware)"
    make_plots(out_dir, sim_m, hw_m, optimum,
               f"{args.tugs} tugs, {args.wind:g} kn, {n} qubits, p={args.p}: simulator vs {backend.name}", hw_label)

    print("\n                       simulator   hardware")
    for key, label in (("share_optimal", "Optimal shots"), ("share_valid", "Valid shots")):
        print(f"  {label:<20} {100*sim_m[key]:8.2f}%  {100*hw_m[key]:8.2f}%")
    print(f"  {'Found optimum':<20} {str(sim_m['found_optimum']):>9}  {str(hw_m['found_optimum']):>9}")
    print(f"  {'Best energy':<20} {sim_m['best_energy']:9.0f}  {hw_m['best_energy']:9.0f}")
    print(f"  QPU usage: {usage_s} s, wall time incl. queue: {wall_s:.0f} s")
    print(f"Saved to {out_dir}")


if __name__ == "__main__":
    main()
