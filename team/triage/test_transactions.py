import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from triage import Pipeline, State, CLI, cli_environment, ids
from test_triage import config


class MemoryAPI:
    def __init__(self, kind="bug"):
        self.kind = kind
        self.source = {"id": "1", "name": "Feature Request x" if kind == "feature" else "bug", "description": "Complete original body", "state": "pending"}
        self.target = None
        self.created = 0
        self.updates = []
        self.fail_source = False

    def get(self, item_id, keys):
        return copy.deepcopy(self.source if str(item_id) == "1" else self.target)

    def query(self, typ, keys, where=""):
        if self.target:
            yield dict(copy.deepcopy(self.target), work_item_id="2")

    def user(self, email):
        return "pm"

    def call(self, domain, action, **params):
        assert action == "create"
        self.created += 1
        values = {}
        for field in params["fields"]:
            value = field["field_value"]
            if field["field_key"] in ("role_owners", "sources"):
                value = json.loads(value)
            values[field["field_key"]] = value
        roles = [{"key": r["role"], "members": [{"key": u} for u in r["owners"]]} for r in values.pop("role_owners", [])]
        self.target = dict(values, id="2", watchers=[{"key": "existing-default"}], followers=[{"key": "another-default"}], _attribute={"work_item_type": {"key": self.kind}, "role_members": roles})
        return {"work_item_id": 2}

    def update(self, item_id, values):
        if str(item_id) == "1" and self.fail_source:
            raise RuntimeError("source write failed")
        self.updates.append((str(item_id), copy.deepcopy(values)))
        (self.source if str(item_id) == "1" else self.target).update(copy.deepcopy(values))
        return {}


class Transactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = config(self.temp.name)
        self.c["project_url"] = "https://project.example/p"
        self.state = State(self.temp.name)

    def pipeline(self, api):
        p = Pipeline(self.c, api, self.state, lambda *args: self.fail("unneeded model call"))
        p.owner = lambda issue: {"name": "PM", "rule": "unknown", "reason": "insufficient evidence",
                                 "owner_decision_reason": "insufficient_evidence", "candidate_owners": [],
                                 "missing_evidence": ["定位信息"], "user_key": "pm"}
        return p

    def issue(self, api):
        return {"id": "1", "name": api.source["name"], "url": "https://github.com/a/b/issues/7", "repository": "a/b", "number": "7"}

    def test_bug_defaults_merge_existing_watchers_and_readback(self):
        api = MemoryAPI()
        result = self.pipeline(api).process(self.issue(api), True)
        self.assertEqual(result["action"], "verified")
        self.assertEqual(api.target["watchers"], ["existing-default", "pm"])
        self.assertEqual(api.target["followers"], ["another-default", "pm"])
        self.assertEqual(api.target["bug-status"], "verify")
        self.assertEqual(api.source["state"], "done-bug")
        self.assertIn("**AI 分诊提示（未核实）**", api.target["description"])
        self.assertIn("Complete original body", api.target["description"])

    def test_source_failure_recovers_without_duplicate_create(self):
        api = MemoryAPI()
        p = self.pipeline(api)
        api.fail_source = True
        with self.assertRaisesRegex(RuntimeError, "source write"):
            p.process(self.issue(api), True)
        self.assertEqual(self.state.data["items"]["a/b#7"]["target"], "2")
        api.fail_source = False
        p.process(self.issue(api), True)
        self.assertEqual(api.created, 1)
        self.assertEqual(api.source["state"], "done-bug")
        self.assertEqual(api.target["description"].count("**AI 分诊提示（未核实）**"), 1)

    def test_missing_ai_appendix_readback_does_not_complete_source(self):
        class DroppingDescriptionAPI(MemoryAPI):
            def get(self, item_id, keys):
                value = super().get(item_id, keys)
                if str(item_id) != "1" and value:
                    value["description"] = value["description"].split("\n\n**AI 分诊提示（未核实）**", 1)[0]
                return value
            def update(self, item_id, values):
                values = {key: value for key, value in values.items() if key != "description"}
                return super().update(item_id, values)
        api = DroppingDescriptionAPI()
        with self.assertRaisesRegex(RuntimeError, "AI triage section missing"):
            self.pipeline(api).process(self.issue(api), True)
        self.assertEqual(api.source["state"], "pending")

    def test_feature_keeps_server_default_follower(self):
        api = MemoryAPI("feature")
        p = self.pipeline(api)
        p.owner = lambda issue: self.fail("feature must not resolve an owner")
        p.process(self.issue(api), True)
        self.assertEqual(api.target["watchers"], [{"key": "existing-default"}])
        self.assertFalse(any("watchers" in values or "role_owners" in values for _, values in api.updates))
        self.assertEqual(api.source["state"], "done-feature")

    def test_reused_target_keeps_human_status_and_owner(self):
        api = MemoryAPI()
        api.target = {"id": "2", "name": "bug", "description": "https://github.com/a/b/issues/7\n\nComplete original body", "bug-status": "human-done", "watchers": [{"key": "human"}], "_attribute": {"work_item_type": {"key": "bug"}, "role_members": []}}
        p = self.pipeline(api)
        p.owner = lambda issue: self.fail("existing target must not resolve owner")
        p.process(self.issue(api), True)
        self.assertEqual(api.created, 0)
        self.assertEqual(api.target["bug-status"], "human-done")
        self.assertFalse(any(item_id == "2" for item_id, _ in api.updates))

    def test_uncertain_creation_found_remotely_finishes_defaults(self):
        api = MemoryAPI()
        api.target = {"id": "2", "name": "bug", "description": "https://github.com/a/b/issues/7\n\nComplete original body", "bug-status": "verify", "watchers": [], "followers": [], "_attribute": {"work_item_type": {"key": "bug"}, "role_members": [{"key": "operator", "members": [{"key": "pm"}]}]}}
        self.state.data['items']['a/b#7'] = {'phase': 'creating', 'kind': 'bug', 'source': '1', 'decision': {'name': 'PM', 'user_key': 'pm', 'rule': 'unknown', 'reason': 'insufficient evidence'}}
        self.pipeline(api).process(self.issue(api), True)
        self.assertEqual(api.created, 0)
        self.assertEqual(api.target['watchers'], ['pm'])
        self.assertEqual(self.state.data['items']['a/b#7']['phase'], 'done')

    def test_owner_cache_and_document_change(self):
        api = MemoryAPI()
        self.c['document_command'] = ['read-document']
        calls = []
        def resolver(issue, document):
            calls.append(document)
            return {'name': 'fallback', 'rule': 'rule', 'reason': 'evidence', 'owner_decision_reason': 'matched', 'candidate_owners': [], 'missing_evidence': []}
        def response(body):
            return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'data': {'document': {'content': body}}}))
        with patch('triage.subprocess.run', return_value=response('version-one')):
            Pipeline(self.c, api, self.state, resolver).owner(self.issue(api))
            second = Pipeline(self.c, api, self.state, resolver)
            second.owner(self.issue(api))
            self.assertEqual(second.cache_hits, 1)
        with patch('triage.subprocess.run', return_value=response('version-two')):
            Pipeline(self.c, api, self.state, resolver).owner(self.issue(api))
        self.assertEqual(calls, ['version-one', 'version-two'])

    def test_document_failure_is_classified(self):
        api = MemoryAPI()
        self.c['document_command'] = ['read-document']
        failed = SimpleNamespace(returncode=1, stdout='')
        with patch('triage.subprocess.run', return_value=failed):
            decision = Pipeline(self.c, api, self.state, lambda *_: self.fail('model called')).owner(self.issue(api))
        self.assertEqual(decision['owner_decision_reason'], 'document_unavailable')

    def test_missing_owner_account_is_classified(self):
        api = MemoryAPI()
        api.user = lambda email: 'pm' if email == self.c['fallback_user']['email'] else None
        self.c['document_command'] = ['read-document']
        decision = {'name': 'Missing', 'rule': 'R1', 'reason': 'matched', 'owner_decision_reason': 'matched',
                    'candidate_owners': [], 'missing_evidence': []}
        response = SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'data': {'document': {'content': 'rules'}}}))
        with patch('triage.subprocess.run', return_value=response):
            result = Pipeline(self.c, api, self.state, lambda *_: decision).owner(self.issue(api))
        self.assertEqual(result['owner_decision_reason'], 'account_unavailable')


class Pagination(unittest.TestCase):
    def test_meegle_relation_readback_uses_id(self):
        self.assertEqual(ids([{'id': 1497, 'name': 'issue'}]), ['1497'])

    def test_cron_cli_uses_persistent_login_profile(self):
        with patch.dict('os.environ', {'HOME': '/opt/data/home', 'HERMES_HOME': '/opt/data'}):
            self.assertEqual(cli_environment()['HOME'], '/opt/data')

    def test_fetches_all_pages(self):
        api = CLI({"project_key": "p"})
        calls = []
        def call(domain, action, **params):
            calls.append(params["mql"])
            start, stop = (0, 50) if len(calls) == 1 else (50, 53)
            return {"data": {"1": [{"moql_field_list": [{"key": "work_item_id", "value_type": "long_value", "value": {"long_value": n}}]} for n in range(start, stop)]}}
        api.call = call
        self.assertEqual(len(list(api.query("bug", []))), 53)
        self.assertIn("LIMIT 50, 50", calls[1])


if __name__ == "__main__":
    unittest.main()
