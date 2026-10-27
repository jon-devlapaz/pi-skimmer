status: confirmed for intake
# Seed: I delete pi-skimmer's leftover recovery folders myself from within Pi, using a /skim clean command.
revision: r1
Confirmed by: "I confirm seed r1: /skim clean removes only pi-skimmer's own exact-shape folders, skips anything a skim may still be writing, and reports how many it removed.; caller-declared declared: agent; typed confirm at this terminal; declaration is not verified identity" (revision r1, 2026-10-27). Not approved for implementation.
date: 2026-10-26

## You are confirming
Goal: delete pi-skimmer's leftover recovery folders myself from within Pi, using a /skim clean command.
What gets built: a /skim clean subcommand that removes only pi-skimmer's own pi-skimmer-XXXX folders from the temp dir and says how many it removed, plus tests written like the existing ones.
What does not: touching anything else in tmp, deleting a folder a skim is still writing, and any automatic expiry of old folders.
Proposed first slice (proposal only): run npm test at the repository root to record a baseline, then add a failing /skim clean test in extensions/skimmer/index.test.ts.
Unknowns: how long a folder must sit idle before it counts as not in progress, and the Pi host's behaviour for commands in other sessions. Neither has been established.

## Problem statement
Each skim creates a pi-skimmer-XXXX folder in the temp dir (extensions/skimmer/artifacts.ts:6-16). It holds original.json, and later result.json, with the full text or items people submitted. Nothing in the extension ever deletes these folders. The README says they persist until cleaned up (README.md:97), but the only /skim subcommands are enable, disable and status (extensions/skimmer/index.ts:240-296). The folders therefore pile up with submitted text in them, and the only way to remove them is outside Pi. The confirmed goal asks for a /skim clean command so the operator can delete them from within Pi.

## Proposed outcome
Running /skim clean inside Pi deletes pi-skimmer's leftover recovery folders and reports how many it removed. The count is what the operator sees, via the same notify or message path the other subcommands use (index.ts:232-239). Tests for the command are written like the existing node:test cases in extensions/skimmer/index.test.ts and run under npm test. Two Must nots hold: nothing in tmp other than pi-skimmer's own folders is touched, and a folder a skim is still writing is not deleted. The Acceptance criteria block carries the exact check records.

## Acceptance criteria

/skim clean says how many folders it removed; it is tested like the existing tests.
not yet runnable: The /skim clean command and its test do not exist yet, so no behaviour-specific assertion exists to run.

Must not: touch anything in tmp other than pi-skimmer's own folders.
not yet runnable: The decoy-based test does not exist yet, and the matching rule it checks is not implemented.

Must not: delete a folder a skim is still writing.
not yet runnable: The test and the in-progress detection do not exist yet, and the idle margin is not chosen.

The TypeScript check passes with the new code.
check: `npm run check`

The change is prettier-formatted.
check: `npm run format:check`

The /skim clean output and README text are clear about what was removed and what was skipped.
you judge: Read the notification text from a /skim clean run and the README controls and persistence sections. Check that the removed count is stated plainly, that any skipped folders are explained, and that the README lists /skim clean.

## Affected users and systems
The affected user is the operator who runs skims in Pi and wants to clear stored submissions. Likely files are listed below, so this is the expected blast radius and not a finished design.
- extensions/skimmer/artifacts.ts: the natural home for the cleanup logic, next to saveOriginal, which creates the folders.
- extensions/skimmer/index.ts: the /skim command handler, its usage error text at line 296, and the skim execute path that creates folders at line 168.
- extensions/skimmer/index.test.ts: new tests using the existing harness, which injects artifactRoot.
- README.md: the controls section (lines 44-48) and the persistence note (line 97) should mention the new command.
- package.json: its files list (lines 27-33) names each shipped source file individually. If cleanup goes in a new source file, it must be added there or it will not be published.
Other Pi sessions that share the same temp dir are also affected, because they may have skims in progress that this session cannot see.

## Constraints and boundaries
Non-negotiable, from the confirmed goal:
- Must not touch anything in tmp other than pi-skimmer's own folders.
- Must not delete a folder a skim is still writing.

The goal says the change is small and tested like the existing tests.

Excluded: automatic or time-based expiry of old folders. The goal asks for a command the operator runs, and the draft option for auto-expiry was only a provisional alternative that goes beyond what was asked.

The settled pre-mortem names two failure modes: deleting data someone still needed, and matching too loosely, for example symlinks or folders owned by another user. The design must guard against both. Cleanup should work whether or not skimming is enabled, because it sends nothing off the machine. That is a proposal, not a settled decision.

