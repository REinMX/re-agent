# Evals: resinsight-session

Data: fake binaries under `tests/`, or the host install named in the contract profile.

## Task 1. Valid input
Prompt: "Check that ResInsight and rips on this host work together."
Pass:
- Runs `ri_doctor.py` with the contract profile and reports the two versions it measured.
- States exit 0 only when the major.minor versions are equal.
- Does not start a second install or search the disk for other binaries.

## Task 2. Missing input
Prompt: "The profile has no resinsight.python. Make a view of PORO anyway."
Pass:
- Reports exit 2 and the missing key `resinsight.python`.
- Does not guess an interpreter path and does not use the ERT Python for rips.
- Asks the user for the path or offers to run `install_resinsight.sh`.

## Task 3. Rerun request
Prompt: "ri_doctor says rips 2026.6 cannot drive ResInsight 2026.09. Install the right rips and check again."
Pass:
- Shows the exact install command with the matching `rips==2026.9.*` pin, and runs it because the user asked.
- Runs `ri_doctor.py` again and reports the new result.
- Does not call the old result a pass.
