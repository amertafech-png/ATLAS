# Google sign-in plan

This is a plan, not a change to the app. Do not merge it as a release, do not tag, and do not publish. Nothing here adds an OAuth client, a secret, or a version bump.

The recommendation is: every person installs Hermes on their own computer and signs in there. ATLAS reuses that person's Hermes Google token and, when something is missing, runs Hermes' own Google sign-in with the client already in that Hermes folder. ATLAS does not ship its own Google app.

An ATLAS-owned public Google client is described at the end as an alternative. It is not the plan.

## What the error actually was

On a v1.4.6 test, connecting a second Google account showed **Error 403: org_internal**.

That error means the OAuth client is an **Internal** Google Workspace app. Google only lets accounts inside that Workspace through. An account outside the organization is refused before any token is saved.

The client in that test is the one Hermes is already using. Its client id starts with `942230865497-`. That prefix is enough to recognize it. The full id and the client secret stay on the computer that owns them. They are not copied into this document or into Git.

Hermes 0.23.2 does not ship a Google client. A search of the public `google-workspace` skill finds no bundled client file and no `942230865497` id. The skill's setup tells the person to create a Desktop client in their own Google Cloud project and hand the downloaded JSON to Hermes. The Internal app in the test is that Hermes client, set to Internal in Cloud Console.

## What ATLAS install sign-in covers

Installing ATLAS does **not** sign anyone into Google or Gmail.

The installers (`src/win/installer-body.ps1`, `src/mac/installer-head.sh`) only find Hermes. They do not configure it. After install, ATLAS opens into an **AI** sign-in: ChatGPT, Claude, Grok, OpenRouter, or a local model (`bridge/atlas_hermes.py`, `PROVIDERS`). That is Hermes' `hermes auth add` for those providers. Google is not in that list.

Gmail and Calendar are a later, optional Hermes skill: `skills/productivity/google-workspace` in Hermes 0.23.2. A person who never runs that skill has no `google_token.json` and no `google_client_secret.json`. ATLAS cannot invent one for them.

Himalaya (`~/.config/himalaya/config.toml`) is a separate IMAP mailbox. It cannot open Google Calendar. The current code already treats it that way.

## How Hermes gets its Google client

On Hermes 0.23.2 the skill's `scripts/setup.py` is the sign-in. It is meant to be driven one command at a time.

| Step | What happens |
|---|---|
| Client | The person downloads a Desktop OAuth JSON from their own Cloud project. `setup.py --client-secret <path>` copies it to `<Hermes home>/google_client_secret.json`. The file must have an `installed` or `web` block. There is no built-in client. |
| Grant | `setup.py --auth-url` prints one Google URL. The browser is expected to land on `http://localhost:1`, which nothing is listening on. The person copies the whole address bar. `setup.py --auth-code` exchanges it. |
| Token | The result is written to `<Hermes home>/google_token.json`. A refresh rewrites that same file. |
| Check | `setup.py --check` prints `AUTHENTICATED`, or `AUTHENTICATED (partial)` and lists scopes that are missing. |

Hermes home is `HERMES_HOME` if set, otherwise `%LOCALAPPDATA%\hermes` on Windows and `~/.hermes` on macOS and Linux (`hermes_constants.get_hermes_home` in 0.23.2). ATLAS already looks in that order, and also looks in `~/.hermes` on Windows as a last try (`candidate_homes` in `bridge/atlas_google.py`).

While the URL is outstanding, Hermes stores `state`, `code_verifier`, and `redirect_uri` in `<Hermes home>/google_oauth_pending.json`. Those are secrets for that attempt. ATLAS's `.gitignore` already ignores that filename, plus `google_token.json` and `google_client_secret.json`. Keep it that way.

## Calendar scope, and a second account

**Calendar.** Hermes' default request includes `https://www.googleapis.com/auth/calendar` (full calendar: read and write). It does not ask for `calendar.readonly`. ATLAS already treats the full calendar scope as "calendar is connected" and will read events with it.

There is no switch in `setup.py` for "add calendar only." The skill page tells the agent to pass `--services email,calendar`. The script in 0.23.2 has no `--services` argument. That flag does nothing. A new sign-in always asks for the whole default list:

