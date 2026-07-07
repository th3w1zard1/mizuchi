/**
 * Report Generator
 *
 * Generates run reports in JSON and HTML formats.
 * The HTML report is a self-contained React application.
 */
import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';

import type { RunReport } from './types.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Find the report UI template by checking multiple possible locations
 */
async function findReportTemplate(): Promise<string> {
  // Possible locations for the report UI template:
  // 1. Relative to current file (for running from source with tsx)
  // 2. In src/ directory (for running from compiled dist/)
  const possiblePaths = [
    path.join(__dirname, '..', 'ui', 'run-report', 'dist', 'index.html'),
    path.join(__dirname, '..', '..', 'src', 'ui', 'run-report', 'dist', 'index.html'),
  ];

  for (const templatePath of possiblePaths) {
    try {
      await fs.access(templatePath);
      return templatePath;
    } catch {
      // Try next path
    }
  }

  throw new Error(
    `Report UI template not found. Searched paths:\n${possiblePaths.join('\n')}\nRun 'npm run build:run-report' first.`,
  );
}

/**
 * Save run report as JSON
 */
export async function saveJsonReport(report: RunReport, outputPath: string): Promise<void> {
  await fs.writeFile(outputPath, JSON.stringify(report, null, 2), 'utf-8');
}

/**
 * Generate HTML report
 */
export async function generateHtmlReport(report: RunReport, outputPath: string): Promise<void> {
  // Find the pre-built report template
  const templatePath = await findReportTemplate();

  const template = await fs.readFile(templatePath, 'utf-8');

  // Inject the report data using base64 encoding to avoid HTML parsing issues
  const jsonString = JSON.stringify(report);
  const base64Data = Buffer.from(jsonString).toString('base64');
  const dataScript = `<script>
  window.__RUN_REPORT__ = JSON.parse(atob('${base64Data}'));
</script>`;
  const html = template.replace('</head>', `${dataScript}</head>`);

  await fs.writeFile(outputPath, html, 'utf-8');
}

/**
 * Write content to a file atomically (write to temp file, then rename).
 * Ensures no corrupted files if the process crashes mid-write.
 */
async function atomicWriteFile(filePath: string, content: string): Promise<void> {
  const dir = path.dirname(filePath);
  const tmpPath = path.join(dir, `.tmp-${path.basename(filePath)}-${process.pid}`);
  await fs.writeFile(tmpPath, content, 'utf-8');
  await fs.rename(tmpPath, filePath);
}

/**
 * Save run report as JSON (atomic write)
 */
export async function saveJsonReportAtomic(report: RunReport, outputPath: string): Promise<void> {
  await atomicWriteFile(outputPath, JSON.stringify(report, null, 2));
}

/**
 * Generate HTML report (atomic write)
 */
export async function generateHtmlReportAtomic(report: RunReport, outputPath: string): Promise<void> {
  const templatePath = await findReportTemplate();
  const template = await fs.readFile(templatePath, 'utf-8');

  const jsonString = JSON.stringify(report);
  const base64Data = Buffer.from(jsonString).toString('base64');
  const dataScript = `<script>
  window.__RUN_REPORT__ = JSON.parse(atob('${base64Data}'));
</script>`;
  const html = template.replace('</head>', `${dataScript}</head>`);

  await atomicWriteFile(outputPath, html);
}

/**
 * Silently delete a file if it exists
 */
export async function deleteFileIfExists(filePath: string): Promise<void> {
  try {
    await fs.unlink(filePath);
  } catch {
    // File may not exist — ignore
  }
}

export type { RunReport, ReportSection, ReportPluginResult } from './types.js';
export { transformToReport, type ReportPluginConfigs } from './transform.js';
