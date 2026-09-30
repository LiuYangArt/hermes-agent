import unittest

from owner_resolver import parse_decision


class DecisionValidationTests(unittest.TestCase):
    def test_valid_decision(self):
        text = '{"name":" 77 ","rule":"无明确匹配","reason":"信息不足","owner_decision_reason":"insufficient_evidence","candidate_owners":[],"missing_evidence":["发生阶段"]}'
        self.assertEqual(parse_decision(text)["name"], "77")

    def test_rejects_missing_or_added_fields(self):
        for text in ('{}', '{"name":"77","rule":"R1","reason":"x","command":"x"}'):
            with self.assertRaises(ValueError):
                parse_decision(text)

    def test_rejects_non_string_and_empty_values(self):
        for value in ('null', '7', '""'):
            with self.assertRaises(ValueError):
                parse_decision('{"name":' + value + ',"rule":"R1","reason":"x"}')

    def test_rejects_contradictory_or_bad_lists(self):
        invalid = [
            '{"name":"77","rule":"R1","reason":"x","owner_decision_reason":"matched","candidate_owners":[],"missing_evidence":[]}',
            '{"name":"77","rule":"无明确匹配","reason":"x","owner_decision_reason":"insufficient_evidence","candidate_owners":[],"missing_evidence":[]}',
            '{"name":"77","rule":"无明确匹配","reason":"x","owner_decision_reason":"insufficient_evidence","candidate_owners":"甲","missing_evidence":["阶段"]}',
        ]
        for text in invalid:
            with self.assertRaises(ValueError):
                parse_decision(text)


if __name__ == "__main__":
    unittest.main()
