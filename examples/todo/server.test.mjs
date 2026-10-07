import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createApp} from './server.mjs';
test('a todo can be added, toggled and read back',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'todo-test-'));const server=createApp(join(dir,'todos.json'));
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base=`http://127.0.0.1:${server.address().port}`;
 try{const post=body=>fetch(base+'/api/todos',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const added=await(await post({title:'Test a real feature'})).json();const item=added.find(t=>t.title==='Test a real feature');assert.equal(item.done,false);
 await post({id:item.id});const saved=await(await fetch(base+'/api/todos')).json();assert.equal(saved.find(t=>t.id===item.id).done,true);
 assert.equal((await post({title:''})).status,400);
 }finally{await new Promise(resolve=>server.close(resolve));await rm(dir,{recursive:true,force:true});}
});
