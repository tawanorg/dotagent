import { Mastra } from '@mastra/core/mastra';
import { createStep, createWorkflow } from '@mastra/core/workflows';
import { LibSQLStore } from '@mastra/libsql';
import { Observability, MastraStorageExporter } from '@mastra/observability';
import { SpanType } from '@mastra/core/observability';
import { z } from 'zod';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

// The bridge is the process boundary to native execution and resource adapters.
export type Bridge = (operation: string, input: Record<string, any>) => Promise<any>;
const request = z.object({ ticket: z.string().min(1) });
const checkpoint = z.object({
  ticket: z.string(), runId: z.string(), phase: z.string(), status: z.string(),
  worktree: z.string().default(''), branch: z.string().default(''), host: z.string().default(''),
  attempts: z.number(), failures: z.number(), stagnant: z.number(),
  elapsed: z.number(), cost: z.number(), costKnown: z.boolean(),
  paused: z.boolean(), retryAt: z.number(), nextAction: z.string(),
  blockers: z.array(z.string()), progress: z.string(),
});
const envelope = z.object({ task: checkpoint, outcome: z.record(z.string(), z.any()).default({}) });
export type Checkpoint = z.infer<typeof checkpoint>;
type Limits = { maxIterations: number; maxFailures: number; maxStagnant: number; taskSeconds: number; backoffSeconds: number; maxSpendUsd?: number };
const defaults: Limits = { maxIterations: 30, maxFailures: 3, maxStagnant: 4, taskSeconds: 28800, backoffSeconds: 10 };

