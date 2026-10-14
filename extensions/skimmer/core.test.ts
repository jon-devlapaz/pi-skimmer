import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_BATCH_BYTES,
  MAX_INPUT_BYTES,
  judgePrepared,
  jsonValue,
  prepareFilter,
  prepareSkim,
  splitText,
  type Prepared,
} from "./core.ts";

function client(answers: Record<string, unknown>, failures = 0) {
  return {
    evaluateMany: async (_requests: unknown, options: unknown) => ({
      ok: !failures,
      answers,
      failures,
      skipped: failures,
      results: [],
      model: "fixture-model",
      usage: { input_tokens: 100, output_tokens: 10 },
      elapsedMs: 1,
      options,
    }),
  } as any;
}

function allAnswers(prepared: Prepared, p = 0.9) {
  return Object.fromEntries(
    prepared.units.map((unit) => [
      `unit_${unit.index}`,
      { type: "noul", noul: p },
    ]),
  );
}

test("chunks preserve Unicode, whitespace, CRLF and exact line citations", () => {
  const text = Array.from(
    { length: 300 },
    (_, i) => `Line ${i}: 😀 café\r\n`,
  ).join("");
  const plain = splitText(text, 500, 0);
  assert.equal(plain.map((unit) => unit.text).join(""), text);
  const chunks = splitText(text);
  let nextSearch = 0;
  let coveredUntil = 0;
  for (const unit of chunks) {
    const start = text.indexOf(unit.text, nextSearch);
    assert.ok(start >= 0 && start <= coveredUntil);
    const end = start + unit.text.length;
    assert.equal(unit.start_line, text.slice(0, start).split("\n").length);
    assert.equal(unit.end_line, text.slice(0, end - 1).split("\n").length);
    assert.ok(Array.from(unit.text).length <= 2_000);
    coveredUntil = end;
    nextSearch = start + 1;
  }
  assert.equal(coveredUntil, text.length);
});

test("defaults are 2000 characters, 10 percent overlap and threshold 0.5", () => {
  const input = prepareSkim({ text: "x".repeat(4_000), goal: "find x" });
  assert.equal(input.threshold, 0.5);
  assert.equal(input.units[0]!.text.length, 2_000);
  assert.equal(input.units[1]!.text.length, 2_000);
  assert.equal(input.units[2]!.text.length, 400);
  assert.equal(input.input_chars, 4_000);
});

test("rejects invalid controls, non-JSON input, cycles and inherited objects", () => {
  for (const threshold of [-1, 2, NaN, Infinity]) {
    assert.throws(
      () => prepareSkim({ text: "text", goal: "goal", threshold }),
      /threshold/,
    );
  }
  for (const chunk_chars of [0, 499, 8_001, 500.5]) {
    assert.throws(
      () => prepareSkim({ text: "text", goal: "goal", chunk_chars }),
      /chunk_chars/,
    );
  }
  for (const overlap of [-0.1, 0.6, NaN]) {
    assert.throws(
      () => prepareSkim({ text: "text", goal: "goal", overlap }),
      /overlap/,
    );
  }
  assert.throws(() => prepareSkim({ text: " ", goal: "goal" }), /nonempty/);
  assert.throws(() => prepareSkim({ text: "text", goal: " " }), /goal/);
  for (const value of [
    undefined,
    NaN,
    Infinity,
    1n,
    new Date(),
    new Set(),
    () => 1,
  ]) {
    assert.throws(() => jsonValue(value), /JSON/);
  }
  assert.throws(() => jsonValue({ [Symbol("hidden")]: "value" }), /JSON/);
  assert.throws(() => jsonValue(Array(1)), /JSON/);
  assert.throws(() => jsonValue(Array(MAX_INPUT_BYTES + 1)), /limit/);
  assert.throws(() => jsonValue(Object.assign([1], { extra: true })), /JSON/);
  let getterInvoked = false;
  const accessorArray = [1];
  Object.defineProperty(accessorArray, "0", {
    enumerable: true,
    get() {
      getterInvoked = true;
      throw new Error("secret exception body");
    },
  });
  assert.throws(() => jsonValue(accessorArray), /JSON/);
  assert.equal(getterInvoked, false);
  class CustomArray extends Array {}
  assert.throws(() => jsonValue(new CustomArray(1)), /plain JSON/);
  const cycle: any = {};
  cycle.self = cycle;
  assert.throws(() => jsonValue(cycle), /cycles/);
  assert.throws(
    () => jsonValue(Object.create({ title: "inherited" })),
    /plain JSON/,
  );
  assert.throws(
    () =>
      jsonValue({
        get secret() {
          throw new Error("getter ran");
        },
      }),
    /data properties/,
  );
});

