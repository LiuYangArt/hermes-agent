"""Deterministic, restart-safe Meegle triage. Deployment supplies private IDs."""
import argparse
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from html.parser import HTMLParser


DECISION_CACHE_VERSION = 'owner-decision-v2'


def cli_environment():
    # Cron gives shell tools a scratch HOME; these installed CLIs use the persistent login profile.
    env = dict(os.environ)
    env['HOME'] = os.environ.get('HERMES_HOME', '/opt/data')
    return env


def scalar(v):
    if isinstance(v, dict):
        return str(v.get('value', v.get('key', v.get('id', ''))))
    return '' if v is None else str(v)


def ids(v):
    return [scalar(x) for x in (v or [])]


def category(name):
    return 'feature' if 'featurerequest' in re.sub(r'[\s_-]', '', name).lower() else 'bug'


def document_content(output):
    payload=json.loads(output)
    if payload.get('ok') is False: raise RuntimeError('document fetch rejected')
    doc=payload.get('data',payload)
    doc=doc.get('document',doc)
    content=doc.get('content') or doc.get('markdown')
    if not isinstance(content,str) or not content.strip(): raise RuntimeError('document content absent')
    return content


class Images(HTMLParser):
    def __init__(self):
        super().__init__(); self.urls = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'img':
            src = dict(attrs).get('src')
            if src and src not in self.urls:
                self.urls.append(src)


def image_urls(body):
    p = Images(); p.feed(body)
    for url in re.findall(r'!\[[^\]]*\]\((https?://[^\s)]+)', body):
        if url not in p.urls: p.urls.append(url)
    return p.urls


def source_identity(issue):
    url = str(issue.get('url') or '').strip()
    match = re.fullmatch(r'https://github\.com/([^/\s]+/[^/\s]+)/issues/([1-9][0-9]*)/?(?:[?#].*)?', url)
    repo, num = str(issue.get('repository') or '').strip(), str(issue.get('number') or '').strip()
    if match:
        if repo and repo.lower() != match[1].lower(): raise ValueError('GitHub repository conflicts with URL')
        if num and num != match[2]: raise ValueError('GitHub issue number conflicts with URL')
        repo, num = match[1], match[2]
    elif not (re.fullmatch(r'[^/\s]+/[^/\s]+', repo) and re.fullmatch(r'[1-9][0-9]*', num)):
        return None
    issue.update(repository=repo, number=num, url=f'https://github.com/{repo}/issues/{num}')
    return f'{repo.lower()}#{num}'


AI_TRIAGE_HEADING = 'AI 分诊提示（未核实）'


def ai_triage_block(decision):
    candidates = '、'.join(decision.get('candidate_owners') or []) or '无'
    missing = '\n'.join('- ' + item for item in decision.get('missing_evidence') or []) or '- 无'
    return (f'**{AI_TRIAGE_HEADING}**\n'
            f'候选负责人：{candidates}（仅供核实，非正式派单）\n'
            f'待补信息：\n{missing}')


def normalized_ai_text(value):
    value = html.unescape(value or '')
    value = re.sub(r'<[^>]*>', '', value)
    value = re.sub(r'\[([^]]*)\]\([^)]*\)', r'\1', value)
    return re.sub(r'[\s*_#>`\-]+', '', value)


def insert_ai_triage(text, source_url, decision):
    block = ai_triage_block(decision)
    normalized = normalized_ai_text(text)
    if normalized_ai_text(block) in normalized:
        return text
    if normalized_ai_text(AI_TRIAGE_HEADING) in normalized:
        raise RuntimeError('AI triage section was edited or is incomplete')
    lines = text.splitlines(keepends=True)
    if not lines:
        raise RuntimeError('Source URL line missing; cannot insert AI triage section')
    first = lines[0].strip()
    escaped = re.escape(source_url)
    allowed = (re.fullmatch(escaped, first) or re.fullmatch(r'<' + escaped + r'>', first) or
               re.fullmatch(r'\[[^]]+\]\(' + escaped + r'\)', first) or
               re.fullmatch(r'<a\s+[^>]*href=["\']' + escaped + r'["\'][^>]*>.*</a>', first, re.I))
    if not allowed:
        raise RuntimeError('Source URL is not an independent first-line link')
    remainder = ''.join(lines[1:]).lstrip('\r\n')
    return lines[0].rstrip('\r\n') + '\n\n' + block + ('\n\n' + remainder if remainder else '')


