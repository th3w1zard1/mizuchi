/**
 * Generate prompts for 10 easy-to-decompile functions.
 *
 * Selection criteria:
 *   - Must be Thumb encoding
 *   - Must have at least one decompiled example (via vector similarity)
 *   - Must NOT be in the m4a module or related to m4a
 *   - Must be short (fewest assembly lines)
 *
 * Usage:
 *   npx tsx scripts/generate-easy-prompts.ts
 *   npx tsx scripts/generate-easy-prompts.ts -c path/to/config.yaml
 */
import fs from 'fs/promises';
import path from 'path';
import YAML from 'yaml';

import { isArmPlatform, loadConfig } from '~/shared/config.js';
import { parseMapFile, resolveObjectPath } from '~/shared/map-file/map-file.js';
import { type DecompFunctionDoc, MizuchiDb, type MizuchiDbDump } from '~/shared/mizuchi-db/mizuchi-db.js';
import { stripTrailingAsmLines } from '~/shared/prompt-builder/craft-prompt.js';
import { createDecompilePrompt } from '~/shared/prompt-builder/prompt-builder.js';
import type { PromptSettings } from '~/shared/prompt-builder/prompt-settings.js';

const TARGET_COUNT = 10;

/** Module paths and name prefixes associated with the m4a sound engine. */
const M4A_PATTERNS = [
  'm4a',
  'ply_',
  'MPlay',
  'Midi',
  'Sound',
  'Cgb',
  'Sappy',
  'Voice',
  'Track',
  'ChannelMix',
  'SoundBuffer',
  'SoundCmd',
  'SoundEffect',
  'SoundHardware',
  'SoundContext',
  'SoundClear',
  'SoundMain',
  'SoundMixer',
];

/** Modules that are compiler runtime / not actual game code. */
const EXCLUDED_MODULES = ['crt0', 'libgcc'];

/** Minimum cosine similarity for a decompiled example to count. */
const MIN_EXAMPLE_SIMILARITY = 0.75;

function isM4aRelated(fn: DecompFunctionDoc): boolean {
  const mod = fn.asmModulePath ?? fn.cModulePath ?? '';
  const name = fn.name;

  if (mod.includes('m4a')) {
    return true;
  }

  return M4A_PATTERNS.some((pattern) => name.startsWith(pattern));
}

function isCompilerRuntime(fn: DecompFunctionDoc): boolean {
  const mod = fn.asmModulePath ?? fn.cModulePath ?? '';
  return EXCLUDED_MODULES.some((pattern) => mod.includes(pattern));
}

function countAsmLines(asmCode: string): number {
  return asmCode.split('\n').filter((l) => l.trim()).length;
}

/**
 * Detect whether the assembly falls through to the next function.
 * Walks backwards past pool constants (.4byte/.word/.2byte), padding
 * (lsls r0, r0, #0x00), labels, and nops. If the last real instruction
 * is not a return (bx, pop pc, mov pc) or unconditional branch, the
 * function falls through.
 */
function fallsThrough(asmCode: string): boolean {
  const lines = asmCode.split('\n').filter((l) => l.trim());
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = lines[i].trim();
    if (l.includes('.4byte') || l.includes('.word') || l.includes('.2byte')) {
      continue;
    }
    if (l === 'lsls r0, r0, #0x00') {
      continue;
    }
    if (l.endsWith(':')) {
      continue;
    }
    if (l === 'nop') {
      continue;
    }
    // Last real instruction — check if it's a return or unconditional branch
    if (/^bx\s/.test(l)) {
      return false;
    }
    if (/^pop\s/.test(l) && l.includes('pc')) {
      return false;
    }
    if (/^mov\s+pc\s*,/.test(l)) {
      return false;
    }
    if (/^b\s+\w/.test(l)) {
      return false;
    }
    return true;
  }
  return true;
}

function parseArgs(): { configPath: string } {
  const args = process.argv.slice(2);
  const cIndex = args.indexOf('-c');
  const configPath = cIndex !== -1 && args[cIndex + 1] ? path.resolve(args[cIndex + 1]) : path.resolve('mizuchi.yaml');
  return { configPath };
}