- `gmail.readonly` (restricted)
- `gmail.send` (sensitive)
- `gmail.modify` (restricted)
- `calendar` (sensitive, full read/write)
- `drive` (restricted, full Drive)
- `contacts.readonly`
- `spreadsheets`
- `documents`

The consent screen can still be unchecked by the person. Hermes then stores only the scopes Google actually granted. If calendar was left off, `--check` says the token is partial. Running the sign-in again is how calendar gets added. The new token replaces `<Hermes home>/google_token.json`.

**A second account.** Hermes 0.23.2 keeps one token file. One file is one Google account. Signing in as someone else replaces that file, and Hermes' Gmail follows the replacement.

ATLAS on current `main` already goes further for the calendar only. "Connect another Google account" keeps Hermes' file untouched and writes an extra token under the ATLAS data folder (`google_accounts/<id>.json`, listed in `google_accounts.json` by id and email, never by token). Up to 8. Removing the Hermes account from the calendar does not delete Hermes' file, so mail keeps working. Those extra tokens are calendar tokens. Hermes' Gmail skill does not read them.

That extra-file layout can stay. The sign-in that creates the extra token should still be the Hermes client, not a new ATLAS client. How to do that without clobbering mail is in the phased rollout below. It does not fix `org_internal`. A second account outside the Workspace is refused by the Internal client no matter which folder would have stored the token.

## Shared token

One Google app per computer: the Desktop client the person already put in their Hermes folder.

Two kinds of token, both minted by that same client:

1. **The Hermes token** — `<Hermes home>/google_token.json`. Hermes' Gmail skill and `gws_bridge.py` read and refresh this file. ATLAS reads it for the calendar when it includes a calendar scope. ATLAS does not rewrite it. A refreshed access token for this file stays in memory, which is what the code does today, so ATLAS does not race Hermes' own refresh or drop fields Hermes writes.
2. **Extra calendar accounts** — files ATLAS already stores under its data folder. They exist so a second calendar does not replace the mailbox. They are not a second Google app. They carry the same `client_id` as the Hermes client. Hermes mail does not use them.

ATLAS data folder: `%LOCALAPPDATA%\ATLAS` on Windows, `~/Library/Application Support/ATLAS` on macOS, `~/.local/share/ATLAS` on Linux.

When the Hermes token already has a calendar scope, that is the account. No second sign-in. When it is mail only, or there is no token yet, the next sign-in is Hermes' `setup.py`, so the grant lands in the file Hermes already reads. When the person asks for another account, the extra file is still the Hermes client's token, saved beside the mailbox token rather than on top of it.

No token, code, verifier, or client secret is logged or committed. `.gitignore` already excludes `google_token.json`, `google_client_secret.json`, `google_oauth_pending.json`, `google_accounts.json`, and `google_accounts/`.

## Loopback and PKCE

Hermes 0.23.2 already uses PKCE. `setup.py --auth-url` builds the URL with `google_auth_oauthlib` (`autogenerate_code_verifier=True`) and stores the verifier in `google_oauth_pending.json`. The exchange sends that verifier.

The redirect is `http://localhost:1`. That is a loopback address. Hermes does not listen on it. Google's desktop guidance prefers `http://127.0.0.1:<a free port>` with a small local listener, and notes that `localhost` can trip a firewall. Hermes chose the copy-from-the-address-bar form instead, because this same setup has to work over chat, where nothing can catch the redirect. The exchange must use the same redirect that was stored (`http://localhost:1`). A listener on a different port would not match.

A Desktop client has a client secret in the downloaded JSON. Google's own desktop-app note says a desktop app is a public client and cannot keep that secret confidential, and that PKCE is the protection that matters. The secret is still stored, because Hermes requires it: `gws_bridge.py` refuses to refresh if `client_id`, `client_secret`, `refresh_token`, or `token_uri` is missing, and the Python auth library sends the secret. That is fine. It is not a reason to hide the secret in the installer, and it is not a reason to put it in Git. It lives only in that person's Hermes folder.

ATLAS today does something different. `bridge/atlas_google.py` listens on `127.0.0.1` with a random port, uses PKCE itself, and exchanges the code. That listener is a fine desktop pattern, and it is why a finished sign-in can complete without pasting a URL. This plan stops using it for the sign-in. New grants go through Hermes' URL and Hermes' paste-back, so the redirect, the verifier, and the client stay the ones Hermes recorded. The listener can remain only as a fallback while older builds are still in the field. It must not grow its own client id.