## Decisions (from the ledger)
- goal [delegated] I delete pi-skimmer's leftover recovery folders myself from within Pi, using a /skim clean command. Why: every skim leaves a pi-skimmer-XXXX folder in the temp dir forever, holding original.json and result.json with the full text people submitted; the README says they persist until cleaned up, but Pi has no way to clean them up. Done when: /skim clean says how many folders it removed; it is tested like the existing tests. Must not: touch anything in tmp other than pi-skimmer's own folders. Must not: delete a folder a skim is still writing. (source: Goal confirmed in Tinkery with typed confirm and Enter; caller-declared attribution: declared: agent; typed confirm at this terminal; declaration is not verified identity; goal only, not seed or implementation approval.)
  Delegation (caller-declared, not authenticated): {"agent": "agent"}
- probe-pre-mortem [delegated] It deletes recovery data someone still needed: a folder from a skim running in another Pi session, which this session can't see, or the files behind a result that was just shown and not read yet. Second most likely: it matches too loosely and removes something that only looks like ours, such as a symlink named pi-skimmer-* or a folder owned by another user. (source: The agent answered with these words in Tinkery Shape.; caller-declared declared: agent; typed confirm at this terminal; declaration is not verified identity)
- obs-artifacts-create [evidence] saveOriginal makes the folder with mkdtemp(join(root, "pi-skimmer-")), with root defaulting to tmpdir(), and writes original.json with mode 0600 and the wx flag. It only returns the result.json path. saveResult writes result.json later with mode 0600 and the wx flag. No function in the file deletes anything. (source: read-only investigation in Tinkery Shape)
- obs-index-flow [evidence] Dependencies includes an optional artifactRoot. A skim has a 30,000 ms timeout at line 163. The folder is created at line 168 and result.json is written at line 176 after judging. On failure the catch block rethrows with the message that the original input is retained at the original path, so the original is intentionally kept. (source: read-only investigation in Tinkery Shape)
- obs-index-command [evidence] The handler synchronizes, then branches on disable (line 240), enable (line 251) and status (default). Any other action throws 'Usage: /skim enable | disable | status' at line 296. A local report function uses ctx.ui.notify when there is a UI and pi.sendMessage otherwise. Disable and status do not require consent to run. (source: read-only investigation in Tinkery Shape)
- obs-test-harness [evidence] harness() creates a private temp root with mkdtempSync, passes it as artifactRoot, and exposes root and command(args). A test at lines 480-517 starts a skim whose evaluate call hangs until aborted, waits until it has started, runs the disable command, and then reads the one folder left under h.root. Other tests assert readdirSync(h.root) is empty at lines 155 and 264. (source: read-only investigation in Tinkery Shape)
- obs-package-scripts [evidence] The published files list names index.ts, core.ts and artifacts.ts individually, with README and LICENSE. Scripts: check is tsc --noEmit, test is node --test --experimental-strip-types extensions/skimmer/*.test.ts, and format:check runs prettier over extensions/**/*.ts, *.json, README.md and the workflow files. CI runs npm ci, check, test, format:check and pack --dry-run on Node 22 and 24. (source: read-only investigation in Tinkery Shape)
- obs-readme [evidence] The controls section lists only /skim enable, status and disable. Line 97 says recovery directories are owner-only (0700, files 0600) and persist until cleaned up. Line 113 says tests use synthetic fixtures and stubbed HTTP and cover recovery permissions. (source: read-only investigation in Tinkery Shape)
- inference-no-inflight-marker [evidence] Nothing on disk marks a folder as in progress. An in-progress skim and a failed or cancelled one both have original.json and no result.json. A finished skim has both files. Telling in-progress from abandoned therefore needs extra information, such as in-memory tracking of this session's writes or a file or folder modification time compared with the 30 second timeout. (source: read-only investigation in Tinkery Shape)
- inference-test-feasibility [evidence] The injected artifactRoot and the hang-until-abort pattern let a test seed decoy entries in an isolated root and run /skim clean while a skim is genuinely mid-write. Both Must not behaviours look testable in the existing style. (source: read-only investigation in Tinkery Shape)
- inference-counter-example [evidence] A failed or cancelled skim leaves original.json with no result.json for good. A rule that keeps every folder lacking result.json would never clean it, and the operator would see the same leftovers after /skim clean. A finished skim in another session is the opposite case: it has both files but may be unread. (source: read-only investigation in Tinkery Shape)
- inference-outside-view [evidence] The default root is the shared OS temp dir, so entries named pi-skimmer-* may come from other users or be symlinks. An implementer would ask for exact-shape matching, lstat and owner checks, and whether the count should separate removed from skipped. These are agent-decidable design items and not operator-only decisions. (source: read-only investigation in Tinkery Shape)
- unknown-pi-host-api [evidence] This investigation has not established what the Pi host guarantees about command handlers, such as whether two sessions can run handlers at once or how headless runs surface notifications. (source: read-only investigation in Tinkery Shape)
- unknown-idle-margin [evidence] This investigation has not established what idle margin is right. The only data point is the 30 second per-skim timeout, which gives a lower bound. (source: read-only investigation in Tinkery Shape)
- unknown-shared-tmp [evidence] This investigation has not established how ownership, permissions or symlinks behave in a shared /tmp on Linux or on Windows. Node 22.19 or newer is the only platform requirement found. (source: read-only investigation in Tinkery Shape)