async function main() {
  const { configPath } = parseArgs();
  console.log(`Using config: ${configPath}`);
  const config = await loadConfig(configPath);

  const projectRoot = config.global.projectRoot;
  const platform = config.global.target ?? 'gba';
  const promptsDir = config.global.promptsDir ?? './prompts';
  const mapFilePath = config.global.mapFilePath;

  // Load mizuchi-db
  console.log(`Loading mizuchi-db.json from ${projectRoot}...`);
  const raw = await fs.readFile(path.join(projectRoot, 'mizuchi-db.json'), 'utf-8');
  const dump: MizuchiDbDump = JSON.parse(raw);

  const db = MizuchiDb.fromDump(dump);
  const stats = db.getStats();
  console.log(
    `Loaded ${stats.totalFunctions} functions (${stats.decompiledFunctions} decompiled, ${stats.asmOnlyFunctions} asm-only)`,
  );

  // Load symbol map
  let symbolMap: Map<string, string> | null = null;
  if (mapFilePath) {
    console.log(`Parsing map file: ${mapFilePath}`);
    const mapContent = await fs.readFile(mapFilePath, 'utf-8');
    symbolMap = parseMapFile(mapContent);
    console.log(`Found ${symbolMap.size} symbols in map file`);
  }

  // Get difficulty scores to filter Thumb-only
  const scores = db.getDifficultyScores();

  // Collect eligible functions: undecompiled, Thumb, non-m4a
  const eligible: Array<{ fn: DecompFunctionDoc; lines: number; hasExample: boolean }> = [];
  for (const fn of db.functions) {
    if (fn.cCode) {
      continue;
    }

    // Must be Thumb on ARM platforms
    if (isArmPlatform(platform)) {
      const diffScore = scores.get(fn.id);
      if (!diffScore || diffScore.metrics.armEncoding !== 'thumb') {
        continue;
      }
    }

    // Must not be m4a-related
    if (isM4aRelated(fn)) {
      continue;
    }

    // Must not be compiler runtime (libgcc, crt0)
    if (isCompilerRuntime(fn)) {
      continue;
    }

    // Assembly must not fall through to the next function
    if (fallsThrough(fn.asmCode)) {
      continue;
    }

    // Must have at least one decompiled similar function with good similarity
    const similar = db.findSimilar(fn.id, 50);
    const bestExample = similar.find((s) => s.function.cCode && !isM4aRelated(s.function));
    const hasGoodExample = bestExample !== undefined && bestExample.similarity >= MIN_EXAMPLE_SIMILARITY;

    eligible.push({
      fn,
      lines: countAsmLines(fn.asmCode),
      hasExample: hasGoodExample,
    });
  }

  console.log(`\nEligible functions (Thumb, non-m4a, undecompiled): ${eligible.length}`);

  // Filter to those with examples and sort by line count (shortest = easiest)
  const withExamples = eligible.filter((e) => e.hasExample);
  console.log(`With decompiled examples: ${withExamples.length}`);

  withExamples.sort((a, b) => a.lines - b.lines);

  // Pick the shortest 10
  const selected = withExamples.slice(0, TARGET_COUNT);

  console.log(`\nSelected ${selected.length} functions (sorted by asm line count):`);
  for (const { fn, lines } of selected) {
    const similar = db.findSimilar(fn.id, 50);
    const bestExample = similar.find((s) => s.function.cCode && !isM4aRelated(s.function));
    const exampleInfo = bestExample
      ? `best example: ${bestExample.function.name} (${bestExample.similarity.toFixed(3)})`
      : 'no example';
    console.log(`  ${String(lines).padStart(3)} lines | ${fn.name} | ${fn.asmModulePath} | ${exampleInfo}`);
  }

  // Build and save prompts
  console.log(`\nBuilding and saving prompts to ${promptsDir}...`);
  let savedCount = 0;
  let failedCount = 0;

  for (const { fn, lines } of selected) {
    process.stdout.write(`  ${fn.name} (${lines} lines)... `);

    try {
      const prompt = await createDecompilePrompt({
        db,
        functionId: fn.id,
        projectRoot,
        platform,
      });

      const promptDir = path.join(promptsDir, fn.name);
      await fs.mkdir(promptDir, { recursive: true });

      // Write prompt.md
      await fs.writeFile(path.join(promptDir, 'prompt.md'), prompt, 'utf-8');

      // Resolve targetObjectPath
      const targetObjectPath = symbolMap ? await resolveObjectPath(fn.name, projectRoot, symbolMap) : null;

      if (!targetObjectPath) {
        console.log(`SKIPPED: could not resolve targetObjectPath`);
        failedCount++;
        continue;
      }

      // Write settings.yaml
      const settings: PromptSettings = {
        functionName: fn.name,
        targetObjectPath,
        asm: stripTrailingAsmLines(fn.asmCode),
      };
      await fs.writeFile(path.join(promptDir, 'settings.yaml'), YAML.stringify(settings), 'utf-8');

      console.log(`saved`);
      savedCount++;
    } catch (err) {
      console.log(`FAILED: ${err instanceof Error ? err.message : String(err)}`);
      failedCount++;
    }
  }

  console.log(`\nDone! Saved ${savedCount} prompts, ${failedCount} failed.`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
