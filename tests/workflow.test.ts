import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createDotagent, type Bridge } from '../src/mastra/workflow.js';

test('Ralph repairs a failed verification and only finishes after delivery evidence', async () => {
  const root = await mkdtemp(join(tmpdir(), 'dotagent-workflow-'));
  let task = {
    ticket: 'FIXTURE-1', phase: 'plan', status: 'active', attempts: 0,
    failures: 0, stagnant: 0, elapsed: 0, cost: 0, costKnown: true,
    paused: false, retryAt: 0, nextAction: '', blockers: [] as string[],
    runId: 'fixture-run', progress: '0',
  };
  let checks = 0;
  const actions: string[] = [];
  const bridge: Bridge = async (op, input) => {
    if (op === 'read') return task;
    if (op === 'checkpoint') {
      task = { ...task, ...input.patch };
      return task;
    }
    if (op === 'perform') {
      actions.push(input.phase);
      if (input.phase === 'plan' || input.phase === 'implement')
        return { action: 'verify', progress: String(actions.length), uiChanged: false };
      if (input.phase === 'verify') return { passed: ++checks > 1, uiChanged: false, progress: String(actions.length) };
      if (input.phase === 'deliver') return { pr: 'https://github.com/example/repo/pull/1', artifacts: false, progress: 'delivered' };
      if (input.phase === 'jira') return { complete: true, progress: 'review' };
    }
    throw new Error('Unexpected operation ' + op);
  };
  const mastra = createDotagent({ root, bridge, limits: { maxIterations: 20 } });
  const run = await mastra.getWorkflow('dotagent').createRun({ runId: task.runId });
  const result = await run.start({ inputData: { ticket: task.ticket } });
  assert.equal(result.status, 'success');
  assert.equal(task.status, 'review');
  assert.deepEqual(actions, ['plan', 'verify', 'implement', 'verify', 'deliver', 'jira']);
  await mastra.observability?.flush();
  await mastra.shutdown();
});

test('a blocked task resumes in a replacement Mastra instance without repeating completed phases', async () => {
  const root = await mkdtemp(join(tmpdir(), 'dotagent-resume-'));
  let task = {
    ticket: 'FIXTURE-2', phase: 'plan', status: 'active', attempts: 0,
    failures: 0, stagnant: 0, elapsed: 0, cost: 0, costKnown: true,
    paused: false, retryAt: 0, nextAction: '', blockers: [] as string[],
    runId: 'resume-run', progress: '0',
  };
  const actions: string[] = [];
  const bridge: Bridge = async (op, input) => {
    if (op === 'read') return task;
    if (op === 'checkpoint') {
      task = { ...task, ...input.patch };
      return task;
    }
    if (op === 'perform') {
      actions.push(input.phase);
      if (input.phase === 'plan') return { action: 'blocked', blockers: ['Need product decision'] };
      if (input.phase === 'implement') return { action: 'verify' };
      if (input.phase === 'verify') return { passed: true, uiChanged: false };
      if (input.phase === 'deliver') return { pr: 'https://github.com/example/repo/pull/2', artifacts: false };
      if (input.phase === 'jira') return { complete: true };
    }
    throw new Error('Unexpected operation ' + op);
  };
  let mastra = createDotagent({ root, bridge });
  const first = await mastra.getWorkflow('dotagent').createRun({ runId: task.runId });
  assert.equal((await first.start({ inputData: { ticket: task.ticket } })).status, 'suspended');
  await mastra.observability?.flush(); await mastra.shutdown();
  task = { ...task, status: 'active', phase: 'implement', blockers: [] };
  mastra = createDotagent({ root, bridge });
  const replacement = await mastra.getWorkflow('dotagent').createRun({ runId: task.runId });
  assert.equal((await replacement.resume({ resumeData: {} })).status, 'success');
  assert.deepEqual(actions, ['plan', 'implement', 'verify', 'deliver', 'jira']);
  assert.equal(task.status, 'review');
  await mastra.observability?.flush(); await mastra.shutdown();
});
