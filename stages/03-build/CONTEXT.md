# Stage 03: Implement

Inputs: current approved spec for full runs, or combined brief for light runs. A seed-bound run's
confirmed `seed-contract.md` and locked checks define it; its `brief.md` may stay the template.
Output: `runs/<slug>/03-build/output/plan.md` for full runs, the approved checklist definitions
in `runs/<slug>/checklist.json` for both profiles, and code in an isolated worktree/clone.
Items should carry a `check` whenever an automated proof exists; `verify` runs it and no mark is needed.

Gate: inspect code and follow the stage 3 gate before implementation; for seed-bound
runs use the guide's [seed definition comparison](../../_system/SDLC.md#seed-definition-comparison).
For full runs, write the plan first. Light runs have ONE definition gate, recorded as stage 3 (`sdlc.py decide <run> 3 ...`): the approved `brief.md` + `checklist.json` are the intent, design and plan. An explicit user instruction to execute a reviewed proposal is
implementation authority; never fabricate separate role approvals.
Each concurrent writer needs its own checkout. Use the serialized skill wrapper
in `_system/SDLC.md`; no shared writable skill symlinks or automatic lockfile rewrites.

For bug fixes, reproduce the expected failure first, obtain independent acceptance,
and record protected test inputs with `sdlc.py lock-tests`. Implement and verify in
a loop with stage 04. Update the plan when scope changes and renew stale decisions.
Mark items only with `sdlc.py mark <slug> <item-id> passed|failed --evidence <text>`; never hand-edit receipts.
For launcher-managed build agents, use the guide's [pinned build steps](../../_system/SDLC.md#pinned-build-steps).
Read the supplied step contract, write the handoff at its exact absolute report path,
and retain that contract and model pin on resume. A missing report refuses step completion.

## Skills

Skillset: `build-skillset` (pin: `.tink/skillsets/build-skillset.json`).
- Once per machine/library, after reviewing the pin (it selects exact upstream code):
  `tink library fetch .tink/skillsets/build-skillset.json`
- At stage open, compile the required disciplines, then start a NEW session so
  `AGENTS.md` is re-read: `tink use build-skillset --snapshot runs/<slug>/03-build`
  The launcher does this for you: `python3 _system/scripts/sdlc.py stage <slug> 3`.
- For a capability gap: `tink-route --receipt runs/<slug>/skills.jsonl "<what you need>"`
  (searches the whole library; prints the skill on stdout. Exit 1 means nothing fits; exit 1 or 2 means
  continue without a skill).

Stage skills (always for this stage; `tink use` compiles the same set):
- `principle-build-the-lever`: Non-trivial work: build the script or tool that does or proves it, so a reviewer can rerun it, instead of doing it by hand.
- `unslop`: Any prose you write (docs, commit messages, replies): cut AI tells and filler before you send it.
When one of these applies, read that skill in full before acting on it. In the handoff note, name each skill that changed a decision and the decision it changed.
