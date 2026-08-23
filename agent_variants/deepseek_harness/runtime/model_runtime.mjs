import { createHash } from 'node:crypto'

import { startDeterministicModel } from './deterministic_model.mjs'

const OLLAMA_LOOPBACK_BASE_URL = 'http://127.0.0.1:11434/v1'

function sha256Text(value) {
  return `sha256:${createHash('sha256').update(value, 'utf8').digest('hex')}`
}

function toolResultText(message) {
  const content = Array.isArray(message?.content) ? message.content : []
  const result = content.find((block) => block?.type === 'tool-result')
  const blocks = Array.isArray(result?.content) ? result.content : []
  return blocks
    .filter((block) => block?.type === 'text' && typeof block.text === 'string')
    .map((block) => block.text)
    .join('')
}

function realModelRuntime(modelOptions) {
  if (
    modelOptions?.provider !== 'ollama'
    || modelOptions.endpoint !== 'http://127.0.0.1:11434'
    || typeof modelOptions.model_name !== 'string'
    || modelOptions.model_name.length === 0
  ) {
    throw new Error('real Harness mode requires the locked loopback Ollama identity')
  }
  const decisions = []
  const tokenUsage = { prompt_tokens: 0, completion_tokens: 0 }
  let priorToolResultSha256 = null

  return {
    baseUrl: OLLAMA_LOOPBACK_BASE_URL,
    modelName: modelOptions.model_name,
    decisions,
    tokenUsage,
    ingest(events) {
      for (const event of events) {
        if (event?.type === 'tool/result') {
          const text = toolResultText(event.data?.message)
          if (!text) throw new Error('Harness tool result has no model-visible text')
          priorToolResultSha256 = sha256Text(text)
          continue
        }
        if (event?.type !== 'assistant/message') continue
        const usage = event.data?.usage
        if (usage) {
          tokenUsage.prompt_tokens += Number(usage.inputTokens ?? 0)
          tokenUsage.completion_tokens += Number(usage.outputTokens ?? 0)
        }
        const content = Array.isArray(event.data?.message?.content)
          ? event.data.message.content
          : []
        const calls = content.filter((block) => block?.type === 'tool-call')
        if (calls.length > 1) {
          throw new Error('parallel Harness tool calls are outside the locked contract')
        }
        if (calls.length === 1) {
          const call = calls[0]
          let args
          try {
            args = JSON.parse(call.arguments)
          } catch (error) {
            throw new Error('Harness tool arguments are not valid JSON', { cause: error })
          }
          if (!args || Array.isArray(args) || typeof args !== 'object') {
            throw new Error('Harness tool arguments must be a JSON object')
          }
          decisions.push({
            kind: String(call.name).endsWith('__submit') ? 'submit' : 'tool_call',
            tool_name: call.name,
            arguments: args,
            prior_tool_result_sha256: priorToolResultSha256,
          })
          continue
        }
        const text = content
          .filter((block) => block?.type === 'text' && typeof block.text === 'string')
          .map((block) => block.text)
          .join('')
        if (text) decisions.push({ kind: 'final_text', text })
      }
    },
    async close() {},
  }
}

async function startModelRuntime(executionRequest) {
  if (executionRequest.model?.provider === 'fake') {
    const runtime = await startDeterministicModel(executionRequest)
    runtime.modelName = 'qwen3.5:27b-q4_K_M'
    runtime.ingest = () => {}
    return runtime
  }
  return realModelRuntime(executionRequest.model)
}

export { OLLAMA_LOOPBACK_BASE_URL, realModelRuntime, startModelRuntime }