def description(issue, decision=None):
    body = issue.get('description') or ''
    if body.startswith(issue['url']): body = body[len(issue['url']):].lstrip('\r\n')
    def replace(m):
        p = Images(); p.feed(m[0])
        return f'[图片]({p.urls[0]})' if p.urls else m[0]
    body = re.sub(r'<img\b[^>]*>', replace, body, flags=re.I)
    result = issue['url'] + '\n\n' + body
    return insert_ai_triage(result, issue['url'], decision) if decision else result


def canonical_text(value):
    value = html.unescape(value or '')
    value = re.sub(r'<img\b[^>]*>', '', value, flags=re.I)
    value = re.sub(r'!?\[[^\]]*\]\((https?://[^)]+)\)', r'\1', value)
    value = re.sub(r'<[^>]*>', '', value)
    return re.sub(r'\s+', '', value)


def fields(values):
    return [{'field_key': k, 'field_value': v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)} for k,v in values.items()]


class Rejected(RuntimeError):
    pass


class CLI:
    def __init__(self, config): self.c = config
    def call(self, domain, action, **params):
        params.setdefault('project_key', self.c['project_key'])
        cp = subprocess.run(self.c['meegle_command'] + [domain, action, '-P', json.dumps(params, ensure_ascii=False)], capture_output=True, text=True, timeout=90, env=cli_environment())
        if cp.returncode: raise RuntimeError(f'{domain} {action} failed (exit {cp.returncode}): {cp.stderr[:800]}')
        result = json.loads(cp.stdout)
        if isinstance(result, dict) and (result.get('error') or result.get('code', 0)):
            raise Rejected(f'{domain} {action} API error: {str(result)[:800]}')
        return result
    def query(self, typ, keys, where=''):
        offset = 0; seen = set()
        while True:
            columns = ', '.join('`'+x+'`' for x in dict.fromkeys(['work_item_id','name']+keys))
            mql = f'SELECT {columns} FROM `{self.c["project_key"]}`.`{typ}`'
            if where: mql += ' WHERE ' + where
            mql += f' ORDER BY `work_item_id` ASC LIMIT {offset}, 50'
            result = self.call('workitem','query',mql=mql)
            raw = [row for group in result.get('data',{}).values() for row in group]
            for row in raw:
                item = {}
                for f in row['moql_field_list']:
                    value = f.get('value') or {}
                    item[f['key']] = value.get(f.get('value_type'))
                key = str(item['work_item_id'])
                if key in seen: raise RuntimeError('Pagination repeated a work item')
                seen.add(key); yield item
            if len(raw) < 50: break
            offset += 50
    def get(self, item_id, keys):
        r = self.call('workitem','get',work_item_id=str(item_id),fields=list(dict.fromkeys(['description','watchers']+keys)))
        a = r['work_item_attribute']
        return dict({f['key']: f['value'] for f in r.get('work_item_fields',[])}, id=str(a['work_item_id']), name=a['work_item_name'], _attribute=a)
    def update(self, item_id, values):
        return self.call('workitem','update',work_item_id=str(item_id),fields=fields(values))
    def user(self, email):
        result = self.call('user','search',user_keys=[email])
        matches = [u for u in result if u.get('email','').lower()==email.lower() and u.get('status')=='activated']
        return str(matches[0]['user_key']) if len(matches)==1 else None
    def metadata(self, typ, keys):
        found={}; page=1
        while True:
            r=self.call('workitem','meta-fields',work_item_type=typ,field_keys=list(dict.fromkeys(keys)),page_num=page)
            found.update({x['field_key']:x for x in r.get('list',[])})
            if not r.get('pagination',{}).get('has_more'): break
            page+=1
        missing=set(keys)-found.keys()
        if missing: raise RuntimeError('Missing configured fields: '+','.join(sorted(missing)))
        return found


