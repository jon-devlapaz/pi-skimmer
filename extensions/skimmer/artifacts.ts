import {
  lstat,
  mkdtemp,
  readdir,
  rmdir,
  unlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MAX_OUTPUT_BYTES, type Outcome, type Prepared } from "./core.ts";

// A skim is capped at 30 seconds, so anything modified within this window may
// belong to a skim in another Pi session or to a result nobody has read yet.
export const IDLE_MS = 5 * 60_000;

// mkdtemp appends six alphanumeric characters to the prefix.
const FOLDER_SHAPE = /^pi-skimmer-[A-Za-z0-9]{6}$/;
const FOLDER_FILES = new Set(["original.json", "result.json"]);

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

export interface CleanReport {
  removed: number;
  recent: number;
  unrecognised: number;
  failed: number;
}

type Verdict = keyof CleanReport | "gone";

function missing(error: unknown) {
  return (error as NodeJS.ErrnoException).code === "ENOENT";
}

async function cleanFolder(
  directory: string,
  active: ReadonlySet<string>,
  idleMs: number,
): Promise<Verdict> {
  if (active.has(directory)) return "recent";
  const uid = process.getuid?.();
  try {
    // lstat so a symlink is never followed.
    const info = await lstat(directory);
    if (!info.isDirectory() || (uid !== undefined && info.uid !== uid))
      return "unrecognised";
    const names = await readdir(directory);
    let modified = info.mtimeMs;
    for (const name of names) {
      if (!FOLDER_FILES.has(name)) return "unrecognised";
      const file = await lstat(join(directory, name));
      if (!file.isFile() || (uid !== undefined && file.uid !== uid))
        return "unrecognised";
      modified = Math.max(modified, file.mtimeMs);
    }
    if (Date.now() - modified < idleMs) return "recent";
    for (const name of names) await unlink(join(directory, name));
    // rmdir, not a recursive remove: a file added meanwhile makes this fail.
    await rmdir(directory);
    return "removed";
  } catch (error) {
    return missing(error) ? "gone" : "failed";
  }
}

export async function cleanArtifacts(
  root = tmpdir(),
  options: { active?: ReadonlySet<string>; idleMs?: number } = {},
): Promise<CleanReport> {
  const report: CleanReport = {
    removed: 0,
    recent: 0,
    unrecognised: 0,
    failed: 0,
  };
  let names: string[];
  try {
    names = await readdir(root);
  } catch (error) {
    if (missing(error)) return report;
    throw error;
  }
  for (const name of names) {
    if (!FOLDER_SHAPE.test(name)) continue;
    const verdict = await cleanFolder(
      join(root, name),
      options.active ?? new Set(),
      options.idleMs ?? IDLE_MS,
    );
    if (verdict !== "gone") report[verdict]++;
  }
  return report;
}

export function describeClean(report: CleanReport) {
  const plural = (count: number) => (count === 1 ? "folder" : "folders");
  const message = `Removed ${report.removed} recovery ${plural(report.removed)}.`;
  const reasons = [
    report.recent > 0 &&
      `${report.recent} in progress or modified within the last ${IDLE_MS / 60_000} minutes (run /skim clean again later)`,
    report.unrecognised > 0 &&
      `${report.unrecognised} not a plain pi-skimmer folder (symlink, other owner, or unexpected contents)`,
    report.failed > 0 && `${report.failed} could not be removed`,
  ].filter(Boolean);
  if (reasons.length === 0) return message;
  const skipped = report.recent + report.unrecognised + report.failed;
  return `${message} Skipped ${skipped}: ${reasons.join("; ")}.`;
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
