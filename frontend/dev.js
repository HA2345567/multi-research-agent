import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontend = dirname(fileURLToPath(import.meta.url))
const root = dirname(frontend)
const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const vite = join(frontend, 'node_modules/vite/bin/vite.js')
if (!existsSync(python) || !existsSync(vite)) {
  console.error('Use the existing Python .venv and run bun install in frontend first. See frontend/README.md.')
  process.exit(1)
}

let stopping = false
const bridge = spawn(python, ['-B', join(frontend, 'bridge.py')], { cwd: root, stdio: ['pipe', 'inherit', 'inherit'], windowsHide: true, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } })
const ui = spawn(process.execPath, [vite], { cwd: frontend, stdio: 'inherit', windowsHide: true })
function stop(code = 0) {
  if (stopping) return
  stopping = true
  process.exitCode = code
  if (!bridge.stdin.destroyed) bridge.stdin.end('stop\n')
  ui.kill()
  const timer = setTimeout(() => bridge.kill(), 4000)
  timer.unref()
}
for (const child of [bridge, ui]) {
  child.on('error', error => { console.error(error.message); stop(1) })
  child.on('exit', code => { if (!stopping) stop(code ?? 1) })
}
bridge.stdin.on('error', () => {})
process.on('SIGINT', () => stop())
process.on('SIGTERM', () => stop())