## Assumed (not confirmed by you)
- none

## Knowledge map
What we know, with proof (all observed 2026-10-26 in this working tree):
- Folders are created with mkdtemp(join(root, "pi-skimmer-")), and original.json is written first (artifacts.ts:6-14). result.json is written later with the exclusive-create flag (artifacts.ts:20-25), so the result path is only a name until the skim finishes.
- A skim is capped at 30 seconds (index.ts:163). If it fails or is cancelled, the original is deliberately kept (index.ts:204-209). Re-check if the timeout or the catch path changes.
- The artifactRoot dependency lets tests point the code at a private temp root (index.ts:26, 168; index.test.ts:98).
- A test already holds a skim in flight mid-evaluation and then runs a command (index.test.ts:480-517), so the in-flight case is testable with existing machinery.
- The repository's checks are npm run check, npm test and npm run format:check (package.json:65-67; ci.yml:25-27). Re-check if package.json scripts change.

What we know we don't know:
- How long a folder must sit idle before it is treated as not in progress. Owner: the implementer, with the operator able to override. Find out by choosing a margin above the 30 second timeout and testing it.
- How the Pi host behaves when several sessions share one temp dir. Owner: the implementer. Find out by reading the Pi extension API types in node_modules, which this investigation did not do.
- Whether the temp dir can be shared with other users. This matters for ownership checks. Owner: the implementer. Find out by checking uid and mode with fs.lstat in a test.

What we have not read: node_modules/@earendil-works/pi-coding-agent type declarations; core.ts and core.test.ts; sdk.test.ts beyond its imports; the _system and stages SDLC files.

What could surprise us:
- A folder with original.json but no result.json is either in progress or abandoned, and nothing on disk says which. That follows from reading artifacts.ts and index.ts (see the inference finding).
- A concurrent skim in another Pi session is invisible to in-memory tracking.
- A concrete signal that we were wrong: a test that starts a skim, runs /skim clean mid-flight, and still finds the folder deleted, or a decoy entry that disappears.

## Evidence and uncertainty
Grounded in:
- .github/workflows/ci.yml
- README.md
- extensions/skimmer/artifacts.ts
- extensions/skimmer/index.test.ts
- extensions/skimmer/index.ts
- package.json

Provenance unavailable: reported reads do not verify filesystem origins or unrecorded reads. Some recorded findings have no attributable read path.

