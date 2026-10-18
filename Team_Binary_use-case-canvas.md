# Use-Case Canvas

## 1. The problem

> One paragraph. Assume the reader knows nothing about your industry. No jargon. If your grandmother wouldn't follow it, rewrite it.

Large ships cannot steer safely inside a harbor on their own. Small, powerful boats called tugs push and pull them to the dock. A tug company owns only a few of these boats, and each one is expensive to buy and to crew.

Every day the company has to decide which tug helps which ship, and when. Several things make that hard. Some ports are tidal, so the biggest ships can only come in during two 15-minute windows a day, and every waiting ship has to be handled inside that window. Strong wind means each ship needs more tugs, and the wind hits every ship in the port at once. Crews have legally required rest periods.

Ships under contract must confirm at least 2 hours ahead, and until then the company has to keep tugs ready for them. Ships without a contract are extra income, but only if a tug is free. If the company holds back too many tugs "just in case," it loses that income. If it holds back too few, it breaks its contracts. Today a manager makes this plan by hand every day. The question is how to serve every contract and win the most extra jobs with the fewest boats.

## 2. Who has this problem

> Be specific. Not "banks": "mid-size asset managers rebalancing multi-asset portfolios monthly under UCITS constraints."

Harbor towage operators that run fleets of roughly 5 to 50 tugs in ports where two to four competing operators split the traffic. Their 24-hour operations planners schedule tug-to-vessel assignments against a list of announced arrivals. The pressure is highest in two settings:

- **Tidal ports**, like Port of XXX (2 operators, 12 tugs in total, 3 to 4 large vessels per tide window). Demand is concentrated into very short windows.
- **Large hub ports** (Rotterdam, major US ports). Arrivals come in continuously, with peaks, so tugs have to be reassigned throughout the day.

A second decision-maker is the fleet and finance owner. They decide how many tugs to own and which crew roster model to run: 3 crew working 12 on and 12 off, or 6 crew running the tug 24 hours.

## 3. How it's solved today

> What tool, what algorithm, how long does it take, how good is the answer? This is your baseline. Without it you have no story. If you don't know, that's your first research task.

The plan is built by hand every day, and nobody has measured how good it is.

- **Tool:** an arrivals list, similar to an air-traffic list, plus a Gantt chart. The operations manager picks the arrivals that matter and places tugs and crews on the chart for the next 24 hours.
- **Algorithm:** human judgment and experience. Wind, tide, crew rest and contract status are all handled in the manager's head.
- **Time:** to be measured; likely 1 to 2 hours per daily plan, plus manual re-planning when wind or ETAs change.
- **Quality:** unknown, and that is the first research task. Replay past days from historical job logs and count (a) tugs held for contracted vessels that did not confirm by the 2-hour cutoff, (b) spot jobs declined for lack of capacity, (c) tug idle hours. The client's own hypothesis is that fewer tugs could have met past demand.

**Honest note:** a classical MILP or constraint-programming solver (Gurobi, OR-Tools CP-SAT) would probably beat manual planning on its own. The fair baseline for any quantum claim is that solver, not the manager.

## 4. What "better" is worth

> Put a number on it. Currency, hours, lives, tonnes of CO2, error rate, any unit, but a number. "It would be nice" is not an answer.

For one operator with 6 tugs, removing one tug or winning one extra spot job a day is each worth roughly **$2M per year**, on an opex base of about $9M.

Inputs from the client: tug capex $9M; opex $1.5M per year with 3 crew ($0.7M crew plus $0.8M other); 6-crew operation adds about $0.7M or more per tug per year.

| Lever | Value per year | How it is derived |
|---|---|---|
| Avoid one tug | about $1.95M | $1.5M opex plus $9M capex over 20 years (about $0.45M), before financing cost |
| Fleet roster mix | about $0.5M net per tug removed | Moving 2 tugs to 24h operation costs about $1.4M; worth it only if it removes one tug. The optimizer tells you whether it does. |
| Spot revenue | 365 × R per extra daily job | R = average spot fee per job. The client confirmed 5k (EUR or USD) as a fair global average, so about 1.8M. |

## 5. The quantum shape

> Which of the three shapes is it? (Optimization / Simulation / Learning) Why? What's the combinatorial explosion, the quantum system, or the hidden structure?

**Optimization.** Each decision is a yes/no choice: does tug *t* serve vessel *v* in time slot *s*, and do we accept spot vessel *v*? Every choice affects every other one through:

- **Tide windows**, which force many assignments into the same 15 minutes
- **Wind**, which raises the demand of every vessel at the same time
- **Crew rest**, a sliding-window rule that links decisions across 24 hours
- **Contract uncertainty**, where tugs are held for contracted vessels until their confirmation, due at least 2 hours ahead

