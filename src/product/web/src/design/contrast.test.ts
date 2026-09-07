/**
 * Every colour that carries text has to be readable on every ground it is set on.
 *
 * The design language leans on colour more than most of this product does —
 * one attention colour meaning *this changed*, verdict colours reserved for
 * engine verdicts — and a token can be nudged for aesthetic reasons without
 * anyone noticing it has dropped under WCAG AA. Two had:
 *
 *   · `--color-ink-faint` at 3.44:1 on a raised row. That token carries every
 *     rationale line under a finding — the sentences the audit is most
 *     persuasive through, and the ones the demo is built to make people argue
 *     with.
 *   · `--color-blocked` at 4.33:1 on a raised row. The single most important
 *     sentence this product ever prints.
 *
 * Neither was visible by looking. Both were arithmetic.
 *
 * The ratios are computed here rather than read off a browser, because the
 * point is to fail in CI when someone edits `styles.css` — and jsdom resolves
 * no colours at all, so the conversion from `oklch()` is done in full.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// Read from the project root rather than from `import.meta.url`: under jsdom
// the module URL is not a file: URL, and this test is about the stylesheet on
// disk rather than about whatever a bundler decided to inline.
const CSS = readFileSync(join(process.cwd(), "src", "styles.css"), "utf-8");

/** `--name: oklch(L% C H);` → [L, C, H], from the `@theme` block. */
function token(name: string): [number, number, number] {
  const match = CSS.match(
    new RegExp(`${name}:\\s*oklch\\(\\s*([\\d.]+)%?\\s+([\\d.]+)\\s+([\\d.]+)`),
  );
  if (!match) throw new Error(`${name} is not declared as an oklch() triple`);
  const l = Number(match[1]);
  return [l > 1 ? l / 100 : l, Number(match[2]), Number(match[3])];
}

/** OKLCH → linear sRGB, per the Ottosson definition. */
function linearRGB([L, C, H]: [number, number, number]): [number, number, number] {
  const h = (H * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);

  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;

  const l = l_ ** 3;
  const m = m_ ** 3;
  const s = s_ ** 3;

  return [
    +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

/** Relative luminance, WCAG 2.x. Linear sRGB is already gamma-decoded. */
function luminance(name: string): number {
  const [r, g, b] = linearRGB(token(name)).map((v) => Math.min(Math.max(v, 0), 1));
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

function ratio(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/** Every surface a token's text is ever set on. */
const GROUNDS = ["--color-ground", "--color-panel", "--color-raised"];

const TEXT_TOKENS = [
  "--color-ink",
  "--color-ink-muted",
  "--color-ink-faint",
  "--color-changed",
  "--color-verified",
  "--color-blocked",
  "--color-review",
];

const AA_NORMAL = 4.5;

describe("text is readable wherever it is set", () => {
  it.each(TEXT_TOKENS)("%s clears AA on every ground", (name) => {
    for (const ground of GROUNDS) {
      const value = ratio(name, ground);
      expect(
        value,
        `${name} on ${ground} is ${value.toFixed(2)}:1, under ${AA_NORMAL}:1`,
      ).toBeGreaterThanOrEqual(AA_NORMAL);
    }
  });

  it("checks the darkest ground, not the most flattering one", () => {
    // A token measured only against the page background passes while being
    // unreadable on a raised row, which is exactly how both failures survived.
    expect(luminance("--color-raised")).toBeGreaterThan(luminance("--color-ground"));
  });
});

describe("the ink scale still reads as three steps", () => {
  it("keeps them in order and apart", () => {
    // Raising `ink-faint` for contrast compresses the scale toward `ink-muted`.
    // Three tones that are hard to tell apart are one tone with extra steps.
    const ink = luminance("--color-ink");
    const muted = luminance("--color-ink-muted");
    const faint = luminance("--color-ink-faint");
    expect(ink).toBeGreaterThan(muted);
    expect(muted).toBeGreaterThan(faint);
    expect(ratio("--color-ink-muted", "--color-ink-faint")).toBeGreaterThan(1.2);
  });
});
