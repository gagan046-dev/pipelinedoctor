// Copies the self-hosted font files the console uses from the @fontsource npm packages into
// src/pipelinedoctor_console/static/fonts/. Runs at image build time (Dockerfile "fonts" stage)
// and locally via `npm ci && npm run fonts`. The browser then never contacts a font CDN.
import { copyFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = join(root, "src", "pipelinedoctor_console", "static", "fonts");

// Only the latin subset and the weights console.css declares in @font-face.
const fonts = {
  "source-serif-4": ["400-normal", "400-italic", "600-normal", "700-normal"],
  "ibm-plex-mono": ["400-normal", "500-normal", "600-normal"],
};

mkdirSync(outDir, { recursive: true });
let copied = 0;
for (const [family, variants] of Object.entries(fonts)) {
  const pkg = join(root, "node_modules", "@fontsource", family);
  if (!existsSync(pkg)) {
    console.error(`missing ${pkg}: run "npm ci" first`);
    process.exit(1);
  }
  for (const variant of variants) {
    const file = `${family}-latin-${variant}.woff2`;
    copyFileSync(join(pkg, "files", file), join(outDir, file));
    copied += 1;
  }
  // The SIL Open Font License requires the license to travel with the font files.
  copyFileSync(join(pkg, "LICENSE"), join(outDir, `${family}.LICENSE.txt`));
}
console.log(`copied ${copied} font files to ${outDir}`);
