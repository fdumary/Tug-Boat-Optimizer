"""
Improved Hardware Run (run 2)
=============================
Same tug instance and the same QAOA circuit shape as run_hardware_experiment.py (run 1), with two changes
aimed at raising the share of optimal shots on real hardware:

  1. Leaner placement. The circuit is transpiled with many seeds on each available IBM machine and we keep
     the (machine, seed) with the highest estimated success probability (ESP = product of (1 - error) over
     every gate and measurement, from each machine's current calibration). This favours fewer two-qubit
     gates on the best qubits of the best-calibrated machine.
  2. Noise-aware angles. (gamma, beta) are chosen to maximise the share of optimal shots under a simple
     model of the chosen qubits' noise, instead of CVaR on the noiseless simulator:
        ideal QAOA distribution
        -> per-qubit asymmetric bit flips: readout error (prob_meas1_prep0, prob_meas0_prep1) plus
           T1 decay 1 -> 0 over the circuit's duration
        -> global depolarising with fidelity F = ESP (spreads the rest uniformly; does not move the best angles,
           only scales the prediction)
     The angles come from a grid scan followed by Nelder-Mead refinement. This uses no QPU time.
     They replace run 1's angles only if the model predicts more optimal shots with them; otherwise run 1's
     angles are kept, so step 1 is the only change.

Then ONE SamplerV2 job, same options as run 1 (dynamical decoupling, gate and measurement twirling,
4,000 shots, max_execution_time 180 s). Results go to
  results/hardware/<UTC time>_<backend>_T<tugs>_w<wind>_improved/
with run.json, raw counts, outcomes.png and energy_cdf.png comparing run 1 and run 2.

Credentials: IBM_QUANTUM_TOKEN and IBM_QUANTUM_CRN environment variables only; never written anywhere.

Usage:
  python scripts/run_hardware_improved.py --run1 results/hardware/2026-10-17T2350Z_ibm_fez_T4_w12 --no-submit
  python scripts/run_hardware_improved.py --run1 results/hardware/2026-10-17T2350Z_ibm_fez_T4_w12
"""

import sys
import os
import json
import time
import argparse
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import minimize
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import dimod
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from tug_optimizer import QAOASimulator
from run_hardware_experiment import build_instance, score_counts


def esp(tqc, target) -> float:
    """Estimated success probability: product of (1 - error) over every gate and measurement."""
    log_f = 0.0
    for inst in tqc.data:
        name = inst.operation.name
        if name in ("barrier", "delay"):
            continue
        qargs = tuple(tqc.find_bit(q).index for q in inst.qubits)
        try:
            err = target[name][qargs].error
        except (KeyError, TypeError):
            err = None
        if err:
            log_f += np.log1p(-min(err, 0.999))
    return float(np.exp(log_f))


def logical_to_physical(tqc, n: int) -> list:
    """Physical qubit each logical qubit is measured on (after routing)."""
    return list(tqc.layout.final_index_layout()[:n])


def flip_rates(backend, phys: list, duration_s: float):
    """Per logical qubit: P(read 1 | was 0) and P(read 0 | was 1), including T1 decay over the circuit."""
    props = backend.properties()
    p01, p10, info = [], [], []
    for q in phys:
        r01 = props.qubit_property(q, "prob_meas1_prep0")[0]
        r10 = props.qubit_property(q, "prob_meas0_prep1")[0]
        t1 = props.t1(q)
        decay = 1.0 - np.exp(-duration_s / t1) if t1 else 0.0
        p01.append(r01)
        p10.append(r10 + (1 - r10) * decay)
        info.append({"physical": int(q), "readout_p01": r01, "readout_p10": r10, "t1_us": t1 * 1e6, "decay": decay})
    return np.array(p01), np.array(p10), info


def apply_flips(probs: np.ndarray, n: int, p01: np.ndarray, p10: np.ndarray) -> np.ndarray:
    """Apply independent per-qubit bit-flip channels to a distribution (bit i of the index is qubit i)."""
    t = probs.reshape((2,) * n)  # axis 0 is the highest qubit (n-1), axis n-1 is qubit 0
    for q in range(n):
        ax = n - 1 - q
        a = np.take(t, 0, axis=ax)
        b = np.take(t, 1, axis=ax)
        new0 = (1 - p01[q]) * a + p10[q] * b
        new1 = p01[q] * a + (1 - p10[q]) * b
        t = np.stack([new0, new1], axis=ax)
    return t.reshape(-1)


