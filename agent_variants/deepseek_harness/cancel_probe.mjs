import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { DeepSeekHarness } from '@deepseek-ai/dsh-sdk-client'

import { startOpenAiStub } from './fixtures/openai_stub.mjs'

const variantRoot = dirname(fileURLToPath(import.meta.url))
const sessionRoot = await mkdtemp(join(tmpdir(), 'wp2-dsh-h1-cancel-'))
const model = await startOpenAiStub({ hang: true })
const harness = new DeepSeekHarness({
  launch: {
    command: process.execPath,
    args: [
      join(variantRoot, 'node_modules/@deepseek-ai/dsh-sdk-jsonrpc-demo/lib/bin.js'),
      join(variantRoot, 'runtime.cordis.yml'),
    ],
    cwd: variantRoot,
    env: {
      ...process.env,
      DSH_MODEL_BASE_URL: model.baseUrl,
      DSH_PROBE_API_KEY: 'synthetic-h1-probe-key',
      DSH_SESSION_ROOT: sessionRoot,
      DSH_VARIANT_ROOT: variantRoot,
    },
    requestTimeoutMs: 30000,
    shutdownTimeoutMs: 1000,
    disposeEofGraceMs: 1000,
    disposeGraceMs: 1000,
  },
  cwd: variantRoot,
  provider: 'office-local',
  model: 'qwen3.5:27b-q4_K_M',
  maxTokens: 512,
})

try {
  const run = harness.run('Wait for the synthetic model response.', {
    sessionId: 'h1-cancel-probe',
  })
  const deadline = Date.now() + 5000
  while (model.requests.length === 0 && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  assert.equal(model.requests.length, 1)

  const close = harness.close()
  const rejected = await run.then(
    () => false,
    () => true,
  )
  await close
  assert.equal(rejected, true)

  process.stdout.write(`${JSON.stringify({
    schema_version: 'deepseek-harness-h1-cancel-probe-v1',
    status: 'passed',
    model_requests_before_cancel: model.requests.length,
    cancellation_boundary: 'runtime_process_close',
  }, null, 2)}\n`)
} finally {
  await harness.close()
  await model.close()
  await rm(sessionRoot, { recursive: true, force: true })
}
