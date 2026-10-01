import copy
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parent))
from triage import Pipeline, State, category, source_identity, description, image_urls, fields, document_content, needs_translation, insert_ai_triage


class DeterministicRules(unittest.TestCase):
    def test_nested_document_and_rejection(self):
        self.assertEqual(document_content('{"ok":true,"data":{"document":{"content":"rules","revision_id":3}}}'),'rules')
        with self.assertRaises(RuntimeError): document_content('{"ok":false,"data":{"content":"error"}}')
        with self.assertRaises(RuntimeError): document_content('{"ok":true,"data":{}}')
    def test_title_only(self):
        for title in ('[FeatureRequest] x','FEATURE-REQUEST x','feature_request','feature request'):
            self.assertEqual(category(title),'feature')
        self.assertEqual(category('bug requesting features'),'bug')
    def test_identity_conflicts_fail_closed(self):
        with self.assertRaises(ValueError):
            source_identity({'url':'https://github.com/a/b/issues/7','number':'8'})
        self.assertIsNone(source_identity({'url':'https://evil.com/a/b/issues/7'}))
        i={'repository':'a/b','number':'7'}
        self.assertEqual(source_identity(i),'a/b#7')
        self.assertEqual(i['url'],'https://github.com/a/b/issues/7')
    def test_image_conversion_preserves_all_urls(self):
        body='First\n<img src="https://x/a?a=1&amp;b=2">\n<img src="https://x/b">\n![already](https://x/c)'
        result=description({'url':'https://github.com/a/b/issues/7','description':body})
        self.assertTrue(result.startswith('https://github.com/a/b/issues/7\n\nFirst'))
        self.assertNotIn('<img',result)
        for url in image_urls(body): self.assertIn(url,result)

    def test_ai_triage_has_divider_before_issue_body(self):
        url='https://github.com/a/b/issues/7'
        decision={'candidate_owners':['mmz'], 'missing_evidence':['定位信息']}
        result=insert_ai_triage(url+'\n\n原始缺陷描述', url, decision)
        self.assertEqual(result.count(url), 1)
        self.assertIn('待补信息：\n- 定位信息\n\n---\n\n原始缺陷描述', result)

    def test_description_keeps_source_url_once(self):
        url='https://github.com/a/b/issues/7'
        result=description({'url':url, 'description':url+'\n\n正文\n\n'+url+'\n'})
        self.assertEqual(result.count(url), 1)
    def test_protocol_stringifies_nested_values(self):
        result=fields({'role_owners':[{'role':'operator','owners':['u']}]})
        self.assertIsInstance(result[0]['field_value'],str)

    def test_translation_rule_only_triggers_for_non_chinese_body(self):
        self.assertTrue(needs_translation('Crash when opening the inventory'))
        self.assertFalse(needs_translation('打开背包时崩溃'))
        self.assertFalse(needs_translation('Crash 打开背包'))

    def test_translation_rule_ignores_title(self):
        self.assertTrue(needs_translation('Open inventory'))


class FakeAPI:
    def __init__(self, source, target=None):
        self.source=source; self.target=target; self.writes=[]; self.created=0
    def get(self,id,keys):
        return copy.deepcopy(self.source if str(id)=='1' else self.target)
    def query(self,typ,keys,where=''):
        if typ=='bug' and self.target:
            yield dict(self.target,work_item_id='2')
    def call(self,domain,action,**params):
        if action=='create':
            self.created+=1
            raise TimeoutError('unknown server outcome')
        raise AssertionError((domain,action))
    def update(self,id,values):
        self.writes.append((id,values))
    def user(self,email): return 'fallback-user'


def config(root):
    return {'state_dir':root,'project_key':'p','source':{'type':'source','fields':{'url':'url','repository':'repo','number':'num','labels':'labels','status':'state','category':'category','reason':'reason','bug_relation':'bugs','feature_relation':'features'},'pending_statuses':['pending'],'done_statuses':{'bug':'done-bug','feature':'done-feature'},'category_options':{'bug':'bug','feature':'feature'}},'targets':{'bug':{'type':'bug','template':'t','fields':{'status':'bug-status','followers':'followers'},'initial_status':'verify'},'feature':{'type':'feature','template':'t','fields':{'status':'review','source_relation':'sources'},'initial_status':'pending'}},'fallback_user':{'name':'fallback','email':'fallback@test'},'owner_accounts':{'fallback':'fallback@test'},'operator_role':'operator'}


class TransactionSafety(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.c=config(self.temp.name)
        self.issue={'id':'1','name':'bug','url':'https://github.com/a/b/issues/7','repository':'a/b','number':'7'}
        self.api=FakeAPI({'id':'1','name':'bug','description':'original body','state':'pending'})
        self.state=State(self.temp.name)
        self.p=Pipeline(self.c,self.api,self.state,lambda *x:self.fail('unexpected model call'))
        self.p.translator = lambda body: '翻译后的正文' if needs_translation(body) else body
    def test_dry_run_never_model_or_write(self):
        r=self.p.process(dict(self.issue),False)
        self.assertEqual(r['action'],'create'); self.assertEqual(self.api.writes,[]); self.assertEqual(self.api.created,0)
    def test_feature_never_model(self):
        self.issue['name']='Feature Request new'
        with self.assertRaises(TimeoutError): self.p.process(dict(self.issue),True)
        self.assertEqual(self.api.created,1)
    def test_uncertain_create_never_retries(self):
        self.issue['name']='Feature Request new'
        with self.assertRaises(TimeoutError): self.p.process(dict(self.issue),True)
        with self.assertRaisesRegex(RuntimeError,'uncertain'): self.p.process(dict(self.issue),True)
        self.assertEqual(self.api.created,1)
    def test_missing_body_never_completes_source(self):
        self.api.target={'id':'2','name':'bug','description':self.issue['url'],'bug-status':{'value':'changed-by-human'},'_attribute':{'work_item_type':{'key':'bug'}}}
        with self.assertRaisesRegex(RuntimeError,'original text'): self.p.process(dict(self.issue),True)
        self.assertEqual(self.api.writes,[])
    def test_empty_input_never_model_or_metadata(self):
        self.assertEqual(self.p.run(True)['scanned_pending'],0)

if __name__=='__main__': unittest.main()
