/**
 * Generate prompts for specific functions by name.
 *
 * Usage:
 *   npx tsx scripts/generate-specific-prompts.ts -c path/to/config.yaml func1 func2 ...
 */
import fs from 'fs/promises';
import path from 'path';

import { isArmPlatform, loadConfig } from '~/shared/config.js';
import { parseMapFile } from '~/shared/map-file/map-file.js';
import { MizuchiDb, type MizuchiDbDump } from '~/shared/mizuchi-db/mizuchi-db.js';
import { stripTrailingAsmLines } from '~/shared/prompt-builder/craft-prompt.js';
import { createDecompilePrompt } from '~/shared/prompt-builder/prompt-builder.js';

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

function parseArgs(): { configPath: string; functionNames: string[] } {
  const args = process.argv.slice(2);
  const cIndex = args.indexOf('-c');
  let configPath = path.resolve('mizuchi.yaml');
  const functionNames: string[] = [];

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '-c') {
      configPath = path.resolve(args[++i]);
    } else {
      functionNames.push(args[i]);
    }
  }

  if (functionNames.length === 0) {
    console.error('Usage: npx tsx scripts/generate-specific-prompts.ts [-c config.yaml] func1 func2 ...');
    process.exit(1);
  }

  return { configPath, functionNames };
}

async function main() {
  const { configPath, functionNames } = parseArgs();
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

  // Load symbol map
  let symbolMap: Map<string, string> | null = null;
  if (mapFilePath) {
    console.log(`Parsing map file: ${mapFilePath}`);
    const mapContent = await fs.readFile(mapFilePath, 'utf-8');
    symbolMap = parseMapFile(mapContent);
    console.log(`Found ${symbolMap.size} symbols in map file`);
  }

  // Find functions by name
  console.log(`\nGenerating prompts for ${functionNames.length} functions...`);
  let savedCount = 0;
  let failedCount = 0;

  for (const name of functionNames) {
    process.stdout.write(`  ${name}... `);

    const fn = db.functions.find((f) => f.name === name);
    if (!fn) {
      console.log('FAILED: function not found in mizuchi-db');
      failedCount++;
      continue;
    }

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

      console.log('saved');
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
