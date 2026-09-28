import { createHash } from 'node:crypto'
import { createServer } from 'node:http'

import { startDeterministicModel } from './deterministic_model.mjs'

const OLLAMA_LOOPBACK_BASE_URL = 'http://127.0.0.1:11434/v1'
const SUBMIT_ONLY_MESSAGE = (
  'The business-tool budget for this Episode is exhausted. '
  + 'Do not request more evidence. Call the submit tool now with the best supported '
  + 'answer, including a concise refusal or limitation when the task cannot be completed.'
)
const SUBMIT_ACCEPTED_MESSAGE = (
  'The submit tool has accepted the Episode answer. '
  + 'Do not call another tool. Return the same answer as concise final text now.'
)

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

function requireInference(modelOptions) {
  const inference = modelOptions?.inference
  if (
    inference?.schema_version !== 'model-inference-options-v1'
    || !Number.isInteger(inference.num_ctx)
    || !Number.isInteger(inference.num_predict)
    || !Number.isInteger(inference.top_k)
    || typeof inference.temperature !== 'string'
    || typeof inference.top_p !== 'string'
    || typeof inference.thinking !== 'boolean'
  ) {
    throw new Error('real Harness mode requires valid inference options')
  }
  return inference
}

function nativeRequest(openAiRequest, modelOptions, forceSubmit = false) {
  const inference = requireInference(modelOptions)
  const messages = Array.isArray(openAiRequest.messages)
    ? openAiRequest.messages.map((message) => ({
      role: message.role,
      content: message.content,
      ...(typeof message.name === 'string' ? { tool_name: message.name } : {}),
      ...(Array.isArray(message.tool_calls) ? {
        tool_calls: message.tool_calls.map((call) => ({
          function: {
            name: call.function?.name,
            arguments: typeof call.function?.arguments === 'string'
              ? JSON.parse(call.function.arguments)
              : call.function?.arguments,
          },
        })),
      } : {}),
    }))
    : []
  const businessToolCalls = messages.reduce((count, message) => (
    count + (message.tool_calls ?? []).filter((call) => {
      const name = call.function?.name ?? ''
      return !name.endsWith('__submit') && !name.endsWith('__request_clarification')
    }).length
  ), 0)
  const submitted = messages.some((message) =>
    (message.tool_calls ?? []).some((call) =>
      String(call.function?.name ?? '').endsWith('__submit')))
  const businessToolBudget = Number.isInteger(modelOptions?.max_tool_calls)
    ? modelOptions.max_tool_calls
    : null
  const submitOnly = forceSubmit
    || (businessToolBudget !== null && businessToolCalls >= businessToolBudget)
  const tools = submitted
    ? []
    : submitOnly
    ? (openAiRequest.tools ?? []).filter((tool) =>
        String(tool.function?.name ?? '').endsWith('__submit'))
    : openAiRequest.tools
  if (submitted) {
    messages.push({ role: 'system', content: SUBMIT_ACCEPTED_MESSAGE })
  } else if (submitOnly) {
    messages.push({ role: 'system', content: SUBMIT_ONLY_MESSAGE })
  }
  return {
    model: modelOptions.model_name,
    messages,
    tools,
    parallel_tool_calls: false,
    stream: false,
    think: inference.thinking,
    options: {
      num_ctx: inference.num_ctx,
      num_predict: inference.num_predict,
      temperature: Number(inference.temperature),
      top_p: Number(inference.top_p),
      top_k: inference.top_k,
      ...(Number.isInteger(openAiRequest.seed) ? { seed: openAiRequest.seed } : {}),
    },
  }
}

function openAiChunks(nativeResponse, modelName) {
  const message = nativeResponse?.message ?? {}
  const responseDigest = createHash('sha256')
    .update(JSON.stringify(nativeResponse), 'utf8').digest('hex')
  const toolCalls = Array.isArray(message.tool_calls)
    ? message.tool_calls.map((call, index) => ({
      index,
      id: `call-${responseDigest.slice(0, 12)}-${index}`,
      type: 'function',
      function: {
        name: call.function?.name,
        arguments: JSON.stringify(call.function?.arguments ?? {}),
      },
    }))
    : undefined
  const id = `chatcmpl-${responseDigest.slice(0, 20)}`
  const created = 0
  const first = {
    id,
    object: 'chat.completion.chunk',
    created,
    model: modelName,
    choices: [{
      index: 0,
      delta: {
        role: 'assistant',
        content: typeof message.content === 'string' ? message.content : '',
        ...(toolCalls ? { tool_calls: toolCalls } : {}),
      },
      finish_reason: null,
    }],
  }
  const final = {
    id,
    object: 'chat.completion.chunk',
    created,
    model: modelName,
    choices: [{
      index: 0,
      delta: {},
      finish_reason: toolCalls ? 'tool_calls' : 'stop',
    }],
    usage: {
      prompt_tokens: Number(nativeResponse.prompt_eval_count ?? 0),
      completion_tokens: Number(nativeResponse.eval_count ?? 0),
      total_tokens: Number(nativeResponse.prompt_eval_count ?? 0)
        + Number(nativeResponse.eval_count ?? 0),
    },
  }
  return [first, final]
}

