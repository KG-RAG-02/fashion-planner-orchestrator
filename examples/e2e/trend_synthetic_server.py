"""Run Trend's real v0.1 HTTP handler with a test-only synthetic searcher."""

from http.server import ThreadingHTTPServer

from trend_agent.api.server import build_handler
from trend_agent.schemas.trend_analysis_result import TrendAnalysisResult


class SyntheticSearcher:
    def search(self, query):
        return TrendAnalysisResult(
            request_id=query.request_id,
            status="no_results",
            limitations=["Synthetic E2E only; no Trend source or Neo4j was queried."],
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8768), build_handler(SyntheticSearcher())).serve_forever()
