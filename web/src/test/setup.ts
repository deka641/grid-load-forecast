import '@testing-library/jest-dom/vitest'

// Tests never reach the network. Mock fetch per test where a response is needed.
globalThis.fetch = () => Promise.reject(new Error('network access is disabled in tests'))
