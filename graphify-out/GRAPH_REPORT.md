# Graph Report - ccxtv2-next  (2026-05-10)

## Corpus Check
- 60 files · ~64,454 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 717 nodes · 1028 edges · 68 communities (50 shown, 18 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 14 edges (avg confidence: 0.54)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]

## God Nodes (most connected - your core abstractions)
1. `IntelligenceHub` - 41 edges
2. `ShadowTester` - 19 edges
3. `MLVetoSystem` - 18 edges
4. `RedisBridge` - 17 edges
5. `ICTEngine` - 15 edges
6. `ZScoreEngine` - 15 edges
7. `_ts()` - 12 edges
8. `setup_scalp_institutional()` - 11 edges
9. `SentinelOrchestrator` - 11 edges
10. `ExecutionGuardian` - 10 edges

## Surprising Connections (you probably didn't know these)
- `SentinelOrchestrator` --uses--> `IntelligenceHub`  [INFERRED]
  sentinels/orchestrator.py → shared/hub.py
- `senior_desk_universe_audit()` --calls--> `get_full_market_snapshot()`  [INFERRED]
  action_servers/funding_server/actions/audit_actions.py → action_servers/funding_server/actions/funding_actions.py
- `_get_toxicity_index_internal()` --calls--> `AbsorptionDetector`  [INFERRED]
  action_servers/funding_server/actions/market_actions.py → action_servers/funding_server/actions/absorption_detector.py
- `LiveMetrics` --uses--> `IntelligenceHub`  [INFERRED]
  shared/hub_reader.py → shared/hub.py
- `VetoResult` --uses--> `IntelligenceHub`  [INFERRED]
  shared/veto_system.py → shared/hub.py

