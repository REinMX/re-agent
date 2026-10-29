# Site setup

This repository is a set of skills and tools, not an agent framework. Use the Claude Code or Codex
interface that your site approves. Keep engineering execution on the Linux host with your FMU/Komodo
environment. This guide certifies no site.

## 1. Agent host and execution host

| Mode | Agent process | Engineering execution |
|---|---|---|
| Windows/manual | Windows IDE extension | You run each command in a separate Linux terminal |
| Linux/direct | Remote IDE extension or Linux CLI | The same Linux host, environment active |

A visible agent panel and a remote terminal can be on different hosts. Check the OS and the interpreter of
the agent's own tool process. Automatic SSH from a Windows agent is not implemented.

## 2. Site decisions first

Get these from your team or IT. Do not guess them: the approved extension and model endpoint, the accepted
data classes, the Linux host and connection method, the Komodo release and its activation command and
interpreter, your scratch and report folders, the simulator and version, and the queue policy.
Running next to the data does not keep model context local. `.gitignore` is not a data filter.
Until the endpoint is approved, use public or synthetic data only. Do not install packages into a shared Komodo.

## 3. Install a workspace

With a Python 3.11 or later, in the source checkout:

```bash
python tools/install_project.py --target <workspace>               # dry run: review the plan
python tools/install_project.py --target <workspace> --apply       # manual mode (default)
python tools/install_project.py --target <workspace> --mode direct --apply   # Linux/direct
```

Use `--harness claude` or `--harness codex` if only one harness is approved. The installer copies real
folders into `.claude/skills` and `.agents/skills`, keeps a `.reservoir-agent` snapshot, merges existing
instructions and settings, and refuses to overwrite a differing managed file. Open the target workspace,
review and trust its settings, reload the extension, and check skill discovery.

For a Linux source checkout, link the skills for your user instead:

```bash
tools/install_user_skills.sh --dry-run
tools/install_user_skills.sh
```

Update a checkout with `make update` (preview: `make update ARGS=--dry-run`).

## 4. Site contract

Copy `site.contract.example.yml` to `site.contract.yml` in the workspace or project root, or to
`~/.config/re-agent/site.contract.yml`. Keep the filled copy local. Never commit it.

Schema version 2.1 holds one block per host under `profiles:`. The examples are
`profiles/home.example.yml` and `profiles/company.example.yml`. They hold placeholders only.
Python, scratch and output values are paths on the Linux execution host, not Windows paths.
Set `endpoint_approved: true` only after the human site decision. The flag is an attestation, not a check.

Doctor selects the profile from `--profile`, then `RE_AGENT_PROFILE`, then `active_profile`, and exits 2
for an unknown profile. On a tcsh login shell:

```tcsh
setenv RE_AGENT_PROFILE company
```

## 5. Doctor

```bash
python tools/doctor.py --mode agent --contract site.contract.yml --json
<contract python> tools/doctor.py --mode execution --contract site.contract.yml --json
make check-full PY=<contract python>
```

Agent mode checks the contract only. Execution mode, on the Linux host, also compares `ert.version` and the
optional `versions:` map with the distributions of the running interpreter. Doctor reports `<...>` values as
unfilled. It never imports ERT plugins, starts a simulator, or submits a job, and it always prints
`site_verified: false`. `simulator.version` is reported as declared, not checked.

## 6. Manual handoff

In Windows/manual mode the agent prepares one read-only command at a time with the contract Python and
`output_root`. You review it, run it in the Linux terminal, and share only output that the data policy allows.
For example:

```bash
<contract python> skills/case-qc/scripts/case_qc.py --config <case.ert> \
  --runpaths '<scratch>/realization-*/iter-0' --expected <N> --keys FOPT FWPT --outdir <new empty folder>
```

Case QC needs a new empty output directory. Missing keys, members or observations are failures.

## 7. First pilot

Follow `skills/case-qc/references/runbook.md` on public Drogon first. Then approved real-field read-only QC,
then small ERT jobs. Record locally the endpoint approval, the host, the source revision, the Komodo versions
and the doctor and test results. Use `CONTINUOUS_LEARNING.md` for lessons and skill changes.