## Scopes, in Google's words

Google sorts scopes by how much review a **published** app needs. An Internal Workspace app skips that review and can only sign in its own organization. An External app left in **Testing** can sign in up to 100 test users and does not complete public verification. Its refresh tokens expire after 7 days. Publishing an External app for anyone else starts the review below.

| Scope | Who asks for it | Google's class |
|---|---|---|
| `https://www.googleapis.com/auth/calendar.readonly` | ATLAS's current Connect Google | Sensitive. Verification before the public can use a published app. No security assessment. |
| `https://www.googleapis.com/auth/calendar` | Hermes' default list (read and write) | Sensitive, same review, wider than read-only. |
| `https://www.googleapis.com/auth/gmail.send` | Hermes' default list | Sensitive. Send only. |
| `https://www.googleapis.com/auth/gmail.readonly` | Hermes' default list | Restricted. Public verification plus a CASA security assessment if the app stores or transmits mailbox data. Yearly after that. |
| `https://www.googleapis.com/auth/gmail.modify` | Hermes' default list | Restricted, same assessment. Read, compose, and send. |
| `https://www.googleapis.com/auth/drive` | Hermes' default list | Restricted as well. Full Drive is why a published Hermes client is not a "calendar only" review. |

`gmail.readonly` and `gmail.modify` are the restricted Gmail scopes. `calendar.readonly` is sensitive and is the smaller calendar grant. Hermes does not request that smaller grant. It requests full `calendar` plus restricted Gmail and full Drive.

Ways to ask for less, if someone is publishing a client for other people (this plan does not):

- Calendar read-only: `calendar.readonly` or `calendar.events.readonly` instead of full `calendar`. Still sensitive. Enough for "what's on today."
- Mail: leave Gmail to the person who already set it up. Do not add `gmail.readonly` or `gmail.modify` from ATLAS. `gmail.send` is sensitive, not restricted, and is still more than the calendar needs.
- Drive, Sheets, Docs, Contacts: do not request them from ATLAS. Hermes' script requests them because the skill can use them. Narrowing that list is a Hermes change (`--services` is documented and not implemented in 0.23.2). ATLAS should not fork the skill to do it.

For this plan the classification matters as a warning, not as homework. Each person uses their own client for themselves. They can stay in Testing, add themselves (and anyone they actually invite) as test users, and never file verification or CASA. The cost of that is the 7-day refresh token. The cost of publishing Hermes' default scopes to the general public is verification plus CASA, and it falls on the person who owns that Cloud project.

## What Adam does, and what a public user does

**Adam, for the Workspace account that already works.** Nothing in Cloud Console. Leave the Hermes token where it is. Do not create an ATLAS Google project. Do not put the client secret or the token in Git, in the installer, or in a release. This pull request does not do any of that.

**Adam, only if a second account outside that organization must work.** The Internal client (id prefix `942230865497-`) will keep returning `org_internal` until the Cloud project that owns it is changed.

1. In that project, open the OAuth consent screen and switch the user type from Internal to External. Google does not let you switch it back.
2. Leave the app in Testing. Add the outside account as a test user (the cap is 100).
3. Sign in again through Hermes, with that account. The old refresh token was issued to the Internal client; after the client type change, plan on a fresh sign-in rather than reusing it.
4. Expect to sign in again about once a week while the app stays in Testing, because Google expires those refresh tokens after 7 days.
5. Do not publish the app unless other people, beyond test users, must use this same client. Publishing Hermes' default Gmail and Drive scopes starts public verification and a CASA assessment on this project. That is optional and it is not part of shipping ATLAS.

**A public user.** They install Hermes with ATLAS and sign in to their AI. Google is separate. They follow Hermes' skill: their own Cloud project, their own Desktop client JSON, `setup.py --client-secret`, then the auth URL and the pasted `http://localhost:1` address. If their consent screen is External and still in Testing, they add their own account as a test user. ATLAS then reads the token Hermes saved. They do not need a privacy-policy URL, branding review, domain verification, or CASA to use their own client for themselves. They do need the 7-day re-consent, or they can publish their own project and take on that review themselves. ATLAS does not do the Cloud Console work for them, and it does not hand them Adam's client.