test("enforces byte limits and never silently truncates text or items", () => {
  assert.throws(
    () => prepareSkim({ text: "😀".repeat(MAX_INPUT_BYTES / 4), goal: "goal" }),
    /UTF-8 JSON bytes/,
  );
  assert.throws(
    () => prepareFilter({ items: ["x".repeat(MAX_INPUT_BYTES)], goal: "goal" }),
    /UTF-8 JSON bytes/,
  );
  assert.throws(
    () => prepareFilter({ items: ["x".repeat(60_000)], goal: "goal" }),
    /too large/,
  );
  assert.throws(
    () => prepareFilter({ items: [], goal: "goal" }),
    /between 1 and 256/,
  );
  assert.throws(
    () => prepareFilter({ items: Array(257).fill("x"), goal: "goal" }),
    /between 1 and 256/,
  );
});

test("obvious credentials are refused in text, goal, labels and projected fields", () => {
  const marker = ["-----BEGIN ", "PRIVATE KEY-----"].join("");
  assert.throws(
    () => prepareSkim({ text: marker, goal: "goal" }),
    /credential/,
  );
  assert.throws(
    () =>
      prepareSkim({
        text: "x".repeat(490) + marker,
        goal: "goal",
        chunk_chars: 500,
        overlap: 0,
      }),
    /credential/,
  );
  assert.throws(
    () => prepareSkim({ text: "text", goal: marker }),
    /credential/,
  );
  assert.throws(
    () => prepareSkim({ text: "text", goal: "goal", source: marker }),
    /credential/,
  );
  assert.throws(
    () =>
      prepareFilter({
        items: [{ token: "ghp_" + "a".repeat(30) }],
        goal: "goal",
      }),
    /credential/,
  );
});

test("field projection never falls back, even for one missing field among present ones", () => {
  assert.throws(
    () =>
      prepareFilter({
        items: [{ title: "public" }, { private: "not sent" }],
        fields: ["title"],
        goal: "goal",
      }),
    /Item 1 is missing/,
  );
  assert.throws(
    () =>
      prepareFilter({
        items: [{ title: "public" }],
        fields: ["title", "body"],
        goal: "goal",
      }),
    /missing/,
  );
  for (const fields of [[], ["title", "title"], [""]]) {
    assert.throws(
      () =>
        prepareFilter({ items: [{ title: "public" }], fields, goal: "goal" }),
      /fields/,
    );
  }
  assert.throws(
    () => prepareFilter({ items: ["string"], fields: ["title"], goal: "goal" }),
    /must be an object/,
  );
});

test("only selected fields are transmitted; local items remain untouched and ordered", async () => {
  const items = [
    { id: 1, title: "Public", hidden: "ghp_" + "a".repeat(30) },
    { id: 2, title: "Other", hidden: "PRIVATE_SENTINEL" },
  ];
  const prepared = prepareFilter({
    items,
    fields: ["title"],
    goal: "find relevant titles",
  });
  const wire = JSON.stringify(prepared.requests);
  assert.ok(!wire.includes("PRIVATE_SENTINEL"));
  assert.ok(!wire.includes("ghp_"));
  assert.deepEqual(prepared.original, {
    items,
    fields: ["title"],
    goal: "find relevant titles",
  });
  const output = await judgePrepared(client(allAnswers(prepared)), prepared);
  assert.deepEqual(
    output.kept.map((unit) => unit.item),
    items,
  );
});

