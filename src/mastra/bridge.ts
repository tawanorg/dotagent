import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import type { Bridge } from './workflow.js';

export function configuration() {
  const file = process.env.DOTAGENT_RUNTIME_CONFIG;
  if (!file) throw new Error('Start with dotagent start (project configuration required)');
  return JSON.parse(readFileSync(file, 'utf8'));
}

export function nativeBridge(executable = process.env.DOTAGENT_PYTHON || 'python3'): Bridge {
  return async (operation, input) => {
    if (process.env.DOTAGENT_EXECUTE !== '1')
      throw new Error('Studio is an inspection surface. Use dotagent start/resume/cancel to execute safely.');
    return await new Promise((resolve, reject) => {
      const child = spawn(executable, ['-m', 'dotagent.bridge'], { stdio: ['pipe', 'pipe', 'inherit'] });
      let output = '';
      child.stdout.setEncoding('utf8');
      child.stdout.on('data', data => {
        output += data;
        if (output.length > 4 * 1024 * 1024) { child.kill(); reject(new Error('Bridge output limit exceeded')); }
      });
      child.once('error', reject);
      child.once('exit', code => {
        try {
          const result = JSON.parse(output);
          if (code || result.bridgeError) reject(new Error(result.bridgeError || 'Bridge process failed'));
          else resolve(result);
        } catch { reject(new Error('Bridge failed; inspect local supervisor log')); }
      });
      child.stdin.on('error', () => {});
      child.stdin.end(JSON.stringify({ operation, input }));
    });
  };
}
