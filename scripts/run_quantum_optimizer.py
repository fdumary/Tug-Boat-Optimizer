"""
Quantum Tugboat Optimizer (Arbitrary T Fleet Size with Quantum Error Correction)
===============================================================================
Runs the complete quantum optimization pipeline for any user-defined fleet size T:
  1. Fleet & Ship Generation: T tugboats + 4 port vessels (2 contract, 2 spot).
  2. QUBO Model: Translates the 4 whiteboard operational rules with pre-filtering of resting tugs.
  3. Ising Hamiltonian: Maps binary variables to Pauli Z operators with caching.
  4. QAOA Quantum Circuit: Constructs parameterized phase separator and mixer.
  5. Quantum Execution:
     - Local Simulation: Runs on Qiskit quantum statevector with fast COBYLA variational optimization.
     - IBM Quantum Hardware: Submits to real IBM QPUs via Qiskit Runtime V2 SamplerV2 with dynamical decoupling and twirling.
  6. Quantum Error Correction (QEC) & Mitigation:
     - Syndrome detection for double-booking, phantom spot allocations, and contract deficits.
     - Minimum-distance projection / error recovery repairing corrupted bitstrings.
  7. Solution Decoder: Scores every measured bitstring by QUBO energy and decodes the best into tug schedules.

Usage:
  python scripts/run_quantum_optimizer.py --tugs 4 --wind 12.0 --qec
  python scripts/run_quantum_optimizer.py --tugs 6 --wind 22.0 --qec
  python scripts/run_quantum_optimizer.py --tugs 4 --ibm-token "YOUR_TOKEN" --backend "ibm_brisbane" --qec
"""

import sys
import os
import argparse
import numpy as np

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tug_optimizer import (
    Ship,
    Tug,
    TugOptimizationQUBO,
    QAOASimulator,
    build_qaoa_circuit,
    qubo_to_ising_pauli,
    apply_quantum_error_correction,
    rank_samples_by_energy,
)


