import assert from 'node:assert/strict'

import {
  nativeRequest,
  openAiChunks,
  requireInference,
} from './model_runtime.mjs'

const driverSource = await import('node:fs/promises').then(({ readFile }) =>
  readFile(new URL('./driver.mjs', import.meta.url), 'utf8'))
const bridgeSource = await import('node:fs/promises').then(({ readFile }) =>
  readFile(new URL('./office_bridge.py', import.meta.url), 'utf8'))
assert.match(driverSource, /Continue the same task using the tool result/)
assert.match(driverSource, /activityDecisions\.at\(-1\)/)
assert.match(driverSource, /activityDecisions\.some\(\(decision\) => decision\.kind === 'submit'\)/)
assert.match(driverSource, /Call the submit tool now with the concise final answer/)
assert.match(driverSource, /BRIDGE_SUMMARY_WAIT_MS = 5000/)
assert.match(driverSource, /readBridgeSummary\(summaryPath\)/)
assert.match(driverSource, /'agent_no_submit'/)
assert.match(bridgeSource, /reason in \{"stdio_closed", "terminated"\}/)
assert.match(driverSource, /error_code: error\?\.code/)

const inference = {
  schema_version: 'model-inference-options-v1',
  num_ctx: 8192,
  num_predict: 4096,
  temperature: '0.2',
  top_p: '0.8',
  top_k: 20,
  thinking: true,
  source_config_digest: `sha256:${'a'.repeat(64)}`,
}
const model = {
  provider: 'ollama',
  endpoint: 'http://127.0.0.1:11434',
  model_name: 'qwen3.5:27b-q4_K_M',
  inference,
  max_tool_calls: 4,
}

assert.equal(requireInference(model), inference)
assert.throws(
  () => requireInference({ ...model, inference: null }),
  /valid inference options/,
)

const request = nativeRequest({
  messages: [
    {
      role: 'assistant',
      tool_calls: [{
        id: 'openai-call-id',
        type: 'function',
        function: { name: 'read_file', arguments: '{"id":"x"}' },
      }],
    },
  ],
  tools: [{ type: 'function', function: { name: 'read_file' } }],
  seed: 7,
}, model)
assert.deepEqual(request.options, {
  num_ctx: 8192,
  num_predict: 4096,
  temperature: 0.2,
  top_p: 0.8,
  top_k: 20,
  seed: 7,
})
assert.equal(request.parallel_tool_calls, false)
assert.equal(request.think, true)
assert.deepEqual(Object.keys(request.messages[0].tool_calls[0]), ['function'])
assert.deepEqual(request.messages[0].tool_calls[0].function.arguments, { id: 'x' })

const businessTool = {
  type: 'function',
  function: { name: 'mcp__office_v2__read_file', parameters: { type: 'object' } },
}
const submitTool = {
  type: 'function',
  function: { name: 'mcp__office_v2__submit', parameters: { type: 'object' } },
}
const exhausted = nativeRequest({
  messages: Array.from({ length: model.max_tool_calls }, (_, index) => ({
    role: 'assistant',
    tool_calls: [{
      id: `call-${index}`,
      type: 'function',
      function: { name: businessTool.function.name, arguments: '{}' },
    }],
  })),
  tools: [businessTool, submitTool],
}, model)
assert.deepEqual(exhausted.tools, [submitTool])
assert.match(exhausted.messages.at(-1).content, /Call the submit tool now/)

const forcedSubmit = nativeRequest({
  messages: [{ role: 'user', content: 'finish now' }],
  tools: [businessTool, submitTool],
}, model, true)
assert.deepEqual(forcedSubmit.tools, [submitTool])
assert.match(forcedSubmit.messages.at(-1).content, /Call the submit tool now/)

const submitted = nativeRequest({
  messages: [{
    role: 'assistant',
    tool_calls: [{
      id: 'submit-call',
      type: 'function',
      function: { name: submitTool.function.name, arguments: '{"answer":"done"}' },
    }],
  }],
  tools: [businessTool, submitTool],
}, model)
assert.deepEqual(submitted.tools, [])
assert.match(submitted.messages.at(-1).content, /Return the same answer as concise final text/)

const chunks = openAiChunks({
  message: { tool_calls: [{ function: { name: 'submit', arguments: { answer: 'done' } } }] },
  prompt_eval_count: 12,
  eval_count: 3,
}, model.model_name)
assert.equal(chunks[0].choices[0].delta.tool_calls[0].function.arguments, '{"answer":"done"}')
assert.equal(chunks[0].created, 0)
assert.match(chunks[0].choices[0].delta.tool_calls[0].id, /^call-[a-f0-9]{12}-0$/)
assert.equal(chunks[1].choices[0].finish_reason, 'tool_calls')
assert.deepEqual(chunks[1].usage, {
  prompt_tokens: 12,
  completion_tokens: 3,
  total_tokens: 15,
})
