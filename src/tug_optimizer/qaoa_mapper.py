"""
QAOA Mapper & Quantum Simulator Module (Optimized)
===================================================
Transforms the Tugboat Optimization QUBO into an Ising Hamiltonian (SparsePauliOp)
and constructs the parameterized QAOA quantum circuit (Ansatz) in Qiskit.

Efficiency Improvements Implemented:
  1. Caching of Ising Hamiltonian and Pauli operators.
  2. Fast Vectorized Diagonal Simulator for variational optimization (used for n <= 20 qubits).
  5. CVaR objective and multi-start COBYLA for angle tuning.
  3. Top-k probability sampling using heapq.nlargest (avoids O(2^N log 2^N) full statevector sort).
  4. Quantum Error Correction & Mitigation integration.
"""

from typing import Dict, List, Tuple, Optional
import heapq
import numpy as np
from scipy.optimize import minimize
from qiskit.circuit import QuantumCircuit, ParameterVector
from qiskit.quantum_info import SparsePauliOp, Statevector

from .qubo_builder import TugOptimizationQUBO
from .error_mitigation import apply_quantum_error_correction, ConstraintErrorCorrection


def qubo_to_ising_pauli(
    optimizer: TugOptimizationQUBO,
    normalize: bool = False
) -> Tuple[SparsePauliOp, float, float]:
    """
    Translates QUBO binary variables x_i in {0, 1} to Ising spin variables z_i in {+1, -1}:
        x_i = (I - Z_i) / 2
    Yields:
        H_C = sum_i h_i Z_i + sum_{i < j} J_{ij} Z_i Z_j + offset * I
    Returns:
        cost_operator: SparsePauliOp
        offset: float
        norm_factor: float
    """
    bqm = optimizer.build_bqm()
    n = optimizer.num_variables
    var_to_idx = optimizer.var_to_idx

    h = np.zeros(n, dtype=np.float64)
    J = np.zeros((n, n), dtype=np.float64)
    offset = float(bqm.offset)

    # Linear terms: a_i * x_i = (a_i / 2) * I - (a_i / 2) * Z_i
    for var, bias in bqm.linear.items():
        i = var_to_idx[var]
        offset += bias / 2.0
        h[i] -= bias / 2.0

    # Quadratic terms: b_ij * x_i * x_j
    # = (b_ij / 4) * (I - Z_i - Z_j + Z_i * Z_j)
    for (u, v), bias in bqm.quadratic.items():
        i = var_to_idx[u]
        j = var_to_idx[v]
        if i > j:
            i, j = j, i
        offset += bias / 4.0
        h[i] -= bias / 4.0
        h[j] -= bias / 4.0
        J[i, j] += bias / 4.0

    norm_factor = 1.0
    if normalize:
        max_h = np.max(np.abs(h)) if len(h) > 0 else 1.0
        max_J = np.max(np.abs(J)) if np.any(J) else 1.0
        norm_factor = max(max_h, max_J, 1.0)
        h /= norm_factor
        J /= norm_factor
        offset /= norm_factor

    # Build SparsePauliOp terms
    pauli_list = []
    for i in range(n):
        if abs(h[i]) > 1e-9:
            z_str = ['I'] * n
            z_str[n - 1 - i] = 'Z'
            pauli_list.append(("".join(z_str), h[i]))

    for i in range(n):
        for j in range(i + 1, n):
            if abs(J[i, j]) > 1e-9:
                zz_str = ['I'] * n
                zz_str[n - 1 - i] = 'Z'
                zz_str[n - 1 - j] = 'Z'
                pauli_list.append(("".join(zz_str), J[i, j]))

    if not pauli_list:
        cost_op = SparsePauliOp.from_list([('I' * n, 0.0)])
    else:
        cost_op = SparsePauliOp.from_list(pauli_list).simplify()

    return cost_op, offset, norm_factor


