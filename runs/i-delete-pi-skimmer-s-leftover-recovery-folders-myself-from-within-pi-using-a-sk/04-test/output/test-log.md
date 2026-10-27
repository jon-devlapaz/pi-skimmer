
$ ["npm", "ci", "--ignore-scripts"]
npm warn deprecated node-domexception@1.0.0: Use your platform's native DOMException instead

added 126 packages, and audited 127 packages in 7s

11 packages are looking for funding
  run `npm fund` for details

3 vulnerabilities (2 moderate, 1 high)

Some issues need review, and may require choosing
a different dependency.

Run `npm audit` for details.

$ ["npm", "run", "check"]
npm notice run pi-skimmer@0.1.0 check
npm notice run tsc --noEmit

$ ["npm", "test"]
npm notice run pi-skimmer@0.1.0 test
npm notice run node --test --experimental-strip-types extensions/skimmer/*.test.ts
✔ chunks preserve Unicode, whitespace, CRLF and exact line citations (0.993708ms)
✔ defaults are 2000 characters, 10 percent overlap and threshold 0.5 (0.471959ms)
✔ rejects invalid controls, non-JSON input, cycles and inherited objects (1.21125ms)
✔ enforces byte limits and never silently truncates text or items (0.845083ms)
✔ obvious credentials are refused in text, goal, labels and projected fields (0.151041ms)
✔ field projection never falls back, even for one missing field among present ones (0.1285ms)
✔ only selected fields are transmitted; local items remain untouched and ordered (0.436583ms)
✔ batches have unique per-unit questions, bounded bytes, and no whole-document repetition (1.428959ms)
✔ threshold comparisons preserve source order and the inclusive boundary (0.132875ms)
✔ partial and total API failures retain every unjudged item with warnings (0.428333ms)
✔ missing, invalid and out-of-range probabilities are unjudged, not coerced (0.078666ms)
✔ cancellation stops before submission and remains cancellation after a batch settles (0.171541ms)
✔ registers only the two explicit-input tools, without replacing reads or fetching (1.010875ms)
✔ TypeSafe tool consent does not enable skimming, and refused confirmation sends nothing (0.873041ms)
✔ bare SDK hosts and host-port endpoints work in enable and status (0.34625ms)
✔ consent discloses the configured destination without URL credentials (0.189542ms)
✔ backend changes require new consent and never reset a cached request budget (2.955375ms)
✔ a backend change during confirmation cannot acquire consent (0.284333ms)
✔ explicit consent enables processing, private recovery files and structured output (0.891042ms)
✔ projection failures are rejected before any credential client, artifact or request (0.369625ms)
✔ filter output preserves original fields while Jev sees only projected fields (0.863917ms)
✔ API degradation is explicitly an error result but retains all unjudged material (1.469208ms)
✔ display limits never discard retained or unjudged material from full results (3.448416ms)
✔ pretty-printed nested items obey the display byte cap without losing full results (2.805125ms)
✔ session changes, cwd changes and reloads revoke interactive consent (0.677834ms)
✔ disable and re-enable do not reset the session request budget (0.874791ms)
✔ headless use requires dedicated operator opt-in, never TypeSafe's flag alone (0.592458ms)
✔ missing or rejected credentials block transmission and report unavailability (0.608125ms)
✔ a confirmation cannot grant consent to a changed session (0.209542ms)
✔ concurrent confirmations cannot replace the active consent lifetime (0.59075ms)
✔ disable cancels an active batch without returning partial verdicts (0.718542ms)
✔ clean removes abandoned and finished recovery folders and reports how many (2.476917ms)
✔ clean works without consent or a UI and sends nothing (0.82275ms)
✔ clean never touches entries that are not exact pi-skimmer folders (1.683ms)
✔ clean skips folders modified within the idle margin and removes them once idle (1.582708ms)
✔ clean does not delete a folder a skim is still writing, even when it looks idle (0.965458ms)
✔ unknown subcommands list clean in the usage error (0.174333ms)
✔ unexpected transport failures do not expose exception bodies or submitted secrets (0.424959ms)
✔ real SDK default authentication supports enable, status and disable commands (2.232791ms)
✔ real pi-typesafe adapter validates requests, projects fields, and records shared usage (14.040625ms)
✔ real SDK session budget halts submission and remaining items are unjudged and retained (4.747333ms)
✔ real SDK shared daily budget blocks transmission and preserves unjudged items (1.134875ms)
✔ real SDK malformed responses fail explicitly rather than manufacturing low scores (1.445792ms)
ℹ tests 43
ℹ suites 0
ℹ pass 43
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 155.564083

$ ["npm", "run", "format:check"]
npm notice run pi-skimmer@0.1.0 format:check
npm notice run prettier --check "extensions/**/*.ts" "*.json" "README.md" ".github/workflows/*.yml"
Checking formatting...
All matched files use Prettier code style!

# seed check seed-4
$ ["bash", "-c", "npm run check"]
cwd: .
expect (operator confirms): exit code 0 with no TypeScript errors
npm notice run pi-skimmer@0.1.0 check
npm notice run tsc --noEmit

# seed check seed-5
$ ["bash", "-c", "npm run format:check"]
cwd: .
expect (operator confirms): exit code 0 and prettier reports all matched files use its style
npm notice run pi-skimmer@0.1.0 format:check
npm notice run prettier --check "extensions/**/*.ts" "*.json" "README.md" ".github/workflows/*.yml"
Checking formatting...
All matched files use Prettier code style!
