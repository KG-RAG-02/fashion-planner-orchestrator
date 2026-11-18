"""The planning sheet's focus Trend style.

new-product-agent's Draft names the style its plan is built on (draft.trend_basis.source_style_id); without that
id the focus follows the Draft's trend citations (trend-analysis-agent R-4: no order). Every reader of the focus
style (poster, stock card, trend photos, SWOT input) follows the same choice.
"""
import unittest

from fashion_planner_orchestrator.planning_sheet import (build_sheet, draft_trend_basis_id, draft_trend_citations, focus_stock,
                                               focus_trend, trend_image_urls)
from fashion_planner_orchestrator.swot import swot_input


def style(style_id, name, article_id):
    return {'style': {'id': style_id, 'name': name, 'article_id': article_id,
                      'example_images': [{'url': f'https://img.vogue.co.kr/{style_id}.jpg'}]}, 'items': []}


def result(draft, trends=None):
    trends = trends or [style('990101-s0', '카우보이 스타일', '990101'), style('990107-s0', '윔지맥싱', '990107')]
    # A stock-agent slot names its style by id and name (stock-agent-result-1.0: trend_result.style requires both),
    # so focus_stock's name match runs here as it does on recorded runs.
    stock = {'products': [{'style_id': 'ABCDEFGH'[index]} for index in range(len(trends))],
             'trend_results': [{'style': {'id': trend['style']['id'], 'name': trend['style']['name']},
                                'product_index': index} for index, trend in enumerate(trends)]}
    return {'sources': {'trend': {'raw_response': {'result': {'trends': trends}}},
                        'stock': {'raw_response': {'result': stock}},
                        'planning_draft': {'raw_response': {'result': {'draft': draft}}}}}


def basis(style_id, name='카우보이 스타일'):
    """draft.trend_basis as new-product-agent records it (P06 25a4ff3; only source_style_id is read here)."""
    return {'source_style_id': style_id, 'name': name, 'selection_rule': 'most_evidence_linked_design_attributes/1',
            'evidence_ids': [], 'design_attributes': [], 'candidates_considered': 2}


# Claims citing 윔지맥싱 (990107) more often than 카우보이 스타일 (990101): the citation rule alone picks 윔지맥싱.
CITES_990107 = {'opportunity': {'evidence_ids': ['trend:990101:3:b', 'trend:990107:24:c']},
                'attributes': [{'name': 'detail', 'value': '자수', 'evidence_ids': ['trend:990107:24:c']}]}


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


class TrendBasisTests(unittest.TestCase):
    """draft.trend_basis (P06 selects one primary trend; user decision 2026-11-17)."""

    def test_focus_is_the_style_the_draft_names_as_its_basis(self):
        value = result({**CITES_990107, 'trend_basis': basis('990101-s0')})
        self.assertEqual(draft_trend_citations(value), {'990101': 1, '990107': 2})  # the citation rule would pick 윔지맥싱
        self.assertEqual(draft_trend_basis_id(value), '990101-s0')
        self.assertEqual(focus_trend(value)['style']['name'], '카우보이 스타일')

    def test_every_reader_of_the_focus_style_follows_the_basis(self):
        value = result({**CITES_990107, 'trend_basis': basis('990101-s0')})
        self.assertEqual(build_sheet({'result': value}).trend.name, '카우보이 스타일')  # poster Analysis column
        self.assertEqual(focus_stock(value)[1]['style_id'], 'A')  # stock card
        self.assertEqual(trend_image_urls(value), ('https://img.vogue.co.kr/990101-s0.jpg',))  # stage-06 photos
        self.assertEqual(swot_input({'result': value})['trend']['name'], '카우보이 스타일')  # SWOT input

    def test_the_basis_selects_a_style_not_only_its_article(self):
        # Two styles of one article: the citation rule counts articles and keeps the first of them.
        trends = [style('990101-s0', '카우보이 스타일', '990101'), style('990101-s1', '프린지 재킷', '990101')]
        self.assertEqual(focus_trend(result({}, trends))['style']['id'], '990101-s0')
        value = result({'trend_basis': basis('990101-s1', '프린지 재킷')}, trends)
        self.assertEqual(focus_trend(value)['style']['name'], '프린지 재킷')
        self.assertEqual(focus_stock(value)[1]['style_id'], 'B')

    def test_the_stock_card_is_the_slot_of_the_focus_style_when_two_styles_share_a_name(self):
        # Recorded skirt runs: two '스커트' styles (877475-s0, 882345-s0) whose stock slots both match the focus
        # style's name; the focus style's own slot is the card, whichever of the two the basis names.
        trends = [style('877475-s0', '스커트', '877475'), style('882345-s0', '스커트', '882345')]
        self.assertEqual([slot['style'] for slot in result({}, trends)['sources']['stock']['raw_response']['result']
                          ['trend_results']], [{'id': '877475-s0', 'name': '스커트'}, {'id': '882345-s0', 'name': '스커트'}])
        for style_id, product_id in (('882345-s0', 'B'), ('877475-s0', 'A')):
            with self.subTest(basis=style_id):
                value = result({'trend_basis': basis(style_id, '스커트')}, trends)
                self.assertEqual(focus_trend(value)['style']['id'], style_id)
                slot, product = focus_stock(value)
                self.assertEqual((slot['style']['id'], product['style_id']), (style_id, product_id))

    def test_without_a_usable_basis_the_citation_rule_stays(self):
        # Absent, malformed, empty, non-string, or naming no style of the stored Trend result.
        for trend_basis in (None, 'not a mapping', {}, basis(None), basis(''), basis(990101), basis('999999-s0')):
            with self.subTest(trend_basis=trend_basis):
                draft = dict(CITES_990107) if trend_basis is None else {**CITES_990107, 'trend_basis': trend_basis}
                value = result(draft)
                self.assertEqual(focus_trend(value)['style']['name'], '윔지맥싱')
                self.assertEqual(focus_stock(value)[1]['style_id'], 'B')
        self.assertIsNone(draft_trend_basis_id(result({'trend_basis': basis('')})))
        self.assertEqual(draft_trend_basis_id(result({'trend_basis': basis('999999-s0')})), '999999-s0')

    def test_no_trend_result_means_no_focus_even_with_a_basis(self):
        value = result({'trend_basis': basis('990101-s0')})
        value['sources']['trend'] = None
        self.assertEqual(focus_trend(value), {})
        self.assertIsNone(build_sheet({'result': value}).trend)


if __name__ == '__main__':
    unittest.main()
