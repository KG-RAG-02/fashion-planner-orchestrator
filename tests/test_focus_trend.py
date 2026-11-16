"""The planning sheet's focus Trend style follows the Draft's trend citations (trend-analysis-agent R-4: no order)."""
import unittest

from vogue_orchestrator.planning_sheet import draft_trend_citations, focus_stock, focus_trend


def result(draft):
    trends = [{'style': {'id': '990101-s0', 'name': '카우보이 스타일', 'article_id': '990101'}, 'items': []},
              {'style': {'id': '990107-s0', 'name': '윔지맥싱', 'article_id': '990107'}, 'items': []}]
    stock = {'products': [{'style_id': 'A'}, {'style_id': 'B'}],
             'trend_results': [{'style': {'id': '990101-s0'}, 'product_index': 0},
                               {'style': {'id': '990107-s0'}, 'product_index': 1}]}
    return {'sources': {'trend': {'raw_response': {'result': {'trends': trends}}},
                        'stock': {'raw_response': {'result': stock}},
                        'planning_draft': {'raw_response': {'result': {'draft': draft}}}}}


class FocusTrendTests(unittest.TestCase):
    def test_focus_is_the_style_the_draft_cites_most(self):
        draft = {'evidence_ids': ['trend:990101:1:a'] * 9,  # the full evidence list does not count
                 'opportunity': {'evidence_ids': ['trend:990101:3:b', 'trend:990107:24:c']},
                 'attributes': [{'name': 'detail', 'value': '자수', 'evidence_ids': ['trend:990107:24:c']}]}
        value = result(draft)
        self.assertEqual(draft_trend_citations(value), {'990101': 1, '990107': 2})
        self.assertEqual(focus_trend(value)['style']['name'], '윔지맥싱')
        self.assertEqual(focus_stock(value)[1]['style_id'], 'B')  # the stock card follows the focus style

    def test_first_style_without_citations_or_on_a_tie(self):
        self.assertEqual(focus_trend(result({}))['style']['name'], '카우보이 스타일')
        tie = {'opportunity': {'evidence_ids': ['trend:990101:3:b', 'trend:990107:24:c']}}
        self.assertEqual(focus_trend(result(tie))['style']['name'], '카우보이 스타일')


if __name__ == '__main__':
    unittest.main()
