import { createHash } from 'node:crypto'
import { existsSync } from 'node:fs'
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { DeepSeekHarness } from '@deepseek-ai/dsh-sdk-client'

import { startModelRuntime } from './model_runtime.mjs'

const DRIVER_SCHEMA = 'deepseek-harness-h4-driver-v1'
const REQUEST_SCHEMA = 'deepseek-harness-h4-request-v1'
const FOLLOWUP_SCHEMA = 'deepseek-harness-h4-followup-v1'
const ACK_SCHEMA = 'deepseek-harness-h4-followup-ack-v1'
const MAX_REQUEST_BYTES = 16 * 1024 * 1024

let sequence = 0
function emit(executionId, eventType, data = {}) {
  process.stdout.write(`${JSON.stringify({
    schema_version: DRIVER_SCHEMA,
    execution_id: executionId,
    sequence: sequence++,
    event_type: eventType,
    data,
  })}\n`)
}

function sha256Text(value) {
  return `sha256:${createHash('sha256').update(value, 'utf8').digest('hex')}`
}

async function readRequest() {
  const parts = []
  let size = 0
  for await (const part of process.stdin) {
    size += part.length
    if (size > MAX_REQUEST_BYTES) throw new Error('driver request exceeds size limit')
    parts.push(part)
  }
  const value = JSON.parse(Buffer.concat(parts).toString('utf8'))
  if (value?.schema_version !== REQUEST_SCHEMA) {
    throw new Error('unsupported driver request schema')
  }
  if (!value.execution_request?.execution_id || !value.episode_dir) {
    throw new Error('driver request is incomplete')
  }
  return value
}

async function followups(path) {
  const payload = await readFile(path, 'utf8')
  if (!payload.trim()) return []
  return payload.trimEnd().split('\n').map((line) => JSON.parse(line))
}

let executionId = 'unknown-execution'
let harness
let model
let stopping = false
let cancelTimer
let progressPath
let latestActivityCount = 0

async function writeProgress(status, activityCount = latestActivityCount) {
  if (!progressPath || !model) return
  const payload = {
    schema_version: 'deepseek-harness-h5-progress-v1',
    execution_id: executionId,
    status,
    activity_count: activityCount,
    decision_count: model.decisions.length,
    token_usage: model.tokenUsage,
  }
  const temporary = `${progressPath}.partial`
  await writeFile(temporary, JSON.stringify(payload), 'utf8')
  await rename(temporary, progressPath)
}

async function stopForSignal() {
  if (stopping) return
  stopping = true
  await writeProgress('cancelled').catch(() => {})
  if (harness) await harness.close().catch(() => {})
  if (model) await model.close().catch(() => {})
  process.exit(130)
}

for (const signalName of ['SIGTERM', 'SIGINT', 'SIGBREAK']) {
  try {
    process.on(signalName, () => void stopForSignal())
  } catch {
    // The signal is not available on this host.
  }
}

