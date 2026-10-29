# Method of mbal.py

Tested on three OPM Flow runs in `tests/data/mbal` (synthetic decks in this repository). Results:

| Case | Simulator in-place | mbal.py | Note |
|---|---|---|---|
| OILBOX, depletion below Pb | N 21.373 MMstb | N 21.381 MMstb (+0.04 %) | Range at 1 % data error: 21.27 to 21.49 |
| GASBOX, dry gas | G 35.996 Bscf | G 35.994 Bscf (-0.01 %) | p/z line gives 36.45 Bscf. It ignores cw and cf |
| OILBOX_AQ, Fetkovich aquifer | N 21.373 MMstb | range 8.8 to 42 MMstb | The data does not determine N. See below |

For OILBOX_AQ with N fixed at the simulator value, the Fetkovich fit gives K 13 965 rb/psi (deck 14 000 stb/psi),
tau 688 days (deck 700), and a cumulative influx within 1 % of the simulator.

## Equations
Havlena and Odeh (1963), in the form F = N Et + We.

| Term | Oil | Gas |
|---|---|---|
| F | Np (Bo + (Rp - Rs) Bg) + Wp Bw - Wi Bw - Gi Bg | Gp Bg + Wp Bw - Wi Bw |
| Eo | (Bo - Boi) + (Rsi - Rs) Bg | - |
| Eg | Boi (Bg / Bgi - 1) | Bg - Bgi |
| Efw | (1 + m) Boi (cw Swi + cf) / (1 - Swi) (Pi - P) | Bgi (cw Swi + cf) / (1 - Swi) (Pi - P) |
| Et | Eo + m Eg + Efw | Eg + Efw |

## Aquifer models
| Model | We | Parameters |
|---|---|---|
| pot | K (Pi - P) | K = ct W. Instant response |
| schilthuis | C * integral of (Pi - P) dt | C = productivity. Steady state, infinite aquifer |
| fetkovich | stepwise, Fetkovich (1971) | K = ct W and tau = K / J |

Fetkovich step n: w_n = w_(n-1) + (Pi - w_(n-1) - Pmean_n) (1 - exp(-dt / tau)).
Pmean_n is the mean reservoir pressure of step n. The influx is We = K w.
For a fixed tau, We is linear in K. The script searches tau. For each tau, it fits N and K by least squares.

## The range of the in-place volume
The script fixes N at values from 0.25 to 4 times the best N and fits the aquifer again for each value.
The range holds the N values whose RMS residual is less than `--data-error` times the RMS of F.
This is a sensitivity range, not a confidence interval. It shows what the data cannot tell apart.

With a strong aquifer, a large change of N changes the fit very little. In OILBOX_AQ, N from 15 to 35 MMstb
changes the residual by less than 0.7 % of F. Field pressure data has errors of 1 % or more.
So the production data alone cannot give N. Use the volumetric N and fit only the aquifer.

## Diagnostic
The ratio of the mean F/Et in the last third of the points to the first third (Campbell plot for oil, Cole plot
for gas). The script reports a change larger than `--trend-limit` (5 %).
- Increase: an aquifer, a gas cap larger than m, or compaction that cf does not describe.
- Decrease: m too large, an error in the pressure or the PVT data, or production that the data misses.

## Input from a simulator (extract_case.py)
- Pressure: FPR, the hydrocarbon pore volume weighted average.
- Time 0: the summary has no row at time 0. The script extrapolates the first two rows to zero production.
- PVT: the script interpolates 1/B linearly in pressure, as OPM Flow does. Linear interpolation of B gives
  errors near 1 % between table points.
- Rsi: FGIP / FOIP at the first row. This value is right only without an initial gas cap.
- Swi: from FWIP, FOIP (or FGIP), and the formation volume factors at Pi.
- One PVT region only. A model with more regions needs a tank for each region. This is not done.

## Not covered
Volatile oil, gas condensate, multiple tanks, gas cap fit (m as an unknown), Carter-Tracy and van Everdingen-Hurst
aquifers, and the uncertainty of the PVT data.
