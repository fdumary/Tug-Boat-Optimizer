"""
Statistics and distribution chart for all IBM hardware runs (run 1, runs 2-4, and the repeats set).
Reads only saved results (no QPU). Usage:
  python scripts/summarize_hardware_runs.py results/hardware/<repeats folder>
Writes stats.json and optimal_share_distribution.png into the repeats folder.
"""
import sys, os, json, csv, glob
import numpy as np
from scipy import stats as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

rep = sys.argv[1]
hw = os.path.dirname(os.path.abspath(rep))
runs = []  # (run, folder, job, share_optimal, share_valid, among_valid, found, best_energy, usage_per_run, shots)
early = sorted(d for d in glob.glob(os.path.join(hw, "2026-*")) if not d.rstrip("/").endswith("_repeats"))
for i, d in enumerate(early, start=1):
    r = json.load(open(os.path.join(d, "run.json")))
    m = r["metrics_hardware"]
    runs.append({"run": i, "setup": "run 1 (ibm_fez)" if i == 1 else "improved", "job_id": r["hardware"]["job_id"],
                 "shots": m["shots"], "share_optimal": m["share_optimal"], "share_valid": m["share_valid"],
                 "share_optimal_among_valid": m["share_optimal"] / m["share_valid"], "found_optimum": m["found_optimum"],
                 "best_energy": m["best_energy"], "usage_seconds_per_run": r["hardware"]["qpu_usage_seconds"]})
for row in csv.DictReader(open(os.path.join(rep, "runs.csv"))):
    runs.append({"run": int(row["run"]), "setup": "improved", "job_id": row["job_id"], "shots": int(row["shots"]),
                 "share_optimal": float(row["share_optimal"]), "share_valid": float(row["share_valid"]),
                 "share_optimal_among_valid": float(row["share_optimal_among_valid"]),
                 "found_optimum": row["found_optimum"] == "True", "best_energy": float(row["best_energy"]),
                 "usage_seconds_per_run": float(row["usage_seconds_per_run"])})

def describe(sel):
    so = np.array([r["share_optimal"] for r in sel]); sv = np.array([r["share_valid"] for r in sel])
    sa = np.array([r["share_optimal_among_valid"] for r in sel]); n = len(sel)
    shots = np.array([r["shots"] for r in sel]); k = so * shots
    p = k.sum() / shots.sum()
    # Over-dispersion test: are the runs more spread out than 4,000-shot sampling alone would make them?
    chi2 = float(((k - shots * p) ** 2 / (shots * p * (1 - p))).sum())
    return {"n_runs": n, "found_optimum": int(sum(r["found_optimum"] for r in sel)),
            "optimal": {"mean": so.mean(), "sd": so.std(ddof=1), "sem": so.std(ddof=1) / np.sqrt(n), "median": float(np.median(so)),
                        "min": so.min(), "max": so.max(), "pooled": p, "pooled_optimal_shots": int(k.sum()), "pooled_shots": int(shots.sum()),
                        "binomial_sd_expected": float(np.sqrt(p * (1 - p) / shots.mean())),
                        "dispersion_chi2": chi2, "dispersion_df": n - 1, "dispersion_p": float(st.chi2.sf(chi2, n - 1))},
            "valid": {"mean": sv.mean(), "sd": sv.std(ddof=1), "min": sv.min(), "max": sv.max()},
            "optimal_among_valid": {"mean": sa.mean(), "sd": sa.std(ddof=1), "min": sa.min(), "max": sa.max()},
            "usage_seconds_total": float(sum(r["usage_seconds_per_run"] for r in sel))}

imp = [r for r in runs if r["setup"] == "improved"]
rep_only = [r for r in imp if r["run"] >= 5]
out = {"all_60": describe(runs), "improved_59": describe(imp), "runs_2_4": describe([r for r in imp if r["run"] <= 4]),
       "repeats_5_60": describe(rep_only), "run_1": runs[0]}
# Do the 7 jobs differ from each other? (one-way ANOVA on optimal share)
groups = {}
for r in rep_only:
    groups.setdefault(r["job_id"], []).append(r["share_optimal"])
