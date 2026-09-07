import "@testing-library/jest-dom/vitest";

// jsdom has no ResizeObserver, and the canvas stage uses one to fit the slide.
// A no-op is the correct stub: the tests assert on structure, and layout
// measurement in jsdom is fiction either way.
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver ??= NoopResizeObserver as unknown as typeof ResizeObserver;

// `scrollIntoView` is not implemented in jsdom; the filmstrip calls it to keep
// the selection visible.
Element.prototype.scrollIntoView ??= function scrollIntoView() {};
