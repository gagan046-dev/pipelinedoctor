# PipelineDoctor Console — Design Brief

The console must **not** look AI-generated or template-made. It should look like a tool with its own point of view.

## Concept: "the accident investigation board"

PipelineDoctor investigates *failures*. The console borrows its visual language from **transport-safety accident
investigation reports** (the kind published by air and rail safety boards) and from **engineering lab notebooks**,
not from SaaS dashboards. Every failed pipeline run is treated as an "occurrence", and every diagnosis is a short report.

- **Occurrence register (home page):** a dense, ruled table that reads like a docket. It has an occurrence number
  (e.g. `PD-2026-0042`), date/time in monospace, repository, workflow → job → failed step, a **category stamp**, a
  confidence grade, and the status (diagnosed / pending / skipped). Rows have hairline separators, no cards, and zebra striping only if subtle.
- **Report view:** typeset like a formal investigation report:
  `1. Synopsis` · `2. Sequence of events` (the failed step's timeline) · `3. Evidence` (cited log excerpts with
  line numbers, in a monospace "exhibit" box labelled `EXHIBIT A`, `EXHIBIT B`…) · `4. Probable cause` (in a ruled box) ·
  `5. Recommended actions` (numbered `R1`, `R2`…) · `6. Verification`. A small footer gives the model, prompt version, cost
  and latency, like a report's publication data.
- **Category stamps:** each failure category (dependency, test, lint, build, docker, config, secret/permission, flaky,
  infrastructure) has a rubber-stamp style label: uppercase, letter-spaced, a thin double border, slightly
  rotated (−1.5°), in one ink color per category. These are the only "decorative" elements, and they carry meaning.
- **Eval scoreboard page:** history of eval runs as a **ledger** (date, commit SHA, accuracy, judge score, cost, latency,
  pass/fail against the threshold), plus one simple line chart of accuracy over commits with the threshold drawn as a dashed rule.
- **Pipeline health strip:** DORA-style numbers (deploy frequency, lead time, change-failure rate, time to restore) set in
  large tabular figures with small-caps labels, like a report's summary table. No up/down arrows or "stat cards".

## Visual language

- **Light-first "paper" theme:** warm off-white paper (`#f6f3ec`-ish), near-black ink, one accent ink
  (signal red `#b3261e`-ish) for failures and "probable cause". A **dark "night shift" theme** too (`prefers-color-scheme` + toggle):
  dark graphite, paper-colored text, the same ink hues lightened for contrast.
- **Typography:** a serif for headings and report body (e.g. Source Serif 4 or IBM Plex Serif), a monospace for occurrence numbers,
  SHAs, timestamps and log exhibits (e.g. IBM Plex Mono or JetBrains Mono), and small caps for labels. **Self-host fonts** via
  `@fontsource` packages, with no external font requests.
- Layout: a narrow, readable report column (~72ch) for reports, and a full-width ruled table for the register. Hairline rules, 0–2px radius,
  no drop shadows, no floating cards.
- Motion: none except functional (a subtle progress rule while a diagnosis is pending).

## Explicitly avoid (these make it look AI-generated)

- Purple/indigo/violet gradients, gradient text, glassmorphism, blurred blobs, neon glow.
- Hero headers ("Welcome back 👋"), marketing copy, emoji as icons.
- Rounded white cards with big shadows in a uniform grid; pill badges everywhere.
- The default Inter/Roboto/system-font look; Tailwind-default palette (slate/indigo/emerald-500); lucide/heroicons dropped in everywhere.
  If an icon is needed, draw a small custom inline SVG.
- "Stat cards" with up/down percentage arrows; a sidebar nav with many items; fake data or features that don't exist.

## Craft details

- Occurrence numbers and SHAs are copyable (click to copy, with a small "copied" note).
- Empty states read like a docket: "No occurrences recorded. All pipelines nominal." (no illustrations).
- Keyboard accessible, visible focus rings, WCAG AA contrast in both themes, works at 400px width.
- All log text and LLM output are rendered with `textContent` only.
