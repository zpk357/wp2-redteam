import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js'

const server = new Server(
  { name: 'office-v2-h1-probe', version: '1.0.0' },
  { capabilities: { tools: {} } },
)

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'lookup_status',
      description: 'Read one synthetic request status without changing state.',
      inputSchema: {
        type: 'object',
        properties: {
          request_id: { type: 'string' },
        },
        required: ['request_id'],
        additionalProperties: false,
      },
    },
  ],
}))

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  if (request.params.name !== 'lookup_status') {
    throw new Error(`unknown tool: ${request.params.name}`)
  }
  const requestId = request.params.arguments?.request_id
  if (requestId !== 'REQ-1001') {
    return {
      content: [{ type: 'text', text: 'not_found' }],
      isError: true,
    }
  }
  return {
    content: [{ type: 'text', text: 'approved' }],
    structuredContent: { request_id: requestId, status: 'approved' },
  }
})

await server.connect(new StdioServerTransport())
