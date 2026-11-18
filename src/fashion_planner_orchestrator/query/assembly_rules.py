"""Shared normalization rules for the current StructuredQuery assembler."""

from . import vocabulary as vocab
from .config import CONFIG

WARN_SEASON_YEAR_INFERRED = "season_year_inferred"
WARN_RAW_NOT_IN_QUERY = "raw_not_in_query"
WARN_NO_CONDITIONS_EXTRACTED = "no_conditions_extracted"

UNSUPPORTED_REASONS = {
    kind: text.format(major_count=len(vocab.CATEGORY_MAJOR), minor_count=len(vocab.CATEGORY_MINOR))
    for kind, text in CONFIG.unsupported_reasons.items()
}
GENERIC_UNSUPPORTED_REASON = CONFIG.generic_unsupported_reason
