from datetime import UTC, datetime, timedelta

from autolp.providers import dexscreener, geckoterminal, jupiter
from tests.conftest import load_fixture

BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"


def test_jupiter_token_fields() -> None:
    tokens = {t.mint: t for t in jupiter.parse_tokens(load_fixture("jupiter_search.json"))}
    bonk = tokens[BONK]
    assert bonk.symbol == "Bonk" and bonk.decimals == 5
    assert bonk.price_usd and bonk.liquidity_usd and bonk.market_cap
    assert bonk.mint_authority_disabled is True and bonk.freeze_authority_disabled is True
    assert bonk.first_pool_created_at == datetime(2023, 5, 11, 17, 9, 10, tzinfo=UTC)
    assert bonk.updated_at is not None and bonk.updated_at.tzinfo is not None
    assert set(bonk.windows) == {"5m", "1h", "6h", "24h"}
    w = bonk.windows["24h"]
    assert w.num_traders == 4972
    assert w.volume_usd == w.buy_volume_usd + w.sell_volume_usd  # type: ignore[operator]


def test_jupiter_missing_fields_stay_none() -> None:
    t = jupiter.parse_token({"id": "m", "audit": {}, "stats1h": {"buyVolume": 5}})
    assert t.mint_authority_disabled is None  # unknown, never assumed safe
    assert t.top_holders_pct is None
    assert t.windows["1h"].volume_usd is None  # sell side missing -> total unknown
    assert "24h" not in t.windows


def test_jupiter_parse_skips_malformed_entries() -> None:
    assert [t.mint for t in jupiter.parse_tokens([{"id": "a"}, {"no_id": 1}, "x"])] == ["a"]


def test_jupiter_quote() -> None:
    q = jupiter.parse_quote(load_fixture("jupiter_quote.json"))
    assert q.in_amount_raw == 10_000_000_000 and q.out_amount_raw == 340462
    assert q.price_impact_pct is not None and abs(q.price_impact_pct - 0.07608) < 1e-4
    assert q.route_labels == ["Bonkswap", "BisonFi", "Kipseli"]


def test_dexscreener_pairs_filtered_to_requested_base_tokens() -> None:
    payload = load_fixture("dexscreener_tokens.json")
    pairs = dexscreener.parse_pairs(payload, {BONK})
    assert [p.token_mint for p in pairs] == [BONK]
    p = pairs[0]
    assert p.pool_address == "5zpyutJu9ee6jFymDGoK7F6S5Kczqtc9FomP3ueKuyA9"
    assert p.quote_symbol == "SOL" and p.reserve_quote and p.reserve_base
    assert p.buys["h24"] == 3500 and p.sells["m5"] == 7
    assert p.pair_created_at is not None and p.pair_created_at.year == 2022
    other_chain = [{**payload[0], "chainId": "base"}]
    assert dexscreener.parse_pairs(other_chain, {BONK}) == []


def test_gecko_candles_sorted_deduped_and_completeness() -> None:
    payload = load_fixture("geckoterminal_ohlcv.json")
    rows = payload["data"]["attributes"]["ohlcv_list"]
    payload["data"]["attributes"]["ohlcv_list"] = [rows[0], *rows, [rows[0][0], 9, 9, 9, 9, 9]]
    candles = geckoterminal.parse_ohlcv(payload)
    assert len(candles) == 3
    assert [c.bucket for c in candles] == sorted(c.bucket for c in candles)
    newest = candles[-1]
    assert newest.close == rows[0][4]  # the duplicate did not overwrite the first value
    assert not newest.is_complete(newest.bucket + timedelta(minutes=4))
    assert newest.is_complete(newest.bucket + timedelta(minutes=5))
    assert geckoterminal.parse_ohlcv({}) == []