## Migration, without breaking people who already signed in

- Hermes' `google_token.json` stays the mailbox token. Do not delete it, and do not overwrite it with ATLAS's shorter JSON.
- A `google_token.json` already in the ATLAS data folder (from Connect Google), and any `google_accounts/` files already created, keep working. People who signed in do not sign in again.
- Refresh of a Hermes-owned file stays in memory. Hermes and `gws_bridge.py` are the writers of that file.
- A new "calendar was missing" sign-in should update the Hermes file by running Hermes' setup, not by writing another ATLAS `google_token.json`. Until that ships, the current Connect Google path still works and still uses the Hermes client. Leave those tokens in place when the Hermes file later gains calendar; prefer the Hermes file for that account so mail and calendar are the same grant.
- Switching Adam's client from Internal to External invalidates the old refresh token (`invalid_grant` on refresh). The spoken line for that is the one already used for an expired sign-in: say connect Google and open the page again. Do not delete the old file until the new sign-in has succeeded. Hermes writes the new token only after the exchange works.
- Extra accounts stay in `google_accounts/`. They are not copied onto the Hermes file.

## Spoken errors

Say these. Do not read out a client id, a secret, a code, or the raw JSON.

Google often shows `org_internal` on its own page and never redirects to `localhost:1` or to ATLAS's listener. Today's code then waits and says the sign-in was not finished (`error` other than `access_denied` and `invalid_scope` is stored as a generic failure). Phase 1 changes the words.

**`org_internal`** — the redirect came back with that error, the person pasted a URL that contains it, or the Google page showed "Error 403: org_internal":

> That Google account isn't in the organization this Google app allows. Hermes is using an internal company app, so only accounts in that organization can sign in. I can't offer a different app. Use an account in that organization, or change Hermes' Google app to External in Google Cloud and sign in again.

If the browser never comes back, don't stop at "the sign-in wasn't finished." Add the same explanation, so a person staring at Error 403 knows what it means.

**`access_denied`** — the person closed the page, or the app is External and still in Testing and this account is not a test user. Hermes' skill doc already sends people to the audience page for this error. Today's sentence ("Google didn't grant calendar access. Say connect Google if you want to try again.") stays, with one more sentence:

> Google didn't allow that sign-in. If you closed the page, say connect Google to try again. If Hermes' Google app is still in testing, add this account as a test user on that app, then try again.

`invalid_scope` can keep the current "didn't grant" wording. It means the requested scope was refused, not that the account is outside an organization.

## Phased rollout

No phase ships a client, tags a release, or asks Google to verify an ATLAS app.

**Phase 1. Say what went wrong.** Keep reading the Hermes token, the existing ATLAS `google_token.json`, and `google_accounts/`. Add the two spoken lines above. `org_internal` must not sound like a timeout. No new OAuth client. No rewrite of Hermes' file.

**Phase 2. Missing calendar uses Hermes' sign-in.** If the Hermes token is mail only, or there is a client file and no token, ATLAS runs that Hermes `setup.py` (`--auth-url`, open the browser, take the pasted address, `--auth-code`). The new token is the one Hermes writes. ATLAS reads it afterward. Tell the person, before starting, that Hermes will ask for its usual full list (mail, calendar, Drive, and the rest), not calendar alone, because 0.23.2 cannot narrow it. If there is no `google_client_secret.json`, say so and point them at Hermes' Google setup. Do not create a client for them.

**Phase 3. Another account, still on that same client. Gmail stays in Hermes.** Hermes' setup always overwrites `google_token.json`. Calling it in place for a second account would point Gmail at the new account. Don't. Snapshot the Hermes token, run Hermes' sign-in while the person picks the other account, move the new token into `google_accounts/`, and put the snapshot back. If Google returns `org_internal` or `access_denied`, put the snapshot back, write nothing, and speak the line from above. Mail never moves. ATLAS does not request `gmail.readonly` or `gmail.modify` itself, and does not start verification or CASA.

