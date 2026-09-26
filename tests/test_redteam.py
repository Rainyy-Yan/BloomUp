import json
import unittest

from challenge.redteam import run_attacks


class RedTeamTests(unittest.TestCase):
    def test_all_eight_reported_without_hiding_failures_or_model_na(self):
        out=run_attacks()
        self.assertEqual([r['attack_id'] for r in out['rows']],[f'R{i:02}' for i in range(1,9)])
        self.assertEqual(next(r for r in out['rows'] if r['attack_id']=='R02')['pass_fail_na'],'NA')
        self.assertEqual(next(r for r in out['rows'] if r['attack_id']=='R04')['pass_fail_na'],'FAIL')
        self.assertEqual(next(r for r in out['rows'] if r['attack_id']=='R06')['pass_fail_na'],'FAIL')
        text=json.dumps(out,ensure_ascii=False)
        for private in ('student_key','turn_id','question','SYNTHETIC-'):
            self.assertNotIn(private,text)
        self.assertTrue(out['synthetic'])
        self.assertFalse(out['real_model_robustness_verified'])


if __name__=='__main__':unittest.main()