export function createDotagent(options: { root: string; bridge: Bridge; limits?: Partial<Limits>; port?: number }) {
  const { bridge } = options;
  const limits = { ...defaults, ...options.limits };
  mkdirSync(options.root, { recursive: true, mode: 0o700 });
  const load = createStep({
    id: 'load-handover', inputSchema: request, outputSchema: envelope,
    resumeSchema: z.object({}).passthrough(), suspendSchema: checkpoint,
    execute: async ({ inputData, runId, suspend }) => {
      const task = checkpoint.parse(await bridge('read', { ticket: inputData.ticket, runId }));
      if (task.runId !== runId) throw new Error('Another workflow owns this ticket');
      if (task.status === 'cancelled' || task.status === 'review') return { task, outcome: {} };
      if (task.paused || task.status === 'blocked' || task.retryAt > Date.now() / 1000)
        return await suspend(task);
      if (task.attempts >= limits.maxIterations || task.elapsed >= limits.taskSeconds ||
          (limits.maxSpendUsd !== undefined && (!task.costKnown || task.cost >= limits.maxSpendUsd))) {
        const blocked = await bridge('checkpoint', { ticket: task.ticket, runId,
          patch: { status: 'blocked', blockers: ['Configured execution/spending limit reached'] } });
        return await suspend(checkpoint.parse(blocked));
      }
      return { task, outcome: {} };
    },
  });

  // Each graph node is a real engineering phase. One phase runs per Ralph iteration.
  const phases = ['plan', 'implement', 'verify', 'evidence', 'deliver', 'render', 'jira'];
  const phaseStep = (phase: string) => createStep({
    id: phase, inputSchema: envelope, outputSchema: envelope,
    execute: async ({ inputData, runId, tracingContext, abortSignal }) => {
      const { task } = inputData;
      // Keep the persisted graph's legacy last-step ID/index for existing runs.
      if ((task.phase !== phase && !(phase === 'jira' && task.phase === 'source-sync')) || task.status !== 'active') return inputData;
      const started = Date.now();
      let outcome: Record<string, any>;
      try {
        outcome = await bridge('perform', { ticket: task.ticket, runId, phase: task.phase, attempt: task.attempts + 1 });
      } catch (error) {
        if (abortSignal?.aborted) throw error;
        outcome = { error: String(error) };
      }
      outcome.seconds = (outcome.seconds || 0) + (Date.now() - started) / 1000;
      tracingContext?.currentSpan?.update({ metadata: {
        ticket: task.ticket, phase, attempt: task.attempts + 1,
        worktree: task.worktree, branch: task.branch, host: task.host,
        result: outcome.error ? 'failed' : outcome.passed === false ? 'verification-failed' : 'checkpoint',
      } });
      if (outcome.usage) tracingContext?.currentSpan?.createEventSpan({
        type: SpanType.GENERIC, name: 'native-host-usage', metadata: outcome.usage,
      });
      return { task, outcome };
    },
  });

  const persist = createStep({
    id: 'persist-evidence-and-next-action', inputSchema: envelope, outputSchema: checkpoint,
    execute: async ({ inputData: { task, outcome }, runId }) => {
      if (task.status !== 'active') return task;
      if (outcome.reconciled) return checkpoint.parse(await bridge('read', { ticket: task.ticket, runId }));
      const patch: Partial<Checkpoint> = {
        attempts: task.attempts + 1, elapsed: task.elapsed + (outcome.seconds || 0),
        failures: 0, retryAt: 0,
      };
      if (outcome.interrupted) {
        patch.nextAction = 'Resume from checkpoint';
        patch.attempts = task.attempts;
      } else if (outcome.error) {
        patch.failures = task.failures + 1;
        patch.nextAction = outcome.error;
        patch.retryAt = Date.now() / 1000 + Math.min(300, 2 ** patch.failures * limits.backoffSeconds);
        if (patch.failures >= limits.maxFailures) {
          patch.status = 'blocked'; patch.blockers = [outcome.error];
        }
      } else if (outcome.guidance) {
        patch.phase = 'implement'; patch.nextAction = 'Apply new user instructions before verification';
      } else if (outcome.stale) {
        patch.phase = 'verify'; patch.nextAction = 'Content changed; rerun required checks';
      } else if (task.phase === 'plan' || task.phase === 'implement') {
        patch.phase = outcome.action;
        if (!['implement', 'verify', 'blocked'].includes(outcome.action)) throw new Error('Invalid native checkpoint');
        if (outcome.action === 'blocked') { patch.status = 'blocked'; patch.blockers = outcome.blockers || []; }
      } else if (task.phase === 'verify') {
        patch.phase = outcome.passed ? (outcome.uiChanged ? 'evidence' : 'deliver') : 'implement';
        patch.nextAction = outcome.passed ? 'Verification passed' : 'Fix failed checks without weakening criteria';
      } else if (task.phase === 'evidence') {
        if (outcome.safe) patch.phase = 'deliver';
        else if (outcome.blocked) { patch.status = 'blocked'; patch.blockers = [outcome.reason]; }
        else { patch.phase = 'implement'; patch.nextAction = 'Repair evidence: ' + outcome.reason; }
      } else if (task.phase === 'deliver') {
        patch.phase = outcome.artifacts ? 'render' : 'source-sync';
        patch.nextAction = 'Verify delivery: ' + outcome.pr;
      } else if (task.phase === 'render') {
        if (outcome.rendered) patch.phase = 'source-sync';
        else { patch.status = 'blocked'; patch.blockers = ['Screenshot rendering unverified: ' + outcome.reason]; }
      } else if (task.phase === 'source-sync' || task.phase === 'jira') {
        if (!outcome.complete) throw new Error('Delivery evidence incomplete');
        patch.phase = 'review'; patch.status = 'review'; patch.nextAction = 'Await human PR review';
      } else throw new Error('Unknown task phase ' + task.phase);
      // Only code/evidence/delivery changes count; alternating phases alone do not.
      patch.stagnant = outcome.progress && outcome.progress !== task.progress
        ? 0 : task.stagnant + 1;
      if (outcome.interrupted) patch.stagnant = task.stagnant;
      if (patch.stagnant >= limits.maxStagnant && patch.status !== 'review') {
        patch.status = 'blocked'; patch.blockers = ['Repeated iterations without verified progress'];
      }
      return checkpoint.parse(await bridge('checkpoint', {
        ticket: task.ticket, runId, patch, receipt: outcome.receipt, expectedAttempts: task.attempts,
      }));
    },
  });
  let iteration = createWorkflow({ id: 'engineering-iteration', inputSchema: request, outputSchema: checkpoint })
    .then(load);
  for (const phase of phases) iteration = iteration.then(phaseStep(phase));
  const cycle = iteration.then(persist).commit();
  const dotagent = createWorkflow({
    id: 'dotagent', description: 'Task → Ralph iterations → verified draft PR',
    inputSchema: request, outputSchema: checkpoint,
    options: { autoRestartActiveRuns: false, shouldPersistSnapshot: () => true },
  }).dowhile(cycle, async ({ inputData }) => !['review', 'cancelled'].includes(inputData.status)).commit();

  return new Mastra({
    workflows: { dotagent },
    storage: new LibSQLStore({ id: 'dotagent', url: 'file:' + join(options.root, 'mastra.db') }),
    observability: new Observability({ configs: { default: {
      serviceName: 'dotagent', exporters: [new MastraStorageExporter()],
    } } }),
    server: { host: '127.0.0.1', port: options.port || 4111,
      cors: { origin: ['http://127.0.0.1:' + (options.port || 4111)] } },
  });
}
