import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createTypeSafe, openUsageLedger } from "pi-typesafe";
import { judgePrepared, prepareFilter } from "./core.ts";
import register from "./index.ts";

async function isolated(run: (root: string) => Promise<void>) {
  const previous = process.env.PI_CODING_AGENT_DIR;
  const root = mkdtempSync(join(tmpdir(), "skimmer-sdk-"));
  process.env.PI_CODING_AGENT_DIR = root;
  try {
    await run(root);
  } finally {
    if (previous === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previous;
  }
}

test("real SDK default authentication supports enable, status and disable commands", async () => {
  await isolated(async () => {
    const previousKey = process.env.TYPESAFE_API_KEY;
    const previousEnabled = process.env.PI_SKIMMER_ENABLED;
    const previousFetch = globalThis.fetch;
    process.env.TYPESAFE_API_KEY = "fixture-command-key";
    delete process.env.PI_SKIMMER_ENABLED;
    let requests = 0;
    globalThis.fetch = async () => {
      requests++;
      throw new Error("commands must not send");
    };
    try {
      const commands = new Map<string, any>();
      const notices: string[] = [];
      const confirmations: string[] = [];
      register({
        on: () => {},
        registerTool: () => {},
        registerCommand: (name: string, command: any) =>
          commands.set(name, command),
      } as any);
      const ctx: any = {
        cwd: "/fixture/project",
        hasUI: true,
        sessionManager: { getSessionId: () => "real-sdk-command-fixture" },
        ui: {
          setStatus: () => {},
          notify: (message: string) => notices.push(message),
          confirm: async (_title: string, message: string) => {
            confirmations.push(message);
            return true;
          },
        },
      };
      for (const action of ["enable", "status", "disable"])
        await commands.get("skim").handler(action, ctx);
      assert.match(confirmations[0]!, /api.typesafe.ai/);
      assert.ok(notices.some((message) => message.includes("consent granted")));
      assert.ok(!notices.join("").includes("fixture-command-key"));
      assert.equal(requests, 0);
    } finally {
      globalThis.fetch = previousFetch;
      if (previousKey === undefined) delete process.env.TYPESAFE_API_KEY;
      else process.env.TYPESAFE_API_KEY = previousKey;
      if (previousEnabled === undefined) delete process.env.PI_SKIMMER_ENABLED;
      else process.env.PI_SKIMMER_ENABLED = previousEnabled;
    }
  });
});

test("real pi-typesafe adapter validates requests, projects fields, and records shared usage", async () => {
  await isolated(async (root) => {
    const requests: any[] = [];
    const client = createTypeSafe({
      apiKey: "fixture-not-a-real-key",
      model: "fixture-configured-model",
      maxRequests: 5,
      ledger: openUsageLedger({ path: join(root, "usage.json") }),
      fetch: async (_url, options) => {
        const request = JSON.parse(options!.body as string);
        requests.push(request);
        const answers = Object.fromEntries(
          Object.entries(request.state.units).map(([key, text]) => [
            key,
            { type: "noul", noul: String(text).includes("keep") ? 0.9 : 0.1 },
          ]),
        );
        return new Response(
          JSON.stringify({
            model: "fixture-model",
            answers,
            usage: { input_tokens: 20, output_tokens: 2 },
          }),
          { headers: { "content-type": "application/json" } },
        );
      },
    });
    const input = prepareFilter({
      items: [
        { title: "keep", private: "NO_REMOTE_ACCESS" },
        { title: "drop", private: "NO_REMOTE_ACCESS" },
      ],
      fields: ["title"],
      goal: "keep matching titles",
    });
    const outcome = await judgePrepared(
      client,
      input,
      undefined,
      client.getSpend().usdPerMTok,
    );
    assert.equal(outcome.status, "complete");
    assert.deepEqual(
      outcome.kept.map((unit) => unit.index),
      [0],
    );
    assert.ok(!JSON.stringify(requests).includes("NO_REMOTE_ACCESS"));
    assert.equal(requests[0].model, "fixture-configured-model");
    assert.equal(client.getUsage().requestsSucceeded, 1);
    assert.equal(client.getSpend().today.inputTokens, 20);
    assert.equal(
      JSON.parse(readFileSync(join(root, "usage.json"), "utf8")).version,
      1,
    );
  });
});

test("real SDK session budget halts submission and remaining items are unjudged and retained", async () => {
  await isolated(async (root) => {
    let calls = 0;
    const client = createTypeSafe({
      apiKey: "fixture-not-a-real-key",
      maxRequests: 1,
      ledger: openUsageLedger({ path: join(root, "usage.json") }),
      fetch: async (_url, options) => {
        calls++;
        const request = JSON.parse(options!.body as string);
        return new Response(
          JSON.stringify({
            model: "fixture-model",
            answers: Object.fromEntries(
              Object.keys(request.questions).map((key) => [
                key,
                { type: "noul", noul: 0.1 },
              ]),
            ),
            usage: { input_tokens: 20, output_tokens: 2 },
          }),
          { headers: { "content-type": "application/json" } },
        );
      },
    });
    const input = prepareFilter({
      items: Array.from({ length: 70 }, (_, i) => `entry ${i}`),
      goal: "find relevant entries",
    });
    const outcome = await judgePrepared(client, input);
    assert.equal(calls, 1);
    assert.equal(outcome.status, "degraded");
    assert.equal(outcome.omitted_count, 24);
    assert.equal(outcome.unjudged_count, 46);
    assert.equal(outcome.kept_count, 46);
    assert.deepEqual(
      outcome.kept.map((unit) => unit.index),
      Array.from({ length: 46 }, (_, i) => i + 24),
    );
  });
});

test("real SDK shared daily budget blocks transmission and preserves unjudged items", async () => {
  await isolated(async (root) => {
    let calls = 0;
    const sharedLedger = openUsageLedger({ path: join(root, "usage.json") });
    sharedLedger.recordStart();
    sharedLedger.recordSuccess(1_000_000, 0);
    const client = createTypeSafe({
      apiKey: "fixture-not-a-real-key",
      maxUsdPerDay: 0.01,
      ledger: openUsageLedger({ path: join(root, "usage.json") }),
      fetch: async () => {
        calls++;
        throw new Error("must not send");
      },
    });
    const outcome = await judgePrepared(
      client,
      prepareFilter({ items: ["first", "second"], goal: "goal" }),
    );
    assert.equal(calls, 0);
    assert.equal(outcome.status, "degraded");
    assert.equal(outcome.unjudged_count, 2);
    assert.equal(outcome.kept_count, 2);
  });
});

test("real SDK malformed responses fail explicitly rather than manufacturing low scores", async () => {
  await isolated(async (root) => {
    const client = createTypeSafe({
      apiKey: "fixture-not-a-real-key",
      maxRequests: 2,
      ledger: openUsageLedger({ path: join(root, "usage.json") }),
      fetch: async () =>
        new Response(
          JSON.stringify({
            model: "fixture-model",
            answers: {},
            usage: { input_tokens: 0, output_tokens: 0 },
          }),
          { headers: { "content-type": "application/json" } },
        ),
    });
    const outcome = await judgePrepared(
      client,
      prepareFilter({ items: ["first", "second"], goal: "goal" }),
    );
    assert.equal(outcome.status, "degraded");
    assert.equal(outcome.unjudged_count, 2);
    assert.equal(outcome.kept_count, 2);
  });
});
