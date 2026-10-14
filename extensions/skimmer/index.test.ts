import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import register from "./index.ts";
import { MAX_OUTPUT_BYTES } from "./core.ts";

function harness(
  options: {
    enabled?: boolean;
    hasUI?: boolean;
    usable?: boolean;
    backend?: string;
    probability?: number;
    evaluate?: (...args: any[]) => any;
  } = {},
) {
  const events = new Map<string, (...args: any[]) => any>();
  const tools = new Map<string, any>();
  const commands = new Map<string, any>();
  const notifications: string[] = [];
  const confirmations: string[] = [];
  const statuses: string[] = [];
  const requests: any[] = [];
  const root = mkdtempSync(join(tmpdir(), "skimmer-test-"));
  let id = "session-one";
  let accepted = true;
  let usable = options.usable ?? true;
  let backend = options.backend ?? "api.typesafe.ai";
  let constructors = 0;
  let nextConfirm: (() => Promise<boolean>) | undefined;
  const spend = {
    usdPerMTok: 0.042,
    session: { requestsStarted: 0 },
    today: { estimatedUsd: 0 },
    caps: { maxUsdPerDay: 1 },
  };
  const client = {
    getSpend: () => spend,
    evaluateMany: async (batches: any[], config: any) => {
      requests.push(...batches);
      spend.session.requestsStarted += batches.length;
      if (options.evaluate) return options.evaluate(batches, config);
      return {
        ok: true,
        results: [],
        failures: 0,
        skipped: 0,
        model: "fixture-model",
        answers: Object.fromEntries(
          batches.flatMap((batch) =>
            Object.keys(batch.questions).map((key) => [
              key,
              { type: "noul", noul: options.probability ?? 0.9 },
            ]),
          ),
        ),
        usage: { input_tokens: 123, output_tokens: 12 },
        elapsedMs: 1,
      };
    },
  };
  const ctx: any = {
    cwd: "/fixture/project",
    hasUI: options.hasUI ?? true,
    sessionManager: { getSessionId: () => id },
    ui: {
      notify: (text: string) => notifications.push(text),
      setStatus: (_key: string, value: string) => statuses.push(value),
      confirm: async (_title: string, text: string) => {
        confirmations.push(text);
        return nextConfirm ? nextConfirm() : accepted;
      },
    },
  };
  register(
    {
      on: (name: string, fn: any) => events.set(name, fn),
      registerTool: (tool: any) => tools.set(tool.name, tool),
      registerCommand: (name: string, command: any) =>
        commands.set(name, command),
      sendMessage: (message: any) => notifications.push(message.content),
    } as any,
    {
      createClient: () => {
        constructors++;
        return client as any;
      },
      auth: () => ({
        usable,
        backend,
        text: usable ? "Fixture Jev available" : "Fixture key unavailable",
      }),
      environment: options.enabled
        ? { PI_SKIMMER_ENABLED: "1" }
        : { PI_TYPESAFE_ENABLED: "1" },
      artifactRoot: root,
    },
  );
  events.get("session_start")!({}, ctx);
  return {
    ctx,
    tools,
    commands,
    events,
    requests,
    root,
    notifications,
    confirmations,
    statuses,
    command: (args: string) => commands.get("skim").handler(args, ctx),
    execute: (name: string, params: unknown, signal?: AbortSignal) =>
      tools.get(name).execute("fixture-call", params, signal, undefined, ctx),
    constructors: () => constructors,
    accept: (value: boolean) => {
      accepted = value;
    },
    auth: (value: boolean) => {
      usable = value;
    },
    session: (value: string) => {
      id = value;
    },
    backend: (value: string) => {
      backend = value;
    },
    confirm: (fn: () => Promise<boolean>) => {
      nextConfirm = fn;
    },
  };
}

test("registers only the two explicit-input tools, without replacing reads or fetching", () => {
  const h = harness();
  assert.deepEqual([...h.tools.keys()], ["skim_text", "filter_items"]);
  assert.deepEqual([...h.events.keys()], ["session_start", "session_shutdown"]);
  assert.equal(h.constructors(), 0);
  for (const tool of h.tools.values()) {
    assert.equal(tool.parameters.additionalProperties, false);
    assert.equal(tool.executionMode, "sequential");
    assert.ok(tool.outputSchema);
  }
  assert.ok(!("paths" in h.tools.get("skim_text").parameters.properties));
  assert.ok(!("max" in h.tools.get("filter_items").parameters.properties));
});

