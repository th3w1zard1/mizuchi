/**
 * Generate prompts for 10 random functions per difficulty tier.
 *
 * Usage:
 *   npx tsx scripts/generate-tier-prompts.ts                        # Uses mizuchi.yaml
 *   npx tsx scripts/generate-tier-prompts.ts -c path/to/config.yaml # Custom config
 *
 * Reads config YAML, loads mizuchi-db.json from projectRoot,
 * computes difficulty tiers, picks 10 random undecompiled functions per tier,
 * builds prompts, and saves them to ./prompts/.
 */
import fs from 'fs/promises';
import path from 'path';

import { isArmPlatform, loadConfig } from '~/shared/config.js';
import { parseMapFile } from '~/shared/map-file/map-file.js';
import type { DifficultyTier } from '~/shared/mizuchi-db/logistic-regression.js';
import { type DecompFunctionDoc, MizuchiDb, type MizuchiDbDump } from '~/shared/mizuchi-db/mizuchi-db.js';
import { stripTrailingAsmLines } from '~/shared/prompt-builder/craft-prompt.js';
import { createDecompilePrompt } from '~/shared/prompt-builder/prompt-builder.js';

const FUNCTIONS_PER_TIER = 10;

function pickRandom<T>(arr: T[], n: number): T[] {
  const shuffled = [...arr].sort(() => Math.random() - 0.5);
  return shuffled.slice(0, n);
}

async function resolveObjectPath(
  functionName: string,
  projectRoot: string,
  symbolMap: Map<string, string>,
): Promise<string | null> {
  const relativePath = symbolMap.get(functionName);
  if (!relativePath) {
    return null;
  }

  const directPath = path.join(projectRoot, relativePath);
  try {
    await fs.access(directPath);
    return directPath;
  } catch {
    // Try globbing under build/
  }

  const fileName = path.basename(relativePath);
  const buildDir = path.join(projectRoot, 'build');
  for await (const match of fs.glob(`**/${fileName}`, { cwd: buildDir })) {
    return path.join(buildDir, match);
  }

  return null;
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

  // Collect eligible (undecompiled) functions with their scores
  const scores = db.getDifficultyScores();
  const eligible: Array<{ fn: DecompFunctionDoc; score: number }> = [];
  for (const fn of db.functions) {
    if (fn.cCode) {
      continue; // skip already decompiled
    }
    const diffScore = scores.get(fn.id);
    if (!diffScore) {
      continue;
    }
    // Filter to Thumb-only for ARM platforms
    if (isArmPlatform(platform) && diffScore.metrics.armEncoding !== 'thumb') {
      continue;
    }
    eligible.push({ fn, score: diffScore.score });
  }

  // Compute tier thresholds from undecompiled functions only
  const sortedScores = eligible.map((e) => e.score).sort((a, b) => a - b);
  const n = sortedScores.length;
  const p33 = n > 0 ? sortedScores[Math.floor(n / 3)] : 0;
  const p66 = n > 0 ? sortedScores[Math.floor((2 * n) / 3)] : 0;

  console.log(
    `\nDifficulty thresholds (undecompiled only): easy ≤ ${p33.toFixed(4)}, medium ≤ ${p66.toFixed(4)}, hard > ${p66.toFixed(4)}`,
  );

  // Group by tier
  const byTier: Record<DifficultyTier, DecompFunctionDoc[]> = { easy: [], medium: [], hard: [] };
  for (const { fn, score } of eligible) {
    if (score <= p33) {
      byTier.easy.push(fn);
    } else if (score <= p66) {
      byTier.medium.push(fn);
    } else {
      byTier.hard.push(fn);
    }
  }

  console.log(`\nUndecompiled functions per tier:`);
  console.log(`  easy:   ${byTier.easy.length}`);
  console.log(`  medium: ${byTier.medium.length}`);
  console.log(`  hard:   ${byTier.hard.length}`);

  // Pick random functions
  const selected: Array<{ tier: DifficultyTier; fn: DecompFunctionDoc }> = [];
  for (const tier of ['easy', 'medium', 'hard'] as DifficultyTier[]) {
    const picks = pickRandom(byTier[tier], FUNCTIONS_PER_TIER);
    for (const fn of picks) {
      selected.push({ tier, fn });
    }
  }

  console.log(`\nSelected ${selected.length} functions:`);
  for (const { tier, fn } of selected) {
    const score = scores.get(fn.id)?.score ?? 0;
    console.log(`  [${tier.padEnd(6)}] ${fn.name} (score: ${score.toFixed(4)})`);
  }

  // Build and save prompts
  console.log(`\nBuilding and saving prompts to ${promptsDir}...`);
  let savedCount = 0;
  let failedCount = 0;

  for (const { tier, fn } of selected) {
    const score = scores.get(fn.id)?.score ?? 0;
    process.stdout.write(`  [${tier.padEnd(6)}] ${fn.name}... `);

    try {
      const prompt = await createDecompilePrompt({
        db,
        functionId: fn.id,
        projectRoot,
        platform,
      });

      const promptDir = path.join(promptsDir, `${tier}-${fn.name}`);
      await fs.mkdir(promptDir, { recursive: true });

      // Write prompt.md
      await fs.writeFile(path.join(promptDir, 'prompt.md'), prompt, 'utf-8');

      // Resolve targetObjectPath
      let targetObjectPath: string | null = null;
      if (symbolMap) {
        targetObjectPath = await resolveObjectPath(fn.name, projectRoot, symbolMap);
      }

      // Write settings.yaml
      const settingsLines = [`functionName: ${fn.name}`];
      if (targetObjectPath) {
        settingsLines.push(`targetObjectPath: ${targetObjectPath}`);
      }
      settingsLines.push(
        `asm: |`,
        ...stripTrailingAsmLines(fn.asmCode)
          .split('\n')
          .map((line) => `  ${line}`),
      );
      await fs.writeFile(path.join(promptDir, 'settings.yaml'), settingsLines.join('\n') + '\n', 'utf-8');

      console.log(`saved (score: ${score.toFixed(4)})`);
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
