import { createServer } from 'node:http'

function sse(response, chunks) {
  response.writeHead(200, {
    'content-type': 'text/event-stream',
    'cache-control': 'no-cache',
    connection: 'keep-alive',
  })
  for (const chunk of chunks) {
    response.write(`data: ${JSON.stringify(chunk)}\n\n`)
  }
  response.end('data: [DONE]\n\n')
}

export async function startOpenAiStub({ hang = false, textOnly = false } = {}) {
  const requests = []
  const sockets = new Set()
  const server = createServer(async (request, response) => {
    if (request.method !== 'POST' || request.url !== '/v1/chat/completions') {
      response.writeHead(404).end()
      return
    }

    const body = await new Promise((resolve, reject) => {
      const parts = []
      request.on('data', (part) => parts.push(part))
      request.on('end', () => {
        try {
          resolve(JSON.parse(Buffer.concat(parts).toString('utf8')))
        } catch (error) {
          reject(error)
        }
      })
      request.on('error', reject)
    })
    requests.push(body)

    if (hang) {
      return
    }

    const model = body.model
    if (textOnly) {
      sse(response, [
        {
          id: 'chatcmpl-h1-headless',
          object: 'chat.completion.chunk',
          created: 1,
          model,
          choices: [
            {
              index: 0,
              delta: { role: 'assistant', content: 'headless-ready' },
              finish_reason: null,
            },
          ],
        },
        {
          id: 'chatcmpl-h1-headless',
          object: 'chat.completion.chunk',
          created: 1,
          model,
          choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
          usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12 },
        },
      ])
      return
    }

    const toolResultSeen = body.messages.some((message) => message.role === 'tool')
    if (!toolResultSeen) {
      const toolName = body.tools?.[0]?.function?.name
      if (!toolName) {
        response.writeHead(400).end('missing tool schema')
        return
      }
      sse(response, [
        {
          id: 'chatcmpl-h1-tool',
          object: 'chat.completion.chunk',
          created: 1,
          model,
          choices: [
            {
              index: 0,
              delta: {
                role: 'assistant',
                tool_calls: [
                  {
                    index: 0,
                    id: 'call_h1_status',
                    type: 'function',
                    function: {
                      name: toolName,
                      arguments: '{"request_id":"REQ-1001"}',
                    },
                  },
                ],
              },
              finish_reason: null,
            },
          ],
        },
        {
          id: 'chatcmpl-h1-tool',
          object: 'chat.completion.chunk',
          created: 1,
          model,
          choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }],
          usage: { prompt_tokens: 20, completion_tokens: 8, total_tokens: 28 },
        },
      ])
      return
    }

    sse(response, [
      {
        id: 'chatcmpl-h1-final',
        object: 'chat.completion.chunk',
        created: 2,
        model,
        choices: [
          {
            index: 0,
            delta: { role: 'assistant', content: 'REQ-1001 is approved.' },
            finish_reason: null,
          },
        ],
      },
      {
        id: 'chatcmpl-h1-final',
        object: 'chat.completion.chunk',
        created: 2,
        model,
        choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
        usage: { prompt_tokens: 32, completion_tokens: 6, total_tokens: 38 },
      },
    ])
  })
  server.on('connection', (socket) => {
    sockets.add(socket)
    socket.on('close', () => sockets.delete(socket))
  })

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  return {
    baseUrl: `http://127.0.0.1:${address.port}/v1`,
    requests,
    close: () => new Promise((resolve, reject) => {
      for (const socket of sockets) socket.destroy()
      server.close((error) => error ? reject(error) : resolve())
    }),
  }
}