def solve_tug_scheduling_quantum(
    T: int = 4,
    wind_speed_knots: float = 12.0,
    p_layers: int = 1,
    ibm_token: str = None,
    backend_name: str = None,
    shots: int = 4000,
    enable_qec: bool = True
):
    """
    Solves the tugboat allocation problem on a quantum computer/simulator for arbitrary T with QEC.
    """
    print("=" * 80)
    print(f"QUANTUM TUGBOAT OPTIMIZER: FLEET SIZE T = {T} (QEC: {'ENABLED' if enable_qec else 'DISABLED'})")
    print("=" * 80)

    # 1. Generate Fleet of T Tugboats
    tugs = [
        Tug(id=i, name=f"Tug-{i+1:02d}", activation_cost=500.0, crew_available=True)
        for i in range(T)
    ]

    # 2. Port Arrival Vessels: 2 Contract (Mandatory), 2 Spot (Opportunistic)
    ships = [
        Ship(id=0, name="Contract Container (250m)", loa_meters=250.0, is_contract=True),
        Ship(id=1, name="Contract Feeder (180m)", loa_meters=180.0, is_contract=True),
        Ship(id=2, name="Spot Bulk Carrier (220m)", loa_meters=220.0, is_contract=False, spot_revenue=5000.0),
        Ship(id=3, name="Spot Tanker (280m)", loa_meters=280.0, is_contract=False, spot_revenue=5500.0),
    ]

    # 3. Construct QUBO Model from Whiteboard Rules (with pre-filtering improvement)
    optimizer = TugOptimizationQUBO(tugs, ships, wind_speed_knots=wind_speed_knots, prefilter_resting_tugs=True)
    num_qubits = optimizer.num_variables
    print(f"[1] Problem Setup:")
    print(f"    - Available Tugs (T): {T}")
    print(f"    - Ships in Window: {len(ships)} (2 Contracted, 2 Spot)")
    print(f"    - Wind Speed: {wind_speed_knots} knots")
    print(f"    - Required Tug Demands: {optimizer.demands}")
    print(f"    - Total Decision Variables (Qubits N = T*4 + 2): {num_qubits} qubits")

    # 4. Map QUBO to Ising Cost Hamiltonian
    cost_op, offset, norm_factor = qubo_to_ising_pauli(optimizer, normalize=True)
    print(f"\n[2] Quantum Hamiltonian:")
    print(f"    - Number of Pauli Terms in H_C: {len(cost_op)}")
    print(f"    - Normalization Scale Factor: {norm_factor:.1f}")

    # 5. Build QAOA Simulator / Circuit
    print(f"\n[3] Synthesizing QAOA Quantum Circuit (Depth p={p_layers})...")
    sim = QAOASimulator(optimizer, p_layers=p_layers, normalize=True)
    print(f"    - Virtual Circuit Depth: {sim.qc.depth()}")
    print(f"    - Gate Operations: {dict(sim.qc.count_ops())}")

    if ibm_token:
        # -------------------------------------------------------------
        # EXECUTION PATH A: REAL IBM QUANTUM HARDWARE (QPU)
        # -------------------------------------------------------------
        print(f"\n[4] Connecting to IBM Quantum Hardware via Qiskit Runtime...")
        from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2 as Sampler
        from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

        service = QiskitRuntimeService(channel="ibm_quantum_platform", token=ibm_token)
        backend = service.backend(backend_name) if backend_name else service.least_busy(
            operational=True, simulator=False, min_num_qubits=num_qubits
        )
        print(f"    - Target QPU Backend: {backend.name} ({backend.num_qubits} physical qubits)")

        # Fast pre-tuning of variational angles
        print(f"    - Pre-calibrating variational angles (gamma, beta)...")
        opt_params, _ = sim.optimize_parameters(max_iter=25)
        bound_qc = sim.bind_parameters(opt_params)
        # Sampler needs classical bits to return counts
        bound_qc.measure_all()

        # Transpile virtual circuit to physical heavy-hex topology
        print(f"    - Transpiling to physical topology (Optimization Level 3)...")
        pm = generate_preset_pass_manager(backend=backend, optimization_level=3)
        transpiled_qc = pm.run(bound_qc)
        print(f"    - Physical Transpiled Circuit Depth: {transpiled_qc.depth()}")

        # SamplerV2 has no resilience_level (that is EstimatorV2); use dynamical decoupling and twirling instead
        print(f"    - Submitting {shots} shots to {backend.name} (Dynamical Decoupling + Pauli Twirling)...")
        sampler = Sampler(mode=backend)
        sampler.options.dynamical_decoupling.enable = True
        sampler.options.twirling.enable_gates = True
        sampler.options.twirling.enable_measure = True
        job = sampler.run([transpiled_qc], shots=shots)
        print(f"    - Job Submitted! Job ID: {job.job_id()}")
        print(f"    - Waiting for QPU results...")
        result = job.result()

        counts = result[0].data.meas.get_counts()
        raw_samples = rank_samples_by_energy(optimizer, counts)
    else:
        # -------------------------------------------------------------
        # EXECUTION PATH B: LOCAL QUANTUM SIMULATOR (No Token Required)
        # -------------------------------------------------------------
        print(f"\n[4] Executing on Local Quantum Statevector Simulator...")
        opt_params, _ = sim.optimize_parameters(max_iter=35)
        print(f"    - Optimal Variational Parameters: Gamma={opt_params[:p_layers]}, Beta={opt_params[p_layers:]}")
        # Draw shots from the statevector, like hardware, and keep the lowest-energy ones
        counts = sim.sample_counts(opt_params, shots=shots)
        raw_samples = rank_samples_by_energy(optimizer, counts)

    # 6. Quantum Error Correction (QEC) & Mitigation
    print(f"\n[5] Quantum Error Correction & Mitigation (QEC/QEM):")
    if enable_qec:
        corrected_results = apply_quantum_error_correction(optimizer, raw_samples)
        print(f"    - QEC Status: ACTIVE (Constraint Syndrome Check + Repair)")
        samples_to_show = [
            (bstr, prob, energy, decoded, info)
            for bstr, prob, energy, decoded, info in corrected_results[:5]
        ]
    else:
        print(f"    - QEC Status: DISABLED")
        samples_to_show = [
            (bstr, prob, energy, decoded, {"corrected": False, "hamming_edits": 0})
            for bstr, prob, energy, decoded in raw_samples[:5]
        ]

    # 7. Decode Quantum Measurement Bitstrings
    print(f"\n[6] Top Quantum Measurement Bitstrings & Validated Schedules:")
    print("-" * 80)
    for rank, (bitstring, prob, energy, decoded, info) in enumerate(samples_to_show[:3], 1):
        qec_tag = f"[QEC Repaired: {info['hamming_edits']} bit-flips corrected]" if info['corrected'] else "[Exact Valid Code State]"
        print(f"Rank {rank} | Sampling Probability: {prob * 100:.3f}% | QUBO Energy: {energy:.1f} {qec_tag}")
        print(f"  Accepted Spot Ships: {decoded['accepted_spot_ships']}")
        print(f"  Total Tugs Deployed: {decoded['total_tugs_used']}/{T}")
        print(f"  Tug Assignments:")
        for t_name, s_name in decoded["tugs_assigned"].items():
            print(f"    * {t_name} -> {s_name if s_name else 'IDLE'}")
        print("-" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quantum Tugboat Optimizer for arbitrary T with QEC.")
    parser.add_argument("--tugs", "-T", type=int, default=4, help="Fleet size T (default: 4)")
    parser.add_argument("--wind", "-w", type=float, default=12.0, help="Wind speed in knots (default: 12.0)")
    parser.add_argument("--ibm-token", type=str, default=None, help="IBM Quantum API token (optional)")
    parser.add_argument("--backend", type=str, default=None, help="IBM backend name (optional)")
    parser.add_argument("--p", type=int, default=1, help="QAOA layers p (default: 1)")
    parser.add_argument("--shots", type=int, default=4000, help="Number of shots (default: 4000)")
    parser.add_argument("--qec", action="store_true", default=True, help="Enable Quantum Error Correction (default: True)")
    parser.add_argument("--no-qec", dest="qec", action="store_false", help="Disable Quantum Error Correction")

    args = parser.parse_args()
    solve_tug_scheduling_quantum(
        T=args.tugs,
        wind_speed_knots=args.wind,
        p_layers=args.p,
        ibm_token=args.ibm_token,
        backend_name=args.backend,
        shots=args.shots,
        enable_qec=args.qec
    )
