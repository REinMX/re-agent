# Update an installed folder without a clone

Use this procedure when the work machine can access GitHub through its web UI.
Keep the filled `site.contract.yml` in the existing folder.

1. Download the release asset `reservoir-engineer-agent-<version>-drop.zip` from GitHub.
   If no release asset exists, select the branch and use **Code > Download ZIP**.
   Copy the ZIP to the work machine through your approved file-transfer procedure.
2. From the existing folder, preview the update. Use Python 3.11 or newer:

   ```sh
   python tools/apply_drop.py --zip <zip> --dest <existing-folder>
   ```

   The default is a dry run. Review `COPY`, `PRESERVE`, and `REMOVED` lines.
   If the existing folder has no `tools/apply_drop.py`, unzip the download into a temporary folder.
   Run that temporary copy of `tools/apply_drop.py` with the same ZIP and destination arguments.
3. Apply the update:

   ```sh
   python tools/apply_drop.py --zip <zip> --dest <existing-folder> --apply
   ```

   Before changes, the tool creates `<existing-folder>.bak-<timestamp>.zip` beside the destination.
   Keep this backup until you have checked the updated folder.
   The tool preserves `.git`, `.venv`, `site.contract.yml`, `*.local*`, ignored files, and unrelated user files.
   With a previous manifest, it also preserves locally changed files and new paths that collide with user files.
   On the first update without a manifest, matching drop paths establish repository ownership.
   Review that first preview against any changes you made to repository files.
   The tool also removes the approval gates that older versions installed: the old hook entries in
   `~/.claude/settings.json` and `<existing-folder>/.claude/settings.json` (a `.bak` copy is kept),
   the old Codex rules file in `~/.codex/rules/`, and the old gate files in the folder. The preview lists these as
   `LEGACY GATES` lines. Use `--home <folder>` to select another user folder.
4. Compare `site.contract.yml` with `site.contract.example.yml` for new empty keys.
   Add only values that you know. The example uses v2.1 `profiles.home` and `profiles.company` blocks.
   Keep machine settings in the selected profile and project settings at the top level.
   Existing flat contracts remain supported. The updater never rewrites your contract.
5. In the updated folder, preview and refresh user skill links on Linux:

   ```sh
   bash tools/install_user_skills.sh --with-pstack --dry-run
   bash tools/install_user_skills.sh --with-pstack
   ```

   For an existing Git clone with remote access, `make update` is the alternative Git workflow.
   It fetches and fast-forwards the clone. A downloaded folder needs the installer commands above.
   The installer also removes the approval gates that older versions installed. See [the harness setup guide](docs/PSTACK_AGENTS.md).
6. Run the repository checks with the approved development interpreter:

   ```sh
   make check PY=<python>
   ```

   Read any skip messages. Optional fixture skips do not verify a live site or simulator.
7. Reload the VS Code window and check discovery:
   - Copilot: select **Agent** mode. Check custom agents from `.github/agents` and prompt files such as `/pstack-how`.
   - Claude Code: check `/skills` and the project agents.
   - Codex: check `AGENTS.md` and the skills from `.agents/skills`.
   - Cloud agents: open `plugins/pstack/templates/copilot-setup-steps.yml` and
     copy this into .github/workflows/ via the GitHub web UI (Add file) to enable cloud agents.
     Keep `.github/workflows/check.yml` as supplied.

For a source directory instead of a ZIP, replace `--zip <zip>` with `--src <temporary-source-folder>`.
The source and destination folders must not overlap. The updater needs only the Python standard library.
Git is optional for updates. When Git is available in the destination, its ignore settings also apply.
Without Git, the tool conservatively honors `.gitignore` files and `.git/info/exclude`.

## Remove files from an older drop

The release ZIP contains `DROP_MANIFEST.txt` with a SHA256 for every supplied file.
The updater writes a destination manifest and keeps records for removed files until you prune them.
GitHub **Download ZIP** has no manifest, so the updater builds the incoming records from the ZIP files.
Only a previous destination manifest identifies deletions. The first update does not infer deletions.

To preview and delete unchanged files removed from the previous drop, add `--prune`:

```sh
python tools/apply_drop.py --zip <zip> --dest <existing-folder> --prune
python tools/apply_drop.py --zip <zip> --dest <existing-folder> --apply --prune
```

To remove dangling user skill links into this folder's `skills` or `plugins` directories,
also add `--prune-links`. The flag affects only `~/.claude/skills` and `~/.codex/skills`.
Existing links to other folders and live links stay in place. Review the deletion preview before applying it.

## Build a release drop

In the reviewed source checkout, run:

```sh
python tools/make_drop.py
```

The output is `dist/reservoir-engineer-agent-<version>-drop.zip`.
The tool uses `git ls-files`, excludes protected local files, preserves relative symlinks,
and writes the SHA256 manifest inside the ZIP. Untracked files are not included.
Use a clean, checked checkout. An unresolved merge is an error.
An existing output ZIP is not replaced. Use `--output <new-zip>` for another build.
Publish the checked ZIP through your normal release process.
