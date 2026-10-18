"""
Comprehensive QAOA Benchmark Script
====================================
Evaluates QAOA performance on the 18-qubit Tugboat Optimization instance across:
  - Depths: p = 1 and p = 2
  - Optimizers: COBYLA, baseline (expectation value, 1 start) vs CVaR(0.1) with 8 starts
  - Scenarios: Calm Wind (10 kts) vs High Wind (22 kts)
  - Metrics: Approximation ratio, Ground state overlap, Circuit gate counts, Execution time.
Generates performance summary plots saved to artifacts.
"""

import sys
import os
import time
import json
import numpy as np
import matplotlib.pyplot as plt

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import dimod
from qiskit.quantum_info import Statevector
from tug_optimizer import (
    Ship,
    Tug,
    TugOptimizationQUBO,
    QAOASimulator,
    qubo_to_ising_pauli,
    build_qaoa_circuit
)


# (label, cvar_alpha, n_starts, max_iter)
TUNINGS = [
    ("baseline: expectation, 1 start", None, 1, 45),
    ("CVaR(0.1), 8 starts", 0.1, 8, 100),
]
SHOTS = 4000


def run_benchmark():
    print("=" * 80)
    print("RUNNING 18-QUBIT QAOA SIMULATION BENCHMARK (QISKIT STATEVECTOR)")
    print("=" * 80)

    # 4 Tugs
    tugs = [
        Tug(id=0, name="Tug Alpha"),
        Tug(id=1, name="Tug Bravo"),
        Tug(id=2, name="Tug Charlie"),
        Tug(id=3, name="Tug Delta"),
    ]

    # 4 Ships
    ships = [
        Ship(id=0, name="Contract Container (250m)", loa_meters=250.0, is_contract=True),
        Ship(id=1, name="Contract Feeder (180m)", loa_meters=180.0, is_contract=True),
        Ship(id=2, name="Spot Bulk Carrier (220m)", loa_meters=220.0, is_contract=False, spot_revenue=5000.0),
        Ship(id=3, name="Spot Tanker (280m)", loa_meters=280.0, is_contract=False, spot_revenue=5500.0),
    ]

    scenarios = [
        ("Calm Wind (10 kts)", 10.0),
        ("High Wind (22 kts)", 22.0)
    ]

    results = []

    for sc_name, wind_speed in scenarios:
        print(f"\n>>> Scenario: {sc_name}")
        optimizer = TugOptimizationQUBO(tugs, ships, wind_speed_knots=wind_speed)
        bqm = optimizer.build_bqm()

        # Classical ground truth via exact solver
        exact_solver = dimod.ExactSolver()
        classical_res = exact_solver.sample(bqm)
        ground_truth_sample = classical_res.first.sample
        ground_truth_energy = classical_res.first.energy
        max_energy = classical_res.data_vectors['energy'].max()
        min_energy = ground_truth_energy

        # QUBO energy of every basis state, in Qiskit index order (variable i = bit i)
        Q, q_offset = optimizer.to_qubo_matrix()
        n = optimizer.num_variables
        idx = np.arange(1 << n)
        X = ((idx[:, None] >> np.arange(n)) & 1).astype(np.float64)
        all_energies = np.einsum("ki,ij,kj->k", X, Q, X) + q_offset

        print(f"  Classical Optimal Energy: {ground_truth_energy:.1f}")
        print(f"  Classical Accepted Spot Ships: {optimizer.decode_solution(ground_truth_sample)['accepted_spot_ships']}")

        for tuning, cvar_alpha, n_starts, max_iter in TUNINGS:
          for p in [1, 2]:
            print(f"\n  --- QAOA depth p = {p}, tuning = {tuning} ---")
            start_t = time.time()
            sim = QAOASimulator(optimizer, p_layers=p, normalize=True, cvar_alpha=cvar_alpha)

            circuit_depth = sim.qc.depth()
            op_counts = dict(sim.qc.count_ops())
            num_rzz = op_counts.get("rzz", 0)

            # Optimize
            opt_params, _ = sim.optimize_parameters(max_iter=max_iter, seed=123, n_starts=n_starts)
            elapsed = time.time() - start_t

            # Un-normalize expected energy
            # expectation_value already includes the (normalized) Ising offset
            raw_exp_energy = sim.expectation_value(opt_params) * sim.norm_factor

            # Sampling top 10 states
            samples = sim.sample_probabilities(opt_params, top_k=10, apply_qec=False)

            # Check ground state overlap
            # Sum over ALL optimal bitstrings: the optimum is degenerate (tugs are interchangeable),
            # so no single bitstring need appear in the top samples
            probs = Statevector.from_instruction(sim.bind_parameters(opt_params)).probabilities()
            ground_truth_prob = float(probs[all_energies <= ground_truth_energy + 1e-4].sum())
            p_found = 1.0 - (1.0 - ground_truth_prob) ** SHOTS

            approx_ratio = (max_energy - raw_exp_energy) / (max_energy - min_energy) if max_energy != min_energy else 1.0

            print(f"  Circuit Depth: {circuit_depth}, RZZ Gates: {num_rzz}")
            print(f"  Optimal Parameters (gamma, beta): {np.round(opt_params, 4)}")
            print(f"  Expected QUBO Energy: {raw_exp_energy:.1f}")
            print(f"  Approximation Ratio: {approx_ratio:.4f}")
            print(f"  Ground State Overlap (all optimal states): {ground_truth_prob * 100:.3f}%")
            print(f"  P(optimum in {SHOTS} shots): {p_found * 100:.1f}%")
            print(f"  Optimization Runtime: {elapsed:.2f}s")

            results.append({
                "scenario": sc_name,
                "tuning": tuning,
                "p_layers": p,
                "circuit_depth": circuit_depth,
                "rzz_gates": num_rzz,
                "approx_ratio": float(approx_ratio),
                "expected_energy": float(raw_exp_energy),
                "ground_truth_energy": float(ground_truth_energy),
                "p_optimal_per_shot": ground_truth_prob,
                f"p_optimum_found_in_{SHOTS}_shots": p_found,
                "tuning_seconds": elapsed,
                "opt_params": [float(x) for x in opt_params],
                "top_samples": [
                    {
                        "bitstring": bitstr,
                        "prob": float(prob),
                        "energy": float(energy),
                        "accepted_spot": decoded["accepted_spot_ships"]
                    }
                    for bitstr, prob, energy, decoded in samples[:3]
                ]
            })

    # Save benchmark data to json
    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "results"))
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "qaoa_benchmark_results.json")
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n[OK] Benchmark results saved to: {json_path}")

    # Plot Comparison Chart: chance per shot of measuring an optimal schedule
    fig, ax = plt.subplots(figsize=(9, 5))
    labels = [f"{sc.split(' (')[0]}, p={p}" for sc, _ in scenarios for p in (1, 2)]
    x = np.arange(len(labels))
    width = 0.38
    for k, (tuning, *_rest) in enumerate(TUNINGS):
        vals = [100 * r["p_optimal_per_shot"] for r in results if r["tuning"] == tuning]
        bars = ax.bar(x + (k - 0.5) * width, vals, width, label=tuning)
        ax.bar_label(bars, fmt="%.2f%%", fontsize=8)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Chance per shot of an optimal schedule (%)")
    ax.set_title("QAOA, 4 tugs / 18 qubits: baseline vs CVaR + multi-start tuning")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.6)

    plt.tight_layout()
    chart_path = os.path.join(out_dir, "qaoa_benchmark_chart.png")
    plt.savefig(chart_path, dpi=150)
    plt.close()
    print(f"[OK] Benchmark chart saved to: {chart_path}")


if __name__ == "__main__":
    run_benchmark()