class State:
    def __init__(self, root):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'journal.json'
        self.data=json.loads(self.path.read_text()) if self.path.exists() else {'items':{},'owners':{}}
    def save(self):
        temp=self.path.with_suffix('.tmp')
        with temp.open('w') as f:
            json.dump(self.data,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        os.chmod(temp,0o600); os.replace(temp,self.path)


class Pipeline:
    def __init__(self,c,api,state,resolver=None):
        self.c,self.api,self.state,self.resolver=c,api,state,resolver
        self.document=None; self.doc_error=None; self.users={}; self.model_calls=0; self.cache_hits=0
    def validate(self, kinds):
        s=self.c['source']; sm=self.api.metadata(s['type'],list(s['fields'].values()))
        def option(meta,key,values):
            options={str(x['option_id']) for x in meta[key].get('option',[])}
            if not set(map(str,values)) <= options: raise RuntimeError('Configured option no longer exists: '+key)
        option(sm,s['fields']['status'],s['pending_statuses']+list(s['done_statuses'].values()))
        option(sm,s['fields']['category'],s['category_options'].values())
        for kind in kinds:
            t=self.c['targets'][kind]
            meta=self.api.metadata(t['type'],list(t['fields'].values())+['template'])
            option(meta,'template',[t['template']]); option(meta,t['fields']['status'],[t['initial_status']])
            required=self.api.call('workitem','meta-create-fields',work_item_type=t['type'])
            supplied=set(t['fields'].values())|{'name','description','template','role_owners'}
            for f in required.get('FieldConfList',[]):
                if f.get('is_required',f.get('required',False)) and f.get('field_key') not in supplied:
                    raise RuntimeError('Unconfigured required field: '+str(f.get('field_key')))
            if kind=='bug':
                roles=self.api.call('workitem','meta-roles',work_item_type=t['type'],page_num=1)
                if self.c['operator_role'] not in {x.get('role_id') for x in roles.get('list',[])}: raise RuntimeError('Assignee role missing')
                if not self.user(self.c['fallback_user']['email']): raise RuntimeError('Fallback user unavailable')
    def user(self,email):
        if email not in self.users: self.users[email]=self.api.user(email)
        return self.users[email]
    def owner(self,issue):
        fallback=self.c['fallback_user']
        if self.document is None and self.doc_error is None:
            try:
                p=subprocess.run(self.c['document_command'],capture_output=True,text=True,timeout=90,env=cli_environment())
                if p.returncode or not p.stdout.strip(): raise RuntimeError('document fetch failed')
                self.document=document_content(p.stdout)
            except Exception as e: self.doc_error=type(e).__name__+': document unavailable'
        if self.doc_error:
            user = self.user(fallback['email'])
            if not user: raise RuntimeError('Fallback user unavailable')
            return {'name':fallback['name'],'rule':'document-unavailable','reason':self.doc_error,
                    'owner_decision_reason':'document_unavailable','candidate_owners':[],
                    'missing_evidence':[],'user_key':user}
        digest=hashlib.sha256(json.dumps([DECISION_CACHE_VERSION,issue,self.document],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        decision=self.state.data['owners'].get(digest)
        if not decision:
            self.model_calls+=1
            decision=self.resolver(issue,self.document)
            from owner_resolver import parse_decision
            try:
                decision=parse_decision(json.dumps(decision,ensure_ascii=False))
            except Exception as exc:
                raise RuntimeError('Invalid owner decision') from exc
            self.state.data['owners'][digest]=decision; self.state.save()
        else: self.cache_hits+=1
        decision=dict(decision)
        email={k.casefold():v for k,v in self.c['owner_accounts'].items()}.get(decision['name'].casefold())
        user=self.user(email) if email else None
        if not user:
            decision.update(name=fallback['name'],reason=decision['reason']+'; no unique active account, fallback',
                            owner_decision_reason='account_unavailable')
            user=self.user(fallback['email'])
            if not user: raise RuntimeError('Fallback user unavailable')
        decision['user_key']=user
        return decision
    def candidates(self):
        s=self.c['source']; f=s['fields']
        recovering={str(v.get('source')) for v in self.state.data['items'].values() if v.get('phase') not in ('done','rejected')}
        for row in self.api.query(s['type'],list(f.values())):
            if scalar(row.get(f['status'])) not in s['pending_statuses'] and str(row['work_item_id']) not in recovering: continue
            issue={'id':str(row['work_item_id']),'name':row['name']}
            issue.update({k:row.get(v) for k,v in f.items()})
            yield issue
    def find(self,kind,issue):
        t=self.c['targets'][kind]; tf=t['fields']
        url=issue['url'].replace('\\','\\\\').replace('_','\\_').replace('%','\\%').replace("'","''")
        where=f"`description` LIKE '%{url}%'"
        if 'url' in tf: where+=f" OR `{tf['url']}` = '{issue['url'].replace(chr(39),chr(39)*2)}'"
        rows=self.api.query(t['type'],list(tf.values())+['description'],where)
        matched=[]
        for row in rows:
            candidate={'url':row.get(tf.get('url','')),'repository':row.get(tf.get('repository','')),'number':row.get(tf.get('number',''))}
            match=source_identity(candidate) if any(candidate.values()) else None
            urls=re.findall(r'https://github\.com/[^/\s]+/[^/\s]+/issues/[1-9][0-9]*',str(row.get('description') or ''))
            if match==source_identity(dict(issue)) or issue['url'].lower() in [u.lower() for u in urls]: matched.append(str(row['work_item_id']))
        if len(matched)>1: raise RuntimeError('Multiple targets share GitHub identity: '+','.join(matched))
        return matched[0] if matched else None
    def process(self,issue,apply):
        identity=source_identity(issue)
        if not identity: return {'source':issue['id'],'action':'skip','reason':'No valid GitHub identity'}
        s=self.c['source']
        full=self.api.get(issue['id'],list(s['fields'].values()))
        sf=s['fields']
        journal=self.state.data['items'].get(identity,{})
        recovery_status=s['done_statuses'].get(journal.get('kind')) if journal.get('phase') not in ('done','rejected') else None
        if scalar(full.get(sf['status'])) not in s['pending_statuses'] and scalar(full.get(sf['status'])) != recovery_status:
            return {'source':issue['id'],'action':'skip','reason':'Source changed since scan'}
        issue.update(name=full['name'],description=full.get('description') or '')
        for key in ('url','repository','number','labels'):
            if sf[key] in full: issue[key]=full[sf[key]]
        identity=source_identity(issue)
        if not identity: raise RuntimeError('Source GitHub identity changed since scan')
        kind=category(issue['name']); t=self.c['targets'][kind]; tf=t['fields']
        existing=ids(full.get(sf[kind+'_relation']))
        other='feature' if kind=='bug' else 'bug'
        if full.get(sf[other+'_relation']) or len(existing)>1: raise RuntimeError('Conflicting source target relations')
        target=existing[0] if existing else self.find(kind,issue)
        journal=self.state.data['items'].get(identity,{})
        if not target and journal.get('target'): target=str(journal['target'])
        if not apply: return {'source':issue['id'],'kind':kind,'action':'reuse' if target else 'create','target':target,'owner':'deferred; dry-run does not call model'}
        if target and journal.get('phase')=='creating' and journal.get('source')==issue['id'] and journal.get('kind')==kind:
            journal.update(target=target,phase='created'); self.state.save()
        decision=None
        if not target:
            if journal.get('phase')=='creating': raise RuntimeError('Previous create outcome uncertain; remote lookup empty, manual reconciliation required')
            decision=self.owner(issue) if kind=='bug' else {'name':'default','rule':'title-feature-request','reason':'Title matches feature request'}
            target_description=description(issue, decision if decision.get('owner_decision_reason') == 'insufficient_evidence' else None)
            values={'template':str(t['template']),'name':issue['name'],'description':target_description,tf['status']:t['initial_status']}
            for key in ('url','repository','number','labels'):
                if key in tf and issue.get(key) is not None: values[tf[key]]=issue[key]
            if kind=='feature': values[tf['source_relation']]=[int(issue['id'])]
            else: values['role_owners']=[{'role':self.c['operator_role'],'owners':[decision['user_key']]}]
            journal={'phase':'creating','kind':kind,'source':issue['id'],'decision':decision}
            self.state.data['items'][identity]=journal; self.state.save()
            try:
                response=self.api.call('workitem','create',work_item_type=t['type'],fields=fields(values))
            except Rejected:
                journal['phase']='rejected'; self.state.save(); raise
            target=response.get('work_item_id') or response.get('id')
            if not target: raise RuntimeError('Create returned no work_item_id; reconcile before retry')
            target=str(target); journal.update(target=target,phase='created'); self.state.save()
        current=self.api.get(target,list(tf.values()))
        if current['_attribute']['work_item_type']['key'] != t['type']: raise RuntimeError('Recovered target has wrong type')
        expected=description(issue)
        missing=[u for u in image_urls(issue['description']) if html.unescape(u) not in html.unescape(current.get('description') or '')]
        if missing:
            self.api.update(target,{'description':(current.get('description') or '')+'\n\n'+'\n'.join(f'[图片]({u})' for u in missing)})
            current=self.api.get(target,list(tf.values()))
        owned=journal.get('target')==target and journal.get('phase')!='done'
        decision = journal.get('decision') or {}
        if owned and decision.get('owner_decision_reason') == 'insufficient_evidence':
            repaired = insert_ai_triage(current.get('description') or '', issue['url'], decision)
            if repaired != (current.get('description') or ''):
                self.api.update(target, {'description': repaired})
                current = self.api.get(target, list(tf.values()))
            try:
                verified = insert_ai_triage(current.get('description') or '', issue['url'], decision)
            except RuntimeError:
                raise RuntimeError('AI triage section missing; no source completion') from None
            if verified != (current.get('description') or ''):
                raise RuntimeError('AI triage section missing; no source completion')
        original_text=issue['description'].replace(issue['url'], '')
        original_text=re.sub(r'<img\b[^>]*>', '', original_text, flags=re.I)
        original_text=re.sub(r'!\[[^\]]*\]\([^)]*\)', '', original_text)
        # Link repair can append pictures, so compare body text independently of image positions.
        target_text=(current.get('description') or '').replace(issue['url'], '')
        for url in image_urls(issue['description']):
            original_text=original_text.replace(url,'')
            target_text=re.sub(r'!?\[[^\]]*\]\('+re.escape(url)+r'\)', '', target_text).replace(url,'')
        if (canonical_text(original_text) not in canonical_text(target_text) or
                issue['url'] not in (current.get('description') or '')):
            raise RuntimeError('Target original text is not preserved; no source completion')
        if any(html.unescape(u) not in html.unescape(current.get('description') or '') for u in image_urls(issue['description'])):
            raise RuntimeError('Target image links missing')
        # Only a target created by this transaction receives initial defaults; existing human changes are preserved.
        if kind=='bug' and owned:
            fallback=self.user(self.c['fallback_user']['email']); updates={}
            for key in (tf['followers'],'watchers'):
                existing=ids(current.get(key))
                if fallback not in existing: updates[key]=existing+[fallback]
            if updates: self.api.update(target,updates); current=self.api.get(target,list(tf.values()))
            owner_key=journal['decision']['user_key']
            roles=current['_attribute'].get('role_members',[])
            owners=[scalar(m) for r in roles if r['key']==self.c['operator_role'] for m in r.get('members',[])]
            if owner_key not in owners or any(fallback not in ids(current.get(k)) for k in (tf['followers'],'watchers')):
                raise RuntimeError('Owner/follower verification failed')
        if owned and scalar(current.get(tf['status'])) != str(t['initial_status']): raise RuntimeError('Initial status verification failed')
        if kind=='feature' and issue['id'] not in ids(current.get(tf['source_relation'])):
            self.api.update(target,{tf['source_relation']:list(dict.fromkeys(ids(current.get(tf['source_relation']))+[issue['id']]))})
            current=self.api.get(target,list(tf.values()))
            if issue['id'] not in ids(current.get(tf['source_relation'])): raise RuntimeError('Feature source relation verification failed')
        reason=(journal.get('decision') or {'rule':'remote-recovery','reason':'Existing target reused without resetting human fields',
                                            'candidate_owners':[],'missing_evidence':[]})
        sf=s['fields']; updates={sf['category']:s['category_options'][kind],sf['status']:s['done_statuses'][kind],sf[kind+'_relation']:[int(target)],sf['reason']:f"{reason.get('rule')}: {reason.get('reason')}; target={target}"}
        self.api.update(issue['id'],updates)
        verify=self.api.get(issue['id'],list(sf.values()))
        if scalar(verify.get(sf['status']))!=s['done_statuses'][kind] or target not in ids(verify.get(sf[kind+'_relation'])) or scalar(verify.get(sf['category']))!=s['category_options'][kind]:
            raise RuntimeError('Source completion verification failed')
        journal.update(target=target,phase='done'); self.state.data['items'][identity]=journal; self.state.save()
        return {'source':issue['id'],'target':target,'kind':kind,'action':'verified','owner':reason.get('name'),
                'owner_decision_reason':reason.get('owner_decision_reason'),'status':scalar(current.get(tf['status'])),
                'candidate_owners':reason.get('candidate_owners',[]),'missing_evidence':reason.get('missing_evidence',[]),
                'followers':ids(current.get(tf.get('followers','watchers'))), 'watchers':ids(current.get('watchers')),
                'source_url':f"{self.c.get('project_url','')}/{s['type']}/detail/{issue['id']}",
                'target_url':f"{self.c.get('project_url','')}/{t['type']}/detail/{target}"}
    def run(self,apply=False):
        pending=list(self.candidates())
        if not pending: return {'results':[],'scanned_pending':0,'model_calls':0,'cache_hits':0,'owner_reason_counts':{}}
        kinds=set()
        for item in pending:
            try:
                if source_identity(dict(item)): kinds.add(category(item['name']))
            except ValueError: pass
        if kinds: self.validate(kinds)
        results=[]
        for issue in pending:
            try: results.append(self.process(issue,apply))
            except Exception as e:
                result={'source':issue['id'],'action':'failed','error':str(e)[:1200]}
                if hasattr(e, 'error_category'): result['error_category']=e.error_category
                results.append(result)
        counts={}
        for result in results:
            reason=result.get('owner_decision_reason')
            if reason: counts[reason]=counts.get(reason,0)+1
        return {'results':results,'scanned_pending':len(pending),'model_calls':self.model_calls,'cache_hits':self.cache_hits,'owner_reason_counts':counts}


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument('--config',default='/opt/data/triage/config.json')
    group=parser.add_mutually_exclusive_group(); group.add_argument('--apply',action='store_true'); group.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv); config=json.loads(Path(args.config).read_text())
    root=Path(config['state_dir']); root.mkdir(parents=True,exist_ok=True)
    with (root/'run.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: print(json.dumps({'skipped':'another run owns lock'})); return 0
        state=State(config['state_dir'])
        from owner_resolver import resolve
        result=Pipeline(config,CLI(config),state,resolve).run(args.apply)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if any(x['action']=='failed' for x in result['results']) else 0

if __name__=='__main__': sys.exit(main())
