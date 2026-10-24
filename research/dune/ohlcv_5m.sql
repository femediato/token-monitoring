-- Phase 4a spike (docs/RESEARCH.md): 5-minute OHLCV from every DEX trade of a token list.
-- Save this as a Dune query with three text parameters:
--   mints  comma-separated token mints
--   start  e.g. 2026-09-24 00:00:00
--   end    e.g. 2026-10-24 00:00:00
-- Price per trade = USD value / token amount on whichever side the token is.
-- Trades under $1 are dropped (dust and rounding).
WITH mints AS (
    SELECT trim(x) AS mint FROM UNNEST(split('{{mints}}', ',')) AS u(x)
),
legs AS (
    SELECT t.block_time, m.mint, t.amount_usd, t.amount_usd / t.token_bought_amount AS price
    FROM dex_solana.trades t JOIN mints m ON t.token_bought_mint_address = m.mint
    WHERE t.block_time >= TIMESTAMP '{{start}}' AND t.block_time < TIMESTAMP '{{end}}'
      AND t.block_month >= date_trunc('month', TIMESTAMP '{{start}}')
      AND t.amount_usd >= 1 AND t.token_bought_amount > 0
    UNION ALL
    SELECT t.block_time, m.mint, t.amount_usd, t.amount_usd / t.token_sold_amount AS price
    FROM dex_solana.trades t JOIN mints m ON t.token_sold_mint_address = m.mint
    WHERE t.block_time >= TIMESTAMP '{{start}}' AND t.block_time < TIMESTAMP '{{end}}'
      AND t.block_month >= date_trunc('month', TIMESTAMP '{{start}}')
      AND t.amount_usd >= 1 AND t.token_sold_amount > 0
)
SELECT
    mint,
    from_unixtime(floor(to_unixtime(block_time) / 300) * 300) AS bucket,
    min_by(price, block_time) AS open,
    max(price) AS high,
    min(price) AS low,
    max_by(price, block_time) AS close,
    sum(amount_usd) AS volume_usd,
    count(*) AS trades
FROM legs
GROUP BY 1, 2
ORDER BY 1, 2