test("TypeSafe tool consent does not enable skimming, and refused confirmation sends nothing", async () => {
  const h = harness();
  await assert.rejects(
    h.execute("skim_text", { text: "hello", goal: "greeting" }),
    /disabled/,
  );
  assert.equal(h.constructors(), 0);
  assert.deepEqual(readdirSync(h.root), []);
  h.accept(false);
  await h.command("enable");
  assert.match(h.confirmations[0]!, /api.typesafe.ai/);
  assert.match(h.confirmations[0]!, /complete input and output/);
  await assert.rejects(
    h.execute("skim_text", { text: "hello", goal: "greeting" }),
    /disabled/,
  );
  assert.equal(h.requests.length, 0);
});

test("bare SDK hosts and host-port endpoints work in enable and status", async () => {
  for (const backend of ["api.typesafe.ai", "localhost:8080"]) {
    const h = harness({ backend });
    await h.command("enable");
    await h.command("status");
    assert.ok(h.confirmations[0]!.includes(backend));
    assert.ok(h.notifications.at(-1)!.includes(backend));
  }
});

test("consent discloses the configured destination without URL credentials", async () => {
  const h = harness({
    backend: "https://user:password@fixture.invalid/custom?key=private",
  });
  await h.command("enable");
  assert.match(h.confirmations[0]!, /fixture.invalid/);
  assert.ok(!h.confirmations[0]!.includes("password"));
  assert.ok(!h.confirmations[0]!.includes("key=private"));
});

test("backend changes require new consent and never reset a cached request budget", async () => {
  const h = harness();
  await h.command("enable");
  await h.execute("skim_text", { text: "hello", goal: "goal" });
  h.backend("https://other.invalid");
  await assert.rejects(
    h.execute("skim_text", { text: "hello", goal: "goal" }),
    /disabled/,
  );
  await h.command("enable");
  await assert.rejects(
    h.execute("skim_text", { text: "hello", goal: "goal" }),
    /Start a new Pi session/,
  );
  assert.equal(h.constructors(), 1);
  assert.equal(h.requests.length, 1);
});

test("a backend change during confirmation cannot acquire consent", async () => {
  const h = harness();
  h.confirm(async () => {
    h.backend("https://other.invalid");
    return true;
  });
  await assert.rejects(h.command("enable"), /backend changed/);
  await assert.rejects(
    h.execute("skim_text", { text: "hello", goal: "goal" }),
    /disabled/,
  );
  assert.equal(h.requests.length, 0);
});

test("explicit consent enables processing, private recovery files and structured output", async () => {
  const h = harness();
  await h.command("enable");
  const result = await h.execute("skim_text", {
    text: "Hello world\nSecond line",
    goal: "greeting",
    source: "unopened.txt",
  });
  const output = result.structuredContent;
  assert.equal(output.status, "complete");
  assert.equal(output.input_truncated, false);
  assert.equal(output.output_truncated, false);
  assert.equal(output.kept[0].start_line, 1);
  assert.equal(output.kept[0].end_line, 2);
  assert.equal(output.kept[0].text, "Hello world\nSecond line");
  assert.equal(result.isError, false);
  assert.equal(result.usage.input, 123);
  assert.equal(result.usage.totalTokens, 135);
  assert.deepEqual(JSON.parse(result.content[0].text), output);
  assert.deepEqual(JSON.parse(readFileSync(output.original_path, "utf8")), {
    text: "Hello world\nSecond line",
    goal: "greeting",
    source: "unopened.txt",
  });
  assert.equal(
    JSON.parse(readFileSync(output.result_path, "utf8")).kept_count,
    1,
  );
  assert.equal(statSync(output.original_path).mode & 0o777, 0o600);
  assert.equal(statSync(output.result_path).mode & 0o777, 0o600);
  assert.equal(statSync(dirname(output.original_path)).mode & 0o777, 0o700);
});

test("projection failures are rejected before any credential client, artifact or request", async () => {
  const h = harness({ enabled: true });
  await assert.rejects(
    h.execute("filter_items", {
      items: [{ title: "visible" }, { private: "SENTINEL" }],
      goal: "find title",
      fields: ["title"],
    }),
    /missing a requested field/,
  );
  assert.equal(h.requests.length, 0);
  assert.equal(h.constructors(), 0);
  assert.deepEqual(readdirSync(h.root), []);
});

