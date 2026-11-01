# Chartink Pre-Breakout & Pre-Breakdown Scans

This document contains the textual queries for finding stocks that are experiencing volatility contraction (NR4/NR7 + Inside Bar) right before a major move. These queries are designed to be pasted into the **Magic Filter** on [Chartink](https://chartink.com/screener/new).

**How to use:**
1. Go to Chartink Screener.
2. Find the "Magic Filter" text box.
3. Select **Replace**.
4. Paste the desired query below and click **Generate**.
5. Run the scan!

---

## 1. Bullish (Pre-Breakout / Uptrend)

Use these scans to find stocks in a strong uptrend that are resting just below a recent high.
* **When to Run:** **End of Day (3:15 PM - 3:30 PM) or after market close.** You need the daily candle to finish forming to confirm the NR4/NR7 Inside Bar setup. Run this to build your watchlist for tomorrow.

### STRICT VERSION (The "Perfect" Setup)
* **What it looks for:** Narrowest range in **7 days** (NR7), hovering within **5%** of the 20-day high, hasn't spiked more than **5%** in the last week.
```text
( {cash} (
latest high - latest low <= 1 day ago high - 1 day ago low and
latest high - latest low <= 2 days ago high - 2 days ago low and
latest high - latest low <= 3 days ago high - 3 days ago low and
latest high - latest low <= 4 days ago high - 4 days ago low and
latest high - latest low <= 5 days ago high - 5 days ago low and
latest high - latest low <= 6 days ago high - 6 days ago low and

latest high < 1 day ago high and
latest low > 1 day ago low and

1 day ago high - 1 day ago low <= 2 * 10 days ago sma( high - low , 10 ) and
latest close <= 1.05 * 5 days ago close and

latest close >= 0.95 * latest max( 20, high ) and
latest close > latest sma( close, 50 ) and
latest sma( close, 10 ) > latest sma( close, 50 ) and
latest sma( close, 50 ) > latest sma( close, 200 ) and
latest volume < latest sma( volume, 20 ) and
latest volume > 50000 and
latest close > 50
) )
```

### RELAXED VERSION (More Results)
* **What it looks for:** Narrowest range in **4 days** (NR4), hovering within **10%** of the 20-day high, hasn't spiked more than **10%** in the last week.
```text
( {cash} (
latest high - latest low <= 1 day ago high - 1 day ago low and
latest high - latest low <= 2 days ago high - 2 days ago low and
latest high - latest low <= 3 days ago high - 3 days ago low and

latest high < 1 day ago high and
latest low > 1 day ago low and

1 day ago high - 1 day ago low <= 2 * 10 days ago sma( high - low , 10 ) and
latest close <= 1.10 * 5 days ago close and

latest close >= 0.90 * latest max( 20, high ) and
latest close > latest sma( close, 50 ) and
latest sma( close, 10 ) > latest sma( close, 50 ) and
latest sma( close, 50 ) > latest sma( close, 200 ) and
latest volume < latest sma( volume, 20 ) and
latest volume > 50000 and
latest close > 50
) )
```

### 📋 How to Trade (Bullish Pre-Breakout Setup)

> [!IMPORTANT]
> This scan only **prepares** you. Do NOT enter the trade when you find this stock. Wait for the actual breakout trigger next day.

**Step 1 — After market close (EOD): Review the chart**
- Open the stock chart. Confirm the tiny inside candle looks like a squeeze with low volume.
- Confirm the broader trend: 10 SMA > 50 SMA > 200 SMA. Ensure Nifty is also in uptrend.

**Step 2 — Before 9:15 AM next morning: Place a Buy Stop Order**
- **Entry:** Place a Buy Stop Limit order at `Yesterday's High + 0.10`
- **Stop Loss:** `Yesterday's Low - 0.10` (the bottom of the tiny candle)
- **Target:** Minimum `1:2 Risk-Reward`. If your risk is ₹5, your target should be ₹10 away.
  - Example: Entry ₹305.50, SL ₹300.90 → Risk = ₹4.60 → Target = ₹314.70

**Step 3 — Trade Management**
- If the stock triggers your Buy Stop and rises, trail your SL to the previous day's low.
- If within 2 days the stock does NOT cross your entry trigger, **cancel the order**. The setup has gone stale.
- **Never average down.** If SL is hit, respect it and move on.

---

## 2. Bearish (Pre-Breakdown / Downtrend)

Use these scans to find stocks in a strong downtrend that are resting just above a major support level, setting up for a short sell.
* **When to Run:** **End of Day (3:15 PM - 3:30 PM) or after market close.** You need the daily candle to finish forming to confirm the setup. Run this to build your watchlist for tomorrow.

### STRICT VERSION (The "Perfect" Setup)
* **What it looks for:** Narrowest range in **7 days** (NR7), hovering within **5%** of the 20-day low, hasn't crashed more than **5%** in the last week.
```text
( {cash} (
latest high - latest low <= 1 day ago high - 1 day ago low and
latest high - latest low <= 2 days ago high - 2 days ago low and
latest high - latest low <= 3 days ago high - 3 days ago low and
latest high - latest low <= 4 days ago high - 4 days ago low and
latest high - latest low <= 5 days ago high - 5 days ago low and
latest high - latest low <= 6 days ago high - 6 days ago low and

latest high < 1 day ago high and
latest low > 1 day ago low and

1 day ago high - 1 day ago low <= 2 * 10 days ago sma( high - low , 10 ) and
latest close >= 0.95 * 5 days ago close and

latest close <= 1.05 * latest min( 20, low ) and

latest close < latest sma( close, 50 ) and
latest sma( close, 10 ) < latest sma( close, 50 ) and
latest sma( close, 50 ) < latest sma( close, 200 ) and
latest volume < latest sma( volume, 20 ) and
latest volume > 50000 and
latest close > 50
) )
```

### RELAXED VERSION (More Results)
* **What it looks for:** Narrowest range in **4 days** (NR4), hovering within **10%** of the 20-day low, hasn't crashed more than **10%** in the last week.
```text
( {cash} (
latest high - latest low <= 1 day ago high - 1 day ago low and
latest high - latest low <= 2 days ago high - 2 days ago low and
latest high - latest low <= 3 days ago high - 3 days ago low and
latest high < 1 day ago high and
latest low > 1 day ago low and
1 day ago high - 1 day ago low <= 2 * 10 days ago sma( high - low , 10 ) and
latest close >= 0.90 * 5 days ago close and
latest close <= 1.10 * latest min( 20, low ) and
latest close < latest sma( close, 50 ) and
latest sma( close, 10 ) < latest sma( close, 50 ) and
latest sma( close, 50 ) < latest sma( close, 200 ) and
latest volume < latest sma( volume, 20 ) and
latest volume > 50000 and
latest close > 50
) )
```

### 📋 How to Trade (Bearish Pre-Breakdown Setup)

> [!IMPORTANT]
> This scan only **prepares** you. Do NOT short the stock immediately. Wait for the actual breakdown trigger next day.

**Step 1 — After market close (EOD): Review the chart**
- Open the stock chart. Confirm the tiny inside candle is sitting just above a major support level.
- Confirm the broader trend: 10 SMA < 50 SMA < 200 SMA. Ensure Nifty is also in downtrend.

**Step 2 — Before 9:15 AM next morning: Place a Sell Stop Order**
- **Entry (Short):** Place a Sell Stop Limit order at `Yesterday's Low - 0.10`
- **Stop Loss:** `Yesterday's High + 0.10` (the top of the tiny candle)
- **Target:** Minimum `1:2 Risk-Reward`. If your risk is ₹5, your target should be ₹10 lower.
  - Example: Entry ₹298.90, SL ₹303.60 → Risk = ₹4.70 → Target = ₹289.50

**Step 3 — Trade Management**
- If the stock breaks down, trail your SL to the previous day's high.
- If within 2 days the stock does NOT cross your entry trigger, **cancel the order**. The setup has gone stale.
- **This is a shorting strategy.** Suitable for Futures, Options (Buy Puts), or BTST short trades.

---

## 3. Bullish Breakout (Actual Trigger)

Use this scan to find stocks that were in a tight NR7/Inside Bar contraction **yesterday**, and have strongly broken out **today** with volume.
* **Use this for:** Finding stocks that are actively breaking out right now (or just broke out today).
* **When to Run:** **Periodically during the trading day** (e.g., 09:30 AM, 11:00 AM, 1:00 PM, 2:30 PM, 3:15 PM). You are looking for a live breakout crossing yesterday's high.

```text
( {cash} (
1 day ago high - 1 day ago low <= 2 days ago high - 2 days ago low and
1 day ago high - 1 day ago low <= 3 days ago high - 3 days ago low and
1 day ago high - 1 day ago low <= 4 days ago high - 4 days ago low and
1 day ago high - 1 day ago low <= 5 days ago high - 5 days ago low and
1 day ago high - 1 day ago low <= 6 days ago high - 6 days ago low and
1 day ago high - 1 day ago low <= 7 days ago high - 7 days ago low and

1 day ago high < 2 days ago high and
1 day ago low > 2 days ago low and

latest close > 1 day ago high and
latest close > latest sma( close, 50 ) and
latest volume > latest sma( volume, 20 ) and
latest volume > 50000 and
latest close > 50
) )
```

### 📋 How to Trade (Bullish Breakout — Live Trade)

> [!NOTE]
> This scan fires **during the trading session**. The breakout is happening RIGHT NOW. Time is critical.

**Step 1 — Confirm the Breakout is Genuine**
- Check that the breakout candle has **strong green volume** (significantly above the 20-day average).
- Check that Nifty/BankNifty is **not falling sharply** at the same time. A Nifty crash will kill the breakout.
- Ideal breakout happens in the **first 30-90 minutes** (09:30 AM – 11:00 AM). Avoid chasing breakouts after 2:00 PM.

**Step 2 — Entry**
- **Entry:** Buy at the **current market price (CMP)** as soon as you confirm the conditions above.
- Do NOT wait. If the stock has already moved more than 2-3% above yesterday's high, it may be too late to enter.

**Step 3 — Stop Loss & Target**
- **Stop Loss:** `Yesterday's Low` (the low of the NR candle from the scan).
- **Target:** `1:2 or 1:3 Risk-Reward` from your entry.
  - Example: Entry ₹306, SL ₹300 → Risk = ₹6 → Target 1 = ₹312 (1:1), Target 2 = ₹318 (1:2)

**Step 4 — Trade Management**
- Book **50% at Target 1** and trail the remaining 50% with a trailing SL.
- If it is a very strong breakout day with high volume, hold until Target 2.
- **Exit all positions before 3:15 PM** if trading intraday.

---

## 4. Bearish Breakdown (Actual Trigger)

Use this scan to find stocks that were in a tight NR7/Inside Bar contraction **yesterday**, and have strongly broken down **today** with volume.
* **Use this for:** Finding stocks that are actively falling off a cliff right now (or just broke support today).
* **When to Run:** **Periodically during the trading day** (e.g., 09:30 AM, 11:00 AM, 1:00 PM, 2:30 PM, 3:15 PM). You are looking for a live breakdown crossing yesterday's low.

```text
( {cash} (
1 day ago high - 1 day ago low <= 2 days ago high - 2 days ago low and
1 day ago high - 1 day ago low <= 3 days ago high - 3 days ago low and
1 day ago high - 1 day ago low <= 4 days ago high - 4 days ago low and
1 day ago high - 1 day ago low <= 5 days ago high - 5 days ago low and
1 day ago high - 1 day ago low <= 6 days ago high - 6 days ago low and
1 day ago high - 1 day ago low <= 7 days ago high - 7 days ago low and

1 day ago high < 2 days ago high and
1 day ago low > 2 days ago low and

latest close < 1 day ago low and
latest close < latest sma( close, 50 ) and
latest volume > latest sma( volume, 20 ) and
latest volume > 50000 and
latest close > 50
) )
```

### 📋 How to Trade (Bearish Breakdown — Live Trade)

> [!NOTE]
> This scan fires **during the trading session**. The breakdown is happening RIGHT NOW. Time is critical.

**Step 1 — Confirm the Breakdown is Genuine**
- Check that the breakdown candle has **strong red volume** (significantly above the 20-day average).
- Check that Nifty/BankNifty is **also weak or falling**. A breakdown is much more powerful when the broader market is also falling.
- Ideal breakdown happens in the **first 30-90 minutes** (09:30 AM – 11:00 AM). Avoid chasing breakdowns after 2:00 PM.

**Step 2 — Entry**
- **Entry (Short):** Enter a short position (Futures/Put Options/BTST Short) at the **current market price (CMP)**.
- Do NOT chase. If the stock has already fallen more than 2-3% below yesterday's low, it may be too late to short safely.

**Step 3 — Stop Loss & Target**
- **Stop Loss:** `Yesterday's High` (the high of the NR candle from the scan).
- **Target:** `1:2 or 1:3 Risk-Reward` from your entry.
  - Example: Entry ₹297, SL ₹303 → Risk = ₹6 → Target 1 = ₹291 (1:1), Target 2 = ₹285 (1:2)

**Step 4 — Trade Management**
- Cover (buy back) **50% at Target 1** and trail the remaining 50% with a trailing SL.
- If it is a very strong breakdown day with high volume, hold until Target 2.
- **Exit all short positions before 3:15 PM** if trading intraday.

---

## 5. Intraday Confluence Breakout (Futures / Indices)

These scans are built for intraday traders targeting Futures & Indices (like Nifty, Bank Nifty, and stock futures). They stack all the confirmation layers of the breakout framework: yesterday's high/low breakout, full candle close, VWAP, EMA alignment (20/200), and Daily Pivot Point clearance.

* **Segment to Select:** Change "Cash" to **"Futures"** in Chartink to find liquid derivative stocks and index contracts.
* **Timeframe to Use:** Recommended **15 minute** or **5 minute** chart.

### 5.1 Bullish Confluence Breakout (Long Setup)
* **What it looks for:**
  - Intraday body close above yesterday's Daily High.
  - Price above VWAP.
  - Price above both 20 EMA and 200 EMA.
  - 20 EMA is sloping up (higher than its value 1 candle ago).
  - 20 EMA is above 200 EMA.
  - Price has cleared both the daily Pivot Point (PP) and Resistance 1 (R1).

```text
( {future} (
latest close > 1 day ago daily high and
latest close > latest vwap and
latest close > latest ema( close , 20 ) and
latest ema( close , 20 ) > 1 candle ago ema( close , 20 ) and
latest close > latest ema( close , 200 ) and
latest ema( close , 20 ) > latest ema( close , 200 ) and
latest close > latest pivot( daily , pp ) and
latest close > latest pivot( daily , r1 )
) )
```

### 5.2 Bearish Confluence Breakdown (Short Setup)
* **What it looks for:**
  - Intraday body close below yesterday's Daily Low.
  - Price below VWAP.
  - Price below both 20 EMA and 200 EMA.
  - 20 EMA is sloping down (lower than its value 1 candle ago).
  - 20 EMA is below 200 EMA.
  - Price has broken below both the daily Pivot Point (PP) and Support 1 (S1).

```text
( {future} (
latest close < 1 day ago daily low and
latest close < latest vwap and
latest close < latest ema( close , 20 ) and
latest ema( close , 20 ) < 1 candle ago ema( close , 20 ) and
latest close < latest ema( close , 200 ) and
latest ema( close , 20 ) < latest ema( close , 200 ) and
latest close < latest pivot( daily , pp ) and
latest close < latest pivot( daily , s1 )
) )
```

### 📋 How to Trade (Intraday Confluence Scans)

> [!TIP]
> Since this scanner requires multiple high-confluence conditions to align, it will return fewer but higher-probability candidates.

1. **Wait for the Trigger Candle to Close:** Do not enter mid-candle. Wait for the 5-min or 15-min candle to close completely to confirm the breakout and EMA slope/VWAP rules.
2. **Stop Loss Placement:** Place your SL at the low of the breakout candle, or below the 20 EMA / VWAP (whichever is closer and makes sense structurally).
3. **Target Booking:** Use R2/R3 (for longs) or S2/S3 (for shorts) as natural take-profit targets. Alternatively, exit if the price closes back on the opposite side of the 20 EMA.
4. **Avoid the Open:** Wait at least 15-30 minutes after the market open (9:15 AM IST) to let the VWAP and EMAs stabilize.

---

## 6. Golden Cross & Death Cross (Strong Trend)

These scans look for the classic 50 SMA and 200 SMA crossovers, filtered by the ADX (Average Directional Index) to ensure a strong trend is present (ADX > 25).

### 6.1 Golden Cross (Today EOD Setup)
* **What it looks for:** 50 SMA crossing *above* the 200 SMA today, with ADX indicating a strong trend.
* **When to Run:** End of Day (3:15 PM - 3:30 PM) or after market close. Use this to find stocks to trade tomorrow.

```text
( {cash} (
latest sma( close , 50 ) crosses above latest sma( close , 200 ) and
latest adx( 14 ) >= 25 and
latest volume > 50000 and
latest close > 50
) )
```

### 6.2 Death Cross / Golden Down (Today EOD Setup)
* **What it looks for:** 50 SMA crossing *below* the 200 SMA today, with ADX indicating a strong trend.
* **When to Run:** End of Day (3:15 PM - 3:30 PM) or after market close. Use this to find stocks to short tomorrow.

```text
( {cash} (
latest sma( close , 50 ) crosses below latest sma( close , 200 ) and
latest adx( 14 ) >= 25 and
latest volume > 50000 and
latest close > 50
) )
```

### 6.3 Next Day Follow-Through: Golden Cross
* **What it looks for:** Stocks that had a Golden Cross *yesterday*, and are now breaking out above yesterday's high.
* **When to Run:** Live market (next day) to catch the immediate follow-through momentum.

```text
( {cash} (
1 day ago sma( close , 50 ) crosses above 1 day ago sma( close , 200 ) and
latest close > 1 day ago high and
latest adx( 14 ) >= 25 and
latest volume > 50000 and
latest close > 50
) )
```

### 6.4 Next Day Follow-Through: Death Cross
* **What it looks for:** Stocks that had a Death Cross *yesterday*, and are now breaking down below yesterday's low.
* **When to Run:** Live market (next day) to catch the immediate breakdown momentum.

```text
( {cash} (
1 day ago sma( close , 50 ) crosses below 1 day ago sma( close , 200 ) and
latest close < 1 day ago low and
latest adx( 14 ) >= 25 and
latest volume > 50000 and
latest close > 50
) )
```

### 📋 How to Trade (Golden / Death Cross)

> [!NOTE]
> Golden and Death Crosses are major structural shifts, so they often take a few days to fully play out. 

1. **EOD Preparation:** If you run the **Today EOD** scan, note the high (for Golden Cross) or low (for Death Cross) of the trigger candle.
2. **Next Day Entry:** Place a stop order just above yesterday's high (for longs) or below yesterday's low (for shorts).
3. **Alternatively:** Use the **Next Day Follow-Through** scans during live market hours to find stocks that are actively confirming yesterday's cross right now.
4. **Stop Loss:** Keep the stop loss on the other side of the crossover candle, or just beyond the 50 SMA.

## 7. Advanced Trend & Momentum Filter (UT Bot, WaveTrend, Squeeze, ADX)

This filter combines multiple concepts (Trend, Momentum Divergence, Volatility Contraction, and Trend Strength) into a single powerful scan.

### 7.1 Bullish (Long) Advanced Filter
* **What it looks for:** A bullish EMA crossover, rising RSI while price is falling (bullish divergence proxy), tight Bollinger Bands (squeeze), strong ADX (trend), and avoiding sideways chop.
* **How to configure:** Run this on the **15 Minute** timeframe in Chartink for Intraday, or the **Daily** timeframe for Swing trading.

```text
( {cash} (
  latest close >= 200 and 
  latest ema( close, 10 ) crosses above latest ema( close, 20 ) and
  latest rsi( 14 ) > 1 candle ago rsi( 14 ) and
  ( ( latest upper bollinger band( 20, 2 ) - latest lower bollinger band( 20, 2 ) ) / latest close ) <= 0.10 and
  latest adx( 14 ) > 20 and
  ( latest atr( 14 ) / latest close ) > 0.002
) )
```

### 7.2 Bearish (Short) Advanced Filter
* **What it looks for:** A bearish EMA crossover, falling RSI, tightening Bollinger Bands, moderate ADX, and avoiding completely dead volatility.
* **How to configure:** Run this on the **15 Minute** timeframe in Chartink for Intraday, or the **Daily** timeframe for Swing trading.

```text
( {cash} (
  latest close >= 200 and 
  latest ema( close, 10 ) crosses below latest ema( close, 20 ) and
  latest rsi( 14 ) < 1 candle ago rsi( 14 ) and
  ( ( latest upper bollinger band( 20, 2 ) - latest lower bollinger band( 20, 2 ) ) / latest close ) <= 0.10 and
  latest adx( 14 ) > 20 and
  ( latest atr( 14 ) / latest close ) > 0.002
) )
```

## 8. Stop-Loss (SL) & Target Framework

Apply these rules consistently when trading the setups from Chartink or TradingView:

### 8.1 Intraday Trades
**Stop-Loss (SL):**
* Place just beyond the nearest pivot level (10–15 points buffer).
* *Example:* If entering long near Pivot 23,715 → SL at 23,700.

**Target:**
* First target = next pivot (R1 or S1).
* Second target = R2/S2 if momentum continues.
* Trail stop if WaveTrend + Squeeze still confirm direction.

### 8.2 Swing Trades
**Stop-Loss (SL):**
* Place below the last swing low (for longs) or above the last swing high (for shorts).
* Wider buffer (1–2%) since swings cover more points.

**Target:**
* First target = next weekly pivot or resistance/support zone.
* Second target = next major level (weekly R2/S2).
* Trail stop using WaveTrend slope or Squeeze bar shrinkage.

### 8.3 Divergence Trades
**Bullish Divergence:**
* SL = below divergence low.
* Target = next pivot or resistance zone.

**Bearish Divergence:**
* SL = above divergence high.
* Target = next pivot or support zone.

### 8.4 Chop Zone + ADX Filter Rule
* If Chop Zone is **ON** + **ADX < 20** → No trade (skip).
* If Chop Zone is **OFF** + **ADX > 25** → Use the above SL/Target rules confidently.
