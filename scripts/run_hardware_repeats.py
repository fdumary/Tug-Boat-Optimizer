"""
Repeated Hardware Runs of the Improved Setup (runs 5 to 60)
==========================================================
Repeats the exact configuration of hardware runs 2 to 4 many times, to measure the run-to-run spread on real hardware.
Nothing is re-tuned: the machine, transpiler seed and QAOA angles are read from a reference run (run 2) and the circuit
is compiled ONCE. The script checks that the compiled circuit matches the reference (same two-qubit gate count and the
same physical qubits) and stops if it does not, so every run executes the same circuit as runs 2 to 4.

Submission, chosen to spend as little IBM time as possible:
  - Each "run" is one PUB (the circuit, 4,000 shots) with the same options as runs 2 to 4: dynamical decoupling, gate and
    measurement twirling. The twirling randomizations are drawn independently for every PUB, as they were for every job.
  - Several PUBs go into one SamplerV2 job (--pubs-per-job), so the fixed per-job overhead is paid once per job, not once
    per run. Each job keeps max_execution_time = 180 s.
  - A pilot job is sent first and its IBM usage is measured. The remaining jobs are sent only if the projected total
    stays under --budget-seconds; otherwise the script sends as many runs as fit and stops.
  - All jobs go out together in one Batch, so they queue back to back.

Outputs (one folder for the whole set):
  results/hardware/<UTC time>_<backend>_T<tugs>_w<wind>_improved_repeats/
    runs.csv            one row per run: metrics, job ID, PUB index, IBM seconds of its job
    batch.json          configuration, circuit check, queue state, per-job usage, per-run metrics
    counts/run_NN.json  raw hardware counts for each run

Credentials: IBM_QUANTUM_TOKEN and IBM_QUANTUM_CRN environment variables only; never written anywhere.

Usage:
  python scripts/run_hardware_repeats.py --ref results/hardware/2026-10-18T0005Z_ibm_kingston_T4_w12_improved --plan
  python scripts/run_hardware_repeats.py --ref results/hardware/2026-10-18T0005Z_ibm_kingston_T4_w12_improved \
      --runs 56 --first-run 5 --pubs-per-job 8 --budget-seconds 200
"""