f, pa = st.f_oneway(*groups.values())
out["repeats_between_jobs_anova"] = {"F": float(f), "p": float(pa), "job_means": {j: float(np.mean(v)) for j, v in groups.items()}}
# Runs 2-4 vs repeats (pooled two-proportion z-test)
a, b = out["runs_2_4"]["optimal"], out["repeats_5_60"]["optimal"]
pp = (a["pooled_optimal_shots"] + b["pooled_optimal_shots"]) / (a["pooled_shots"] + b["pooled_shots"])
z = (b["pooled"] - a["pooled"]) / np.sqrt(pp * (1 - pp) * (1 / a["pooled_shots"] + 1 / b["pooled_shots"]))
out["runs_2_4_vs_repeats_z"] = {"z": float(z), "p_two_sided": float(2 * st.norm.sf(abs(z)))}
out["runs"] = runs
json.dump(out, open(os.path.join(rep, "stats.json"), "w"), indent=2, default=float)

# Chart: histogram of the optimal share over the 59 improved runs, binomial expectation, run 1 and simulator marked
BLUE, INK, INK2, GRID = "#2a78d6", "#0b0b0b", "#52514e", "#e4e3df"
so = np.array([100 * r["share_optimal"] for r in imp]); p = out["improved_59"]["optimal"]["pooled"]
fig, ax = plt.subplots(figsize=(9, 4.8)); fig.patch.set_facecolor("#fcfcfb"); ax.set_facecolor("#fcfcfb")
bins = np.arange(0.3, 1.2001, 0.075)
ax.hist(so, bins=bins, color=BLUE, edgecolor="#fcfcfb", linewidth=2, label=f"Improved setup, {len(imp)} runs")
x = np.arange(0, 60); pm = st.binom.pmf(x, 4000, p)
ax.plot(100 * x / 4000, pm * len(imp) * (0.075 / 0.025), color=INK2, linewidth=2, linestyle="--",
        label="Expected from shot noise alone (binomial)")
r1 = 100 * runs[0]["share_optimal"]
ax.axvline(r1, color=INK, linewidth=2, linestyle=":")
ax.annotate(f"Run 1 (ibm_fez)\n{r1:.2f}%", (r1, ax.get_ylim()[1] * 0.45), xytext=(6, 0), textcoords="offset points", fontsize=9, color=INK)
m = so.mean()
ax.axvline(m, color=INK, linewidth=1.5)
ax.annotate(f"Mean {m:.2f}%", (m, ax.get_ylim()[1] * 0.95), xytext=(6, 0), textcoords="offset points", fontsize=9, color=INK)
ax.set_xlabel("Share of shots that are the optimal schedule (%)", color=INK2)
ax.set_ylabel("Number of runs", color=INK2)
ax.set_title("4 tugs, 18 qubits, QAOA p=1 on IBM hardware: 60 runs of 4,000 shots\n"
             "(noiseless simulator: 3.44%; random guessing: 0.009%)", fontsize=11, color=INK)
ax.grid(axis="y", color=GRID); ax.set_axisbelow(True)
for s in ("top", "right"): ax.spines[s].set_visible(False)
ax.set_xlim(0.2, 1.2); ax.legend(fontsize=9, frameon=True, facecolor="#fcfcfb", edgecolor="none", framealpha=1, loc="upper left")
plt.tight_layout(); plt.savefig(os.path.join(rep, "optimal_share_distribution.png"), dpi=150); plt.close()
for k in ("all_60", "improved_59", "runs_2_4", "repeats_5_60"):
    d = out[k]; o = d["optimal"]
    print(f"{k}: n={d['n_runs']} found={d['found_optimum']} opt mean {100*o['mean']:.3f} sd {100*o['sd']:.3f} sem {100*o['sem']:.3f} "
          f"min {100*o['min']:.3f} max {100*o['max']:.3f} binom_sd {100*o['binomial_sd_expected']:.3f} disp_p {o['dispersion_p']:.3f} | "
          f"valid {100*d['valid']['mean']:.3f}±{100*d['valid']['sd']:.3f} | among {100*d['optimal_among_valid']['mean']:.1f}±{100*d['optimal_among_valid']['sd']:.1f} | usage {d['usage_seconds_total']}")
print("anova", out["repeats_between_jobs_anova"]["F"], out["repeats_between_jobs_anova"]["p"], "z 2-4 vs rep", out["runs_2_4_vs_repeats_z"])
print({j[-6:]: round(100*v,3) for j,v in out["repeats_between_jobs_anova"]["job_means"].items()})
