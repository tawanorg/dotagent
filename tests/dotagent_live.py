"""Opt-in real Docker/browser fixture. No GitHub/Jira writes or LLM calls.

python3 tests/dotagent_live.py /absolute/path/to/node_modules/@playwright/test
Artifacts and ownership receipt survive under ~/.local/state/dotagent-validation.
"""
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotagent.state import State, atomic
from dotagent.hosts import command
from dotagent.environment import Environment, git, prepare_worktree
from dotagent.runtime import verify


def run(package):
    root = Path.home() / '.local/state/dotagent-validation' / str(time.time_ns())
    root.mkdir(parents=True, mode=0o700)
    repo = root / 'fixture'
    repo.mkdir()
    atomic(repo / 'package.json', {'type': 'module'})
    atomic(repo / 'server.mjs', '''import http from 'node:http';
import fs from 'node:fs';
const file='/data/count';
http.createServer((req,res)=>{
 let count=Number(fs.existsSync(file)?fs.readFileSync(file,'utf8'):0);
 if(req.url==='/increment' && req.method==='POST') {
   count+=1; fs.writeFileSync(file,String(count));
 }
 if(req.url==='/health'){res.end('ok');return;}
 if(req.url==='/increment' || req.url==='/count'){res.end(String(count));return;}
 res.setHeader('content-type','text/html');
 res.end(`<h1>Engineer local verification</h1><p>Count: <span id="count">${count}</span></p>
 <button id="increment">Increment</button><script>
 document.querySelector('button').onclick=async()=>{
 document.querySelector('#count').textContent=await(await fetch('/increment',{method:'POST'})).text();
 };</script>`);
}).listen(8080,'0.0.0.0');
''')
    atomic(repo / 'compose.json', {'services': {'app': {'image': 'node:22-alpine',
        'command': ['node', '/app/server.mjs'], 'ports': ['8080:8080'],
        'volumes': ['./server.mjs:/app/server.mjs:ro', 'data:/data'],
        'healthcheck': {'test': ['CMD', 'node', '-e', "fetch('http://127.0.0.1:8080/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],
                        'interval': '1s', 'timeout': '2s', 'retries': 30}}}, 'volumes': {'data': {}}})
    for args in [('init', '-b', 'main'), ('config', 'user.email', 'fixture@example.test'),
                 ('config', 'user.name', 'Fixture'), ('add', '.'), ('commit', '-m', 'fixture')]:
        command(['git', '-C', str(repo), *args])
    state = State(root / 'state')
    config = {'path': str(repo), 'compose_files': ['compose.json'], 'app_port': 'app:8080',
              'services': ['app'], 'checks': [{'argv': ['node', '--check', 'server.mjs'], 'cwd': '.'}],
              'playwright_package': package, 'memory_per_service': '128m', 'cpus_per_service': 0.5}
    tasks = []
    for key in ('FIXTURE-1', 'FIXTURE-2'):
        task = state.claim({'key': key, 'summary': 'Counter behavior', 'url': 'fixture://counter'}, str(repo))
        prepare_worktree(state, task, config)
        directory = state.directory(key)
        atomic(directory / 'behavior.mjs', '''export default async ({page,expect,baseURL,evidence})=>{
 await page.goto(baseURL);
 const before=Number(await page.locator('#count').textContent());
 await page.getByRole('button',{name:'Increment'}).click();
 await expect(page.locator('#count')).toHaveText(String(before+1));
 await evidence('increment','Increment changes the count by exactly one');
};
''')
        task.update(ui_changed=True, browser_script=str(directory / 'behavior.mjs'), criteria=[{
            'id': 'increment', 'description': 'Increment once', 'expected': '+1', 'manual': 'Click Increment',
            'checks': [{'argv': ['node', '--check', 'server.mjs'], 'cwd': '.', 'kind': 'test'}]}])
        state.save(task)
        tasks.append(task)
    report = {'root': str(root), 'checks': {}}
    try:
        for task in tasks:
            assert verify(state, task, config), 'live browser verification failed'
        first, second = tasks
        def count(task):
            return int(urllib.request.urlopen(task['environment']['base_url'] + '/count').read())
        before = count(second)
        urllib.request.urlopen(urllib.request.Request(first['environment']['base_url'] + '/increment', method='POST')).read()
        assert count(second) == before, 'data leaked between tasks'
        assert first['environment']['ports'] != second['environment']['ports']
        report['checks']['ports_and_data_isolated'] = True
        Environment(state, first, config).stop()
        assert count(second) == before, 'cleanup harmed another task'
        report['checks']['shared_resource_preservation'] = True
        # Deliberately break the behavior, prove browser catches it, then fix and rerun.
        source = Path(second['worktree']) / 'server.mjs'
        source.write_text(source.read_text().replace('count+=1', 'count+=2'))
        command(Environment(state, second, config).argv('restart', 'app'))
        assert not verify(state, second, config), 'broken behavior incorrectly passed'
        report['checks']['failed_behavior_rejected'] = True
        source.write_text(source.read_text().replace('count+=2', 'count+=1'))
        command(Environment(state, second, config).argv('restart', 'app'))
        assert verify(state, second, config)
        report['checks']['fixed_behavior_verified'] = True
        report['artifacts'] = second['artifacts']
        report['environments'] = [t['environment'] for t in tasks]
    finally:
        for task in tasks:
            Environment(state, task, config).stop()
        atomic(root / 'result.json', report)
        print(json.dumps(report, indent=2))
    return root


if __name__ == '__main__':
    run(sys.argv[1])
