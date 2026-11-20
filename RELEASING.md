# Releasing ATLAS (plain steps)

ATLAS's self-update looks at this repo's **latest GitHub Release**. A release only counts if it has the right files attached, so follow the steps in order.
The repo name (`amertafech-png/atlas`) and the version live in one file: `bridge/atlas_meta.py` (`REPO`, `VERSION`).

## One-time setup (new repo)
1. On github.com click **New repository** -> name `atlas` -> **Public** -> don't add a README/license (they are already here) -> **Create**.
2. In a terminal inside this project folder:
   ```
   git init -b main
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/amertafech-png/atlas.git
   git push -u origin main
   ```
   (`.gitignore` already keeps build output, old drafts and personal screenshots out.)
3. **Turn on private security reports:** repo **Settings -> Code security** (or *Security*) -> **Private vulnerability reporting -> Enable**.
4. **Protect `main`** (next section).
5. Optional: repo **Settings -> General -> Features**: leave *Issues* on; turn *Wikis*/*Projects* off if you don't need them.

## Protect the `main` branch (only you merge, every change reviewed)
Do this once, right after the first push.
1. Repo **Settings -> Branches -> Add branch protection rule** (or *Rules -> Rulesets -> New branch ruleset* - same options).
2. **Branch name pattern:** `main`
3. Tick:
   * **Require a pull request before merging**
     * **Require approvals** = 1
     * **Require review from Code Owners** (the `.github/CODEOWNERS` file already names you)
     * **Dismiss stale pull request approvals when new commits are pushed**
   * **Restrict who can push to matching branches** -> add only your own account (nobody else can push or merge)
   * **Do not allow force pushes** and **Do not allow deletions** (these are the defaults - leave them on)
4. **Save changes.**
5. Repo **Settings -> Collaborators**: don't add anyone. Everyone else contributes by **fork + pull request**, and only you can merge.
6. Repo **Settings -> Actions -> General -> Fork pull request workflows**: choose **Require approval for all outside collaborators**.

Notes: GitHub doesn't let you approve your own PR. For your own changes either push a branch and merge it yourself as the repo owner with the admin bypass
(leave *Do not allow bypassing the above settings* **unticked**), or just push to `main` directly (you're the only account allowed).
Outside contributors' PRs: read the diff (**Files changed**), check there are no secrets and nothing that phones home, **Approve**, then **Squash and merge**.

## Publish a release
Example for version `1.2.0` (always `MAJOR.MINOR.PATCH`, tag = `v` + version).
1. **Bump the version:** edit `bridge/atlas_meta.py` -> `VERSION = "1.2.0"`. Commit and push (through a PR if you like).
2. **Build the files:**
   ```
   python tools/build.py
   ```
   This creates a `dist/` folder containing exactly:
   * `ATLAS-Setup.bat` (Windows installer + updater)
   * `ATLAS-Setup.command` (macOS installer + updater)
   * `MODI-Setup.bat`, `MODI-Setup.command` - **byte-identical copies under the old product name** (see "Legacy asset names" below)   <!-- legacy-modi -->
   * `SHA256SUMS` (checksums ATLAS's updater checks - **required**, lists all four files)
   * `RELEASE-NOTES-v1.2.0.md` (a template to paste into the release page - not an asset)
3. **Write the notes:** the "What's new" bullets come from `docs/RELEASE-HIGHLIGHTS.md` (edit it *before* step 2 for each release; without that file the template says "(write the highlights here)"). Check `dist/RELEASE-NOTES-v1.2.0.md`, and leave the checksum block in.
4. **Tag it and push the tag:**
   ```
   git tag v1.2.0
   git push origin v1.2.0
   ```
5. On GitHub: **Releases -> Draft a new release** -> *Choose a tag*: `v1.2.0` -> title `ATLAS v1.2.0` -> paste the notes ->
   **drag all five files** `ATLAS-Setup.bat`, `ATLAS-Setup.command`, `MODI-Setup.bat`, `MODI-Setup.command`, `SHA256SUMS` into the assets box -> leave **Set as the latest release** ticked,   <!-- legacy-modi -->
   **Set as a pre-release** unticked -> **Publish release**.
6. **Check it:** download `ATLAS-Setup.bat`, run `certutil -hashfile ATLAS-Setup.bat SHA256` (Windows) or `shasum -a 256 ATLAS-Setup.command` (Mac) and compare with
   `SHA256SUMS`. On a machine with an older ATLAS, open it: an **Update vX** button should appear within ~12 hours, or right away via *Settings -> Updates -> Check now*.

## Rules that keep updates working
* The tag must be `vX.Y.Z` and **higher** than the previous one (ATLAS compares versions; it ignores `-beta` style tags and pre-releases/drafts).
* The asset file names must stay exactly `ATLAS-Setup.bat`, `ATLAS-Setup.command`, `SHA256SUMS` (plus the legacy copies below).
* **Never replace a file on a published release** without rebuilding and re-uploading `SHA256SUMS` too. ATLAS refuses to install when the checksum is missing or doesn't match.
* Want to test a release without users seeing it? Publish it as a **pre-release** (or keep it a draft). ATLAS only offers the *latest full release*.
* **Legacy asset names (keep doing this at least for the first releases after the rename).** Installs of v1.1.x were called MODI. Their built-in updater looks for assets named `MODI-Setup.bat` / `MODI-Setup.command` on this repo's latest release (GitHub redirects the old `amertafech-png/modi` API URL once the repo is renamed to `atlas`), checks them against `SHA256SUMS`, and runs the installer, which then migrates the install to ATLAS (data folder, shortcuts, login item, assistant name). Without those two files, v1.1.x users get "no installer for this system" and cannot update. `tools/build.py` creates the copies and puts all four files in `SHA256SUMS` automatically; just attach all five files. Once nobody runs v1.1.x any more you can drop them (remove `LEGACY_ASSETS` in `bridge/atlas_meta.py` and the lines tagged `legacy-modi` in `tools/build.py`).   <!-- legacy-modi -->
* **Renaming the GitHub repo** (Settings -> General -> Repository name -> `atlas`): do it before publishing the first ATLAS release. GitHub redirects the old web, git and API URLs, so v1.1.x installs keep finding releases. Do NOT create a new repo called `modi` afterwards (it would break the redirect).   <!-- legacy-modi -->
* If you rename the repo or move it to another account, change `REPO` in `bridge/atlas_meta.py` and release again; GitHub redirects the old name, so existing installs keep working.
* Windows SmartScreen / macOS Gatekeeper will warn about the unsigned installers; the README explains the click-through. (Code signing would remove the warnings but costs money.)
