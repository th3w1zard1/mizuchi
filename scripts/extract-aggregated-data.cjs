const fs = require('fs');

const SA3_FILES = [
  'run-results-2026-02-28T09-52-28.json',
  'run-results-2026-03-01T00-58-02.json',
  'run-results-2026-03-07T20-03-41.json',
];
const AF_FILES = [
  'af-reports/run-results-2026-03-07T20-03-42.json',
  'af-reports/run-results-2026-03-08T00-55-50.json',
  'af-reports/run-results-2026-03-08T02-12-45.json',
];

function normalizeTokenUsage(tu) {
  // SA3 Run 1 has flat format, others have nested { model: { ... } }
  if (tu && tu.outputTokens !== undefined) {
    return {
      inputTokens: (tu.inputTokens || 0) + (tu.cacheReadInputTokens || 0) + (tu.cacheCreationInputTokens || 0),
      outputTokens: tu.outputTokens || 0,
      costUsd: tu.costUsd || 0,
    };
  }
  // Nested by model
  let input = 0,
    output = 0,
    cost = 0;
  for (const model of Object.values(tu || {})) {
    input += (model.inputTokens || 0) + (model.cacheReadInputTokens || 0) + (model.cacheCreationInputTokens || 0);
    output += model.outputTokens || 0;
    cost += model.costUsd || 0;
  }
  return { inputTokens: input, outputTokens: output, costUsd: cost };
}

function extractRun(filePath, runIndex, project) {
  const raw = JSON.parse(fs.readFileSync(filePath, 'utf8'));
  const label =
    project === 'sa3'
      ? ['SA3 Run 1 (Feb 28)', 'SA3 Run 2 (Mar 1)', 'SA3 Run 3 (Mar 7)'][runIndex]
      : ['AF Run 1 (Mar 7)', 'AF Run 2 (Mar 8a)', 'AF Run 3 (Mar 8b)'][runIndex];

  const config = {
    maxRetries: raw.config.maxRetries,
    ttftTimeoutMs: raw.config.ttftTimeoutMs || null,
    model: raw.config.model,
    softTimeoutMs: raw.config.softTimeout || null,
  };

  const functions = raw.results.map((r) => {
    const tier = r.promptPath.split('-')[0]; // easy, medium, hard

    // Determine matchSource (SA3 Run 1 may not have it)
    let matchSource = r.matchSource || null;
    if (!matchSource && r.success) matchSource = 'claude';

    // Fix: func_80954E0C_jp was matched by permuter, not m2c
    if (r.functionName === 'func_80954E0C_jp' && matchSource === 'programmatic-phase') {
      matchSource = 'decomp-permuter';
    }

    // Attempt-level data
    const attempts = r.attempts.map((att, ai) => {
      const cr = att.pluginResults.find((p) => p.pluginId === 'claude-runner');
      const obj = att.pluginResults.find((p) => p.pluginId === 'objdiff');
      const comp = att.pluginResults.find((p) => p.pluginId === 'compiler');
      const tu = cr ? normalizeTokenUsage(cr.data?.tokenUsage) : { inputTokens: 0, outputTokens: 0, costUsd: 0 };

      return {
        num: att.attemptNumber || ai + 1,
        durationMs: att.durationMs || 0,
        diffCount: obj?.data?.differenceCount ?? null,
        outputTokens: tu.outputTokens,
        inputTokens: tu.inputTokens,
        costUsd: tu.costUsd,
        softTimeout: Boolean(cr?.data?.softTimeoutTriggered),
        ttftTimedOut: Boolean(cr?.data?.ttftTimedOut),
        ttftMs: cr?.data?.ttftMs ?? null,
        stallDetected: Boolean(cr?.data?.stallDetected),
        hardTimeout:
          att.durationMs > 0 &&
          !att.success &&
          !cr?.data?.softTimeoutTriggered &&
          !cr?.data?.ttftTimedOut &&
          comp?.status === 'skipped',
        compileFail: comp?.status === 'failure',
        success: Boolean(att.success),
      };
    });

    const totalInputTokens = attempts.reduce((s, a) => s + a.inputTokens, 0);
    const totalOutputTokens = attempts.reduce((s, a) => s + a.outputTokens, 0);
    const totalCostUsd = attempts.reduce((s, a) => s + a.costUsd, 0);
    const aiDurationMs = attempts.reduce((s, a) => s + a.durationMs, 0);

    // m2c data from programmatic phase
    let m2cDiffCount = null;
    let m2cHasOutput = false;
    if (r.programmaticPhase) {
      const m2cPlugin = r.programmaticPhase.pluginResults.find((p) => p.pluginId === 'm2c');
      const objPlugin = r.programmaticPhase.pluginResults.find((p) => p.pluginId === 'objdiff');
      m2cHasOutput = Boolean(m2cPlugin?.data?.generatedCode);
      m2cDiffCount = objPlugin?.data?.differenceCount ?? null;
    }

    // Permuter background tasks
    const permuterTasks = (r.backgroundTasks || [])
      .filter((t) => t.pluginId === 'decomp-permuter')
      .map((t) => ({
        triggeredByAttempt: t.triggeredByAttempt,
        baseScore: t.data?.baseScore ?? 0,
        bestScore: t.data?.bestScore ?? 0,
        perfectMatch: Boolean(t.data?.perfectMatch),
        iterations: t.data?.iterationsRun ?? 0,
        durationMs: t.durationMs ?? 0,
      }));

    // Best diff count across all attempts
    const diffCounts = attempts.map((a) => a.diffCount).filter((d) => d !== null);
    const bestDiffCount = diffCounts.length > 0 ? Math.min(...diffCounts) : null;

    return {
      functionName: r.functionName,
      tier,
      success: Boolean(r.success),
      matchSource,
      totalDurationMs: r.totalDurationMs || 0,
      numAttempts: attempts.length,
      bestDiffCount,
      totalInputTokens,
      totalOutputTokens,
      totalCostUsd,
      softTimeouts: attempts.filter((a) => a.softTimeout).length,
      hardTimeouts: attempts.filter((a) => a.hardTimeout).length,
      ttftTimeouts: attempts.filter((a) => a.ttftTimedOut).length,
      compileFailures: attempts.filter((a) => a.compileFail).length,
      m2cDiffCount,
      m2cHasOutput,
      permuterTasks,
      aiDurationMs,
      attempts,
    };
  });

  const successCount = functions.filter((f) => f.success).length;

  return {
    label,
    timestamp: raw.timestamp,
    config,
    functions,
    summary: {
      totalPrompts: functions.length,
      successfulPrompts: successCount,
      successRate: (successCount / functions.length) * 100,
      totalDurationMs: raw.results.reduce((s, r) => s + (r.totalDurationMs || 0), 0),
      totalCost: functions.reduce((s, f) => s + f.totalCostUsd, 0),
      totalInputTokens: functions.reduce((s, f) => s + f.totalInputTokens, 0),
      totalOutputTokens: functions.reduce((s, f) => s + f.totalOutputTokens, 0),
      totalAttempts: functions.reduce((s, f) => s + f.numAttempts, 0),
      avgAttempts: functions.reduce((s, f) => s + f.numAttempts, 0) / functions.length,
    },
  };
}