Checked:
- Caller-declared authorization, not authenticated identity: declared: agent; typed confirm at this terminal; declaration is not verified identity (observed 2026-10-27)
Checked by read-only file tools in /Users/jondev/dev/checkpoint-2026-10-27/pi-skimmer:
- extensions/skimmer/artifacts.ts:6-25: saveOriginal makes the folder with mkdtemp(join(root, "pi-skimmer-")), with root defaulting to tmpdir(), and writes original.json with mode 0600 and the wx flag. It only returns the result.json path. saveResult writes result.json later with mode 0600 and the wx flag. No function in the file deletes anything. (observation; scope: folder creation and file writes in artifacts.ts) (observed 2026-10-27)
- extensions/skimmer/index.ts:22-27, 161-176, 204-209: Dependencies includes an optional artifactRoot. A skim has a 30,000 ms timeout at line 163. The folder is created at line 168 and result.json is written at line 176 after judging. On failure the catch block rethrows with the message that the original input is retained at the original path, so the original is intentionally kept. (observation; scope: skim execution flow and failure handling in index.ts) (observed 2026-10-27)
- extensions/skimmer/index.ts:226-303: The handler synchronizes, then branches on disable (line 240), enable (line 251) and status (default). Any other action throws 'Usage: /skim enable | disable | status' at line 296. A local report function uses ctx.ui.notify when there is a UI and pi.sendMessage otherwise. Disable and status do not require consent to run. (observation; scope: the /skim command handler) (observed 2026-10-27)
- extensions/skimmer/index.test.ts:9-18, 26, 98, 112, 155, 480-517: harness() creates a private temp root with mkdtempSync, passes it as artifactRoot, and exposes root and command(args). A test at lines 480-517 starts a skim whose evaluate call hangs until aborted, waits until it has started, runs the disable command, and then reads the one folder left under h.root. Other tests assert readdirSync(h.root) is empty at lines 155 and 264. (observation; scope: test harness and in-flight test) (observed 2026-10-27)
- package.json:27-33, 65-67; .github/workflows/ci.yml:24-28: The published files list names index.ts, core.ts and artifacts.ts individually, with README and LICENSE. Scripts: check is tsc --noEmit, test is node --test --experimental-strip-types extensions/skimmer/*.test.ts, and format:check runs prettier over extensions/**/*.ts, *.json, README.md and the workflow files. CI runs npm ci, check, test, format:check and pack --dry-run on Node 22 and 24. (observation; scope: scripts, published files and CI) (observed 2026-10-27)
- README.md:44-48, 97, 113: The controls section lists only /skim enable, status and disable. Line 97 says recovery directories are owner-only (0700, files 0600) and persist until cleaned up. Line 113 says tests use synthetic fixtures and stubbed HTTP and cover recovery permissions. (observation; scope: README statements about controls and persistence) (observed 2026-10-27)
Checked by read-only file tools in /Users/jondev/dev/checkpoint-2026-10-27/pi-skimmer; citation not found: artifacts.ts isn't in the folder:
- Reading of artifacts.ts:6-25 and index.ts:161-209: Nothing on disk marks a folder as in progress. An in-progress skim and a failed or cancelled one both have original.json and no result.json. A finished skim has both files. Telling in-progress from abandoned therefore needs extra information, such as in-memory tracking of this session's writes or a file or folder modification time compared with the 30 second timeout. (inference; scope: whether disk state alone shows a skim is still writing; limits: Does not cover file timestamps, which were not examined, or any lock mechanism outside the files read.; supported by: obs-artifacts-create, obs-index-flow) (observed 2026-10-27)
Checked by read-only file tools in /Users/jondev/dev/checkpoint-2026-10-27/pi-skimmer; citation not found: index.test.ts isn't in the folder:
- Reading of index.test.ts:9-132 and 480-517: The injected artifactRoot and the hang-until-abort pattern let a test seed decoy entries in an isolated root and run /skim clean while a skim is genuinely mid-write. Both Must not behaviours look testable in the existing style. (inference; scope: whether the two Must not behaviours can be tested with existing machinery; limits: Does not establish that an age-based rule can be tested without controlling file times, which was not checked.; supported by: obs-test-harness) (observed 2026-10-27)
Checked by read-only file tools in /Users/jondev/dev/checkpoint-2026-10-27/pi-skimmer; citation not found: index.ts isn't in the folder:
- Reading of index.ts:204-209 and artifacts.ts:20-25: A failed or cancelled skim leaves original.json with no result.json for good. A rule that keeps every folder lacking result.json would never clean it, and the operator would see the same leftovers after /skim clean. A finished skim in another session is the opposite case: it has both files but may be unread. (inference; scope: a case where a simple rule would surprise the operator; limits: Reasoned from code only; no run was done.; supported by: obs-artifacts-create, obs-index-flow) (observed 2026-10-27)
Checked by read-only file tools in /Users/jondev/dev/checkpoint-2026-10-27/pi-skimmer; citation not found: artifacts.ts isn't in the folder:
- Reading of artifacts.ts:6-8 and README.md:97: The default root is the shared OS temp dir, so entries named pi-skimmer-* may come from other users or be symlinks. An implementer would ask for exact-shape matching, lstat and owner checks, and whether the count should separate removed from skipped. These are agent-decidable design items and not operator-only decisions. (inference; scope: what an experienced implementer would ask beyond the goal; limits: Experience-based reading; no shared-tmp behaviour was observed here.; supported by: obs-artifacts-create, obs-readme) (observed 2026-10-27)
Checked by read-only file tools in /Users/jondev/dev/checkpoint-2026-10-27/pi-skimmer:
- extensions/skimmer/index.ts:1-4, 226-239 (node_modules type declarations not opened): This investigation has not established what the Pi host guarantees about command handlers, such as whether two sessions can run handlers at once or how headless runs surface notifications. (unknown; scope: Pi extension API behaviour for commands) (observed 2026-10-27)
Checked by read-only file tools in /Users/jondev/dev/checkpoint-2026-10-27/pi-skimmer; citation not found: artifacts.ts isn't in the folder:
- extensions/skimmer/index.ts:163; artifacts.ts:6-25: This investigation has not established what idle margin is right. The only data point is the 30 second per-skim timeout, which gives a lower bound. (unknown; scope: the idle time that separates in-progress from abandoned folders) (observed 2026-10-27)
Checked by read-only file tools in /Users/jondev/dev/checkpoint-2026-10-27/pi-skimmer:
- extensions/skimmer/artifacts.ts:6-8; package.json:24-26: This investigation has not established how ownership, permissions or symlinks behave in a shared /tmp on Linux or on Windows. Node 22.19 or newer is the only platform requirement found. (unknown; scope: behaviour on platforms or temp dirs shared between users) (observed 2026-10-27)

