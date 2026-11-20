import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'

// Tests never reach the network. Mock fetch per test where a response is needed.
const blockedFetch = () => Promise.reject(new Error('network access is disabled in tests'))
globalThis.fetch = blockedFetch

class ResizeObserverStub {
  observe() {
    return undefined
  }
  unobserve() {
    return undefined
  }
  disconnect() {
    return undefined
  }
}

// jsdom has no ResizeObserver.
globalThis.ResizeObserver = ResizeObserverStub

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  globalThis.fetch = blockedFetch
  window.localStorage.clear()
  delete document.documentElement.dataset.theme
})
