// Run a task-authored interaction against the real local app, with independent diagnostics.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const [scenario, baseURL, artifactDir, packageName = '@playwright/test'] = process.argv.slice(2);
const require = createRequire(path.join(process.cwd(), 'package.json'));
const { chromium, expect } = require(packageName);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const consoleErrors = [], networkFailures = [], artifacts = [];
page.on('pageerror', error => consoleErrors.push(String(error)));
page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
page.on('requestfailed', request => networkFailures.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText}`));
page.on('response', response => {
  if (response.status() >= 400 && !response.url().endsWith('/favicon.ico'))
    networkFailures.push(`${response.status()} ${response.url()}`);
});
await mkdir(artifactDir, { recursive: true, mode: 0o700 });
let passed = false, error = null;
try {
  const test = (await import(pathToFileURL(scenario).href)).default;
  await test({ page, expect, baseURL, evidence: async (criterion, description) => {
    if (!/^[a-zA-Z0-9_-]+$/.test(criterion)) throw new Error('Invalid criterion ID');
    const file = path.join(artifactDir, `evidence-${criterion}-${artifacts.length}.png`);
    await page.screenshot({ path: file, fullPage: true });
    artifacts.push({ criterion, description, path: file });
  }});
  if (consoleErrors.length || networkFailures.length) throw new Error('Browser console/network failures');
  passed = true;
} catch (failure) {
  error = String(failure);
  await page.screenshot({ path: path.join(artifactDir, 'browser-failure.png'), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
  await writeFile(path.join(artifactDir, 'browser-result.json'), JSON.stringify({ passed, error, consoleErrors, networkFailures, artifacts }, null, 2), { mode: 0o600 });
}
if (!passed) { console.error(error); process.exitCode = 1; }