try {
  const input = await readRequest()
  executionId = input.execution_request.execution_id
  const runtimeRoot = dirname(fileURLToPath(import.meta.url))
  const variantRoot = resolve(runtimeRoot, '..')
  const episodeDir = resolve(input.episode_dir)
  const sessionRoot = resolve(episodeDir, 'sessions')
  const cancelPath = resolve(episodeDir, 'cancel.requested')
  const followupsPath = resolve(episodeDir, 'bridge-followups.ndjson')
  const ackPath = resolve(episodeDir, 'bridge-followup-ack.json')
  const summaryPath = resolve(episodeDir, 'bridge-summary.json')
  progressPath = resolve(episodeDir, 'driver-progress.json')
  await mkdir(sessionRoot, { recursive: true })
  cancelTimer = setInterval(() => {
    if (existsSync(cancelPath)) void stopForSignal()
  }, 50)

  model = await startModelRuntime(input.execution_request)
  await writeProgress('running')
  harness = new DeepSeekHarness({
    launch: {
      command: process.execPath,
      args: [
        resolve(variantRoot, 'node_modules/@deepseek-ai/dsh-sdk-jsonrpc-demo/lib/bin.js'),
        resolve(variantRoot, 'office_v2.cordis.yml'),
      ],
      cwd: variantRoot,
      env: {
        ...process.env,
        DSH_MODEL_BASE_URL: model.baseUrl,
        DSH_PROBE_API_KEY: 'synthetic-h4-key',
        DSH_SESSION_ROOT: sessionRoot,
        DSH_VARIANT_ROOT: variantRoot,
        DSH_RUNTIME_ROOT: process.env.DSH_PYTHON_RUNTIME_ROOT,
        DSH_PYTHON_EXECUTABLE: input.python_executable,
        DSH_OFFICE_BRIDGE: resolve(runtimeRoot, 'office_bridge.py'),
        DSH_H4_REQUEST_PATH: resolve(episodeDir, 'request.json'),
        DSH_H4_BOOTSTRAP_PATH: resolve(episodeDir, 'bridge-bootstrap.json'),
        DSH_H4_RECORDS_PATH: resolve(episodeDir, 'bridge-records.ndjson'),
        DSH_H4_SUMMARY_PATH: summaryPath,
        DSH_H4_FOLLOWUPS_PATH: followupsPath,
        DSH_H4_FOLLOWUP_ACK_PATH: ackPath,
        DSH_H4_TRACE_PATH: resolve(episodeDir, 'bridge-trace.json'),
        DSH_H4_RECORDING_STATE_PATH: resolve(episodeDir, 'bridge-recording-state.json'),
        DSH_H4_ORACLE_PATH: resolve(episodeDir, 'bridge-oracle.json'),
      },
      requestTimeoutMs: input.timeout_ms,
      shutdownTimeoutMs: 3000,
      disposeEofGraceMs: 1000,
      disposeGraceMs: 2000,
    },
    cwd: variantRoot,
    provider: 'office-local',
    model: model.modelName,
    maxTokens: 512,
  })

  emit(executionId, 'driver_started', { driver_pid: process.pid })
  const sessionId = `h4-${executionId.replaceAll(/[^a-zA-Z0-9_-]/g, '-')}`
  let prompt = input.execution_request.prompt
  let activityCount = 0
  let injectedCount = 0
  let decisionCursor = 0
  let finalResponse = null

  while (activityCount <= input.execution_request.max_steps) {
    const result = await harness.run(prompt, { sessionId })
    model.ingest(result.events)
    finalResponse = result.finalResponse
    for (; decisionCursor < model.decisions.length; decisionCursor += 1) {
      emit(executionId, 'model_decision', {
        request_index: decisionCursor,
        ...model.decisions[decisionCursor],
      })
    }
    emit(executionId, 'harness_activity', {
      activity_index: activityCount,
      runtime_pid: harness.client.child?.pid ?? null,
      session_id: result.sessionId,
      final_response: result.finalResponse,
      event_types: result.events.map((event) => event.type),
    })
    latestActivityCount = activityCount + 1
    await writeProgress('running')

    const pending = await followups(followupsPath)
    if (pending.length === injectedCount) break
    if (pending.length !== injectedCount + 1) {
      throw new Error('trusted followup queue is not contiguous')
    }
    const followup = pending[injectedCount]
    if (
      followup?.schema_version !== FOLLOWUP_SCHEMA
      || followup.execution_id !== executionId
      || followup.followup_index !== injectedCount
      || sha256Text(followup.user_message) !== followup.user_message_digest
    ) {
      throw new Error('trusted followup identity is invalid')
    }
    const ack = {
      schema_version: ACK_SCHEMA,
      execution_id: executionId,
      record_sequence: followup.record_sequence,
      followup_index: followup.followup_index,
      user_message_digest: followup.user_message_digest,
      directive_digest: followup.directive_digest,
    }
    await writeFile(ackPath, JSON.stringify(ack), { encoding: 'utf8', flag: 'wx' })
    emit(executionId, 'trusted_followup', {
      after_activity_index: activityCount,
      record_sequence: followup.record_sequence,
      followup_index: followup.followup_index,
      user_message_digest: followup.user_message_digest,
      directive_digest: followup.directive_digest,
    })
    prompt = followup.user_message
    injectedCount += 1
    activityCount += 1
  }
  if (activityCount > input.execution_request.max_steps) {
    throw new Error('trusted followup activity budget exhausted')
  }

  await harness.close()
  harness = null
  const summary = JSON.parse(await readFile(summaryPath, 'utf8'))
  if (!summary.complete) {
    const recordLines = (await readFile(resolve(episodeDir, 'bridge-records.ndjson'), 'utf8'))
      .trimEnd().split('\n').filter(Boolean)
    const lastRecord = recordLines.length ? JSON.parse(recordLines.at(-1)) : null
    throw new Error(
      `bridge did not publish one complete submitted episode: ${JSON.stringify({
        reason: summary.reason,
        submitted: summary.submitted,
        record_count: summary.record_count,
        followup_count: summary.followup_count,
        final_answer: summary.final_answer,
        last_record_kind: lastRecord?.kind,
        last_visible_result: lastRecord?.visible_result,
      })}`,
    )
  }
  emit(executionId, 'driver_finished', {
    // The SDK activity response is not causally bound to submit. The bridge
    // value is the validated Office execution fact used by TRACE and Oracle.
    final_response: summary.final_answer,
    decision_count: model.decisions.length,
    activity_count: activityCount + 1,
    token_usage: model.tokenUsage,
  })
  await writeProgress('completed', activityCount + 1)
} catch (error) {
  await writeProgress('failed').catch(() => {})
  emit(executionId, 'driver_failed', {
    error_class: error instanceof Error ? error.name : 'Error',
  })
  process.stderr.write(
    `${error instanceof Error ? error.stack ?? error.message : String(error)}\n`,
  )
  if (Array.isArray(harness?.client?.stderrTail)) {
    process.stderr.write(`${harness.client.stderrTail.slice(-40).join('\n')}\n`)
  }
  process.exitCode = 1
} finally {
  if (cancelTimer) clearInterval(cancelTimer)
  if (!stopping) {
    if (harness) await harness.close().catch(() => {})
    if (model) await model.close().catch(() => {})
  }
}
