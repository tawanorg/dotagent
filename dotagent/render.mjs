// Verify actual GitHub-rendered PR HTML; host browser remains the final fallback.
import { createRequire } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const [url, manifest, output, packageName = '@playwright/test'] = process.argv.slice(2);
const require = createRequire(path.join(process.cwd(), 'package.json'));
const { chromium, expect } = require(packageName);
const attachments = JSON.parse(await readFile(manifest, 'utf8'));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const images = [];
let rendered = false, error = '', surface = 'github-pr-page';
try {
  const response = await page.goto(url, { waitUntil: 'domcontentloaded' });
  if (!response?.ok()) {
    const target = new URL(url);
    const match = target.pathname.match(/^\/([^/]+)\/([^/]+)\/pull\/(\d+)$/);
    if (target.hostname !== 'github.com' || !match) throw new Error('Unsupported private PR URL');
    const [, owner, repo, number] = match;
    const data = JSON.parse(execFileSync('gh', ['api', '-H', 'Accept: application/vnd.github.full+json',
      `repos/${owner}/${repo}/issues/${number}`], {encoding:'utf8',maxBuffer:8*1024*1024}));
    if (data.html_url !== url || !data.pull_request || !data.body_html) throw new Error('PR HTML read-back mismatch');
    for (const asset of Object.values(attachments))
      if (!data.body.includes(asset)) throw new Error('Recorded attachment is absent from the actual PR body');
    await page.goto('about:blank');
    await page.setContent(data.body_html);
    surface = 'github-api-rendered-pr-body';
  }
  for (const [hash, asset] of Object.entries(attachments)) {
    const image = page.getByAltText(`dotagent-${hash}`, { exact: true }).first();
    await image.scrollIntoViewIfNeeded();
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate(el => el.complete && el.naturalWidth > 0)).toBe(true);
    const info = await image.evaluate(el => ({ src: el.currentSrc, width: el.naturalWidth, height: el.naturalHeight }));
    const source = new URL(info.src), original = new URL(asset);
    const assetId = original.pathname.split('/').at(-1);
    const githubCDN = ['private-user-images.githubusercontent.com', 'user-images.githubusercontent.com'].includes(source.hostname);
    if (info.src !== asset && !(githubCDN && source.pathname.includes(assetId)))
      throw new Error(`Rendered source differs from recorded attachment for ${hash}`);
    // GitHub rewrites stable asset URLs to short-lived signed CDN URLs in the DOM.
    // Keep only the stable URL and dimensions in the receipt, never the signed query.
    images.push({ asset, sourceHost: source.hostname, width: info.width, height: info.height });
  }
  rendered = images.length > 0;
} catch (failure) {
  error = String(failure);
} finally {
  await browser.close();
  await writeFile(output, JSON.stringify({ rendered, surface, images, error }, null, 2), { mode: 0o600 });
}
if (!rendered) process.exitCode = 1;
