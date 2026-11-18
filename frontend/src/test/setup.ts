// Runs before every test file: Testing Library's matchers, a clean page after each test, and the few
// browser features jsdom does not have but the page uses (scroll-in animations, image previews).
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { MotionGlobalConfig } from 'framer-motion';
import { afterEach, vi } from 'vitest';

afterEach(() => cleanup());

// Animations jump to their end, so counters show their final number at once.
MotionGlobalConfig.skipAnimations = true;

// jsdom does not scroll: report every observed element as on screen right away.
class OnScreenObserver {
  readonly root = null;
  readonly rootMargin = '';
  readonly thresholds = [];
  constructor(private readonly callback: IntersectionObserverCallback) {}
  observe(target: Element) {
    const entry = { target, isIntersecting: true, intersectionRatio: 1 } as unknown as IntersectionObserverEntry;
    this.callback([entry], this as unknown as IntersectionObserver);
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

Object.assign(globalThis, { IntersectionObserver: OnScreenObserver });
window.matchMedia ??= vi.fn().mockImplementation((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false
}));
URL.createObjectURL ??= () => 'blob:test';
URL.revokeObjectURL ??= () => {};
Element.prototype.scrollIntoView ??= () => {};