The search space grows exponentially: for T tugs, V vessels and S slots there are 2^(T·V·S) raw assignments. The core maps naturally to a QUBO, the standard binary-optimization input for quantum algorithms like QAOA: rewards for spot revenue, penalties for unmet demand, double-booking and rest violations.

The uncertain parts (wind scenarios, cancellations) make this a stochastic optimization. That is where classical solvers slow down the most, so it is the most credible place to look for quantum advantage.

## 6. The honest problem-size gap

> How big is a real instance? How big can we run this weekend? State both numbers. The gap is usually several orders of magnitude and saying so out loud is a strength, not a weakness.

The gap between a real hub-port instance and what we run this weekend is **4 to 5 orders of magnitude**.

| Instance | Scope | Binary variables |
|---|---|---|
| Real, tidal port | 6 tugs, 2 windows per day, 4 vessels per window, 24h roster with rest rules, about 96 slots of 15 minutes | about 2,000 to 5,000 after pruning |
| Real, hub port | Rotterdam sees roughly 28,000 seagoing calls a year (approximate, to verify), so 150+ tug jobs a day counting arrivals, departures and shifts; 40+ tugs; 96 slots | about 10^5 to 10^6 |
| This weekend | 4 tugs, 4 vessels, 1 tide window, plus spot-accept flags | about 18 qubits |

A classical solver handles the tidal-port case today, probably in seconds. We say that up front. The weekend shows that the problem can be formulated and solved end to end on a quantum computer. It does not show advantage.

## 7. What would have to be true

> For this to be deployable in five years, what has to happen: in hardware, in algorithms, in your industry, in regulation?

Deployment in five years needs progress on four fronts at once.

- **Hardware:** fault-tolerant machines with hundreds to thousands of logical qubits and deep circuits. IBM's public roadmap targets about 200 logical qubits around 2029 (Starling). The hub-port instance needs more than that, or a decomposition into smaller pieces.
- **Algorithms:** better ways to handle constraints than penalty terms, which waste qubits and flatten the search landscape. Hybrid quantum-classical decomposition (classical master problem, quantum subproblems). A demonstrated advantage on stochastic scheduling against a tuned MILP.
- **Industry:** clean historical job data, real-time AIS position feeds, wind and tide forecasts wired into planning, and operators trusting a recommendation engine over planner intuition.
- **Regulation:** port-authority tug-requirement policies (tugs per vessel class and wind band) and crew rest rules written down in a form a machine can read and audit.

## 8. What we'll actually build this weekend

> A tiny, concrete, achievable thing. Not the vision. The demo. "We map a 12-node instance of our routing problem to a QUBO, run it on an IBM QPU, and compare to a classical solver."

We map one tide window at Port of XXX to an **18-qubit QUBO** and solve it with QAOA in Qiskit.

1. **Instance:** 4 tugs, 4 waiting vessels (2 contracted, 2 spot). Tug demand per vessel comes from the client's length and wind rule (table below).
2. **Variables:** x(t,v) for tug-to-vessel assignment (16) plus y(v) for accepting each spot vessel (2), so 18 qubits.
3. **Objective:** maximize spot revenue minus tug activation cost. Constraints: contracted demand is met, each tug serves at most one vessel per window, and spot demand is met only if the vessel is accepted.
4. **Run:** Qiskit statevector simulator first, then an IBM QPU (Heron-class). Compare against brute force (2^18 is trivial) and CP-SAT.
5. **Demo moment:** sweep wind from low to high and show the optimal decision flip from "take both spot jobs" to "take one" to "take none." That is the business trade-off in one chart.
6. **Stretch:** add a second window with a crew-rest constraint, or one contracted vessel still unconfirmed at the 2-hour cutoff (expected-value reserve).

**Tugs required by vessel length overall (LOA)**, the client's rule of thumb. It varies by port.

| LOA | Normal weather | Windy (above X knots) |
|---|---|---|
| under 200 m | 0 | 1 to 2 |
| 200 m+ | 1 to 2 | 2 to 3 |
| 300 m+ | 2 to 3 | 3 to 4 |
| 400 m+ | 4 | 4 to 5 |

We pick vessel lengths so a calm window fits the 4 tugs easily and a windy one forces a choice between spot jobs. The ranges become a minimum the model must meet and a preferred level it can trade away.

## 9. Why anyone should care

> Three sentences. This becomes your opening slide.

Every tug a harbor operator owns but doesn't need costs about $2M a year, and every tug held back "just in case" is a paying job handed to a competitor. Today that trade-off is made by hand every morning, under wind, tide and crew-rest rules that all interact. We show the decision can be written as a quantum optimization problem today, and we are clear about how far the hardware has to go before it beats a classical solver.