## Communities (68 total, 18 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.07
Nodes (50): _calculate_spotdiff(), detect_confluence_trigger(), _evaluate_triggers(), _fetch_funding_history_one(), _fetch_funding_one(), _fetch_obi_one(), _fetch_oi_one(), _fetch_spot_price() (+42 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (27): ContingencyTable, get_shadow_tester(), get_shadow_tester_sync(), go_win_rate(), is_statistically_significant(), p_value(), Tracks discretionary signals vs. ML VetoSystem verdicts.      Builds a statistic, Lazy-load the VetoSystem (avoids import at module level). (+19 more)

### Community 2 - "Community 2"
Cohesion: 0.1
Nodes (22): Exception, health(), instance(), shared/redis_bridge.py — Standalone Redis Singleton with JSON Failover =========, Load or create failover JSON., Persist wall snapshot for velocity calculation., Get previous wall snapshot for velocity comparison., Get latest wall state for velocity calculation. (+14 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (33): 1. WHAT WORKS (Certified ✅), 2. WHAT FAILS (Needs Fix ⚠️), 3. PHD EVOLUTIONARY IMPROVEMENT LOOP, 4. WHAT TO IMPROVE NEXT (Priority Queue), 5. MEMORY LAYER — Lessons Learned, 6. VERIFIED ALPHA SIGNALS (2026-05-10), Action Server: Funding (port 8080) — 36 endpoints, Action Server: Hyperliquid (port 8081) — 6 endpoints (+25 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (31): analyze_market_snapshot(), execute_emergency_kill_switch(), get_basis(), _get_basis_internal(), get_delta_acceleration(), get_htf_zscore(), get_liquidation_monitor(), get_ob_walls() (+23 more)

### Community 5 - "Community 5"
Cohesion: 0.1
Nodes (18): calculate_cvd_divergence(), _cvd_interpretation(), ICTEngine, is_silver_bullet_window(), shared/engines/ict_engine.py — ICT / Smart Money Concepts Engine ===============, Detect if current candles swept previous range highs/lows.                  Retu, High-Frequency Sweep: 4H extreme bias + 3M SFP confirmation.         From GOD_HF, Detect Fair Value Gaps with volume/ATR validation.                  Bullish FVG: (+10 more)

### Community 6 - "Community 6"
Cohesion: 0.09
Nodes (15): BaseSentinelTask, sentinels/base.py — Base Sentinel Task ════════════════════════════════════════, Mixin base for all sentinel tasks.     Each task runs as an asyncio coroutine an, Supervised run loop. Override _cycle() in subclasses., Override in subclass with the actual monitoring logic., Convenience: dispatch a formatted alert through the gateway., main(), sentinels/orchestrator.py — Sentinel Orchestrator ══════════════════════════════ (+7 more)

### Community 7 - "Community 7"
Cohesion: 0.14
Nodes (25): _adjust_weights(), _cvd_delta(), _ep(), evolve_gates(), _fetch_ohlcv(), _market_beta(), Persist reasoning in Redis for self-correction loop., Calculate ATR percentile vs historical distribution. Returns {regime, percentile (+17 more)

### Community 8 - "Community 8"
Cohesion: 0.16
Nodes (23): _compute_health_component(), _cvd_from_df(), _fetch_obi(), get_cvd_divergence(), _get_cvd_divergence_internal(), get_flash_alert(), _get_flash_alert_internal(), get_health_score() (+15 more)

### Community 9 - "Community 9"
Cohesion: 0.11
Nodes (10): IntelligenceHub, Process-wide singleton: one CCXT connection, one cache, all engines.      Usage, Initialize exchange connections (idempotent)., Gracefully close all exchange connections and wait for connectors., Public: VPIN / Absorption composite toxicity index., Public: Wall velocity / spoofing detector., Public: Ticker information., Public: Latest price from ticker. (+2 more)

### Community 10 - "Community 10"
Cohesion: 0.15
Nodes (20): _as(), _hl(), _r(), _r_get(), _r_lpush_capped(), _r_set(), ╔══════════════════════════════════════════════════════════════════════════════╗, Institutional SCALP setup routine — 1 to 15 minute holding period.      GATE ARC (+12 more)

### Community 11 - "Community 11"
Cohesion: 0.12
Nodes (8): MLVetoSystem, Adjust VPIN/ML/entropy gates based on HMM regime entropy., Detect ML vs TimesFM contradiction., Log vetoed trades that would have won. Triggers recalibration., Loosen thresholds when too many winners are being vetoed., Discretionary copilot — ML/HMM/microstructure gates on your signals.      Usage:, Full 4-gate evaluation with adaptive thresholds., VetoResult

### Community 12 - "Community 12"
Cohesion: 0.1
Nodes (19): Arranque, CCXTV2-Next Action Server — Endpoint Map & Trading Routines, code:block1 (get-full-market-snapshot → detect-confluence-trigger → get-o), code:block2 (get-zscore-vs-history → get-basis → get-funding-history(10 v), code:block3 (get-ultra-deep-confluence → get-zscore-vs-history → get-fund), code:block4 (SCALP:    NO_TRADE — confluence=NONE, sin presión institucio), code:bash (cd ~/Escritorio/ccxtv2-next/action_servers/funding_server), code:json ({) (+11 more)

### Community 13 - "Community 13"
Cohesion: 0.18
Nodes (15): cache_stats(), _FundingFeesEngine, instance(), instance_sync(), is_aggression_confirmed(), is_spot_premium(), is_squeeze_condition(), MarketSnapshot (+7 more)

### Community 14 - "Community 14"
Cohesion: 0.12
Nodes (9): ExecutionGuardian, GuardianDecision, Record a new trade entry., Record trade exit and update drawdown state., Increment bar counter for all active trades (called each candle)., Return current state for monitoring., Detect sustained Brier score degradation over window_hours.          If Brier ha, Pre-execution filter with risk management state machine. (+1 more)

### Community 15 - "Community 15"
Cohesion: 0.15
Nodes (9): shared/engines/sr_levels.py — Support/Resistance Level Detection Engine ========, Compute S/R heatmap: touch frequency + pivot boosts + Wyckoff low-vol boost + sm, Extract discrete support/resistance levels from the heatmap.                  Re, Compute key levels for multiple timeframes.                  Args:             d, Find levels that appear across multiple timeframes (confluence zones)., Support/Resistance level detection using fractal pivots,     volume touch freque, Detect fractal pivot highs/lows with reversal confirmation.                  Arg, Calculate Volume Profile: accumulated volume per price level.                  A (+1 more)

### Community 16 - "Community 16"
Cohesion: 0.13
Nodes (15): detect_sfp_confluence(), eth_ele_audit(), microstructure_audit(), _microstructure_audit_internal(), audit_actions.py — Senior Desk Microstructure Audits ━━━━━━━━━━━━━━━━━━━━━━━━━━━, Composite Routine: High-Frequency Micro-Trend Flow.     1. Toxicity Index (VPIN), Composite Routine: Daily Session Capture Flow.     1. Basis (Spot vs Perp)., Performs a specialized 'ETH Liquidity Engine' (ELE) audit.     Checks SFP levels (+7 more)

### Community 17 - "Community 17"
Cohesion: 0.21
Nodes (3): Enhanced Institutional MVRV Z-Score Engine., Execute full Institutional MVRV Z-Score analysis pipeline., ZScoreEngine

### Community 18 - "Community 18"
Cohesion: 0.21
Nodes (14): action(), _call_endpoint(), workflows.py — Tactical Workflow Routines: Scalp, Intraday, Swing ══════════════, INSTITUTIONAL INTRADAY ROUTINE — entry / target / invalidation.      Decision tr, Fallback for local testing without Sema4.ai., INSTITUTIONAL SWING ROUTINE — entry / target / invalidation.      Decision tree:, Full system health: Redis status + endpoint validation + market pulse.      Retu, Call another action server endpoint internally. Uses httpx sync. (+6 more)

### Community 19 - "Community 19"
Cohesion: 0.22
Nodes (13): _ep(), _fetch_ohlcv(), workflows_advanced.py — Institutional Trading Workflows v3.0 ═══════════════════, INSTITUTIONAL SCALP SETUP — all data sources combined.          Combines OBI, VP, INSTITUTIONAL INTRADAY SETUP — multi-timeframe bias + SR levels + SFP., INSTITUTIONAL SWING SETUP — macro regime + multi-TF SR + funding trend., Run ALL 3 institutional modality setups and return the best opportunity., Call any action server endpoint. Returns parsed dict or {"error": ...}. (+5 more)

### Community 20 - "Community 20"
Cohesion: 0.15
Nodes (8): CVDState, OBISnapshot, Order Book Imbalance: (bids - asks) / (bids + asks)., CVD Velocity (CVD') and Acceleration (CVD'').         CVD'' > 0 while OBI < 0 →, Public: Order Book Imbalance snapshot., Public: CVD velocity and acceleration., Composite market snapshot — fires all analysis in parallel.         This is the, Ignition Bridge dual-asset check.         Returns combined state for RotationSen

### Community 21 - "Community 21"
Cohesion: 0.22
Nodes (9): AbsorptionDetector, AbsorptionResult, _get_hub(), absorption_detector.py — PhD-Level Institutional Absorption Rate Detector ══════, PhD-Level Absorption Detector.          Uses multi-snapshot OBI tracking + CVD d, Run full absorption scan on a single symbol., Scan all tickers in the universe., Get shared IntelligenceHub singleton (sync accessor). (+1 more)

### Community 22 - "Community 22"
Cohesion: 0.18
Nodes (7): annualize(), FundingState, Rolling Z-Score computation for funding rates and OBI.     Maintains per-symbol, Push new value, return current z-score. Returns 0 on insufficient data., Fetch, normalize, and z-score the current funding rate., Public: Funding rate with z-score and regime classification., _ZScoreEngine

### Community 23 - "Community 23"
Cohesion: 0.28
Nodes (4): Robust retry wrapper with throttling and 429 cooling circuit., PhD-Level VPIN / Absorption composite.         VPIN > 0.62 = informed flow gate, Wall Velocity Tracker — detects spoofing (ghost walls).         Returns: {v_pric, ToxicityResult

### Community 24 - "Community 24"
Cohesion: 0.27
Nodes (11): Enum, aggregate_quality(), check_funding(), check_obi(), check_vpin(), DataQuality, failover_informed_flow(), core/data_integrity.py — Sensor Health & Circuit Breaker System ════════════════ (+3 more)

### Community 25 - "Community 25"
Cohesion: 0.24
Nodes (12): _fetch_and_score(), _get_hl(), get_hl_token_deepdive(), hl_scanner.py — Hyperliquid Opportunity Scanner ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━, Scans ALL Hyperliquid perpetuals for shortable and longeable opportunities., Deep-dive analysis for a single Hyperliquid token.      Returns full funding con, Advanced scanner: funding + premium + Vol/OI + BTC/ETH majors., Advanced multi-signal Hyperliquid opportunity scanner.      Detects 3 categories (+4 more)

### Community 26 - "Community 26"
Cohesion: 0.32
Nodes (8): default_ticker(), ExchangeConfig, _load_settings(), shared/config.py — Minimal Configuration =======================================, Load from settings.yaml if available, else defaults., Settings, ThresholdsConfig, TickerConfig

### Community 27 - "Community 27"
Cohesion: 0.25
Nodes (10): _fetch_all_hl_funding(), _get_hl(), get_hl_funding_all(), get_hl_funding_single(), get_hl_funding_top(), hl_funding.py — Hyperliquid Funding Rates & Premiums ━━━━━━━━━━━━━━━━━━━━━━━━━━━, Returns top Hyperliquid tokens ranked by funding rate extremes.      Sorts by ab, Returns detailed funding data for a single Hyperliquid token.      Args: (+2 more)

### Community 28 - "Community 28"
Cohesion: 0.18
Nodes (10): CCXTV2-Next — Modular Institutional Trading Stack v0.3, code:block1 (ccxtv2-next/), code:block2 (CADA @action → _run_hub_sync() → nuevo Hub + nuevo event loo), code:bash (cd ccxtv2-next), Estructura (41 archivos), Fases, Lo que se extrajo, Quick Status (+2 more)

### Community 29 - "Community 29"
Cohesion: 0.18
Nodes (10): Acciones (6 endpoints), Arranque, code:bash (cd action_servers/hyperliquid_server), code:block2 (hyperliquid_server/), Diferencias con funding_server, Endpoints, Estructura, Funding Detail (+2 more)

### Community 30 - "Community 30"
Cohesion: 0.18
Nodes (10): Action Server (36 endpoints on port 8080), Available Data Sources, code:python (@action(is_consequential=False)), Hyperliquid Server (6 endpoints on port 8081), Local Engines (Python, no API calls), Modality Signatures, Output Format, PROMPT para Claude — Generar Setup Routines Ultra-Avanzadas (+2 more)

### Community 31 - "Community 31"
Cohesion: 0.36
Nodes (7): NamedTuple, get_live_metrics(), get_live_metrics_sync(), LiveMetrics, hub_reader.py — Interfaz única para leer métricas reales del Core_Intelligence_H, Lee métricas en tiempo real del Core_Intelligence_Hub de forma asíncrona., Versión síncrona para contextos que no soportan await.     ADVERTENCIA: Puede bl

### Community 32 - "Community 32"
Cohesion: 0.28
Nodes (8): deep_dive(), ep(), rank_and_report(), Phase 3: Top 5 ranked report., Call action server endpoint, return parsed dict., Phase 1: Broad scan — filter top N by funding extreme + OI, Phase 2: Sequential deep-dive with OBI cross-reference., scan_hl_broad()

### Community 33 - "Community 33"
Cohesion: 0.33
Nodes (3): BasisSnapshot, Basis divergence: perp_price - spot_price. Negative = spot premium., Public: Spot/Perp basis divergence.

### Community 34 - "Community 34"
Cohesion: 0.53
Nodes (5): ep(), extract_sr_targets(), fetch_ohlcv(), format_setup(), Extract TP1/TP2/TP3 from SR levels in bias direction.

## Knowledge Gaps
- **284 isolated node(s):** `╔══════════════════════════════════════════════════════════════════════════════╗`, `Call Action Server (port 8080) endpoint. Raises on non-2xx.`, `Call Hyperliquid Server (port 8081) endpoint. Raises on non-2xx.`, `Redis GET — returns None on any failure (non-critical path).`, `Redis SET — silent failure (non-critical path).` (+279 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **18 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `IntelligenceHub` connect `Community 9` to `Community 33`, `Community 1`, `Community 6`, `Community 11`, `Community 13`, `Community 20`, `Community 22`, `Community 23`, `Community 31`?**
  _High betweenness centrality (0.056) - this node is a cross-community bridge._
- **Why does `SentinelOrchestrator` connect `Community 6` to `Community 9`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **Why does `ShadowTester` connect `Community 1` to `Community 9`, `Community 11`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `IntelligenceHub` (e.g. with `LiveMetrics` and `VetoResult`) actually correct?**
  _`IntelligenceHub` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `ShadowTester` (e.g. with `MLVetoSystem` and `IntelligenceHub`) actually correct?**
  _`ShadowTester` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `MLVetoSystem` (e.g. with `IntelligenceHub` and `ShadowSignal`) actually correct?**
  _`MLVetoSystem` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `╔══════════════════════════════════════════════════════════════════════════════╗`, `Call Action Server (port 8080) endpoint. Raises on non-2xx.`, `Call Hyperliquid Server (port 8081) endpoint. Raises on non-2xx.` to the rest of the system?**
  _284 weakly-connected nodes found - possible documentation gaps or missing edges._