// Extract all runs
const sa3Runs = SA3_FILES.map((f, i) => extractRun(f, i, 'sa3'));
const afRuns = AF_FILES.map((f, i) => extractRun(f, i, 'af'));

// Build aggregated data
const data = {
  projects: {
    sa3: {
      name: 'Sonic Advance 3',
      platform: 'GBA / ARM / GCC',
      runs: sa3Runs,
      functionNames: sa3Runs[0].functions.map((f) => f.functionName),
    },
    af: {
      name: 'Animal Forest',
      platform: 'N64 / MIPS / IDO',
      runs: afRuns,
      functionNames: afRuns[0].functions.map((f) => f.functionName),
    },
  },
};

const outPath = 'src/ui/aggregated/aggregated-data.json';
fs.mkdirSync('src/ui/aggregated', { recursive: true });
fs.writeFileSync(outPath, JSON.stringify(data, null, 2));
console.log('Wrote', outPath, '(' + (fs.statSync(outPath).size / 1024).toFixed(0) + ' KB)');

// Print summary
for (const [key, proj] of Object.entries(data.projects)) {
  console.log('\n' + proj.name + ' (' + proj.platform + '):');
  for (const run of proj.runs) {
    const s = run.summary;
    console.log(
      '  ' +
        run.label +
        ': ' +
        s.successfulPrompts +
        '/' +
        s.totalPrompts +
        ' matched, $' +
        s.totalCost.toFixed(2) +
        ', ' +
        s.totalAttempts +
        ' attempts, ' +
        (s.totalDurationMs / 60000).toFixed(1) +
        'm',
    );
  }
}
