# Limitations and future work

This project is a proof of concept. It solves an **idealised, simplified** tug-allocation problem to show
that the method works on real quantum hardware. It is not yet a tool a port could run.

## What the model simplifies

- **One time window.** Every ship arrives in the same tidal window, so the model only decides which tug goes to
  which ship. There are no time slots, schedules, tug travel times or changing tides.
- **Identical tugs.** All tugs count the same. Real fleets mix tug sizes and power, and the
  `bollard_pull_tonnes` field is not used yet.
- **Placeholder rules.** Tugs needed per ship come from simple size-and-wind rules (`compute_tug_demand`),
  not from official Port of Hamburg regulations. Wind is one fixed speed (no direction or gusts).
- **Illustrative money.** Tug costs and spot-ship revenues are example numbers, not market prices.
- **Small instance.** The hardware runs used 4 tugs, 4 ships (2 contract, 2 spot), 12 kn wind: 18 qubits.

## What the results do and do not show

- On IBM hardware (QAOA, p = 1, 4,000 shots per run), all 60 runs found the optimal plan. Over the 59 improved
  runs, 0.77% ± 0.14% of shots were optimal: about 85 times random guessing, but only about 22% of the
  noiseless simulator's 3.44%. Source: `results/hardware/SUMMARY.md`.
- Noise is the limit. We did not run more than 4 tugs on hardware; circuit size grows quickly with tugs and
  ships, so larger instances are expected to be much harder on today's gate-based machines.
- **No quantum advantage is claimed.** A classical exhaustive search solves this 18-variable problem in seconds
  on a laptop. Classical methods also handle real port sizes: Jia et al. (2021, *Transportation Science*,
  doi:10.1287/trsc.2021.1079) schedule 50-150 ships with 24-32 tugs within a 1-hour limit.
- The "error correction" step is classical post-processing that repairs measured bitstrings using the problem's
  rules. It is not quantum error correction.

## Future work

1. **The full problem:** many ships over a day, time slots, tugs of different power, travel times, changing
   tides and wind, crew shifts, and outside risks (weather events, disruptions, geopolitical shocks to shipping).
2. **Real data:** official Hamburg tug rules, real ship calls, and live tide and wind feeds.
3. **Quantum annealing:** QAOA is in effect a step-by-step version of quantum annealing, and the problem is
   already a QUBO, so a quantum annealer (for example D-Wave) is the natural next platform for larger
   instances. This has not been tested; no annealing speed-up is claimed. (Simulated annealing is a classical
   method and would not count as a quantum result.)
4. **A fair classical baseline** on the same instances, to measure where, if anywhere, quantum methods help.
