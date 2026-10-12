// Vitest global setup (Phase 2, extended Phase 8.2).
//
// The suite runs with `globals: false`, so every file imports what it uses
// from "vitest" and DOM tests clean up after themselves (see the explicit
// `cleanup()` afterEach in the component tests).
//
// jsdom implements neither `matchMedia` nor `ResizeObserver`, both of which
// framer-motion probes while rendering a component. Stub them only when the
// real API is missing so a jsdom build that does provide them wins.

export {};

if (typeof window !== "undefined") {
  if (typeof window.matchMedia !== "function") {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }

  if (typeof (globalThis as { ResizeObserver?: unknown }).ResizeObserver !== "function") {
    class ResizeObserverStub {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
  }
}
