import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
export function createApp(dataFile) {
  fs.mkdirSync(path.dirname(dataFile),{recursive:true});
  if(!fs.existsSync(dataFile))fs.writeFileSync(dataFile,JSON.stringify([
    {id:'one',title:'Sketch the next big idea',done:true},
    {id:'two',title:'Make something people love',done:false},
    {id:'three',title:'Ship a small improvement',done:false},
  ]));
  return http.createServer(async(req,res)=>{
    try {
      const url=new URL(req.url,'http://localhost');
      if(url.pathname==='/health'){res.end('ok');return;}
      if(url.pathname==='/api/todos'){
        let todos=JSON.parse(fs.readFileSync(dataFile,'utf8'));
        if(req.method==='POST'){
          let body='';for await(const part of req){body+=part;if(body.length>10000)throw Error('too large');}
          const input=JSON.parse(body);
          if(input.id){const item=todos.find(t=>t.id===input.id);if(!item)throw Error('unknown task');item.done=!item.done;}
          else {if(typeof input.title!=='string'||!input.title.trim()||input.title.length>200)throw Error('invalid title');todos.push({id:crypto.randomUUID(),title:input.title.trim(),done:false});}
          fs.writeFileSync(dataFile,JSON.stringify(todos));
        } else if(req.method!=='GET'){res.writeHead(405);res.end();return;}
        res.setHeader('Content-Type','application/json');res.end(JSON.stringify(todos));return;
      }
      if(req.method!=='GET'||!['/','/app.js','/style.css'].includes(url.pathname)){res.writeHead(404);res.end();return;}
      const name=url.pathname==='/'?'index.html':url.pathname.slice(1);
      res.setHeader('Content-Type',name.endsWith('.css')?'text/css':name.endsWith('.js')?'text/javascript':'text/html');
      res.end(fs.readFileSync(path.join(root,name)));
    } catch {res.writeHead(400);res.end('Invalid request');}
  });
}
if(process.argv[1]===fileURLToPath(import.meta.url))createApp(process.env.TODO_DATA||path.join(root,'.data/todos.json')).listen(Number(process.env.PORT||8080),'0.0.0.0');
