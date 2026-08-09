from data.deribit import get_option_flow_data


def get_option_flow(
    currency: str = "BTC",
    max_days_to_expiry: int = 45,
    strike_range_pct: float = 0.3,
):
    """
    Get an options GEX/DEX (gamma/delta exposure) profile for a crypto
    currency, sourced from Deribit's option chain.

    `currency` is the underlying, e.g. "BTC" or "ETH" (Deribit only lists
    options for a handful of majors). `max_days_to_expiry` bounds the option
    chain to near-term expiries (default 45 days). `strike_range_pct` bounds
    strikes to within this fraction of the current spot price (default 0.3
    = +/-30%).

    Returns per-strike open interest, GEX, and DEX, plus chain-wide totals,
    put/call OI ratio, max pain, and the zero-gamma level. Returns None if
    no option data could be fetched (e.g. unsupported currency).
    """
    return get_option_flow_data(
        currency=currency,
        max_days_to_expiry=max_days_to_expiry,
        strike_range_pct=strike_range_pct,
    )
