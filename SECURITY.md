# Security policy

MODI can talk to an AI agent that can act on your computer, so security reports are taken seriously.

## Reporting a vulnerability - privately
Please **do not open a public issue** for security problems.

1. Go to this repository's **Security** tab -> **Report a vulnerability** (GitHub private vulnerability reporting), and describe what you found,
   how to reproduce it and what an attacker could do.
2. If that button isn't available, open a normal issue that says only "I have a security report - please contact me" **without details**, and the maintainer will arrange a private channel.

You'll get an acknowledgement as soon as the maintainer can; please allow reasonable time to fix before disclosing publicly.

## Scope / things worth reporting
* The bridge (`127.0.0.1:8765`) being reachable or drivable by other websites, other users or other machines.
* Ways to make the updater install something that doesn't match a published checksum or comes from the wrong place.
* Secret leakage (API keys in logs, files, screenshots), unsafe temp files, command injection in the installers or launchers.
* Feature modules acting on the computer without the required confirmation.

## Supported versions
Only the latest release receives fixes. MODI updates itself - use the **Update** button when it appears.

## For users
* Keep Hermes' API server on `127.0.0.1` (the default); never expose it to a network.
* Only download MODI from this repository's Releases page and compare the SHA-256 with `SHA256SUMS` if you like.
