"""
Tugboat Optimization QUBO Formulation
=====================================
Translates whiteboard operational penalties into a Quadratic Unconstrained Binary Optimization (QUBO) model:
    1. P(# of ships)   -> More ships competing for fixed fleet capacity; tug double-booking penalty & spot trade-offs.
    2. P(windspeed)    -> Higher wind speed exponentially/stepwise increases required tugs per ship.
    3. P(crew_avail)   -> Deploying tugboats with unavailable or unrested crew incurs severe operational/labor penalty.
    4. P(|ships|)      -> Larger vessel Length Overall (LOA) demands higher baseline tug allocation.

Efficiency Improvements Implemented:
    - Pre-filtering of unavailable tugs to remove redundant qubits.
    - Caching of built BQM and QUBO matrices to eliminate duplicate computations.
    - Optimized solution decoding.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import numpy as np
import dimod


@dataclass
class Ship:
    id: int
    name: str
    loa_meters: float            # Length Overall in meters (|ship|)
    is_contract: bool            # True if contracted (mandatory), False if spot (optional)
    spot_revenue: float = 0.0    # Revenue gained if spot ship is serviced ($)
    arrival_window_min: int = 45 # Minutes within tidal window


@dataclass
class Tug:
    id: int
    name: str
    bollard_pull_tonnes: float = 60.0 # Tug capacity/power class
    crew_available: bool = True       # False if crew is resting or unavailable (crew_avail)
    activation_cost: float = 500.0    # Operating/fuel cost to deploy ($)


def compute_tug_demand(loa_meters: float, wind_speed_knots: float) -> int:
    """
    Computes required tugboats based on ship size (|ships|) and wind speed (windspeed).
    Implements the port harbor rules from Use-Case Canvas & Whiteboard:
        - LOA < 200m:  0 tugs calm, 1-2 windy
        - 200m - 300m: 1 tug calm, 2 windy, 3 gale
        - 300m - 400m: 2 tugs calm, 3 windy, 4 gale
        - >= 400m:     4 tugs calm, 4-5 windy
    """
    is_windy = wind_speed_knots > 15.0
    is_gale = wind_speed_knots > 25.0

    if loa_meters < 200.0:
        if is_gale:
            return 2
        elif is_windy:
            return 1
        return 0
    elif loa_meters < 300.0:
        if is_gale:
            return 3
        elif is_windy:
            return 2
        return 1
    elif loa_meters < 400.0:
        if is_gale:
            return 4
        elif is_windy:
            return 3
        return 2
    else: # Mega container ships (>= 400m)
        if is_windy:
            return 5
        return 4


class TugOptimizationQUBO:
    """
    Constructs and inspects the QUBO formulation for tugboat scheduling.
    """
    def __init__(
        self,
        tugs: List[Tug],
        ships: List[Ship],
        wind_speed_knots: float = 12.0,
        lambda_unique: float = 20000.0,    # Penalty for double-booking a tug
        lambda_contract: float = 30000.0,  # Penalty for unserved contracted demand
        lambda_spot: float = 15000.0,      # Penalty for accepting spot ship without required tugs
        lambda_crew: float = 40000.0,      # Penalty for assigning unavailable crew/tug
        prefilter_resting_tugs: bool = True # Improvement 1: Pre-filter unavailable tugs to save qubits
    ):
        self.tugs = tugs
        self.ships = ships
        self.wind_speed_knots = wind_speed_knots
        self.lambda_unique = lambda_unique
        self.lambda_contract = lambda_contract
        self.lambda_spot = lambda_spot
        self.lambda_crew = lambda_crew
        self.prefilter_resting_tugs = prefilter_resting_tugs

        # Improvement 1: Active tugs list
        if self.prefilter_resting_tugs:
            self.active_tugs = [t for t in self.tugs if t.crew_available]
        else:
            self.active_tugs = list(self.tugs)

        # Map variable names to qubit indices
        self.var_names: List[str] = []
        self.var_to_idx: Dict[str, int] = {}
        self._init_variables()

        # Compute dynamic demand d_s for each ship under current wind
        self.demands: Dict[int, int] = {}
        for ship in self.ships:
            self.demands[ship.id] = compute_tug_demand(ship.loa_meters, self.wind_speed_knots)

        # Improvement 2: Caching of BQM and QUBO matrix
        self._cached_bqm: Optional[dimod.BinaryQuadraticModel] = None
        self._cached_qubo: Optional[Tuple[np.ndarray, float]] = None

    def _init_variables(self):
        # Tug-to-ship assignment variables: x_{t, s} for active tugs only
        for t in self.active_tugs:
            for s in self.ships:
                name = f"x_{t.id}_{s.id}"
                self.var_to_idx[name] = len(self.var_names)
                self.var_names.append(name)

        # Spot ship acceptance variables: y_{s}
        for s in self.ships:
            if not s.is_contract:
                name = f"y_{s.id}"
                self.var_to_idx[name] = len(self.var_names)
                self.var_names.append(name)

    @property
    def num_variables(self) -> int:
        return len(self.var_names)

    def build_bqm(self) -> dimod.BinaryQuadraticModel:
        """
        Builds and caches the dimod BinaryQuadraticModel representing the complete QUBO.
        H_total = Objective + P_unique + P_contract + P_spot + P_crew
        """
        if self._cached_bqm is not None:
            return self._cached_bqm

        bqm = dimod.BinaryQuadraticModel(vartype=dimod.BINARY)

        # Initialize all variables in BQM
        for var in self.var_names:
            bqm.add_variable(var, 0.0)

        # -------------------------------------------------------------
        # 1. Objective Function: Minimize Tug Fuel/Op Cost - Spot Revenue
        # -------------------------------------------------------------
        for t in self.active_tugs:
            for s in self.ships:
                x_var = f"x_{t.id}_{s.id}"
                bqm.add_linear(x_var, t.activation_cost)

        for s in self.ships:
            if not s.is_contract:
                y_var = f"y_{s.id}"
                bqm.add_linear(y_var, -s.spot_revenue)

        # -------------------------------------------------------------
        # 2. P1: Tug Uniqueness (No Double-Booking in 45-min Window)
        #    For each active tug t: sum_{s} x_{t,s} <= 1
        # -------------------------------------------------------------
        for t in self.active_tugs:
            ship_ids = [s.id for s in self.ships]
            for i in range(len(ship_ids)):
                for j in range(i + 1, len(ship_ids)):
                    var_i = f"x_{t.id}_{ship_ids[i]}"
                    var_j = f"x_{t.id}_{ship_ids[j]}"
                    bqm.add_quadratic(var_i, var_j, self.lambda_unique)

        # -------------------------------------------------------------
        # 3. P2 & P4: Wind & Size Demand Fulfillment for Contract Ships
        #    For each contract ship s: (sum_t x_{t,s} - d_s(wind, size))^2 = 0
        # -------------------------------------------------------------
        for s in self.ships:
            if s.is_contract:
                d_s = self.demands[s.id]
                linear_coeff = self.lambda_contract * (1.0 - 2.0 * d_s)
                for t in self.active_tugs:
                    var_t = f"x_{t.id}_{s.id}"
                    bqm.add_linear(var_t, linear_coeff)

                quad_coeff = 2.0 * self.lambda_contract
                tug_ids = [t.id for t in self.active_tugs]
                for i in range(len(tug_ids)):
                    for j in range(i + 1, len(tug_ids)):
                        var_i = f"x_{tug_ids[i]}_{s.id}"
                        var_j = f"x_{tug_ids[j]}_{s.id}"
                        bqm.add_quadratic(var_i, var_j, quad_coeff)

                bqm.offset += self.lambda_contract * (d_s ** 2)

        # -------------------------------------------------------------
        # 4. Spot Demand Fulfillment:
        #    For each spot ship s: (sum_t x_{t,s} - d_s(wind, size) * y_s)^2 = 0
        # -------------------------------------------------------------
        for s in self.ships:
            if not s.is_contract:
                d_s = self.demands[s.id]
                y_var = f"y_{s.id}"

                bqm.add_linear(y_var, self.lambda_spot * (d_s ** 2))

                for t in self.active_tugs:
                    x_var = f"x_{t.id}_{s.id}"
                    bqm.add_linear(x_var, self.lambda_spot * 1.0)
                    bqm.add_quadratic(x_var, y_var, -2.0 * self.lambda_spot * d_s)

                quad_coeff = 2.0 * self.lambda_spot
                tug_ids = [t.id for t in self.active_tugs]
                for i in range(len(tug_ids)):
                    for j in range(i + 1, len(tug_ids)):
                        var_i = f"x_{tug_ids[i]}_{s.id}"
                        var_j = f"x_{tug_ids[j]}_{s.id}"
                        bqm.add_quadratic(var_i, var_j, quad_coeff)

        # -------------------------------------------------------------
        # 5. P3: Crew Availability Penalty (if not prefiltered)
        # -------------------------------------------------------------
        if not self.prefilter_resting_tugs:
            for t in self.tugs:
                if not t.crew_available:
                    for s in self.ships:
                        x_var = f"x_{t.id}_{s.id}"
                        bqm.add_linear(x_var, self.lambda_crew)

        self._cached_bqm = bqm
        return bqm

    def to_qubo_matrix(self) -> Tuple[np.ndarray, float]:
        """
        Converts and caches the upper triangular numpy QUBO matrix Q and constant offset C.
        """
        if self._cached_qubo is not None:
            return self._cached_qubo

        bqm = self.build_bqm()
        n = self.num_variables
        Q = np.zeros((n, n), dtype=np.float64)

        for i, var_name in enumerate(self.var_names):
            Q[i, i] = bqm.linear.get(var_name, 0.0)

        for (u, v), bias in bqm.quadratic.items():
            i, j = self.var_to_idx[u], self.var_to_idx[v]
            if i > j:
                i, j = j, i
            Q[i, j] += bias

        self._cached_qubo = (Q, bqm.offset)
        return self._cached_qubo

    def decode_solution(self, sample: Dict[str, int]) -> Dict:
        """
        Interprets a binary assignment bitstring into human-readable operations.
        """
        assignments = {}
        accepted_spots = []
        tugs_used = 0

        for t in self.tugs:
            if not t.crew_available and self.prefilter_resting_tugs:
                assignments[t.name] = "RESTING (MANDATORY REST)"
                continue

            assigned_ship = None
            for s in self.ships:
                val = sample.get(f"x_{t.id}_{s.id}", 0)
                if val == 1:
                    assigned_ship = s.name
                    tugs_used += 1
            assignments[t.name] = assigned_ship

        for s in self.ships:
            if not s.is_contract:
                val = sample.get(f"y_{s.id}", 0)
                if val == 1:
                    accepted_spots.append(s.name)

        return {
            "tugs_assigned": assignments,
            "accepted_spot_ships": accepted_spots,
            "total_tugs_used": tugs_used,
            "demands": {s.name: self.demands[s.id] for s in self.ships}
        }
