from data.economic_calendar import get_economic_calendar_data


def get_high_impact_calendar(hours_ahead: int = 24, hours_behind: int = 2):
    """
    Get high-impact ("3-star") macro economic events (FOMC, CPI, NFP, PCE,
    ...) from the ForexFactory weekly calendar, filtered to a window around
    now.

    `hours_ahead`/`hours_behind` define the watch window (default: last 2h
    to next 24h). Use this to check if the market is currently near a
    high-impact macro event — these tend to drive outsized crypto futures
    volatility, so trading size/risk should be reduced around them.

    Returns the events inside the window, whether the window currently
    contains any high-impact event, and the next upcoming one beyond the
    window (with hours until it).
    """
    return get_economic_calendar_data(hours_ahead=hours_ahead, hours_behind=hours_behind)