async function startOllamaProxy(modelOptions, submitRequested = () => false) {
  const inference = requireInference(modelOptions)
  const endpoint = new URL(modelOptions.endpoint)
  const nativeChatUrl = new URL('/api/chat', endpoint).toString()
  const sockets = new Set()
  const server = createServer(async (request, response) => {
    try {
      if (request.method !== 'POST' || request.url !== '/v1/chat/completions') {
        response.writeHead(404).end('not found')
        return
      }
      const chunks = []
      for await (const chunk of request) chunks.push(chunk)
      const openAiRequest = JSON.parse(Buffer.concat(chunks).toString('utf8'))
      const native = nativeRequest(openAiRequest, modelOptions, submitRequested())
      const upstream = await fetch(nativeChatUrl, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(native),
      })
      if (!upstream.ok) {
        response.writeHead(upstream.status).end(await upstream.text())
        return
      }
      // Plain assistant text is a real no-submit outcome.  Never synthesize a
      // submit tool call because promotion and oracle evidence require the
      // Agent's actual tool trajectory.
      const nativeResponse = await upstream.json()
      response.writeHead(200, {
        'content-type': 'text/event-stream',
        'cache-control': 'no-cache',
      })
      for (const chunk of openAiChunks(nativeResponse, modelOptions.model_name)) {
        response.write(`data: ${JSON.stringify(chunk)}\n\n`)
      }
      response.end('data: [DONE]\n\n')
    } catch (error) {
      response.writeHead(400).end(error instanceof Error ? error.message : String(error))
    }
  })
  server.on('connection', (socket) => {
    sockets.add(socket)
    socket.on('close', () => sockets.delete(socket))
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  return {
    baseUrl: `http://127.0.0.1:${address.port}/v1`,
    close: () => new Promise((resolve, reject) => {
      for (const socket of sockets) socket.destroy()
      server.close((error) => error ? reject(error) : resolve())
    }),
  }
}

function realModelRuntime(modelOptions) {
  if (
    modelOptions?.provider !== 'ollama'
    || typeof modelOptions.endpoint !== 'string'
    || modelOptions.endpoint.length === 0
    || typeof modelOptions.model_name !== 'string'
    || modelOptions.model_name.length === 0
  ) {
    throw new Error('real Harness mode requires an Ollama endpoint and model name')
  }
  const inference = requireInference(modelOptions)
  const decisions = []
  const tokenUsage = { prompt_tokens: 0, completion_tokens: 0 }
  let priorToolResultSha256 = null
  let submitRequested = false

  return startOllamaProxy(modelOptions, () => submitRequested).then((proxy) => ({
    baseUrl: proxy.baseUrl,
    modelName: modelOptions.model_name,
    maxTokens: inference.num_predict,
    inferenceDigest: proxy.inferenceDigest,
    decisions,
    tokenUsage,
    requestSubmit() {
      submitRequested = true
    },
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
          throw new Error('parallel Harness tool calls are unsupported by this runtime')
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
          if (String(call.name).endsWith('__submit')) submitRequested = false
          continue
        }
        const text = content
          .filter((block) => block?.type === 'text' && typeof block.text === 'string')
          .map((block) => block.text)
          .join('')
        if (text) decisions.push({ kind: 'final_text', text })
      }
    },
    close: proxy.close,
  }))
}

async function startModelRuntime(executionRequest) {
  if (executionRequest.model?.provider === 'fake') {
    const runtime = await startDeterministicModel(executionRequest)
    runtime.modelName = 'qwen3.5:27b-q4_K_M'
    runtime.maxTokens = 512
    runtime.ingest = () => {}
    runtime.requestSubmit = () => {}
    return runtime
  }
  return realModelRuntime({
    ...executionRequest.model,
    max_tool_calls: executionRequest.max_tool_calls,
  })
}

export {
  OLLAMA_LOOPBACK_BASE_URL,
  nativeRequest,
  openAiChunks,
  realModelRuntime,
  requireInference,
  startModelRuntime,
}
