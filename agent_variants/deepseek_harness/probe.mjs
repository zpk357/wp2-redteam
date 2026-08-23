import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { DeepSeekHarness } from '@deepseek-ai/dsh-sdk-client'

import { startOpenAiStub } from './fixtures/openai_stub.mjs'

const variantRoot = dirname(fileURLToPath(import.meta.url))
const sessionRoot = await mkdtemp(join(tmpdir(), 'wp2-dsh-h1-'))
const model = await startOpenAiStub()
const notifications = []

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
    requestTimeoutMs: 15000,
    shutdownTimeoutMs: 3000,
  },
  cwd: variantRoot,
  provider: 'office-local',
  model: 'qwen3.5:27b-q4_K_M',
  maxTokens: 512,
})

try {
  const result = await harness.run(
    'Read the status of synthetic request REQ-1001 and report it.',
    {
      sessionId: 'h1-runtime-probe',
      onNotification: (notification) => notifications.push(notification),
    },
  )

  const eventTypes = result.events.map((event) => event.type)
  const requestHeader = result.events.find((event) => event.type === 'request/header')
  const toolNames = requestHeader?.data?.header?.tools?.map((tool) => tool.name)
  if (result.finalResponse !== 'REQ-1001 is approved.') {
    process.stderr.write(`${JSON.stringify({
      final_response: result.finalResponse,
      model_requests: model.requests.length,
      event_types: eventTypes,
      terminal_events: result.events.slice(-8),
    }, null, 2)}\n`)
  }
  assert.equal(result.finalResponse, 'REQ-1001 is approved.')
  assert.equal(model.requests.length, 2)
  assert.equal(eventTypes.filter((type) => type === 'request/header').length, 1)
  assert.equal(eventTypes.filter((type) => type === 'tool/call').length, 1)
  assert.equal(eventTypes.filter((type) => type === 'tool/result').length, 1)
  assert.equal(eventTypes.filter((type) => type === 'assistant/message').length, 2)
  assert.deepEqual(toolNames, ['mcp__office_probe__lookup_status'])

  const followup = await harness.run(
    'Confirm the same synthetic request status once more.',
    { sessionId: result.sessionId },
  )
  assert.equal(followup.sessionId, result.sessionId)
  assert.equal(followup.finalResponse, 'REQ-1001 is approved.')
  assert.equal(model.requests.length, 3)
  assert.equal(
    followup.events.filter((event) => event.type === 'assistant/message').length,
    1,
  )

  process.stdout.write(`${JSON.stringify({
    schema_version: 'deepseek-harness-h1-probe-v1',
    status: 'passed',
    session_id: result.sessionId,
    final_response: result.finalResponse,
    model_requests: model.requests.length,
    followup_completed: true,
    model_visible_tools: toolNames,
    event_counts: Object.fromEntries(
      [...new Set(eventTypes)].sort().map((type) => [
        type,
        eventTypes.filter((candidate) => candidate === type).length,
      ]),
    ),
    notification_count: notifications.length,
  }, null, 2)}\n`)
} finally {
  await harness.close()
  await model.close()
  await rm(sessionRoot, { recursive: true, force: true })
}
