import unittest

from challenge.core import asymmetric_dhi, make_splits, metrics, parse_dialogue


Q = [0.10, 0.20, 0.25, 0.25, 0.15, 0.05]
W = [0.5, 0.3, 0.2]


class ParseTests(unittest.TestCase):
    def test_roles_and_prior_context_exclude_current_answer(self):
        result = parse_dialogue('Q:什么是模型？\nA:概念解释\nQ:为什么？\nA:这是L6高级创造')
        self.assertEqual(result['status'], 'format_ok_pending_review')
        self.assertEqual([x['question'] for x in result['turns']], ['什么是模型？', '为什么？'])
        self.assertEqual(result['turns'][0]['prior_context'], '')
        self.assertIn('概念解释', result['turns'][1]['prior_context'])
        self.assertNotIn('L6', result['turns'][1]['prior_context'])

    def test_missing_and_url_are_not_labels(self):
        self.assertEqual(parse_dialogue(None)['status'], 'missing')
        self.assertEqual(parse_dialogue('https://example.org/a.txt')['status'], 'url_only')

    def test_broken_alternation_requires_manual_review(self):
        result = parse_dialogue('Q:一\nQ:二\nA:答')
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['turns'], [])

    def test_math_survives_html_cleanup(self):
        result = parse_dialogue('<p>Q:证明 x &lt; y</p><p>A:说明</p>')
        self.assertEqual(result['turns'][0]['question'], '证明 x < y')

    def test_markers_inside_code_are_not_turns(self):
        result = parse_dialogue('Q:解释代码\nA:例子\n```text\nQ:示例\nA:示例回答\n```\nQ:如何改进\nA:讨论')
        self.assertEqual(len(result['turns']), 2)

    def test_prefix_content_is_not_silently_discarded(self):
        result = parse_dialogue('未知作者的前言\nQ:问题\nA:回答')
        self.assertEqual(result['status'], 'needs_review')

    def test_incomplete_final_question_is_reviewed(self):
        self.assertEqual(parse_dialogue('Q:问题')['status'], 'needs_review')


class MetricTests(unittest.TestCase):
    def test_rise_fall_and_cycle(self):
        for seq, expected in [([1, 6], 1.0), ([6, 1], 0.0), ([2, 4, 2], 0.5)]:
            with self.subTest(seq=seq):
                self.assertAlmostEqual(metrics(seq, [seq], Q, W)['ctq'], expected)

    def test_single_turn_is_missing_and_not_zero(self):
        result = metrics([4], [[4]], Q, W)
        self.assertIsNone(result['ctq'])
        self.assertIsNone(result['aiv'])
        self.assertAlmostEqual(result['aiv_upper'] - result['aiv_lower'], 30)

    def test_empty_student_has_no_score(self):
        result = metrics([], [], Q, W)
        self.assertIsNone(result['hot'])
        self.assertIsNone(result['aiv_lower'])

    def test_invalid_labels_and_weights_fail(self):
        for labels, weights in [([0], W), ([7], W), ([True], W), ([1], [-1, 1, 1]), ([1], [0.2, 0.2, 0.2])]:
            with self.subTest(labels=labels, weights=weights):
                with self.assertRaises(ValueError):
                    metrics(labels, [labels], Q, weights)

    def test_conversation_labels_must_reconcile(self):
        with self.assertRaises(ValueError):
            metrics([1, 6], [[1, 1]], Q, W)

    def test_asymmetric_dhi_reference_and_vertices(self):
        self.assertAlmostEqual(asymmetric_dhi(Q, Q, [1, 1, 1, 2, 2, 2], [2, 2, 2, 1, 1, 1]), 1)
        values = []
        for i in range(6):
            p = [int(j == i) for j in range(6)]
            values.append(asymmetric_dhi(p, Q, [1, 1, 1, 2, 2, 2], [2, 2, 2, 1, 1, 1]))
        self.assertAlmostEqual(min(values), 0)
        self.assertTrue(all(0 <= x <= 1 for x in values))


class SplitTests(unittest.TestCase):
    def test_students_and_long_duplicate_questions_cannot_cross_pools(self):
        rows = []
        for i in range(80):
            for j in range(8):
                question = ('共同重复问题需要认真分析其中的条件约束以及求解方法并且比较不同方案的优缺点' if i < 2 else f'学生{i}问题{j}')
                rows.append({'turn_id': f'{i}-{j}', 'student_key': str(i), 'term': 'fall' if i < 40 else 'spring', 'question': question})
        first = make_splits(rows)
        self.assertEqual(first, make_splits(rows))
        pools = first['pool_by_student']
        self.assertEqual(pools['0'], pools['1'])
        ids = [x for values in first['selected'].values() for x in values]
        self.assertEqual(len(ids), len(set(ids)))
        index = {x['turn_id']: x for x in rows}
        for pool, values in first['selected'].items():
            for turn_id in values:
                self.assertEqual(pools[index[turn_id]['student_key']], pool)


if __name__ == '__main__':
    unittest.main()