test("filter output preserves original fields while Jev sees only projected fields", async () => {
  const h = harness({ enabled: true });
  const items = [{ title: "Visible", private: "NOT_FOR_JEV" }];
  const result = await h.execute("filter_items", {
    items,
    goal: "find visible",
    fields: ["title"],
  });
  assert.ok(!JSON.stringify(h.requests).includes("NOT_FOR_JEV"));
  assert.deepEqual(result.structuredContent.kept[0].item, items[0]);
  assert.ok(
    result.structuredContent.warnings.some((warning: string) =>
      warning.includes("original fields"),
    ),
  );
});

test("API degradation is explicitly an error result but retains all unjudged material", async () => {
  const h = harness({
    enabled: true,
    evaluate: async () => ({
      answers: {},
      failures: 2,
      skipped: 1,
      usage: { input_tokens: 0, output_tokens: 0 },
    }),
  });
  const items = Array.from({ length: 40 }, (_, i) => i);
  const result = await h.execute("filter_items", {
    items,
    goal: "find matches",
  });
  assert.equal(result.isError, true);
  assert.equal(result.structuredContent.status, "degraded");
  assert.equal(result.structuredContent.unjudged_count, 40);
  assert.deepEqual(
    result.structuredContent.kept.map((unit: any) => unit.item),
    items,
  );
  assert.equal(result.structuredContent.omitted_count, 0);
});

test("display limits never discard retained or unjudged material from full results", async () => {
  const h = harness({
    enabled: true,
    evaluate: async () => ({
      answers: {},
      failures: 1,
      skipped: 0,
      usage: { input_tokens: 0, output_tokens: 0 },
    }),
  });
  const items = Array.from({ length: 60 }, (_, i) => ({
    id: i,
    text: "x".repeat(2_000),
  }));
  const result = await h.execute("filter_items", {
    items,
    goal: "find matches",
  });
  const output = result.structuredContent;
  assert.equal(output.kept_count, 60);
  assert.equal(output.unjudged_count, 60);
  assert.ok(output.displayed_count < 60);
  assert.equal(output.output_truncated, true);
  assert.ok(Buffer.byteLength(JSON.stringify(output)) <= MAX_OUTPUT_BYTES);
  assert.ok(Buffer.byteLength(result.content[0].text) <= MAX_OUTPUT_BYTES);
  assert.ok(
    output.warnings.some((warning: string) =>
      warning.includes("All kept and unjudged"),
    ),
  );
  const full = JSON.parse(readFileSync(output.result_path, "utf8"));
  assert.equal(full.kept.length, 60);
  assert.deepEqual(
    full.kept.map((unit: any) => unit.item),
    items,
  );
});

test("pretty-printed nested items obey the display byte cap without losing full results", async () => {
  const h = harness({ enabled: true });
  const items = Array.from({ length: 24 }, () =>
    Object.fromEntries([
      ["title", "keep"],
      ...Array.from({ length: 100 }, (_, i) => [`key_${i}`, i]),
    ]),
  );
  const result = await h.execute("filter_items", {
    items,
    fields: ["title"],
    goal: "goal",
  });
  const output = result.structuredContent;
  assert.equal(output.output_truncated, true);
  assert.ok(Buffer.byteLength(result.content[0].text) <= MAX_OUTPUT_BYTES);
  assert.equal(
    JSON.parse(readFileSync(output.result_path, "utf8")).kept.length,
    24,
  );
});

test("session changes, cwd changes and reloads revoke interactive consent", async () => {
  const h = harness();
  await h.command("enable");
  await h.execute("skim_text", { text: "hello", goal: "greeting" });
  h.session("session-two");
  await assert.rejects(
    h.execute("skim_text", { text: "hello", goal: "greeting" }),
    /disabled/,
  );
  await h.command("enable");
  h.ctx.cwd = "/different/project";
  await assert.rejects(
    h.execute("skim_text", { text: "hello", goal: "greeting" }),
    /disabled/,
  );
  await h.command("enable");
  h.events.get("session_start")!({}, h.ctx);
  await assert.rejects(
    h.execute("skim_text", { text: "hello", goal: "greeting" }),
    /disabled/,
  );
});

