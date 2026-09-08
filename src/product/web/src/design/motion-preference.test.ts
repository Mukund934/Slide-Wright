/**
 * A reader who has asked for less motion has to actually get it.
 *
 * `styles.css` has collapsed CSS animations and transitions under
 * `prefers-reduced-motion` since the first commit, and `07-design-language.md`
 * said so. That rule was true of the stylesheet and false of the application:
 * Motion animates by writing inline styles from JavaScript, so a CSS rule about
 * `transition-duration` never touched the panel entrances, the stagger, or the
 * changed-region halo — which is every animation the product actually has.
 *
 * Motion defaults to `reducedMotion: "never"`, meaning "ignore the preference".
 * Nothing opts out of that by accident; it has to be set.
 *
 * These are wiring assertions, and they say so rather than pretending to be
 * behavioural. Motion resolves the preference inside its own render path, so a
 * jsdom test of a `motion.div` would be testing Motion rather than this
 * application. What can go wrong here — and did — is the wiring being absent,
 * so that is what is checked, at the two places it lives.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const read = (...parts: string[]) =>
  readFileSync(join(process.cwd(), "src", ...parts), "utf-8");

describe("Motion is told about the preference", () => {
  const MAIN = read("main.tsx");

  it("wraps the app in a MotionConfig", () => {
    expect(MAIN).toMatch(/<MotionConfig\b/);
  });

  it("sets it to follow the user rather than the default", () => {
    // "never" is Motion's default and means the preference is ignored.
    // "always" would take motion away from readers who did not ask.
    expect(MAIN).toMatch(/reducedMotion=["']user["']/);
  });

  it("wraps App rather than sitting beside it", () => {
    expect(MAIN).toMatch(/<MotionConfig[^>]*>\s*<App\s*\/>\s*<\/MotionConfig>/);
  });
});

describe("the stylesheet still covers what CSS animates", () => {
  const CSS = read("styles.css");

  it("collapses CSS animation and transition under the preference", () => {
    const block = CSS.match(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\n\}/,
    )?.[0];
    expect(block, "no prefers-reduced-motion block at all").toBeTruthy();
    expect(block).toMatch(/animation-duration/);
    expect(block).toMatch(/transition-duration/);
  });

  it("does not disable scroll behaviour only", () => {
    // The narrow version of this rule -- `scroll-behavior: auto` and nothing
    // else -- looks like the preference is handled and covers almost nothing.
    const block = CSS.match(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\n\}/,
    )?.[0];
    expect(block!.split(";").length).toBeGreaterThan(3);
  });
});
