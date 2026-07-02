const fs = require('fs');

const files = [
  'run-results-2026-02-28T09-52-28.json',
  'run-results-2026-03-01T00-58-02.json',
  'run-results-2026-03-07T20-03-41.json',
];
const labels = ['Run1', 'Run2', 'Run3'];

// Patterns that indicate Claude referenced m2c output
// The m2c output is presented as "## Initial Decompilation" in the system prompt
const M2C_PATTERNS = [
  /\bm2c\b/i,
  /initial\s+decompil/i,
  /the\s+decompil(ed|ation)\s+(code|output|attempt|result|version)/i,
  /decompil(ed|er|ation)\s+output/i,
  /starting\s+point/i,
  /provided\s+decompil/i,
  /given\s+decompil/i,
  /existing\s+decompil/i,
  /rough\s+decompil/i,
  /auto(mated|matic)?\s+decompil/i,
  /original\s+decompil/i,
  /reference\s+decompil/i,
  /mechanical\s+decompil/i,
  /raw\s+decompil/i,
  /machine[- ]generated/i,
  /based\s+on\s+the\s+decompil/i,
  /from\s+the\s+decompil/i,
  /improve\s+(upon|on)\s+the\s+decompil/i,
  /the\s+decompiled\s+code/i,
  /decompilation\s+(shows|suggests|indicates|reveals|uses|has|gives)/i,
  /looking\s+at\s+the\s+decompil/i,
  /the\s+decompiler\s+(shows|output|gave|produced|generated)/i,
  /decompilation\s+as\s+a\s+(start|base|reference|guide)/i,
];

const results = {};

for (let fi = 0; fi < files.length; fi++) {
  const data = JSON.parse(fs.readFileSync(files[fi], 'utf8'));

  for (const r of data.results) {
    if (!results[r.functionName]) {
      results[r.functionName] = { tier: r.promptPath.split('-')[0], runs: [] };
    }

    const runResult = {
      runIndex: fi,
      success: r.success,
      references: [],
      totalRefs: 0,
    };

    // Check all attempts
    for (const att of r.attempts) {
      for (const pr of att.pluginResults) {
        if (pr.pluginId !== 'claude-runner') continue;

        // Check chat sections
        for (const section of pr.sections || []) {
          if (section.type !== 'chat') continue;

          for (const msg of section.messages || []) {
            if (msg.role !== 'assistant') continue;

            let content = '';
            if (typeof msg.content === 'string') {
              content = msg.content;
            } else if (Array.isArray(msg.content)) {
              content = msg.content
                .filter((b) => b.type === 'text')
                .map((b) => b.text)
                .join(' ');
            }

            for (const pattern of M2C_PATTERNS) {
              const match = content.match(pattern);
              if (match) {
                const idx = content.indexOf(match[0]);
                const start = Math.max(0, idx - 100);
                const end = Math.min(content.length, idx + match[0].length + 100);
                const context = content.slice(start, end).replace(/\n/g, ' ').trim();

                runResult.references.push({
                  attempt: att.attemptNumber,
                  pattern: match[0],
                  context: (start > 0 ? '...' : '') + context + (end < content.length ? '...' : ''),
                });
                runResult.totalRefs++;
                break; // One match per message is enough
              }
            }
          }
        }
      }
    }

    results[r.functionName].runs.push(runResult);
  }
}

// Output summary
console.log('=== M2C REFERENCE SUMMARY ===\n');

for (let ri = 0; ri < 3; ri++) {
  let withRefs = 0,
    withoutRefs = 0;
  let withRefsSuccess = 0,
    withRefsFail = 0;
  let withoutRefsSuccess = 0,
    withoutRefsFail = 0;

  for (const [fn, data] of Object.entries(results)) {
    const run = data.runs[ri];
    if (run.totalRefs > 0) {
      withRefs++;
      if (run.success) withRefsSuccess++;
      else withRefsFail++;
    } else {
      withoutRefs++;
      if (run.success) withoutRefsSuccess++;
      else withoutRefsFail++;
    }
  }

  console.log(`${labels[ri]}: ${withRefs} functions with m2c refs, ${withoutRefs} without`);
  console.log(
    `  With refs:    ${withRefsSuccess} matched, ${withRefsFail} failed (${((withRefsSuccess / (withRefs || 1)) * 100).toFixed(0)}% success)`,
  );
  console.log(
    `  Without refs: ${withoutRefsSuccess} matched, ${withoutRefsFail} failed (${((withoutRefsSuccess / (withoutRefs || 1)) * 100).toFixed(0)}% success)`,
  );
  console.log('');
}

// Per-function details
console.log('\n=== PER-FUNCTION M2C REFERENCES ===\n');

const sortedFns = Object.entries(results).sort((a, b) => {
  const totalA = a[1].runs.reduce((s, r) => s + r.totalRefs, 0);
  const totalB = b[1].runs.reduce((s, r) => s + r.totalRefs, 0);
  return totalB - totalA;
});

for (const [fn, data] of sortedFns) {
  const totalRefs = data.runs.reduce((s, r) => s + r.totalRefs, 0);
  if (totalRefs === 0) continue;

  console.log(`${fn} (${data.tier}) - ${totalRefs} total references:`);
  for (let ri = 0; ri < 3; ri++) {
    const run = data.runs[ri];
    console.log(`  ${labels[ri]}: ${run.success ? 'MATCH' : 'FAIL'}, ${run.totalRefs} refs`);
    const seen = new Set();
    for (const ref of run.references) {
      const key = ref.pattern;
      if (seen.has(key)) continue;
      seen.add(key);
      console.log(`    Att ${ref.attempt}, pattern="${ref.pattern}": "${ref.context}"`);
    }
  }
  console.log('');
}

// Functions with NO references
console.log('\n=== FUNCTIONS WITH NO M2C REFERENCES (any run) ===\n');
for (const [fn, data] of sortedFns) {
  const totalRefs = data.runs.reduce((s, r) => s + r.totalRefs, 0);
  if (totalRefs > 0) continue;
  const outcomes = data.runs.map((r) => (r.success ? 'MATCH' : 'FAIL')).join(' / ');
  console.log(`${fn} (${data.tier}): ${outcomes}`);
}

// Save for UI
const uiData = {};
for (const [fn, data] of Object.entries(results)) {
  uiData[fn] = {
    tier: data.tier,
    runs: data.runs.map((r) => ({
      runIndex: r.runIndex,
      success: r.success,
      totalRefs: r.totalRefs,
      references: r.references.map((ref) => ({
        attempt: ref.attempt,
        pattern: ref.pattern,
        context: ref.context,
      })),
    })),
  };
}
fs.writeFileSync('src/ui/comparison/m2c-refs.json', JSON.stringify(uiData, null, 2));
console.log('\nSaved m2c-refs.json for UI');
