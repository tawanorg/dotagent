import type { Bridge } from './mastra/workflow.js';
import type { createDotagent } from './mastra/workflow.js';
type DotagentWorkflow = ReturnType<ReturnType<typeof createDotagent>['getWorkflow']>;

export async function runTicket(workflow: DotagentWorkflow, task: { ticket: string; runId: string; host?: string; branch?: string; worktree?: string }, bridge: Bridge, project: string) {
        const snapshot = await workflow.getWorkflowRunById(task.runId);
        const run = await workflow.createRun({ runId: task.runId, resourceId: [project, task.ticket, task.host, task.branch].filter(Boolean).join(' · ') });
        let result;
        if (!snapshot || snapshot.status === 'pending') result = await run.start({ inputData: { ticket: task.ticket }, tracingOptions: {
          rootSpanName: project + ' / ' + task.ticket, metadata: { ticket: task.ticket, host: task.host || '', worktree: task.worktree || '' },
        } });
        else if (snapshot.status === 'suspended') result = await run.resume({ resumeData: {} });
        else if (['running', 'waiting', 'pending', 'paused'].includes(snapshot.status))
          result = await run.restart();
        else {
          await bridge('workflow-terminal', { ticket: task.ticket, runId: task.runId, reason: snapshot.status });
        }
        if (result?.status === 'failed')
          await bridge('workflow-terminal', { ticket: task.ticket, runId: task.runId, reason: 'failed; see local trace' });
}