def rank_samples_by_energy(
    optimizer: TugOptimizationQUBO,
    counts: Dict[str, int]
) -> List[Tuple[str, float, float, Dict]]:
    """
    Scores every measured bitstring by its QUBO energy and returns them lowest-energy first.
    The answer is the best schedule seen in any shot, not the most frequent bitstring.
    Bitstrings are Qiskit order: variable i is character (n - 1 - i).
    """
    bqm = optimizer.build_bqm()
    n = optimizer.num_variables
    total = sum(counts.values())
    ranked = []
    for bitstring, c in counts.items():
        sample = {var: int(bitstring[n - 1 - i]) for i, var in enumerate(optimizer.var_names)}
        ranked.append((bitstring, c / total, bqm.energy(sample), optimizer.decode_solution(sample)))
    ranked.sort(key=lambda r: (r[2], -r[1]))
    return ranked


def build_qaoa_circuit(
    optimizer: TugOptimizationQUBO,
    p_layers: int = 1,
    normalize: bool = True
) -> Tuple[QuantumCircuit, ParameterVector, ParameterVector, SparsePauliOp, float, float]:
    """
    Constructs the parameterized QAOA quantum ansatz for p layers.
    |gamma, beta> = prod_{k=1}^p [ U_M(beta_k) U_C(gamma_k) ] H^{tensor n} |0>
    """
    n = optimizer.num_variables
    cost_op, offset, norm_factor = qubo_to_ising_pauli(optimizer, normalize=normalize)

    qc = QuantumCircuit(n)

    # 1. Initial State Preparation: Uniform Superposition
    for i in range(n):
        qc.h(i)

    gammas = ParameterVector("gamma", p_layers)
    betas = ParameterVector("beta", p_layers)

    bqm = optimizer.build_bqm()
    var_to_idx = optimizer.var_to_idx

    h = np.zeros(n, dtype=np.float64)
    J = np.zeros((n, n), dtype=np.float64)
    for var, bias in bqm.linear.items():
        i = var_to_idx[var]
        h[i] -= bias / 2.0
    for (u, v), bias in bqm.quadratic.items():
        i = var_to_idx[u]
        j = var_to_idx[v]
        if i > j:
            i, j = j, i
        h[i] -= bias / 4.0
        h[j] -= bias / 4.0
        J[i, j] += bias / 4.0

    if normalize:
        h /= norm_factor
        J /= norm_factor

    # 2. Alternating layers
    for layer in range(p_layers):
        gamma_param = gammas[layer]
        beta_param = betas[layer]

        # Phase Separator Unitary: U_C(gamma) = exp(-i * gamma * H_C)
        for i in range(n):
            if abs(h[i]) > 1e-9:
                qc.rz(2.0 * gamma_param * h[i], i)

        for i in range(n):
            for j in range(i + 1, n):
                if abs(J[i, j]) > 1e-9:
                    qc.rzz(2.0 * gamma_param * J[i, j], i, j)

        qc.barrier()

        # Mixer Unitary: U_M(beta) = exp(-i * beta * sum X_i)
        for i in range(n):
            qc.rx(2.0 * beta_param, i)

        qc.barrier()

    return qc, gammas, betas, cost_op, offset, norm_factor


