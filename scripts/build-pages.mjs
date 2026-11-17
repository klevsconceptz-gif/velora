import { cp, mkdir, rm } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = resolve(root, 'deploy/cloudflare-pages/site');
const files = [
  'index.html',
  'styles.css',
  'manifest.webmanifest',
  'robots.txt',
  'sitemap.xml',
  '_headers',
  '_redirects',
  'assets',
  'src',
];

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
for (const path of files) {
  await cp(resolve(root, path), resolve(output, path), { recursive: true });
}
console.log(`Built Cloudflare Pages assets in ${output}`);
