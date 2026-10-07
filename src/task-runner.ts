import { configuration, nativeBridge } from './mastra/bridge.js';
import { createDotagent } from './mastra/workflow.js';
import { runTicket } from './scheduler.js';

// No work before the parent durably records this process group. EOF before the
// handshake means the scheduler died between spawn and registration.
let handshake = '';
for await (const data of process.stdin) handshake += data;
if (handshake !== 'go') process.exit(0);
const [ticket, runId] = process.argv.slice(2);
const config = configuration();
const bridge = nativeBridge();
const limits = config.limits;
const mastra = createDotagent({root:config.state_dir,bridge,limits:{
  maxIterations:limits.max_iterations,maxFailures:limits.max_failures,maxStagnant:limits.max_stagnant,
  taskSeconds:limits.task_seconds,backoffSeconds:limits.backoff_seconds,maxSpendUsd:limits.max_spend_usd,
}});
try {
  const task = await bridge('read', {ticket,runId});
  await runTicket(mastra.getWorkflow('dotagent'), task, bridge, config.project_name || 'dotagent');
  await bridge('workflow-api-ok', {ticket,runId});
} catch (error) {
  await bridge('workflow-api-error', {ticket,runId,reason:String(error)});
} finally {
  await mastra.observability?.flush();
  await mastra.shutdown();
}