class QAOASimulator:
    """
    High-Performance QAOA Simulator.
    Supports both accelerated vectorized diagonal state evolution and Qiskit circuit binding.
    """
    def __init__(
        self,
        optimizer: TugOptimizationQUBO,
        p_layers: int = 1,
        normalize: bool = True,
        cvar_alpha: Optional[float] = None
    ):
        """
        cvar_alpha: if set (e.g. 0.1), angles are tuned to minimise the mean energy of the best
                    alpha fraction of outcomes (CVaR, Barkoutsos et al. 2020) instead of the
                    plain expectation value. None keeps the plain expectation.
        """
        self.optimizer = optimizer
        self.p_layers = p_layers
        self.normalize = normalize
        self.cvar_alpha = cvar_alpha
        self.n = optimizer.num_variables

        (self.qc, self.gammas, self.betas,
         self.cost_op, self.offset, self.norm_factor) = build_qaoa_circuit(optimizer, p_layers, normalize=normalize)

        # Precompute diagonal energies for ultra-fast vectorized simulation
        self._precompute_diagonal_energies()

    def _precompute_diagonal_energies(self):
        """
        Precomputes the diagonal eigenvalues of H_C across all 2^N basis states.
        E[k] = sum_i h_i z_i(k) + sum_{i<j} J_{ij} z_i(k) z_j(k)
        """
        if self.n <= 20: # Fast pre-computation for up to 20 qubits (1M states)
            dim = 1 << self.n
            # Generate z in {-1, +1}
            # Variable index i maps to bit (n - 1 - i)
            # Create bit matrix
            basis_indices = np.arange(dim, dtype=np.uint32)
            bits = np.zeros((self.n, dim), dtype=np.int8)
            for i in range(self.n):
                # Qiskit convention: qubit i is bit position i
                bits[i] = ((basis_indices >> i) & 1).astype(np.int8)

            spins = 1 - 2 * bits # 0 -> +1, 1 -> -1

            bqm = self.optimizer.build_bqm()
            var_to_idx = self.optimizer.var_to_idx

            h = np.zeros(self.n, dtype=np.float64)
            J = np.zeros((self.n, self.n), dtype=np.float64)
            for var, bias in bqm.linear.items():
                i = var_to_idx[var]
                h[i] -= bias / 2.0
            for (u, v), bias in bqm.quadratic.items():
                i = var_to_idx[u]
                j = var_to_idx[v]
                if i > j:
                    i, j = j, i
                h[i] -= bias / 4.0
                h[j] -= bias / 4.0
                J[i, j] += bias / 4.0

            if self.normalize:
                h /= self.norm_factor
                J /= self.norm_factor

            # Vectorized energy evaluation
            diag_E = np.zeros(dim, dtype=np.float64)
            for i in range(self.n):
                if abs(h[i]) > 1e-9:
                    diag_E += h[i] * spins[i]

            for i in range(self.n):
                for j in range(i + 1, self.n):
                    if abs(J[i, j]) > 1e-9:
                        diag_E += J[i, j] * (spins[i] * spins[j])

            self.diag_E = diag_E
            self._energy_order = np.argsort(diag_E, kind="stable")
            self.use_vectorized = True
        else:
            self.use_vectorized = False

    def bind_parameters(self, params: np.ndarray) -> QuantumCircuit:
        """
        Binds params = [gamma_1..gamma_p, beta_1..beta_p] into the ansatz.
        """
        param_dict = {}
        for k in range(self.p_layers):
            param_dict[self.gammas[k]] = params[k]
            param_dict[self.betas[k]] = params[self.p_layers + k]
        return self.qc.assign_parameters(param_dict)

    def sample_counts(self, params: np.ndarray, shots: int = 4000, seed: int = 42) -> Dict[str, int]:
        """
        Draws measurement shots from the simulated statevector, mirroring what a QPU returns.
        """
        sv = Statevector.from_instruction(self.bind_parameters(params))
        sv.seed(seed)
        return dict(sv.sample_counts(shots))

    def statevector_array(self, params: np.ndarray) -> np.ndarray:
        """
        Final QAOA state as a numpy array (index k: bit i of k is qubit i, Qiskit order).
        Uses the diagonal cost energies directly when available (n <= 20), which matches the
        circuit exactly but avoids rebuilding it; otherwise falls back to Qiskit.
        """
        if not self.use_vectorized:
            return Statevector.from_instruction(self.bind_parameters(params)).data

        n = self.n
        psi = np.full(1 << n, 1.0 / np.sqrt(1 << n), dtype=np.complex128)
        for k in range(self.p_layers):
            gamma, beta = params[k], params[self.p_layers + k]
            # Cost layer: RZ(2*gamma*h) and RZZ(2*gamma*J) together = exp(-i*gamma*diag_E)
            psi *= np.exp(-1j * gamma * self.diag_E)
            # Mixer layer: RX(2*beta) = cos(beta) I - i sin(beta) X on every qubit
            c, s_ = np.cos(beta), -1j * np.sin(beta)
            for q in range(n):
                v = psi.reshape(1 << (n - 1 - q), 2, 1 << q)
                a, b = v[:, 0, :].copy(), v[:, 1, :].copy()
                v[:, 0, :] = c * a + s_ * b
                v[:, 1, :] = s_ * a + c * b
        return psi

    def expectation_value(self, params: np.ndarray) -> float:
        """
        Expected (normalized) cost energy <H_C> + offset.
        """
        if self.use_vectorized:
            probs = np.abs(self.statevector_array(params)) ** 2
            return float(probs @ self.diag_E) + self.offset
        sv = Statevector.from_instruction(self.bind_parameters(params))
        return sv.expectation_value(self.cost_op).real + self.offset

    def cvar_value(self, params: np.ndarray, alpha: float) -> float:
        """
        CVaR_alpha: mean (normalized) energy over the lowest-energy outcomes holding alpha of
        the probability mass. alpha = 1 equals the expectation value.
        """
        if not self.use_vectorized:
            raise ValueError("CVaR objective needs the vectorized path (n <= 20 qubits)")
        probs = np.abs(self.statevector_array(params)) ** 2
        p_sorted = probs[self._energy_order]
        e_sorted = self.diag_E[self._energy_order]
        cum = np.cumsum(p_sorted)
        # Weight of each outcome inside the alpha tail (last one partially)
        w = np.clip(alpha - (cum - p_sorted), 0.0, p_sorted)
        return float(w @ e_sorted) / alpha + self.offset

    def objective(self, params: np.ndarray) -> float:
        # CVaR needs the full energy table, so above 20 qubits fall back to the expectation value
        if self.cvar_alpha is None or not self.use_vectorized:
            return self.expectation_value(params)
        return self.cvar_value(params, self.cvar_alpha)

    def optimize_parameters(self, max_iter: int = 50, seed: int = 42, n_starts: int = 1) -> Tuple[np.ndarray, float]:
        """
        Runs COBYLA on self.objective from n_starts random starting angles and keeps the best.
        Returns (params, objective value).
        """
        rng = np.random.default_rng(seed) if n_starts > 1 else None
        if rng is None:
            # Single start keeps the original seeding, so old results stay reproducible
            np.random.seed(seed)
        best_x, best_f = None, np.inf
        for _ in range(n_starts):
            if rng is None:
                init_params = np.random.uniform(0.1, np.pi / 2, 2 * self.p_layers)
            else:
                init_params = rng.uniform(0.1, np.pi / 2, 2 * self.p_layers)
            res = minimize(
                self.objective,
                init_params,
                method="COBYLA",
                options={"maxiter": max_iter, "disp": False}
            )
            if res.fun < best_f:
                best_x, best_f = res.x, res.fun
        return best_x, best_f

    def sample_probabilities(
        self,
        opt_params: np.ndarray,
        top_k: int = 5,
        apply_qec: bool = True
    ) -> List[Tuple[str, float, float, Dict]]:
        """
        Extracts measurement probabilities and applies Quantum Error Correction (QEC) subspace recovery.
        Uses heapq.nlargest to avoid sorting all 2^N elements.
        """
        sv = Statevector.from_instruction(self.bind_parameters(opt_params))
        probs = sv.probabilities_dict()

        # Improvement 4: Use heapq.nlargest (O(2^N log k) instead of full O(2^N log 2^N) sort)
        top_items = heapq.nlargest(top_k * 2, probs.items(), key=lambda x: x[1])

        raw_samples = []
        bqm = self.optimizer.build_bqm()

        for bitstring, prob in top_items:
            sample = {}
            for i, var_name in enumerate(self.optimizer.var_names):
                bit_val = int(bitstring[self.n - 1 - i])
                sample[var_name] = bit_val
            raw_energy = bqm.energy(sample)
            decoded = self.optimizer.decode_solution(sample)
            raw_samples.append((bitstring, prob, raw_energy, decoded))

        if apply_qec:
            # Apply Quantum Error Correction / Subspace Recovery
            corrected_results = apply_quantum_error_correction(self.optimizer, raw_samples)
            final_samples = [
                (bstr, prob, energy, decoded)
                for bstr, prob, energy, decoded, info in corrected_results[:top_k]
            ]
            return final_samples

        return raw_samples[:top_k]