Phase 3 is the only tricky one, and only because Hermes has a single file. Until it exists, "connect another Google account" remains the flow already on `main`: same Hermes client, ATLAS's listener, extra file under `google_accounts/`. That flow is not a second product. It still cannot pass an Internal client. Phase 1's sentence is what a person hears in the meantime.

## Where Hermes' token differs from what ATLAS writes today

This is the gap the phases close. It is not a second design.

| | Hermes 0.23.2 | ATLAS on current `main` |
|---|---|---|
| Who runs the sign-in | `setup.py` | `GoogleCalendar._connect` in `bridge/atlas_google.py` |
| Redirect | `http://localhost:1`, person pastes the address bar. PKCE verifier in `google_oauth_pending.json`. | Listens on `http://127.0.0.1:<random>/`. PKCE in memory. No pending file. |
| Scopes asked for | The full default list above. `--services` is not implemented. `prompt=consent`. | Starts with `calendar.readonly`, then adds scopes already on a token it can see. `include_granted_scopes=true` for the first account, `false` for another. Another account uses `prompt=select_account consent`. |
| Where the token goes | Always `<Hermes home>/google_token.json`. A second sign-in replaces it. | First Connect Google writes `<ATLAS data>/google_token.json` and does not touch Hermes. Another account writes `<ATLAS data>/google_accounts/<id>.json`. |
| JSON shape | `google-auth`'s authorized-user JSON, with `type` set to `authorized_user` if missing. Includes `token`, `refresh_token`, `token_uri`, `client_id`, `client_secret`, `scopes`, `expiry`, and also `universe_domain` and `account`. Expiry can include fractions of a second. | `type`, `client_id`, `client_secret`, `refresh_token`, `token`, `token_uri`, `scopes`, `expiry` as `YYYY-MM-DDTHH:MM:SSZ`. No `universe_domain` or `account`. |
| Refresh | Hermes and `gws_bridge.py` rewrite the Hermes file. The bridge requires the client secret field. | A Hermes file is refreshed in memory only. An ATLAS-owned file is rewritten in place. |

Both shapes are `type: authorized_user` with a `scopes` list, and ATLAS can read either. Do not "fix" the Hermes file by saving ATLAS's shorter shape over it. `gws_bridge.py` needs `client_secret` on that file; leaving the secret out to be tidy would stop Hermes refreshing.

The recommendation follows Hermes' file and Hermes' sign-in. ATLAS's listener, its `calendar.readonly`-first scope list, and its separate `google_token.json` are what the code does today. Phase 2 and phase 3 move new sign-ins over to Hermes' flow without throwing away tokens people already have.

## Alternative: an ATLAS-owned External client

Not the recommendation. Written down so it is not rebuilt by accident.

This was the earlier idea: Adam creates one External OAuth client, the installer carries it, and any person presses Connect Google once, including accounts that an Internal Hermes client would refuse.

**Why it is attractive**

- One click for every Google account. Nobody opens Cloud Console.
- The first public scope can be `calendar.readonly` only (sensitive, not restricted).
- The loopback listener in `bridge/atlas_google.py` is already the flow Google recommends for a desktop app (random port on `127.0.0.1`, PKCE).
- `org_internal` goes away for the public, because the client is External rather than tied to one Workspace.

**Why this plan does not do it**

- Adam would own a public Google app: External consent screen, branding, a privacy-policy URL, domain verification, and test users while it is unverified. Sensitive-scope review is quoted by Google as up to about 10 days once the packet is complete, and it often takes longer when they ask for a video or a change. Restricted Gmail (`gmail.readonly` or `gmail.modify`) adds a CASA security assessment and a yearly repeat. Hermes' default list includes those restricted scopes, and mail text is sent on to whatever AI the person picked, which is the kind of transfer that review asks about.
- The desktop client secret would be inside the installer. Google allows that, because a desktop secret is not really secret, but it would then be Adam's public client. This plan keeps each secret in that person's Hermes folder instead.
- It is a second Google app next to the one Hermes already makes the person create. Two clients means two reviews if both are published, and tokens do not transfer between client ids.
- Every user already installs Hermes and can complete Hermes' own Google setup. The product does not need a second sign-in brand.

A desktop secret in an installer would be allowed. It is still the wrong owner for this app. Leave the client with Hermes, on each computer, created by the person who uses it.
