import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MAX_OUTPUT_BYTES, type Outcome, type Prepared } from "./core.ts";

export async function saveOriginal(prepared: Prepared, root = tmpdir()) {
  const directory = await mkdtemp(join(root, "pi-skimmer-"));
  const originalPath = join(directory, "original.json");
  const resultPath = join(directory, "result.json");
  await writeFile(
    originalPath,
    JSON.stringify(prepared.original, null, 2) + "\n",
    { mode: 0o600, flag: "wx" },
  );
  return { originalPath, resultPath };
}

export type ArtifactPaths = Awaited<ReturnType<typeof saveOriginal>>;

export async function saveResult(outcome: Outcome, paths: ArtifactPaths) {
  await writeFile(paths.resultPath, JSON.stringify(outcome, null, 2) + "\n", {
    mode: 0o600,
    flag: "wx",
  });
}

export function present(outcome: Outcome, paths: ArtifactPaths) {
  const { decisions: _decisions, kept, ...summary } = outcome;
  const display = {
    ...summary,
    original_path: paths.originalPath,
    result_path: paths.resultPath,
    output_truncated: false,
    displayed_count: 0,
    kept: [] as Record<string, unknown>[],
  };
  for (const entry of kept) {
    const unit =
      outcome.kind === "items"
        ? { index: entry.index, item: entry.item!, p: entry.p }
        : {
            index: entry.index,
            text: entry.text,
            start_line: entry.start_line!,
            end_line: entry.end_line!,
            p: entry.p,
          };
    display.kept.push(unit);
    display.displayed_count++;
    if (
      Buffer.byteLength(JSON.stringify(display, null, 2), "utf8") >
      MAX_OUTPUT_BYTES - 1_000
    ) {
      display.kept.pop();
      display.displayed_count--;
      break;
    }
  }
  display.output_truncated = display.displayed_count < outcome.kept_count;
  if (display.output_truncated) {
    display.warnings = [
      ...display.warnings,
      "Only the display was capped. All kept and unjudged material is in result_path; the complete input is in original_path.",
    ];
  }
  return display;
}
