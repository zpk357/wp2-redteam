import { spawn } from 'node:child_process'

const python = process.env.DSH_PYTHON_EXECUTABLE
const bridge = process.env.DSH_OFFICE_BRIDGE
if (!python || !bridge) {
  process.stderr.write('Office V2 bridge launch configuration is incomplete.\n')
  process.exit(2)
}

const child = spawn(python, [bridge], {
  cwd: process.env.DSH_RUNTIME_ROOT,
  env: process.env,
  stdio: ['inherit', 'inherit', 'inherit'],
})

for (const name of ['SIGTERM', 'SIGINT']) {
  process.on(name, () => {
    if (child.exitCode === null) child.kill(name)
  })
}

child.once('error', () => process.exit(1))
child.once('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal)
  else process.exit(code ?? 1)
})
