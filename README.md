# pi-skimmer

Goal-directed filtering for the [Pi coding agent](https://pi.dev), powered by Jev through [pi-typesafe](https://github.com/DevMortimer/pi-typesafe).

- `skim_text`: retain relevant chunks of supplied text, with line ranges.
- `filter_items`: retain relevant JSON records, optionally showing Jev only selected fields.
- Explicit consent, bounded requests, owner-only recovery files, and preservation of unjudged material.

This is a native Pi extension, not a wrapper around the Skimmer CLI. It does not intercept reads, fetch URLs, open source files, or expand paths.

## Install

Requires Pi 1.2.0 or newer and Node.js 22.19 or newer.

Install directly from GitHub:

```sh
pi install git:github.com/jon-devlapaz/pi-skimmer
```

After the npm release is published, you can also use:

```sh
pi install npm:pi-skimmer
```

The TypeSafe SDK is installed as a dependency. To use the interactive `/typesafe login` and `/typesafe status` commands, also install its extension:

```sh
pi install npm:pi-typesafe@0.9.1
```

Restart Pi or run `/reload`, then:

```text
/typesafe login
/skim enable
```

Alternatively, configure `TYPESAFE_API_KEY` in your own environment before starting Pi. Never paste credentials into tool arguments or commit them.

## Consent and controls

```text
/skim enable
/skim status
/skim disable
/skim clean
```

`/skim clean` deletes the leftover recovery folders and reports how many it removed. It needs no consent and sends nothing off the machine. See [Privacy and failure behavior](#privacy-and-failure-behavior) for what it skips.

Consent is separate from `/typesafe enable`, scoped to the current Pi session and working directory, and revoked on reload. The confirmation identifies the destination host. Disabling cancels active skims and blocks future ones.

For headless use, the operator must explicitly set `PI_SKIMMER_ENABLED=1` before starting Pi. Do not have an agent set this as a workaround for missing consent.

The SDK client allows 100 attempted requests per extension session and sets a shared estimated daily spending cap of $1. Lower `PI_TYPESAFE_MAX_*` environment caps still apply. Estimates are not billing guarantees. The default request timeout is 10 seconds, with an overall tool timeout of 30 seconds.

## Tools

### `skim_text`

Supply already obtained, approved text and a focused goal:

```json
{
  "text": "Retry policy: allow at most four attempts. Cancellation interrupts retries immediately.",
  "goal": "Find retry limits and cancellation requirements",
  "source": "supplied retry policy"
}
```

Defaults are 2,000 Unicode characters per chunk, 10% overlap, and a relevance threshold of `0.5`. Optional controls: `chunk_chars` (500–8,000), `overlap` (0–0.5), and `threshold` (0–1).

Line ranges are relative to the supplied text, not an independently read source. `source` is a display label; it is sent to Jev but never fetched or opened.

### `filter_items`

```json
{
  "items": [
    { "title": "Authentication bypass", "tracking_id": "SEC-1" },
    { "title": "Change button color", "tracking_id": "UI-2" }
  ],
  "fields": ["title"],
  "goal": "Keep exploitable security vulnerabilities"
}
```

Accepts 1–256 JSON items. `fields` selects literal top-level fields, not dotted paths. Every requested field must exist on every item; otherwise the entire call fails before transmission. There is no fallback to the full record.

**Field selection is not output redaction.** Jev receives only selected fields, but retained records and local originals preserve all fields. Returned records can be visible to your coding model. Do not include credentials or other unapproved data, even in unselected fields.

## Privacy and failure behavior

- Only submit approved, nonsensitive data. Text or selected fields, the goal, and the source label leave the machine. Requests may incur charges.
- Obvious credential patterns are rejected; this is not comprehensive secret detection.
- Input over 256,000 UTF-8 JSON bytes fails instead of silently truncating. Large individual records must be split explicitly.
- Unjudged items are retained with warnings. Degraded API outcomes are explicitly marked as errors, not successful relevance judgments.
- Full input and results are saved in temporary, owner-only recovery directories (`0700`, files `0600`). They contain the original data and persist until you run `/skim clean`; treat them accordingly. Temporary files are not permanent archival storage.
- `/skim clean` removes only folders named `pi-skimmer-` plus six letters or digits, in the temp directory. It skips, and counts as skipped, anything else with that name: symlinks, entries owned by another user, and folders holding files other than `original.json` and `result.json`. It also skips any folder a skim in this session is writing and any folder modified in the last 5 minutes, which may belong to a skim in another Pi session or hold a result you have not read yet. Run it again later to remove those. Nothing is deleted automatically.
- Displayed output is capped, with explicit truncation metadata and paths to complete recovery files. There is no top-N cap discarding retained material.
- Relevance scores are probabilistic. They are not proof, authorization, or exhaustive verification. Read originals before critical decisions, especially for cross-references, constraints, and exceptions.

The most useful pattern is to obtain approved text or records through existing tools, then filter them in a tool script before displaying the large raw result to the coding model. Calling `skim_text` after a large document is already in model context does not reclaim those earlier tokens.

## Development

```sh
npm ci --ignore-scripts
npm run check
npm test
npm run format:check
npm pack --dry-run --ignore-scripts
```

Tests use synthetic fixtures and stubbed HTTP responses; they do not require a Jev key or make paid inference requests. They cover consent, real SDK command integration, strict projection, partial failure, budgets, cancellation, recovery permissions, recovery cleanup, and display caps.

The development-only npm overrides keep pi-typesafe's older optional Pi peer ranges aligned with the tested Pi SDK. Pi supplies the host packages at runtime; they are not bundled.

## License

MIT. See [LICENSE](LICENSE).
