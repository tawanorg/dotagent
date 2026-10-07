"""Conversational task intake without a tracker, through the installed CLI boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TaskTests(unittest.TestCase):
    def test_plain_language_task_and_guidance_survive_restart_without_jira(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(root / 'repo')], check=True)
            config = root / 'config.toml'
            config.write_text(f'''state_dir = "{root}/state"
[repository]
path = "{root}/repo"
github = "example/todo"
pr_base = "main"
compose_files = ["compose.json"]
app_port = "app:8080"
services = ["app"]
checks = [{{argv=["node","--test"], cwd="."}}]
''')
            def cli(*args):
                result = subprocess.run([sys.executable, str(ROOT / 'bin/dotagent'), '--config', str(config), *args],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout
            first = json.loads(cli('task', 'Add completed-task filtering', '--id', 'todo-filter'))
            self.assertEqual(first['id'], 'todo-filter')
            self.assertEqual(json.loads(cli('task', 'Add completed-task filtering', '--id', 'todo-filter'))['id'], first['id'])
            cli('instruct', first['id'], 'Keep the existing keyboard shortcuts')
            details = json.loads(cli('show', first['id']))
            self.assertEqual(details['ticket']['source']['kind'], 'local')
            self.assertEqual(details['instructions'][0]['text'], 'Keep the existing keyboard shortcuts')
            cli('learn', 'For this project, reproduce a bug before fixing it')
            memories = json.loads(cli('memory', 'list'))
            self.assertEqual(memories[0]['kind'], 'guidance')
            self.assertEqual(len(json.loads(cli('status', '--json'))['tasks']), 1)
            from dotagent.state import State
            import sqlite3
            saved = State(root/'state', readonly=True)
            self.assertIsNotNone(saved.task('todo-filter'))
            with self.assertRaises(sqlite3.OperationalError):
                saved.set('must-not-write', True)
            saved.db.close()
            empty = State(root/'missing', readonly=True)
            self.assertEqual(empty.tasks(), [])
            self.assertFalse((root/'missing').exists())
            empty.db.close()

class MentionTests(unittest.TestCase):
    def test_authorized_mentions_include_review_context_and_polling_does_not_duplicate_tasks(self):
        from unittest.mock import patch
        from dotagent.state import State
        from dotagent.github_mentions import poll
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            config = {'repository':{'path':root,'github':'owner/todo'}, 'github_mentions':{
                'enabled':True,'allowed_users':['owner'],'since':'2026-01-01T00:00:00Z'}}
            comment = {'id':1,'body':'@dotagent fix this PR with the review context', 'user':{'login':'owner'},
                'issue_url':'https://api.github.com/repos/owner/todo/issues/7',
                'updated_at':'2026-10-01T12:00:00Z','html_url':'https://github.com/owner/todo/pull/7#issuecomment-1'}
            outsider = {**comment,'id':2,'user':{'login':'stranger'}}
            def api(path,*args):
                if path.startswith('repos/owner/todo/issues/comments?'):return [[outsider,comment]]
                if path.startswith('repos/owner/todo/pulls/comments?'):return [[]]
                if path.startswith('repos/owner/todo/pulls?'):return [[]]
                if path=='repos/owner/todo/issues/7':return {'title':'Todo filters','body':'Keep keyboard navigation', 'html_url':'https://github.com/owner/todo/pull/7','pull_request':{}}
                if path.endswith('/issues/7/comments?per_page=100'):return [[comment]]
                if path.endswith('/pulls/7'):return {'number':7,'state':'open','head':{'ref':'fix/filter','sha':'abc','repo':{'full_name':'owner/todo'}}}
                if path.endswith('/pulls/7/reviews?per_page=100'):return [[{'body':'Fix the empty state','state':'CHANGES_REQUESTED','user':{'login':'reviewer'}}]]
                if path.endswith('/pulls/7/comments?per_page=100'):return [[{'path':'app.js','line':9,'diff_hunk':'@@ filter @@','body':'Handle completed tasks'}]]
                raise AssertionError(path)
            # A PR response has a nonempty pull_request link.
            original=api
            def response(path,*args):
                data=original(path,*args)
                if path=='repos/owner/todo/issues/7': data['pull_request']={'url':'https://api.github.com/repos/owner/todo/pulls/7'}
                return data
            with patch('dotagent.github_mentions.api',side_effect=response):
                self.assertEqual(poll(state,config,force=True),['gh-7'])
                self.assertEqual(poll(state,config,force=True),[])
            task=state.task('gh-7')
            self.assertIn('Fix the empty state',task['ticket']['description'])
            self.assertIn('Handle completed tasks',task['ticket']['description'])
            self.assertEqual(task['target_pr']['branch'],'fix/filter')
            self.assertEqual(len(state.tasks()),1)
            state.db.close()

    def test_new_instruction_survives_stale_task_save_and_stops_delivery(self):
        from dotagent.state import State
        from dotagent.tasks import add_task
        from dotagent.runtime import perform
        with tempfile.TemporaryDirectory() as root:
            state=State(root)
            task=add_task(state,{'repository':{'path':root}},'Fix filters','filters')
            task['phase']='deliver'
            state.instruct('filters','Preserve keyboard access')
            state.save(task)
            self.assertEqual(perform(state,task,{'repository':{}}),{'guidance':True})
            self.assertEqual(state.instructions('filters')[0]['text'],'Preserve keyboard access')
            state.db.close()


class CrossProjectResourceTests(unittest.TestCase):
    def test_unbound_ports_are_reserved_across_project_databases(self):
        from unittest.mock import patch
        from dotagent.state import State
        from dotagent.environment import allocate_port
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'DOTAGENT_RESOURCE_DIR':root}):
            first, second = State(Path(root)/'first'), State(Path(root)/'second')
            a=allocate_port(first,'task','app',28000,29000)
            b=allocate_port(second,'task','app',28000,29000)
            self.assertNotEqual(a,b)
            self.assertEqual(a,allocate_port(first,'task','app',28000,29000))
            first.db.close();second.db.close()

class FollowupTests(unittest.TestCase):
    def test_reviewed_local_task_reopens_with_new_run_and_source_sync_needs_no_jira(self):
        from unittest.mock import patch
        from dotagent.state import State
        from dotagent.tasks import add_task
        from dotagent.bridge import dispatch
        from dotagent.runtime import perform
        with tempfile.TemporaryDirectory() as root:
            state=State(root)
            config={'repository':{'path':root,'keep_inactive_environments':True},'host':{},'tasks':{'provider':'local'}}
            task=add_task(state,config,'Fix Todo filters','filter')
            task.update(workflow_run='old-run',status='review',phase='review')
            state.save(task)
            state.instruct('filter','Address the keyboard review')
            with patch('dotagent.bridge.reconcile'):
                selected=dispatch(state,config,'next',{'exclude':[]})['task']
            self.assertEqual(selected['phase'],'implement')
            self.assertNotEqual(selected['runId'],'old-run')
            task=state.task('filter')
            task.update(phase='jira',instructions_ack=state.instructions('filter')[-1]['seq'])
            task['delivery']['body_verified']=True
            result=perform(state,task,config)
            self.assertTrue(result['complete'])
            self.assertNotIn('jira_comment',state.task('filter')['delivery'])
            state.db.close()
