import { defineConfig, devices } from '@playwright/test'

// The e2e suite drives the BUILT app against the real engine with a scripted runtime
// (e2e/server.py) — no network, no provider quota. One worker: the specs share one
// workspace on disk, and the engine allows one active run per project by design.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:8799',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: {
    command: 'uv run python e2e/server.py',
    url: 'http://127.0.0.1:8799/api/projects',
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
})