def tune_noise_aware(sim, opt_mask, p01, p10, grid: int):
    """Maximise P(optimal) after the bit-flip channel: grid scan, then Nelder-Mead from the best 3 points."""
    n = sim.n
    def p_opt(x):
        probs = np.abs(sim.statevector_array(x)) ** 2
        return float(apply_flips(probs, n, p01, p10)[opt_mask].sum())
    gs = np.linspace(0, np.pi, grid, endpoint=False)
    bs = np.linspace(0, np.pi, grid, endpoint=False)
    scores = np.array([[p_opt([g, b]) for b in bs] for g in gs])
    best = np.argsort(scores.ravel())[::-1][:3]
    results = []
    for k in best:
        x0 = [gs[k // grid], bs[k % grid]]
        res = minimize(lambda x: -p_opt(x), x0, method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-7})
        results.append((-res.fun, res.x))
    val, x = max(results, key=lambda r: r[0])
    return np.array(x), val, p_opt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run1", required=True, help="Run 1 results folder (for its angles, counts and comparison)")
    ap.add_argument("--seeds", type=int, default=300, help="Transpiler seeds to try per machine")
    ap.add_argument("--backends", default="ibm_fez,ibm_kingston,ibm_marrakesh", help="Machines to consider")
    ap.add_argument("--grid", type=int, default=48, help="Grid points per angle for the noise-aware scan")
    ap.add_argument("--shots", type=int, default=4000)
    ap.add_argument("--max-seconds", type=int, default=180)
    ap.add_argument("--no-submit", action="store_true", help="Do everything except submit the hardware job")
    args = ap.parse_args()

    run1 = json.load(open(os.path.join(args.run1, "run.json")))
    T, wind = run1["instance"]["tugs"], run1["instance"]["wind_knots"]
    params1 = np.array(run1["qaoa"]["params"])
    backend_name = run1["hardware"]["backend"]

    optimizer = build_instance(T, wind)
    n = optimizer.num_variables
    optimum = float(dimod.ExactSolver().sample(optimizer.build_bqm()).first.energy)
    sim = QAOASimulator(optimizer, p_layers=1, cvar_alpha=0.1)
    opt_mask = sim.diag_E <= sim.diag_E.min() + 1e-9
    uniform_p_opt = float(opt_mask.mean())

    from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2 as Sampler
    service = QiskitRuntimeService(channel="ibm_quantum_platform",
                                   token=os.environ["IBM_QUANTUM_TOKEN"], instance=os.environ["IBM_QUANTUM_CRN"])
    print(f"{n} qubits, optimum {optimum}")

    def transpile(backend, params, seed):
        qc = sim.bind_parameters(params)
        qc.measure_all()
        return generate_preset_pass_manager(backend=backend, optimization_level=3, seed_transpiler=seed).run(qc)

    def stats(backend, tqc):
        ops = tqc.count_ops()
        return {"transpiled_depth": tqc.depth(), "two_qubit_gates": int(sum(v for k, v in ops.items() if k in ("cz", "ecr", "cx"))),
                "esp": esp(tqc, backend.target), "duration_us": tqc.estimate_duration(backend.target, unit="s") * 1e6,
                "ops": {k: int(v) for k, v in ops.items()}}

    def predict(backend, params, tqc):
        """Model-predicted share of optimal shots on hardware for these angles and this compiled circuit."""
        s = stats(backend, tqc)
        a01, a10, _ = flip_rates(backend, logical_to_physical(tqc, n), s["duration_us"] * 1e-6)
        probs = apply_flips(np.abs(sim.statevector_array(params)) ** 2, n, a01, a10)
        return s["esp"] * float(probs[opt_mask].sum()) + (1 - s["esp"]) * uniform_p_opt

    p_ideal = lambda x: float((np.abs(sim.statevector_array(x)) ** 2)[opt_mask].sum())

    # Run 1 as it was compiled (its machine, seed 1), under today's calibration: checks the model against the measured value
    b_run1 = service.backend(backend_name)
    t_run1 = transpile(b_run1, params1, seed=1)
    s_run1 = stats(b_run1, t_run1)
    pred_run1 = predict(b_run1, params1, t_run1)
    print(f"Run 1 circuit now: {s_run1['two_qubit_gates']} CZ, depth {s_run1['transpiled_depth']}, ESP {s_run1['esp']:.4f}; "
          f"model predicts {100*pred_run1:.2f}% optimal (measured {100*run1['metrics_hardware']['share_optimal']:.2f}%)")

    # 1. Seed sweep on every machine, keep the highest ESP
    t0 = time.time()
    sweep, best = [], None
    for name in args.backends.split(","):
        b = service.backend(name)
        best_b = None
        for seed in range(args.seeds):
            tq = transpile(b, params1, seed)
            e = esp(tq, b.target)
            if best_b is None or e > best_b[0]:
                best_b = (e, seed, tq)
        st = stats(b, best_b[2])
        sweep.append({"backend": name, "best_seed": best_b[1], "esp": st["esp"], "two_qubit_gates": st["two_qubit_gates"],
                      "depth": st["transpiled_depth"], "pending_jobs": b.status().pending_jobs})
        print(f"  {name}: best seed {best_b[1]}, {st['two_qubit_gates']} CZ, depth {st['transpiled_depth']}, ESP {st['esp']:.4f}")
        if best is None or best_b[0] > best[0]:
            best = (best_b[0], best_b[1], best_b[2], b)
    _, seed, tqc1, backend = best
    print(f"Seed sweep ({time.time()-t0:.0f}s): chose {backend.name} seed {seed}")

    # 2. Noise-aware angles for the chosen qubits; keep them only if the model predicts a gain
    s1 = stats(backend, tqc1)
    p01, p10, qubit_info = flip_rates(backend, logical_to_physical(tqc1, n), s1["duration_us"] * 1e-6)
    t0 = time.time()
    params_na, _, _ = tune_noise_aware(sim, opt_mask, p01, p10, args.grid)
    tune_s = time.time() - t0
    tqc_na = transpile(backend, params_na, seed)
    pred_keep, pred_na = predict(backend, params1, tqc1), predict(backend, params_na, tqc_na)
    use_na = pred_na > pred_keep
    params2, tqc = (params_na, tqc_na) if use_na else (params1, tqc1)
    s_final = stats(backend, tqc)
    pred_run2 = max(pred_na, pred_keep)
    print(f"Noise-aware angles ({tune_s:.0f}s): {np.round(params_na, 4).tolist()}; noiseless P(opt) {100*p_ideal(params_na):.2f}% "
          f"vs run-1 angles {100*p_ideal(params1):.2f}%; model hw P(opt) {100*pred_na:.2f}% vs {100*pred_keep:.2f}% "
          f"-> using {'noise-aware' if use_na else 'run-1'} angles")
    print(f"Final circuit on {backend.name}: {s_final['two_qubit_gates']} CZ, depth {s_final['transpiled_depth']}, ESP {s_final['esp']:.4f}")
    print(f"Model-predicted hardware P(optimal): run 1 {100*pred_run1:.2f}%, run 2 {100*pred_run2:.2f}%")

    sim_counts = sim.sample_counts(params2, shots=args.shots, seed=7)
    sim_m = score_counts(optimizer, sim_counts, optimum)
    sim_m["exact_p_optimal_per_shot"] = p_ideal(params2)

    plan = {
        "instance": run1["instance"],
        "changes_vs_run1": [f"machine and transpiler seed chosen by estimated success probability ({args.seeds} seeds x {args.backends})",
                            "noise-aware angles " + ("used" if use_na else "tested but not used: model predicted no gain")],
        "qaoa": {"p_layers": 1, "params_run1": params1.tolist(), "params": [float(x) for x in params2],
                 "params_noise_aware_candidate": [float(x) for x in params_na], "used_noise_aware": bool(use_na),
                 "noise_aware_tuning": f"grid {args.grid}x{args.grid} over [0,pi)^2 then Nelder-Mead from best 3; objective P(optimal) after bit-flip channel",
                 "tuning_seconds_local": tune_s},
        "seed_sweep": sweep, "chosen_backend": backend.name, "chosen_seed": seed,
        "circuit_run1_recompiled_now": s_run1, "circuit": s_final,
        "noise_model_qubits": qubit_info,
        "predictions": {"noiseless_p_opt_run1_angles": p_ideal(params1), "noiseless_p_opt_noise_aware_angles": p_ideal(params_na),
                        "model_hw_p_opt_run1": pred_run1, "model_hw_p_opt_run2_keep_angles": pred_keep,
                        "model_hw_p_opt_run2_noise_aware_angles": pred_na,
                        "uniform_random_p_opt": uniform_p_opt},
    }
    if args.no_submit:
        print(json.dumps({k: plan[k] for k in ("chosen_backend", "chosen_seed", "circuit", "predictions")}, indent=1))
        return

    # 3. One hardware job
    sampler = Sampler(mode=backend)
    sampler.options.default_shots = args.shots
    sampler.options.dynamical_decoupling.enable = True
    sampler.options.twirling.enable_gates = True
    sampler.options.twirling.enable_measure = True
    sampler.options.max_execution_time = args.max_seconds
    t0 = time.time()
    job = sampler.run([tqc])
    job_id = job.job_id()
    print(f"Submitted job {job_id}, waiting...")
    hw_counts = {k: int(v) for k, v in job.result()[0].data.meas.get_counts().items()}
    wall_s = time.time() - t0
    try:
        usage_s = job.usage()
    except Exception:
        usage_s = None
    hw_m = score_counts(optimizer, hw_counts, optimum)

    # Run 1 hardware, re-scored with the same code
    hw1_m = score_counts(optimizer, json.load(open(os.path.join(args.run1, "counts_hardware.json"))), optimum)
    sim1_m = score_counts(optimizer, json.load(open(os.path.join(args.run1, "counts_simulator.json"))), optimum)
    for m in (sim_m, hw_m, hw1_m, sim1_m):
        m["share_optimal_among_valid"] = m["share_optimal"] / m["share_valid"] if m["share_valid"] else 0.0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    out_dir = os.path.abspath(os.path.join(HERE, "..", "results", "hardware",
                                           f"{stamp}_{backend.name}_T{T}_w{int(wind)}_improved"))
    os.makedirs(out_dir, exist_ok=True)
    json.dump(hw_counts, open(os.path.join(out_dir, "counts_hardware.json"), "w"))
    json.dump({k: int(v) for k, v in sim_counts.items()}, open(os.path.join(out_dir, "counts_simulator.json"), "w"))
    strip = lambda m: {k: v for k, v in m.items() if not k.startswith("_")}
    run = dict(plan)
    run["hardware"] = {"backend": backend.name, "job_id": job_id, "max_execution_time_s": args.max_seconds,
                       "qpu_usage_seconds": usage_s, "wall_seconds_incl_queue": wall_s,
                       "options": "dynamical decoupling + gate and measurement twirling", "run1_job_id": run1["hardware"]["job_id"]}
    run["metrics_simulator"] = strip(sim_m)
    run["metrics_hardware"] = strip(hw_m)
    run["metrics_run1_hardware"] = strip(hw1_m)
    run["metrics_run1_simulator"] = strip(sim1_m)
    json.dump(run, open(os.path.join(out_dir, "run.json"), "w"), indent=2)

    # Plots
    title = f"{T} tugs, {wind:g} kn, {n} qubits, p=1: run 1 ({backend_name}) vs run 2 ({backend.name})"
    if use_na:
        series = [(sim1_m, "Simulator, run-1 angles"), (sim_m, "Simulator, run-2 angles"),
                  (hw1_m, f"Hardware run 1 ({backend_name})"), (hw_m, f"Hardware run 2 ({backend.name})")]
    else:
        series = [(sim1_m, "Simulator (noiseless)"), (hw1_m, f"Hardware run 1 ({backend_name})"),
                  (hw_m, f"Hardware run 2 ({backend.name}, leaner)")]
    cats = ["Optimal schedule", "Valid, not optimal", "Breaks a rule"]
    x = np.arange(len(cats)); w = 0.8 / len(series)
    fig, ax = plt.subplots(figsize=(9, 4.8))
    for i, (m, label) in enumerate(series):
        vals = [100 * m["share_optimal"], 100 * (m["share_valid"] - m["share_optimal"]), 100 * (1 - m["share_valid"])]
        b = ax.bar(x + (i - (len(series) - 1) / 2) * w, vals, w, label=label)
        ax.bar_label(b, fmt="%.2f%%", fontsize=7)
    ax.set_xticks(x, cats); ax.set_ylabel("Share of shots (%)"); ax.set_title(title)
    ax.legend(fontsize=8); ax.grid(axis="y", linestyle="--", alpha=0.6)
    plt.tight_layout(); plt.savefig(os.path.join(out_dir, "outcomes.png"), dpi=150); plt.close()

    fig, ax = plt.subplots(figsize=(9, 4.8))
    for m, label in series:
        e = np.array(m["_energies"]); wt = np.array(m["_weights"], dtype=float); o = np.argsort(e)
        ax.step(e[o], np.cumsum(wt[o]) / wt.sum() * 100, where="post", label=label)
    ax.axvline(optimum, color="gray", linestyle=":", label=f"Optimum ({optimum:,.0f})")
    ax.set_xscale("symlog", linthresh=10000)
    ax.set_xlabel("QUBO energy of shot (lower is better)"); ax.set_ylabel("Shots at or below this energy (%)")
    ax.set_title(title); ax.legend(fontsize=8); ax.grid(linestyle="--", alpha=0.6)
    plt.tight_layout(); plt.savefig(os.path.join(out_dir, "energy_cdf.png"), dpi=150); plt.close()

    print("\n                        simulator  hw run 1  hw run 2")
    for key, label in (("share_optimal", "Optimal shots"), ("share_valid", "Valid shots"),
                       ("share_optimal_among_valid", "Optimal among valid")):
        print(f"  {label:<22} {100*sim_m[key]:10.2f}%  {100*hw1_m[key]:7.2f}%  {100*hw_m[key]:7.2f}%")
    print(f"  Found optimum {sim_m['found_optimum']} {hw1_m['found_optimum']} {hw_m['found_optimum']}")
    print(f"  QPU usage: {usage_s} s, wall incl. queue {wall_s:.0f} s")
    print(f"Saved to {out_dir}")


if __name__ == "__main__":
    main()
