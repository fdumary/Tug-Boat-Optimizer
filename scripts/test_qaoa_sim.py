"""
Test and Verification Script for QAOA Statevector Simulation
Simulates the 18-qubit QAOA circuit in Qiskit to solve the Tugboat Optimization problem.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tug_optimizer import Ship, Tug, TugOptimizationQUBO, QAOASimulator, qubo_to_ising_pauli


def main():
    print("=" * 70)
    print("QAOA Statevector Simulation on Qiskit (18 Qubits)")
    print("=" * 70)

    # 4 Tugs
    tugs = [
        Tug(id=0, name="Tug Alpha"),
        Tug(id=1, name="Tug Bravo"),
        Tug(id=2, name="Tug Charlie"),
        Tug(id=3, name="Tug Delta"),
    ]

    # 4 Ships: 2 Contracted, 2 Spot
    ships = [
        Ship(id=0, name="Contract Container (250m)", loa_meters=250.0, is_contract=True),
        Ship(id=1, name="Contract Feeder (180m)", loa_meters=180.0, is_contract=True),
        Ship(id=2, name="Spot Bulk Carrier (220m)", loa_meters=220.0, is_contract=False, spot_revenue=5000.0),
        Ship(id=3, name="Spot Tanker (280m)", loa_meters=280.0, is_contract=False, spot_revenue=5500.0),
    ]

    # Instantiate QUBO for calm wind (10 kts)
    optimizer = TugOptimizationQUBO(tugs, ships, wind_speed_knots=10.0)
    cost_op, offset, norm_factor = qubo_to_ising_pauli(optimizer, normalize=True)

    print(f"Number of Qubits: {cost_op.num_qubits}")
    print(f"Number of Pauli Terms in Cost Hamiltonian H_C: {len(cost_op)}")
    print(f"Energy offset (normalized): {offset:.4f} (Norm Factor: {norm_factor:.1f})")

    # Build QAOA Simulator with p=1 layer
    print("\nInitializing QAOA Simulator (p=1 layer, normalized Hamiltonian)...")
    simulator = QAOASimulator(optimizer, p_layers=1, normalize=True)
    print(f"Circuit Depth: {simulator.qc.depth()}")
    print(f"Total Circuit Operations: {simulator.qc.count_ops()}")

    # Optimize parameters
    print("\nRunning COBYLA optimizer for variational parameters (gamma, beta)...")
    opt_params, opt_energy = simulator.optimize_parameters(max_iter=60)
    print(f"Optimal Gamma: {opt_params[0]:.4f}, Optimal Beta: {opt_params[1]:.4f}")
    print(f"Normalized Expected Energy: {opt_energy:.4f}")

    # Sample top measurement probabilities
    print("\nSampling Top Measurement States from Quantum Statevector:")
    top_samples = simulator.sample_probabilities(opt_params, top_k=5)
    for rank, (bitstring, prob, raw_energy, decoded) in enumerate(top_samples, 1):
        print(f"\n[Rank {rank}] Probability: {prob * 100:.3f}%, Raw QUBO Energy: {raw_energy:.1f}")
        print(f"  Accepted Spot Ships: {decoded['accepted_spot_ships']}")
        print(f"  Total Tugs Used: {decoded['total_tugs_used']}/4")
        for t_name, s_name in decoded["tugs_assigned"].items():
            print(f"    {t_name} -> {s_name if s_name else 'IDLE'}")


if __name__ == "__main__":
    main()
