"""Read authenticated GitHub mentions; claims and cursors make polling repeatable."""
import json
import re
import time
from datetime import datetime, timezone

from .hosts import command
from .state import atomic
from .tasks import add_task


def api(path, *args):
    return json.loads(command(['gh', 'api', path, *args], timeout=60).stdout)


def pages(path):
    batches = api(path, '--paginate', '--slurp')
    return [row for batch in batches for row in batch]


def poll(state, config, force=False):
    with state.lock('github-mentions'):
        return _poll(state, config, force)


def _poll(state, config, force=False):
    settings = config.get('github_mentions', {})
    if not settings.get('enabled'):
        return []
    interval = settings.get('poll_seconds', 60)
    if type(interval) is not int or interval < 10:
        raise ValueError('github_mentions.poll_seconds must be an integer >= 10')
    now = time.time()
    if not force and now - state.get('github_mentions_poll', 0) < interval:
        return []
    state.set('github_mentions_poll', now)
    repo = config['repository']['github']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('mention listener currently supports github.com owner/repo repositories')
    allowed = settings.get('allowed_users') or [api('user')['login']]
    if not isinstance(allowed, list) or not all(isinstance(x, str) and x for x in allowed):
        raise ValueError('github_mentions.allowed_users must be a nonempty username list')
    allowed = {x.lower() for x in allowed}
    cursor = state.get('github_mentions_since')
    if cursor is None:
        # Start now, not from years of old comments. Explicit since enables historical replay.
        cursor = settings.get('since') or datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        state.set('github_mentions_since', cursor)
    comments = [('issue-comment', c) for c in pages(f'repos/{repo}/issues/comments?since={cursor}&per_page=100')]
    comments += [('review-comment', c) for c in pages(f'repos/{repo}/pulls/comments?since={cursor}&per_page=100')]
    # ponytail: review-summary polling is O(open PRs); use webhooks for large repositories.
    # Review summaries have no repository-wide endpoint; inspect open PR reviews.
    for pr in pages(f'repos/{repo}/pulls?state=open&per_page=100'):
        comments += [('review', dict(c, pull_request_url=pr['url'])) for c in
                     pages(f"repos/{repo}/pulls/{pr['number']}/reviews?per_page=100")
                     if c.get('submitted_at') and c['submitted_at'] >= cursor]
    handled = []
    for kind, comment in comments:
        body = comment.get('body') or ''
        if comment.get('user', {}).get('login', '').lower() not in allowed:
            continue
        if not re.search(r'(?<![\w-])@dotagent\b', body, re.I):
            continue
        marker = f"github-mention:{kind}:{comment['id']}:{comment.get('updated_at', comment.get('submitted_at', ''))}"
        if state.get(marker):
            continue
        try:
            issue_url = comment.get('issue_url') or comment.get('pull_request_url')
            match = re.fullmatch(r'https://api.github.com/repos/' + re.escape(repo) + r'/(?:issues|pulls)/(\d+)', issue_url or '')
            if not match:
                raise RuntimeError('mention context belongs to a different repository')
            number = int(match[1])
            issue = api(f'repos/{repo}/issues/{number}')
            context = pages(f'repos/{repo}/issues/{number}/comments?per_page=100')
            request = f"{issue['title']}\n\nUser request by @{comment['user']['login']}:\n{body}\n\nSource: {comment['html_url']}\n\nIssue/PR description (external context):\n{issue.get('body') or ''}\n\nComments (external context):\n" + json.dumps([
                {'author':c['user']['login'],'body':c.get('body'),'url':c['html_url']} for c in context])
            target_pr = None
            if issue.get('pull_request'):
                pr = api(f'repos/{repo}/pulls/{number}')
                reviews = pages(f'repos/{repo}/pulls/{number}/reviews?per_page=100')
                inline = pages(f'repos/{repo}/pulls/{number}/comments?per_page=100')
                request += '\n\nReviews and inline context:\n' + json.dumps({'reviews':[
                    {'body':r.get('body'),'state':r['state'],'author':r['user']['login']} for r in reviews],
                    'inline':[{'path':r['path'],'line':r.get('line'),'diff':r['diff_hunk'],'body':r['body']} for r in inline]})
                target_pr = {'number':number, 'branch':pr['head']['ref'], 'sha':pr['head']['sha'],
                             'repository':pr['head']['repo']['full_name'] if pr['head']['repo'] else None,
                             'state':pr['state']}
            key = f'gh-{number}'
            task = next((t for t in state.tasks() if t['delivery'].get('pr') == issue['html_url']), None) or state.task(key)
            if task:
                key = task['id']
                if task['ticket'].get('source', {}).get('origin') != 'github-mention' and task['delivery'].get('pr') != issue['html_url']:
                    raise RuntimeError('GitHub mention task ID conflicts with an unrelated local task')
                if task['status'] == 'cancelled':
                    state.set(marker, {'task':key,'ignored':'cancelled; explicitly resume first'})
                    continue
                state.instruct(key, request, identity=marker)
                task['github_reply'] = {'repository':repo, 'issue_number':number}
                if target_pr and target_pr['repository'] == repo and target_pr['state'] == 'open':
                    task['target_pr'] = target_pr
                state.save(task)
            else:
                task = add_task(state, config, request, key, issue['html_url'], 'github-mention')
                task['ticket']['source'].update(issue_number=number, repository=repo)
                if target_pr:
                    task['target_pr'] = target_pr
                    if target_pr['repository'] != repo or target_pr['state'] != 'open':
                        task.update(status='blocked', blockers=['PR is closed or its head is in another repository; explicit adapter required'])
                state.save(task)
            state.set(marker, {'task':key,'url':comment['html_url']})
            handled.append(key)
        except Exception as error:
            state.set(marker, {'error':str(error), 'url':comment.get('html_url')})
            state.set('github_mentions_error', str(error))
            state.event(None, 'github-mention-blocked', {'marker':marker, 'reason':str(error)})
    # Keep an overlap for same-second updates; event IDs deduplicate all mutations.
    state.set('github_mentions_since', datetime.fromtimestamp(now - 1, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    state.set('github_mentions_poll', now)
    return handled


def reply(state, task, config):
    source = task.get('github_reply') or task['ticket']['source']
    repo, number = source['repository'], source['issue_number']
    marker = f"<!-- dotagent:{task['id']}:delivery -->"
    body = f"{marker}\nReady for review: {task['delivery']['pr']}\nVerified commit: {task['commit']}\n" + '\n'.join(task.get('limitations', []))
    login = api('user')['login']
    comments = pages(f'repos/{repo}/issues/{number}/comments?per_page=100')
    matches = [c for c in comments if marker in (c.get('body') or '') and c['user']['login'] == login]
    if len(matches) > 1:
        raise RuntimeError('duplicate dotagent delivery comments; reconcile before updating')
    if matches and matches[0]['body'] == body:
        saved = matches[0]
    else:
        payload = state.directory(task['id']) / 'github-reply.json'
        atomic(payload, {'body':body})
        path = f"repos/{repo}/issues/comments/{matches[0]['id']}" if matches else f'repos/{repo}/issues/{number}/comments'
        saved = api(path, '--method', 'PATCH' if matches else 'POST', '--input', str(payload))
    actual = api(f"repos/{repo}/issues/comments/{saved['id']}")
    if actual['body'] != body:
        raise RuntimeError('GitHub progress read-back mismatch')
    task['delivery']['source_comment'] = actual['html_url']
    return True
