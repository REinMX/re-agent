# Read envelope

This file is a template. It is not a grant.
Copy the fields to `worklog/read-envelope.txt` in the project.
Fill in absolute paths. Delete the file to revoke the grant.

```text
owner: <person who grants access>
expires: <YYYY-MM-DD>
output_root: <absolute directory for reports>
root: <absolute directory the agent may read>
script: case-qc
```

Add one `root:` line for each input directory.
`script` must be `case-qc`. No other script is allowed.
A missing field, a relative path, or a past expiry means the checker asks.

The checker allows only `skills/case-qc/scripts/case_qc.py`.
Inputs must stay under `root`. `--outdir` must stay under `output_root`.
`--outdir` must not overlap a root.
`python -c`, `python -m`, a second command, and an unknown flag all ask.

Run:

```bash
python3 tools/read_envelope.py --command '<exact command>'
```

Exit code 0 means run that command. Do not ask.
Exit code 2 means ask. Show the command and the message.

This checker does not start the script.
Hermes has its own approval prompt. That prompt is separate. Do not bypass it.