test("batches have unique per-unit questions, bounded bytes, and no whole-document repetition", () => {
  const items = Array.from(
    { length: 70 },
    (_, i) => `ONLY_${i}_` + "é".repeat(1_000),
  );
  const prepared = prepareFilter({ items, goal: "find matching entries" });
  assert.ok(prepared.requests.length >= 3);
  const ids: string[] = [];
  for (const request of prepared.requests) {
    assert.ok(Buffer.byteLength(JSON.stringify(request)) <= MAX_BATCH_BYTES);
    assert.ok(Object.keys(request.questions).length <= 24);
    const state = request.state as any;
    assert.deepEqual(Object.keys(state.units), Object.keys(request.questions));
    for (const [id, question] of Object.entries(request.questions)) {
      assert.equal(typeof question.instructions, "string");
      assert.ok(String(question.instructions).includes(`units.${id}`));
      ids.push(id);
    }
  }
  assert.equal(ids.length, items.length);
  assert.equal(new Set(ids).size, items.length);
});

test("threshold comparisons preserve source order and the inclusive boundary", async () => {
  const prepared = prepareFilter({
    items: ["first", "second", "third"],
    goal: "goal",
  });
  const output = await judgePrepared(
    client({
      unit_0: { type: "noul", noul: 0.9 },
      unit_1: { type: "noul", noul: 0.49 },
      unit_2: { type: "noul", noul: 0.5 },
    }),
    prepared,
    undefined,
    0.042,
  );
  assert.deepEqual(
    output.kept.map((unit) => unit.item),
    ["first", "third"],
  );
  assert.equal(output.omitted_count, 1);
  assert.equal(output.status, "complete");
  assert.equal(output.usage.input_tokens, 100);
  assert.equal(output.usage.estimated_usd, (100 * 0.042) / 1_000_000);
});

test("partial and total API failures retain every unjudged item with warnings", async () => {
  const prepared = prepareFilter({
    items: Array.from({ length: 60 }, (_, i) => i),
    goal: "goal",
  });
  for (const failures of [1, 3]) {
    const output = await judgePrepared(client({}, failures), prepared);
    assert.equal(output.kept_count, 60);
    assert.equal(output.unjudged_count, 60);
    assert.equal(output.omitted_count, 0);
    assert.equal(output.status, "degraded");
    assert.ok(output.warnings.some((warning) => warning.includes("retained")));
  }
});

test("missing, invalid and out-of-range probabilities are unjudged, not coerced", async () => {
  const prepared = prepareFilter({
    items: [0, 1, 2, 3, 4, 5, 6],
    goal: "goal",
  });
  const output = await judgePrepared(
    client({
      unit_0: { type: "noul", noul: NaN },
      unit_1: { type: "noul", noul: 1.1 },
      unit_2: { type: "noul", noul: -0.1 },
      unit_3: { type: "score", noul: 0.9 },
      unit_4: { type: "noul", noul: "0.9" },
      unit_5: { type: "noul", noul: Infinity },
    }),
    prepared,
  );
  assert.equal(output.unjudged_count, 7);
  assert.equal(output.kept_count, 7);
  assert.equal(output.status, "degraded");
});

test("cancellation stops before submission and remains cancellation after a batch settles", async () => {
  const prepared = prepareSkim({ text: "hello", goal: "greeting" });
  const aborted = AbortSignal.abort();
  let calls = 0;
  const fake = {
    evaluateMany: async () => {
      calls++;
      throw new Error("should not run");
    },
  } as any;
  await assert.rejects(judgePrepared(fake, prepared, aborted), /abort/i);
  assert.equal(calls, 0);
  const controller = new AbortController();
  const late = {
    evaluateMany: async () => {
      controller.abort();
      return { answers: {} };
    },
  } as any;
  await assert.rejects(
    judgePrepared(late, prepared, controller.signal),
    /abort/i,
  );
});
