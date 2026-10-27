import { dirname } from "node:path";
import type {
  ExtensionAPI,
  ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import {
  authState,
  backendHost,
  createTypeSafe,
  describeAuth,
  type TypeSafe,
} from "pi-typesafe";
import {
  jsonValue,
  judgePrepared,
  prepareFilter,
  prepareSkim,
  type Prepared,
} from "./core.ts";
import {
  cleanArtifacts,
  describeClean,
  present,
  saveOriginal,
  saveResult,
} from "./artifacts.ts";

interface Dependencies {
  createClient(): TypeSafe;
  auth(): { usable: boolean; text: string; backend: string };
  environment: NodeJS.ProcessEnv;
  artifactRoot?: string;
}

const defaults: Dependencies = {
  createClient: () =>
    createTypeSafe({ maxRequests: 100, maxUsdPerDay: 1, timeoutMs: 10_000 }),
  auth: () => {
    const state = authState();
    return {
      usable: state.usable,
      text: describeAuth(state).text,
      backend: backendHost(state.backend),
    };
  },
  environment: process.env,
};

function disclosure(backend: string): string {
  const host = new URL(backend.includes("://") ? backend : `https://${backend}`)
    .host;
  return `Skimming sends the supplied text or selected item fields, goal, and source label to ${host} and may incur charges. Spending is estimated, not a billing guarantee. Only submit approved, nonsensitive material. Obvious credential checks are not comprehensive secret detection. Owner-only local recovery files retain the complete input and output. /skim disable stops future requests and cancels active skims. This permission is separate from /typesafe enable.`;
}
const guidelines = [
  "Use skim_text or filter_items for large, approved, nonsensitive material with a specific goal; not as exhaustive verification. They require separate /skim enable consent (or operator-set PI_SKIMMER_ENABLED=1).",
  "Do not replace every read with skimming. For critical decisions inspect the original, including constraints and exceptions. A relevance probability is not proof or authorization.",
  "These tools never fetch URLs, open source files, or expand grep paths. Submit material already obtained through approved tools. Never submit credentials or private data without authorization.",
];

const outputSchema = Type.Object({
  kind: Type.Union([Type.Literal("text"), Type.Literal("items")]),
  source: Type.String(),
  status: Type.Union([Type.Literal("complete"), Type.Literal("degraded")]),
  threshold: Type.Number(),
  total_count: Type.Integer(),
  kept_count: Type.Integer(),
  omitted_count: Type.Integer(),
  unjudged_count: Type.Integer(),
  input_chars: Type.Integer(),
  kept_chars: Type.Integer(),
  input_truncated: Type.Literal(false),
  output_truncated: Type.Boolean(),
  displayed_count: Type.Integer(),
  original_path: Type.String(),
  result_path: Type.String(),
  warnings: Type.Array(Type.String()),
  model: Type.Union([Type.String(), Type.Null()]),
  usage: Type.Object({
    input_tokens: Type.Integer(),
    output_tokens: Type.Integer(),
    estimated_usd: Type.Number(),
  }),
  kept: Type.Array(
    Type.Object({
      index: Type.Integer(),
      p: Type.Union([Type.Number(), Type.Null()]),
      text: Type.Optional(Type.String()),
      start_line: Type.Optional(Type.Integer()),
      end_line: Type.Optional(Type.Integer()),
      item: Type.Optional(Type.Unknown()),
    }),
  ),
});

export default function skimmer(
  pi: ExtensionAPI,
  dependencies: Dependencies = defaults,
) {
  let client: TypeSafe | undefined;
  let clientBackend: string | undefined;
  let session: string | undefined;
  let scope: string | undefined;
  let approvedScope: string | undefined;
  let approvedBackend: string | undefined;
  let generation = 0;
  let lifetime = new AbortController();
  let lastSummary = "No material processed.";
  const writing = new Set<string>();

  function synchronize(ctx: ExtensionContext) {
    const id = ctx.sessionManager.getSessionId();
    const next = JSON.stringify([id, ctx.cwd]);
    if (scope === next) return;
    lifetime.abort();
    lifetime = new AbortController();
    generation++;
    if (session !== id) {
      client = undefined;
      clientBackend = undefined;
      lastSummary = "No material processed.";
    }
    session = id;
    scope = next;
    approvedScope =
      dependencies.environment.PI_SKIMMER_ENABLED === "1" ? scope : undefined;
    approvedBackend = approvedScope ? dependencies.auth().backend : undefined;
  }

  function updateStatus(ctx: ExtensionContext) {
    if (!ctx.hasUI) return;
    const auth = dependencies.auth();
    const enabled = approvedScope === scope && approvedBackend === auth.backend;
    const available = auth.usable;
    ctx.ui.setStatus(
      "skimmer",
      enabled ? (available ? "skim on" : "skim unavailable") : "skim off",
    );
  }

  function getClient() {
    const backend = dependencies.auth().backend;
    if (client && clientBackend !== backend)
      throw new Error(
        "TypeSafe backend changed after the client was created. Start a new Pi session to refresh it without resetting the current request budget.",
      );
    if (!client) {
      client = dependencies.createClient();
      clientBackend = backend;
    }
    return client;
  }

  async function execute(
    prepared: Prepared,
    signal: AbortSignal | undefined,
    ctx: ExtensionContext,
  ) {
    synchronize(ctx);
    const authentication = dependencies.auth();
    if (approvedScope !== scope || approvedBackend !== authentication.backend)
      throw new Error(
        "Skimming is disabled. The operator must run /skim enable, or explicitly set PI_SKIMMER_ENABLED=1 before starting Pi. /typesafe enable does not grant this permission.",
      );
    if (!authentication.usable)
      throw new Error(
        "Jev is unavailable. Check /typesafe status or /typesafe login; no material was sent.",
      );
    const active = AbortSignal.any([
      lifetime.signal,
      AbortSignal.timeout(30_000),
      ...(signal ? [signal] : []),
    ]);
    active.throwIfAborted();
    const currentClient = getClient();
    const paths = await saveOriginal(prepared, dependencies.artifactRoot);
    const folder = dirname(paths.originalPath);
    writing.add(folder);
    try {
      const outcome = await judgePrepared(
        currentClient,
        prepared,
        active,
        currentClient.getSpend().usdPerMTok,
      );
      await saveResult(outcome, paths);
      active.throwIfAborted();
      const display = jsonValue(present(outcome, paths));
      lastSummary = `${outcome.kept_count}/${outcome.total_count} retained; ${outcome.unjudged_count} unjudged. ${outcome.status}.`;
      updateStatus(ctx);
      const cost = outcome.usage.estimated_usd;
      return {
        content: [
          { type: "text" as const, text: JSON.stringify(display, null, 2) },
        ],
        details: { ...paths, status: outcome.status },
        structuredContent: display,
        isError: outcome.status === "degraded",
        usage: {
          input: outcome.usage.input_tokens,
          output: outcome.usage.output_tokens,
          cacheRead: 0,
          cacheWrite: 0,
          totalTokens: outcome.usage.input_tokens + outcome.usage.output_tokens,
          cost: {
            input: cost,
            output: 0,
            cacheRead: 0,
            cacheWrite: 0,
            total: cost,
          },
        },
      };
    } catch {
      updateStatus(ctx);
      throw new Error(
        `${active.aborted ? "Skimming cancelled or timed out" : "Skimming could not complete"}. Original input retained at ${paths.originalPath}. No discarded output should be treated as a relevance judgment.`,
      );
    } finally {
      writing.delete(folder);
    }
  }

  pi.on("session_start", (_event, ctx) => {
    scope = undefined;
    session = undefined;
    synchronize(ctx);
    updateStatus(ctx);
  });
  pi.on("session_shutdown", (_event, ctx) => {
    lifetime.abort();
    approvedScope = undefined;
    approvedBackend = undefined;
    generation++;
    if (ctx.hasUI) ctx.ui.setStatus("skimmer", undefined);
  });

  pi.registerCommand("skim", {
    description:
      "Enable/disable external Jev skimming for this session and working directory, show status, or clean up old recovery folders",
    async handler(args, ctx) {
      synchronize(ctx);
      const action = args.trim() || "status";
      function report(message: string) {
        if (ctx.hasUI) ctx.ui.notify(message, "info");
        else
          pi.sendMessage(
            { customType: "skimmer-status", content: message, display: true },
            { triggerTurn: false },
          );
      }
      if (action === "disable") {
        approvedScope = undefined;
        approvedBackend = undefined;
        lifetime.abort();
        generation++;
        updateStatus(ctx);
        report(
          "Skimming disabled; active requests cancelled. Recovery files are retained.",
        );
        return;
      }
      if (action === "enable") {
        const authentication = dependencies.auth();
        if (!authentication.usable)
          throw new Error(
            "Jev is unavailable; use /typesafe status or /typesafe login first.",
          );
        if (
          approvedScope !== scope ||
          approvedBackend !== authentication.backend
        ) {
          if (!ctx.hasUI)
            throw new Error(
              "Interactive consent is required. For headless use, the operator must set PI_SKIMMER_ENABLED=1 before starting Pi.",
            );
          const currentGeneration = generation;
          const accepted = await ctx.ui.confirm(
            "Enable external skimming?",
            disclosure(authentication.backend),
          );
          synchronize(ctx);
          if (!accepted) return;
          if (currentGeneration !== generation)
            throw new Error(
              "Session or consent changed while confirming. Run /skim enable again.",
            );
          const currentAuth = dependencies.auth();
          if (!currentAuth.usable)
            throw new Error("Jev became unavailable; nothing was enabled.");
          if (currentAuth.backend !== authentication.backend)
            throw new Error(
              "Jev backend changed; confirm its destination again.",
            );
          lifetime.abort();
          lifetime = new AbortController();
          approvedScope = scope;
          approvedBackend = authentication.backend;
          generation++;
        }
        updateStatus(ctx);
        report(
          "Skimming enabled for this session and working directory. Up to 100 Jev requests per session; the shared estimated daily budget is at most $1 (lower PI_TYPESAFE_MAX_* caps apply). /skim disable stops it.",
        );
        return;
      }
      if (action === "clean") {
        report(
          describeClean(
            await cleanArtifacts(dependencies.artifactRoot, {
              active: writing,
            }),
          ),
        );
        return;
      }
      if (action !== "status")
        throw new Error("Usage: /skim enable | disable | status | clean");
      const auth = dependencies.auth();
      const spend = auth.usable ? getClient().getSpend() : undefined;
      report(
        `Skimming: ${approvedScope === scope && approvedBackend === auth.backend ? "consent granted" : "disabled"}. ${auth.text} ${lastSummary} ${spend ? JSON.stringify(spend) : "Spend unavailable until Jev credentials are usable."} ${disclosure(auth.backend)}`,
      );
    },
  });

  pi.registerTool({
    name: "skim_text",
    label: "Skim Text",
    description:
      "Use Jev to retain goal-relevant portions of already supplied, approved nonsensitive text. Does not fetch URLs or read files. Requires /skim enable; data leaves this machine. Defaults: 2000-character chunks, 10% overlap, threshold 0.5. Input over 256000 UTF-8 JSON bytes fails instead of truncating. Unjudged chunks are retained; all originals and full results have owner-only recovery paths. Filtering is not exhaustive verification.",
    promptSnippet:
      "Filter large approved text with Jev, preserving citations and recovery paths",
    promptGuidelines: guidelines,
    parameters: Type.Object(
      {
        text: Type.String({ minLength: 1 }),
        goal: Type.String({ minLength: 1, maxLength: 2_000 }),
        source: Type.Optional(
          Type.String({
            maxLength: 500,
            description:
              "Display label only; never fetched or opened. Sent to Jev.",
          }),
        ),
        threshold: Type.Optional(Type.Number({ minimum: 0, maximum: 1 })),
        chunk_chars: Type.Optional(
          Type.Integer({ minimum: 500, maximum: 8_000 }),
        ),
        overlap: Type.Optional(Type.Number({ minimum: 0, maximum: 0.5 })),
      },
      { additionalProperties: false },
    ),
    outputSchema,
    executionMode: "sequential",
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      openWorldHint: true,
    },
    async execute(_id, params, signal, _onUpdate, ctx) {
      return execute(prepareSkim(params), signal, ctx);
    },
  });

  pi.registerTool({
    name: "filter_items",
    label: "Filter Items",
    description:
      "Use Jev to filter 1–256 supplied JSON items by a specific goal, preserving original order and values. Requires separate /skim enable consent. fields selects literal top-level fields for every object; any missing field fails the entire call before transmission, never falls back. Only selected fields go to Jev; kept items and local recovery files retain original fields. No silent item truncation or top-N cap; API failures retain unjudged items with warnings. Do not submit credentials. Input limit: 256000 UTF-8 JSON bytes.",
    promptSnippet:
      "Filter approved lists with strict field projection and explicit unjudged results",
    parameters: Type.Object(
      {
        items: Type.Array(Type.Unknown(), { minItems: 1, maxItems: 256 }),
        goal: Type.String({ minLength: 1, maxLength: 2_000 }),
        fields: Type.Optional(
          Type.Array(Type.String({ minLength: 1, maxLength: 100 }), {
            minItems: 1,
            maxItems: 20,
            uniqueItems: true,
          }),
        ),
        threshold: Type.Optional(Type.Number({ minimum: 0, maximum: 1 })),
      },
      { additionalProperties: false },
    ),
    outputSchema,
    executionMode: "sequential",
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      openWorldHint: true,
    },
    async execute(_id, params, signal, _onUpdate, ctx) {
      return execute(prepareFilter(params), signal, ctx);
    },
  });
}