test("disable and re-enable do not reset the session request budget", async () => {
  const h = harness();
  await h.command("enable");
  await h.execute("skim_text", { text: "one", goal: "goal" });
  await h.command("disable");
  await assert.rejects(
    h.execute("skim_text", { text: "two", goal: "goal" }),
    /disabled/,
  );
  await h.command("enable");
  await h.execute("skim_text", { text: "three", goal: "goal" });
  assert.equal(h.constructors(), 1);
  assert.equal(h.requests.length, 2);
  await h.command("status");
  assert.match(h.notifications.at(-1)!, /requestsStarted.*2/);
  assert.match(h.notifications.at(-1)!, /retained/);
});

test("headless use requires dedicated operator opt-in, never TypeSafe's flag alone", async () => {
  const denied = harness({ hasUI: false });
  await assert.rejects(denied.command("enable"), /PI_SKIMMER_ENABLED/);
  await assert.rejects(
    denied.execute("skim_text", { text: "hello", goal: "goal" }),
    /disabled/,
  );
  const allowed = harness({ hasUI: false, enabled: true });
  const result = await allowed.execute("skim_text", {
    text: "hello",
    goal: "goal",
  });
  assert.equal(result.structuredContent.status, "complete");
  await allowed.command("disable");
  await assert.rejects(
    allowed.execute("skim_text", { text: "hello", goal: "goal" }),
    /disabled/,
  );
});

test("missing or rejected credentials block transmission and report unavailability", async () => {
  const h = harness({ enabled: true, usable: false });
  await assert.rejects(
    h.execute("skim_text", { text: "hello", goal: "goal" }),
    /Jev is unavailable/,
  );
  assert.equal(h.requests.length, 0);
  await h.command("status");
  assert.match(h.notifications.at(-1)!, /unavailable/);
  assert.equal(h.constructors(), 0);
  h.auth(true);
  await h.execute("skim_text", { text: "hello", goal: "goal" });
  assert.equal(h.constructors(), 1);
});

test("a confirmation cannot grant consent to a changed session", async () => {
  const h = harness();
  let resolve!: (value: boolean) => void;
  h.confirm(
    () =>
      new Promise<boolean>((done) => {
        resolve = done;
      }),
  );
  const enabling = h.command("enable");
  h.session("session-two");
  h.events.get("session_start")!({}, h.ctx);
  resolve(true);
  await assert.rejects(enabling, /consent changed/);
  await assert.rejects(
    h.execute("skim_text", { text: "hello", goal: "goal" }),
    /disabled/,
  );
});

test("concurrent confirmations cannot replace the active consent lifetime", async () => {
  const h = harness();
  const pending: ((value: boolean) => void)[] = [];
  h.confirm(() => new Promise<boolean>((resolve) => pending.push(resolve)));
  const first = h.command("enable");
  const second = h.command("enable");
  pending[0]!(true);
  await first;
  pending[1]!(true);
  await assert.rejects(second, /consent changed/);
  const result = await h.execute("skim_text", { text: "hello", goal: "goal" });
  assert.equal(result.structuredContent.status, "complete");
});

test("disable cancels an active batch without returning partial verdicts", async () => {
  let entered!: () => void;
  const ready = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const h = harness({
    enabled: true,
    evaluate: async (_requests, config) => {
      entered();
      await new Promise<void>((resolve) =>
        config.signal.addEventListener("abort", () => resolve(), {
          once: true,
        }),
      );
      return {
        answers: {},
        failures: 1,
        skipped: 1,
        usage: { input_tokens: 0, output_tokens: 0 },
      };
    },
  });
  const running = h.execute("skim_text", { text: "hello", goal: "goal" });
  await ready;
  const rejected = assert.rejects(
    running,
    /cancelled.*Original input retained/,
  );
  await h.command("disable");
  await rejected;
  const artifacts = readdirSync(h.root);
  assert.equal(artifacts.length, 1);
  assert.equal(
    JSON.parse(
      readFileSync(join(h.root, artifacts[0]!, "original.json"), "utf8"),
    ).text,
    "hello",
  );
});

test("unexpected transport failures do not expose exception bodies or submitted secrets", async () => {
  const h = harness({
    enabled: true,
    evaluate: async () => {
      throw new Error("UPSTREAM_PRIVATE_BODY");
    },
  });
  await assert.rejects(
    h.execute("skim_text", { text: "hello", goal: "goal" }),
    (error: Error) => {
      assert.match(error.message, /Original input retained/);
      assert.ok(!error.message.includes("UPSTREAM_PRIVATE_BODY"));
      return true;
    },
  );
});