import sys
import os
import csv
import json
import time
import argparse
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import dimod
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from tug_optimizer import QAOASimulator
from run_hardware_experiment import build_instance, score_counts
from run_hardware_improved import esp, logical_to_physical


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True, help="Reference improved run folder (machine, seed, angles, qubits)")
    ap.add_argument("--plan", action="store_true", help="Compile and check only; no QPU time")
    ap.add_argument("--runs", type=int, default=56)
    ap.add_argument("--first-run", type=int, default=5, help="Number of the first run in this set")
    ap.add_argument("--pubs-per-job", type=int, default=8)
    ap.add_argument("--budget-seconds", type=float, default=200, help="Stop before total IBM usage would pass this")
    ap.add_argument("--shots", type=int, default=4000)
    ap.add_argument("--max-seconds", type=int, default=180)
    args = ap.parse_args()

    ref = json.load(open(os.path.join(args.ref, "run.json")))
    T, wind = ref["instance"]["tugs"], ref["instance"]["wind_knots"]
    params = np.array(ref["qaoa"]["params"])
    backend_name, seed = ref["chosen_backend"], ref["chosen_seed"]
    ref_phys = [q["physical"] for q in ref["noise_model_qubits"]]
    ref_cz = ref["circuit"]["two_qubit_gates"]

    optimizer = build_instance(T, wind)
    n = optimizer.num_variables
    optimum = float(dimod.ExactSolver().sample(optimizer.build_bqm()).first.energy)
    sim = QAOASimulator(optimizer, p_layers=1, cvar_alpha=0.1)

    from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2 as Sampler, Batch
    service = QiskitRuntimeService(channel="ibm_quantum_platform",
                                   token=os.environ["IBM_QUANTUM_TOKEN"], instance=os.environ["IBM_QUANTUM_CRN"])
    backend = service.backend(backend_name)
    status = backend.status()
    queue = {"backend": backend_name, "operational": bool(status.operational), "status_msg": status.status_msg,
             "pending_jobs": int(status.pending_jobs), "checked_utc": datetime.now(timezone.utc).isoformat()}
    print(f"{backend_name}: operational {queue['operational']}, {queue['status_msg']}, {queue['pending_jobs']} jobs queued")
    if not status.operational:
        sys.exit(f"{backend_name} is not available; stopping without switching machines.")

    # Compile once, exactly as runs 2 to 4 (same angles, optimization level 3, same seed), and check it matches
    qc = sim.bind_parameters(params)
    qc.measure_all()
    tqc = generate_preset_pass_manager(backend=backend, optimization_level=3, seed_transpiler=seed).run(qc)
    ops = tqc.count_ops()
    cz = int(sum(v for k, v in ops.items() if k in ("cz", "ecr", "cx")))
    phys = [int(q) for q in logical_to_physical(tqc, n)]
    circuit = {"two_qubit_gates": cz, "transpiled_depth": tqc.depth(), "esp_now": esp(tqc, backend.target),
               "physical_qubits": phys, "ops": {k: int(v) for k, v in ops.items()},
               "matches_reference": cz == ref_cz and phys == ref_phys}
    print(f"Compiled: {cz} CZ (reference {ref_cz}), depth {circuit['transpiled_depth']}, ESP now {circuit['esp_now']:.4f}, "
          f"qubits {'same as' if phys == ref_phys else 'DIFFERENT from'} reference")
    if not circuit["matches_reference"]:
        sys.exit("Compiled circuit differs from runs 2 to 4; stopping so the runs stay comparable.")
    if args.plan:
        n_jobs = -(-args.runs // args.pubs_per_job)
        print(f"Plan: {args.runs} runs as {n_jobs} jobs of up to {args.pubs_per_job} PUBs x {args.shots} shots, "
              f"pilot job first, budget {args.budget_seconds:g} s. No job submitted.")
        return

    def make_sampler(mode):
        s = Sampler(mode=mode)
        s.options.default_shots = args.shots
        s.options.dynamical_decoupling.enable = True
        s.options.twirling.enable_gates = True
        s.options.twirling.enable_measure = True
        s.options.max_execution_time = args.max_seconds
        return s

    def collect(job, k):
        res = job.result()
        return [{k2: int(v) for k2, v in res[i].data.meas.get_counts().items()} for i in range(k)]

    def usage(job):
        for _ in range(10):
            try:
                u = job.usage()
                if u is not None:
                    return float(u)
            except Exception:
                pass
            time.sleep(2)
        return None

    t_start = time.time()
    jobs, all_counts = [], []
    remaining = args.runs
    with Batch(backend=backend) as batch:
        sampler = make_sampler(batch)
        # Pilot job
        k = min(args.pubs_per_job, remaining)
        t0 = time.time()
        job = sampler.run([tqc] * k)
        print(f"Pilot job {job.job_id()} with {k} runs submitted, waiting...")
        all_counts += collect(job, k)
        u = usage(job)
        jobs.append({"job_id": job.job_id(), "pubs": k, "usage_seconds": u, "wall_seconds": time.time() - t0})
        remaining -= k
        print(f"  pilot done: {u} s IBM usage for {k} runs")
        per_job = u if u else 3.0 * k
        # How many more full jobs fit in the budget
        afford = int((args.budget_seconds - per_job) // per_job) if per_job else 0
        n_more = min(-(-remaining // args.pubs_per_job), max(afford, 0))
        if n_more * args.pubs_per_job < remaining:
            print(f"  budget allows {n_more} more jobs; {remaining - n_more * args.pubs_per_job} runs will not be sent")
        pending = []
        for _ in range(n_more):
            k = min(args.pubs_per_job, remaining)
            if k <= 0:
                break
            pending.append((sampler.run([tqc] * k), k, time.time()))
            remaining -= k
        print(f"Submitted {len(pending)} more jobs: {[j.job_id() for j, _, _ in pending]}")
        for job, k, t0 in pending:
            all_counts += collect(job, k)
            u = usage(job)
            jobs.append({"job_id": job.job_id(), "pubs": k, "usage_seconds": u, "wall_seconds": time.time() - t0})
            print(f"  {job.job_id()}: {k} runs, {u} s")
        try:
            batch_usage = batch.usage()
        except Exception:
            batch_usage = None
    wall = time.time() - t_start

    # Score every run
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    out_dir = os.path.abspath(os.path.join(HERE, "..", "results", "hardware",
                                           f"{stamp}_{backend_name}_T{T}_w{int(wind)}_improved_repeats"))
    os.makedirs(os.path.join(out_dir, "counts"), exist_ok=True)
    rows, i = [], 0
    for j in jobs:
        for p in range(j["pubs"]):
            counts = all_counts[i]
            run_no = args.first_run + i
            m = score_counts(optimizer, counts, optimum)
            m = {k: v for k, v in m.items() if not k.startswith("_")}
            m["share_optimal_among_valid"] = m["share_optimal"] / m["share_valid"] if m["share_valid"] else 0.0
            rows.append({"run": run_no, "job_id": j["job_id"], "pub_index": p, "job_pubs": j["pubs"],
                         "job_usage_seconds": j["usage_seconds"],
                         "usage_seconds_per_run": (j["usage_seconds"] / j["pubs"]) if j["usage_seconds"] else None,
                         "shots": m["shots"], "optimal_shots": int(round(m["share_optimal"] * m["shots"])),
                         "valid_shots": int(round(m["share_valid"] * m["shots"])),
                         "share_optimal": m["share_optimal"], "share_valid": m["share_valid"],
                         "share_optimal_among_valid": m["share_optimal_among_valid"], "found_optimum": m["found_optimum"],
                         "best_energy": m["best_energy"], "mean_energy": m["mean_energy"],
                         "distinct_bitstrings": m["distinct_bitstrings"]})
            json.dump(counts, open(os.path.join(out_dir, "counts", f"run_{run_no:02d}.json"), "w"))
            i += 1
    with open(os.path.join(out_dir, "runs.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    total_usage = sum(j["usage_seconds"] or 0 for j in jobs)
    json.dump({"instance": ref["instance"], "reference_run": os.path.basename(os.path.normpath(args.ref)),
               "qaoa": {"p_layers": 1, "params": params.tolist()}, "backend": backend_name, "transpiler_seed": seed,
               "options": "dynamical decoupling + gate and measurement twirling", "shots_per_run": args.shots,
               "max_execution_time_s": args.max_seconds, "queue_before_submit": queue, "circuit": circuit,
               "jobs": jobs, "total_usage_seconds": total_usage, "batch_usage_seconds": batch_usage,
               "wall_seconds_incl_queue": wall, "runs": rows}, open(os.path.join(out_dir, "batch.json"), "w"), indent=2)
    so = np.array([r["share_optimal"] for r in rows])
    print(f"\n{len(rows)} runs, {total_usage:g} s IBM usage, wall {wall:.0f} s")
    print(f"Optimal share: mean {100*so.mean():.3f}%, s.d. {100*so.std(ddof=1):.3f}%, "
          f"min {100*so.min():.3f}%, max {100*so.max():.3f}%; optimum found in {sum(r['found_optimum'] for r in rows)}/{len(rows)}")
    print(f"Saved to {out_dir}")


if __name__ == "__main__":
    main()
