import { setTimeout as delay } from 'node:timers/promises';
import { configuration, nativeBridge } from './mastra/bridge.js';
import { createDotagent } from './mastra/workflow.js';

const config = configuration();
const bridge = nativeBridge();
const l = config.limits;
const mastra = createDotagent({ root: config.state_dir, bridge, limits: {
  maxIterations: l.max_iterations, maxFailures: l.max_failures, maxStagnant: l.max_stagnant,
  taskSeconds: l.task_seconds, backoffSeconds: l.backoff_seconds, maxSpendUsd: l.max_spend_usd,
} });
const workflow = mastra.getWorkflow('dotagent');
let stopping = false;
process.on('SIGTERM', () => { stopping = true; });
process.on('SIGINT', () => { stopping = true; });
try {
  while (!stopping) {
    let wait = 2000;
    try {
      for (const runId of await bridge('cancelled-runs', {})) {
        const saved = await workflow.getWorkflowRunById(runId);
        if (saved && !['success', 'failed', 'canceled'].includes(saved.status))
          await (await workflow.createRun({ runId })).cancel();
      }
      const { task, paused } = await bridge('next', {});
      await bridge('intake-error', { error: null });
      if (task && task.status === 'active' && task.retryAt <= Date.now() / 1000) {
        const run = await workflow.createRun({ runId: task.runId, resourceId: config.project_name || 'dotagent' });
        const snapshot = await workflow.getWorkflowRunById(task.runId);
        let result;
        if (!snapshot) result = await run.start({ inputData: { ticket: task.ticket } });
        else if (snapshot.status === 'suspended') result = await run.resume({ resumeData: {} });
        else if (['running', 'waiting', 'pending', 'paused'].includes(snapshot.status))
          result = await run.restart();
        else {
          await bridge('workflow-terminal', { ticket: task.ticket, runId: task.runId, reason: snapshot.status });
        }
        if (result?.status === 'failed')
          await bridge('workflow-terminal', { ticket: task.ticket, runId: task.runId, reason: 'failed; see local trace' });
        await mastra.observability?.flush();
      } else if (!paused && !task) wait = config.jira.poll_seconds * 1000;
    } catch (error) {
      await bridge('intake-error', { error: String(error) });
      wait = Math.min(30000, l.backoff_seconds * 1000);
    }
    if (process.env.DOTAGENT_ONCE === '1') break;
    const control = await bridge('control', {});
    for (let elapsed = 0; elapsed < wait && !stopping; elapsed += 1000) {
      await delay(1000);
      if (elapsed % 2000 === 0) {
        const current = await bridge('control', {});
        if (current.revision !== control.revision || current.paused !== control.paused) break;
      }
    }
  }
} finally {
  await mastra.observability?.flush();
  await mastra.shutdown();
}