## Suggested first slice
Proposal only, non-binding. From the repository root, run npm test to record a passing baseline. Then add one failing test to extensions/skimmer/index.test.ts that creates a pi-skimmer-XXXXXX folder under the harness root and expects /skim clean to remove it and report 1. Implement afterwards. This step does not authorize implementation.

## Risks and verification
Failure modes, ordered by consequence:
1. Deleting a folder another session is still writing. Mitigation: skip folders modified within a margin above the 30 second timeout, and skip folders this session is currently writing.
2. Deleting a result the operator just saw and has not read. A short idle margin only reduces this and cannot remove it. Mitigation: the command reports how many it skipped as recent, and the README says so.
3. Matching too loosely. Mitigation: match the exact mkdtemp shape (prefix plus six random characters), use lstat so symlinks are never followed, require a real directory, require the current user as owner, and remove only known files so an unexpected entry causes a skip.
4. A new source file not shipped. Mitigation: update the package.json files list or reuse artifacts.ts.

Counter-example reading: a skim that was cancelled or failed leaves original.json and no result.json forever, so a rule that keeps every folder without result.json would never clean it. A time margin is therefore needed alongside any in-memory tracking.

Outside-view reading: someone who has done this before would ask who else owns pi-skimmer-* entries on a shared temp dir, and whether the count should separate removed from skipped. We treat both as design items covered by the mitigations above and not as operator-only decisions.

Proposed checks only; none has been run, and the Acceptance criteria block carries the records. The Done when line and the two Must not behaviours need new tests that do not exist yet, so they are provisional. npm run check and npm run format:check are existing repository invocations.

Where we did not look: the Pi host runtime and its extension type declarations, other Pi sessions running concurrently, behaviour on Windows or on Linux with a shared /tmp, and core.ts.

## Open questions and deferrals
No blocking question remains. Non-blocking items:
- The idle margin before a folder counts as not in progress. We propose a value above 30 seconds, to be chosen at implementation and stated in the README. Revisit if the operator wants unread results protected for longer.
- Whether the output also reports a skipped count. We propose it; the goal only requires the removed count.
- Whether to ask for confirmation before deleting. We propose not to, because the operator typed the command. Revisit if the operator wants a prompt.
- Pi host behaviour for commands in headless mode is unverified. Revisit when reading the Pi API types.

## Owners and next steps
Settled, with evidence: the goal and its two Must nots (goal decision); the pre-mortem answer (probe-pre-mortem).
Needs a decision: none blocking.
Watch, parked as risks: the idle margin, the skipped-count output, and the confirmation prompt, all owned by the implementer under operator review.
Still to read: the Pi extension type declarations in node_modules and core.ts, by the implementer before design.
Repeat-surprise guardrail: any entry not matching the exact folder shape is skipped and never deleted, and a test with decoy entries guards that.

## Downstream handoff
The session folder is the handoff: seed-contract.md together with ledger.json, seed-confirmation.json and seed-checks.json. A consumer checks tinkery seed status --json on the session, and handoff.verified is true only while the seed, decisions and check records are exactly what was confirmed. That proves content, not who confirmed it. This seed is discovery input, not an implementation plan, approved specification or review receipt. Later stages should keep the accepted constraints, surface any conflict instead of silently replacing a decision, and not treat this seed as stage approval.

