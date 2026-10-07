import { setTimeout as delay } from 'node:timers/promises';
import { spawn } from 'node:child_process';
import { openSync, closeSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { configuration, nativeBridge } from './mastra/bridge.js';
import { createDotagent } from './mastra/workflow.js';

const config=configuration(), bridge=nativeBridge();
const mastra=createDotagent({root:config.state_dir,bridge});
const workflow=mastra.getWorkflow('dotagent');
const workers=new Map<string, Promise<void>>();
let stopping=false;
process.on('SIGTERM',()=>{stopping=true;});
process.on('SIGINT',()=>{stopping=true;});
async function launch(task: {ticket:string;runId:string;runnerLog:string}) {
  const path=fileURLToPath(new URL('./task-runner.js',import.meta.url));
  const log=openSync(task.runnerLog,'a',0o600);
  const child=spawn(process.execPath,[path,task.ticket,task.runId],{detached:true,stdio:['pipe',log,log]});
  closeSync(log);
  let finish!:()=>void;
  const completed=new Promise<void>(resolve=>{finish=resolve;});
  workers.set(task.ticket,completed);
  child.stdin!.on('error',()=>{});
  child.once('error',()=>finish());
  child.once('close',async (code)=>{
    try { await bridge('runner-ended',{ticket:task.ticket,runId:task.runId,pid:child.pid,code}); }
    catch(error) { console.error(String(error)); }
    finally { workers.delete(task.ticket);finish(); }
  });
  try {
    await bridge('register-runner',{ticket:task.ticket,runId:task.runId,pid:child.pid,argv:[path,task.ticket,task.runId]});
    child.stdin!.end('go');
  } catch(error) {
    child.stdin!.end();
    if(child.pid) {try{process.kill(-child.pid,'SIGTERM');}catch{}}
    throw error;
  }
}
try {
  while(!stopping) {
    let wait=2000;
    try {
      const control=await bridge('control',{});
      for(const runId of await bridge('cancelled-runs',{exclude:[...workers.keys()]})) {
        const saved=await workflow.getWorkflowRunById(runId);
        if(saved && !['success','failed','canceled'].includes(saved.status))
          await (await workflow.createRun({runId})).cancel();
      }
      if(!control.paused && workers.size < control.workers) {
        const {task}=await bridge('next',{exclude:[...workers.keys()]});
        await bridge('intake-error',{error:null});
        if(task && task.status==='active' && task.retryAt<=Date.now()/1000) {
          await launch(task);
          wait=0; // Fill all configured slots without an arbitrary hard cap.
        } else if(!task && workers.size===0) wait=config.jira.poll_seconds*1000;
      }
    } catch(error) {
      await bridge('intake-error',{error:String(error)});
      wait=Math.min(30000,config.limits.backoff_seconds*1000);
    }
    if(process.env.DOTAGENT_ONCE==='1') {await Promise.all(workers.values());break;}
    const control=await bridge('control',{});
    for(let elapsed=0;elapsed<wait && !stopping;elapsed+=1000) {
      await delay(1000);
      if(elapsed%2000===0) {
        const current=await bridge('control',{});
        if(current.revision!==control.revision || current.paused!==control.paused) break;
      }
    }
  }
} finally {
  // The external supervisor reaps registered task groups and native child groups.
  await mastra.observability?.flush();await mastra.shutdown();
}
