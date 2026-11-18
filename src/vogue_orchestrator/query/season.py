"""Season arithmetic for query structuring. Standard library only.

Dates are always computed here, in code. The LLM only reports whether the query mentions a
season and which one; it never guesses today's date (contract rule, contracts/agents/trend/input/trend-query.md).
"""

from __future__ import annotations

from datetime import date

SS = "SS"
FW = "FW"


def next_fashion_season(today: date) -> tuple[int, str]:
    """Season used when the query names none ("default_next").

    Months 3-8 -> FW of the same year, 9-12 -> SS of the next year, 1-2 -> SS of the same year.
    Same table as the contract snapshot, section 3 rule 5.
    """
    if 3 <= today.month <= 8:
        return today.year, FW
    if today.month >= 9:
        return today.year + 1, SS
    return today.year, SS


def resolve_season_year(season: str, today: date) -> int:
    """Year for a season the user named WITHOUT a year (for example "봄").

    ASSUMPTION A-1 (not defined by the contract, needs owner confirmation): pick the nearest
    occurrence of that season that is not earlier than the default next season. Examples:
    today 2026-11 (default 2027 SS): SS -> 2027, FW -> 2027. Today 2026-05 (default 2026 FW):
    FW -> 2026, SS -> 2027. Kept in one function so changing the rule touches nothing else.
    """
    if season not in (SS, FW):
        raise ValueError("season must be SS or FW")
    default_year, default_season = next_fashion_season(today)
    if season == default_season:
        return default_year
    if season == SS:  # default is FW, so the next SS starts a year later
        return default_year + 1
    return default_year  # default is SS, so FW of the same year comes next
