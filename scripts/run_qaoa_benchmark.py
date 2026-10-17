"""
Comprehensive QAOA Benchmark Script
====================================
Evaluates QAOA performance on the 18-qubit Tugboat Optimization instance across:
  - Depths: p = 1 and p = 2
  - Optimizers: COBYLA
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
from tug_optimizer import (
    Ship,
    Tug,
    TugOptimizationQUBO,
    QAOASimulator,
    qubo_to_ising_pauli,
    build_qaoa_circuit
)


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

        print(f"  Classical Optimal Energy: {ground_truth_energy:.1f}")
        print(f"  Classical Accepted Spot Ships: {optimizer.decode_solution(ground_truth_sample)['accepted_spot_ships']}")

        for p in [1, 2]:
            print(f"\n  --- Testing QAOA depth p = {p} ---")
            start_t = time.time()
            sim = QAOASimulator(optimizer, p_layers=p, normalize=True)

            circuit_depth = sim.qc.depth()
            op_counts = dict(sim.qc.count_ops())
            num_rzz = op_counts.get("rzz", 0)

            # Optimize
            opt_params, opt_exp_energy = sim.optimize_parameters(max_iter=45, seed=123)
            elapsed = time.time() - start_t

            # Un-normalize expected energy
            # expectation_value already includes the (normalized) Ising offset
            raw_exp_energy = opt_exp_energy * sim.norm_factor

            # Sampling top 10 states
            samples = sim.sample_probabilities(opt_params, top_k=10, apply_qec=False)

            # Check ground state overlap
            ground_truth_prob = 0.0
            for bitstr, prob, energy, decoded in samples:
                if abs(energy - ground_truth_energy) < 1e-4:
                    ground_truth_prob += prob

            approx_ratio = (max_energy - raw_exp_energy) / (max_energy - min_energy) if max_energy != min_energy else 1.0

            print(f"  Circuit Depth: {circuit_depth}, RZZ Gates: {num_rzz}")
            print(f"  Optimal Parameters (gamma, beta): {np.round(opt_params, 4)}")
            print(f"  Expected QUBO Energy: {raw_exp_energy:.1f}")
            print(f"  Approximation Ratio: {approx_ratio:.4f}")
            print(f"  Ground State Overlap (Top samples): {ground_truth_prob * 100:.3f}%")
            print(f"  Optimization Runtime: {elapsed:.2f}s")

            results.append({
                "scenario": sc_name,
                "p_layers": p,
                "circuit_depth": circuit_depth,
                "rzz_gates": num_rzz,
                "approx_ratio": float(approx_ratio),
                "expected_energy": float(raw_exp_energy),
                "ground_truth_energy": float(ground_truth_energy),
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

    # Plot Comparison Chart
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    scenarios_labels = ["Calm (p=1)", "Calm (p=2)", "Windy (p=1)", "Windy (p=2)"]
    approx_ratios = [r["approx_ratio"] for r in results]
    circuit_depths = [r["circuit_depth"] for r in results]

    axes[0].bar(scenarios_labels, approx_ratios, color=['#2b5c8f', '#1f77b4', '#d95f02', '#e7298a'], width=0.55)
    axes[0].set_title("QAOA Approximation Ratio by Layer & Scenario")
    axes[0].set_ylabel("Approximation Ratio")
    axes[0].set_ylim(0.5, 1.05)
    axes[0].grid(axis='y', linestyle='--', alpha=0.7)

    axes[1].plot(scenarios_labels, circuit_depths, marker='o', linewidth=2, color='#2ca02c')
    axes[1].set_title("Quantum Circuit Depth Scaling")
    axes[1].set_ylabel("Circuit Depth (Gate Layers)")
    axes[1].grid(linestyle='--', alpha=0.7)

    plt.tight_layout()
    chart_path = os.path.join(out_dir, "qaoa_benchmark_chart.png")
    plt.savefig(chart_path, dpi=150)
    plt.close()
    print(f"[OK] Benchmark chart saved to: {chart_path}")


if __name__ == "__main__":
    run_benchmark()
