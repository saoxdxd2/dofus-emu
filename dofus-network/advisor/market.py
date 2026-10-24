"""Market signals from the client's own price cache.

Two different numbers live in the cache and they are not interchangeable:

* `average` is the server-wide average price for an item (all listings and
  sales the server includes in that statistic);
* `listing` prices are supply. A cheap listing is an offer, not revenue.

Buy signals therefore use the average as a reference and a listing as the
actual offer. Sell signals do the reverse: the average is the patient price,
the cheapest listing is what a quick sale has to beat. Nothing here places an
order; buying spends kamas with no undo, so the advisor stops at a signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

from .models import CharacterSnapshot, MarketListing, MarketSnapshot
from .repository import Repository


@dataclass
class MarketSignal:
    gid: int
    name: str
    level: Optional[int]
    type: Optional[str]
    average: Optional[float] = None
    listing: Optional[MarketListing] = None
    owned_quantity: int = 0
    discount: Optional[float] = None
    savings: Optional[float] = None
    buy_score: float = 0.0
    sell_score: float = 0.0
    quick_sale: Optional[int] = None
    patient_price: Optional[int] = None
    verdict: str = ""
    reason: str = ""
    sell_verdict: str = ""
    sell_reason: str = ""
    suspicious: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gid": self.gid,
            "name": self.name,
            "level": self.level,
            "type": self.type,
            "average": round(self.average, 1) if self.average is not None else None,
            "listing": ({"count": self.listing.count, "min": self.listing.min,
                         "median": self.listing.median, "max": self.listing.max,
                         "age_ms": self.listing.age_ms}
                        if self.listing else None),
            "owned_quantity": self.owned_quantity,
            "discount": round(self.discount, 3) if self.discount is not None else None,
            "savings": round(self.savings, 1) if self.savings is not None else None,
            "buy_score": round(self.buy_score, 2),
            "sell_score": round(self.sell_score, 2),
            "quick_sale": self.quick_sale,
            "patient_price": self.patient_price,
            "verdict": self.verdict,
            "reason": self.reason,
            "sell_verdict": self.sell_verdict,
            "sell_reason": self.sell_reason,
            "suspicious": self.suspicious,
        }


@dataclass
class MarketReport:
    average_count: int
    listing_count: int
    priced_equipment: int
    equipped_unknown: int
    average_age_ms: Optional[int]
    listing_age_ms: Optional[int]
    price_median: Optional[float]
    price_p25: Optional[float]
    price_p75: Optional[float]
    buy_signals: List[MarketSignal]
    sell_signals: List[MarketSignal]
    owned_liquidation_value: float
    owned_priced: int
    owned_unpriced: int
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "average_count": self.average_count,
            "listing_count": self.listing_count,
            "priced_equipment": self.priced_equipment,
            "equipped_unknown": self.equipped_unknown,
            "average_age_ms": self.average_age_ms,
            "listing_age_ms": self.listing_age_ms,
            "price_median": round(self.price_median, 1)
                            if self.price_median is not None else None,
            "price_p25": round(self.price_p25, 1) if self.price_p25 is not None else None,
            "price_p75": round(self.price_p75, 1) if self.price_p75 is not None else None,
            "buy_signals": [row.to_dict() for row in self.buy_signals],
            "sell_signals": [row.to_dict() for row in self.sell_signals],
            "owned_liquidation_value": round(self.owned_liquidation_value, 1),
            "owned_priced": self.owned_priced,
            "owned_unpriced": self.owned_unpriced,
            "warnings": self.warnings,
        }


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def analyze_market(
    repo: Repository,
    character: CharacterSnapshot,
    market: MarketSnapshot,
    min_price: float = 100.0,
    min_discount: float = 0.15,
    top: int = 25,
    sell_top: int = 25,
    stale_after_ms: int = 10 * 60 * 1000,
) -> MarketReport:
    warnings: List[str] = []
    owned = character.owned_gids
    if not market.prices:
        warnings.append(
            "No average prices were captured. Log in and run market.capture with "
            "refresh before trusting any buy or sell signal.")
    if market.request_error:
        warnings.append("Average-price request failed: %s" % market.request_error)
    if market.average_age_ms is None and market.prices:
        warnings.append("The capture did not report the age of the average-price table.")
    elif market.average_age_ms is not None and market.average_age_ms > stale_after_ms:
        warnings.append(
            "The average-price table is %.1f minutes old; the market may have moved."
            % (market.average_age_ms / 60000.0))
    if market.prices and not market.listings:
        warnings.append(
            "Only averages are cached. Averages cannot tell you whether a specific "
            "offer is cheap; scan the item category to get listings.")
    warnings.append(
        "Listings are supply, not revenue. A cheap listing is not a sale and a high "
        "average is not a guaranteed buyer; item rolls are not visible in the prices.")
    if character.level and market.prices:
        max_level = character.level + 10
        priced = sum(1 for gid in market.prices
                     if (repo.item(gid) is not None and repo.item(gid).level <= max_level))
        if priced == 0:
            warnings.append(
                "None of the cached average prices belong to known equipment at or "
                "below the character's level + 10; the market table may be partial.")

    signals: List[MarketSignal] = []
    for gid, average in market.prices.items():
        item = repo.item(gid)
        listing = market.listing(gid)
        signal = MarketSignal(
            gid=gid,
            name=item.name if item else "#%d" % gid,
            level=item.level if item else None,
            type=item.type if item else None,
            average=average,
            listing=listing,
            owned_quantity=owned.get(gid, 0),
        )
        if listing is not None and listing.min > 0:
            signal.discount = 1.0 - (listing.min / average)
            signal.savings = average - listing.min
            signal.suspicious = listing.min <= average * 0.05
            if signal.discount >= min_discount and average >= min_price:
                signal.buy_score = max(0.0, signal.discount) * (1.0 + min(3.0, average / 100000.0))
                signal.verdict = "buy"
                signal.reason = (
                    "cheapest listing %d is %.0f%% below the %.0f average"
                    % (listing.min, signal.discount * 100.0, average))
            elif signal.discount >= 0.05:
                signal.verdict = "watch"
                signal.reason = (
                    "listing is %.0f%% below average, below the %.0f%% buy threshold"
                    % (signal.discount * 100.0, min_discount * 100.0))
        if signal.owned_quantity > 0:
            signal.patient_price = int(round(average))
            if listing is not None and listing.min > 0:
                signal.quick_sale = max(1, listing.min - 1)
                signal.sell_score = average / max(1.0, listing.min)
                if signal.sell_score >= 1.2:
                    signal.sell_verdict = "sell_high"
                    signal.sell_reason = (
                        "average %.0f is %.0f%% above the cheapest listing; quick "
                        "sale would undercut at %d, patient price is about the average"
                        % (average, (signal.sell_score - 1.0) * 100.0,
                           signal.quick_sale))
                elif signal.sell_score < 0.9:
                    signal.sell_verdict = "hold"
                    signal.sell_reason = (
                        "cheapest listing is below the average; listing now would "
                        "undercut a weak market")
                else:
                    signal.sell_verdict = "list_at_average"
                    signal.sell_reason = "market is close to the average; list near %.0f" % average
            else:
                signal.sell_score = 1.0
                signal.sell_verdict = "sell_no_supply"
                signal.sell_reason = "no listing cached; average is the only reference"
        signals.append(signal)

    price_values = list(market.prices.values())
    price_median = median(price_values) if price_values else None
    equipment = [signal for signal in signals
                 if signal.level is not None or signal.type is not None]
    buy_signals = [signal for signal in signals if signal.verdict == "buy"]
    buy_signals.sort(key=lambda row: (-row.savings if row.savings is not None else 0,
                                      row.gid))
    sell_signals = [signal for signal in signals
                    if signal.owned_quantity > 0 and signal.sell_verdict]
    sell_signals.sort(key=lambda row: (-row.sell_score, -row.owned_quantity, row.gid))

    owned_priced = 0
    owned_unpriced = 0
    liquidation = 0.0
    for gid, quantity in owned.items():
        average = market.average(gid)
        if average is None:
            owned_unpriced += 1
            continue
        owned_priced += 1
        liquidation += average * max(1, quantity)

    equipped_unknown = sum(1 for item in character.equipment
                           if repo.item(item.gid) is None)
    if owned_unpriced:
        warnings.append(
            "%d owned item(s) have no cached average; their liquidation value is "
            "missing from this report." % owned_unpriced)

    return MarketReport(
        average_count=len(market.prices),
        listing_count=len(market.listings),
        priced_equipment=len(equipment),
        equipped_unknown=equipped_unknown,
        average_age_ms=market.average_age_ms,
        listing_age_ms=min((row.age_ms for row in market.listings.values()
                            if row.age_ms is not None), default=None),
        price_median=price_median,
        price_p25=_percentile(price_values, 0.25) if price_values else None,
        price_p75=_percentile(price_values, 0.75) if price_values else None,
        buy_signals=buy_signals[:top],
        sell_signals=sell_signals[:sell_top],
        owned_liquidation_value=liquidation,
        owned_priced=owned_priced,
        owned_unpriced=owned_unpriced,
        warnings=warnings,
    )
