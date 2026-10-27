# Stage 05: Review and release

Inputs: current stage 04 evidence, candidate diff, approved brief or spec/plan,
and `_shared/REVIEW.md`.
Output: `runs/<slug>/05-deploy/output/REVIEW-findings.md` (the current review only) and a PR when requested.

Two jobs, kept apart:
- **Reviewer** (the session in the review checkout): reads current verification and writes findings only.
  It does not change code, run `verify`, mark items or touch the PR. Missing or stale verification is a finding, not a repair.
- **Owner** (the session that built the candidate): pushes the stage-open commit before review so the PR head
  is what the reviewer reads, brings `REVIEW-findings.md` back to the PR branch, opens or updates the PR, and
  runs verify again after any code change. The agent may open the PR. Only a human authorizes a merge: directly for this PR, or through a recorded standing approval that names merging (tink-substrate's approved-sequence contract). With that authorization the agent may merge.

A repeat review replaces `REVIEW-findings.md`; Git keeps the previous one, because once the owner has brought it back,
the next `stage <slug> 5` commits `runs/<slug>` first. Begin a repeat review with `Previous review: <commit>`. If the old review checkout
still exists, the launcher refuses and names both ways forward.

Manual delivery recipe: `git push -u origin <slug>`, then `gh pr create --fill`.
If `stage <slug> 5` printed a delivery warning (no `origin`, or `gh` missing or not signed in), do not stop silently:
finish the review, then hand the owner these commands at the end.

Check logic, security boundaries, and acceptance criteria. Use separate review
passes for risk that warrants them; a model review is not human approval.
Important findings return to stage 03 and require renewed verification.
For Tinkery-originated work, follow [Tinkery result handoff and retention](../../_system/SDLC.md#tinkery-ownership-and-handoff)
in the existing review findings and PR handoff; do not record a Taste verdict.

Gate: the human's merge authorization (direct or standing), current required CI,
and whatever the forge enforces (protected branches, required reviews). Local review files cannot approve a release. Consult the deployment
system for the deployed revision, health result, and rollback reference.

Skill mounts land in the git-ignored `.active` directory inside `.tink` (created on first mount, not shipped); nothing to clean up at run closure.
See `_system/SDLC.md`.

## Skills

Skillset: `deployment-skillset` (pin: `.tink/skillsets/deployment-skillset.json`).
- Once per machine/library, after reviewing the pin (it selects exact upstream code):
  `tink library fetch .tink/skillsets/deployment-skillset.json`
- At stage open, compile the required disciplines, then start a NEW session so
  `AGENTS.md` is re-read: `tink use deployment-skillset --snapshot runs/<slug>/05-deploy`
  The launcher does this for you: `python3 _system/scripts/sdlc.py stage <slug> 5`.
- For a capability gap: `tink-route --receipt runs/<slug>/skills.jsonl "<what you need>"`
  (searches the whole library; prints the skill on stdout. Exit 1 means nothing fits; exit 1 or 2 means
  continue without a skill).

Stage skills (always for this stage; `tink use` compiles the same set):
- `principle-prove-it-works`: Before declaring done: run the real artifact and show its output; a green build or "it compiles" is not proof.
When one of these applies, read that skill in full before acting on it. In the handoff note, name each skill that changed a decision and the decision it changed.
