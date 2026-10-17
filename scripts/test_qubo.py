"""
Test and Verification Script for QUBO formulation
Solves the exact 18-variable QUBO using dimod.ExactSolver across:
  1. Low Wind (Calm): Spot ships accepted
  2. High Wind (Storm): Spot ships rejected due to contract tug demand
  3. Crew Fatigue: Unavailable tugs correctly avoided
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import dimod
from tug_optimizer import Ship, Tug, TugOptimizationQUBO


def run_experiment(name: str, wind_speed: float, tugs_crew_avail: list):
    print("=" * 70)
    print(f"Scenario: {name} (Wind: {wind_speed} knots)")
    print("=" * 70)

    # 4 Tugs
    tugs = [
        Tug(id=0, name="Tug Alpha", crew_available=tugs_crew_avail[0]),
        Tug(id=1, name="Tug Bravo", crew_available=tugs_crew_avail[1]),
        Tug(id=2, name="Tug Charlie", crew_available=tugs_crew_avail[2]),
        Tug(id=3, name="Tug Delta", crew_available=tugs_crew_avail[3]),
    ]

    # 4 Ships: 2 Contracted, 2 Spot
    ships = [
        Ship(id=0, name="Contract Container (250m)", loa_meters=250.0, is_contract=True),
        Ship(id=1, name="Contract Feeder (180m)", loa_meters=180.0, is_contract=True),
        Ship(id=2, name="Spot Bulk Carrier (220m)", loa_meters=220.0, is_contract=False, spot_revenue=5000.0),
        Ship(id=3, name="Spot Tanker (280m)", loa_meters=280.0, is_contract=False, spot_revenue=5500.0),
    ]

    optimizer = TugOptimizationQUBO(tugs, ships, wind_speed_knots=wind_speed)
    bqm = optimizer.build_bqm()

    print(f"Number of binary variables (qubits): {optimizer.num_variables}")
    print(f"Computed Demands: {optimizer.demands}")

    solver = dimod.ExactSolver()
    response = solver.sample(bqm)
    best_sample = response.first.sample
    best_energy = response.first.energy

    decoded = optimizer.decode_solution(best_sample)
    print(f"Best QUBO Energy: {best_energy:.1f}")
    print(f"Total Tugs Used: {decoded['total_tugs_used']}/4")
    print(f"Accepted Spot Ships: {decoded['accepted_spot_ships']}")
    print("Tug Assignments:")
    for tug_name, ship_name in decoded["tugs_assigned"].items():
        print(f"  - {tug_name} -> {ship_name if ship_name else 'IDLE'}")
    print()


if __name__ == "__main__":
    # 1. Calm Wind (10 kts) -> Should take spot ships
    run_experiment("Calm Wind", wind_speed=10.0, tugs_crew_avail=[True, True, True, True])

    # 2. High Wind (22 kts) -> Contract demands surge, should reject spot ships
    run_experiment("High Wind", wind_speed=22.0, tugs_crew_avail=[True, True, True, True])

    # 3. Calm Wind with Crew Fatigue on Tug Alpha & Bravo
    run_experiment("Calm Wind but 2 Tugs Resting", wind_speed=10.0, tugs_crew_avail=[False, False, True, True])
