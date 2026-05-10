# CCXTV2-Next — Modular Institutional Trading Stack v0.3

> **Production-Grade Multi-Layer Trading Intelligence System**
>
> 60 files · 12 modules · 2 action servers (46+6 endpoints) · Knowledge graph: 717 nodes / 1028 edges / 68 communities
>
> [![graphify](https://img.shields.io/badge/graphify-717_nodes-blue)](graphify-out/graph.html)
> [![Status](https://img.shields.io/badge/status-PRODUCTION_READY-green)]()
> [![Endpoints](https://img.shields.io/badge/endpoints-52-brightgreen)]()
> [![Redis](https://img.shields.io/badge/redis-ONLINE-red)]()

---

## Overview

CCXTV2-Next is a refactored institutional trading stack for crypto derivatives (perpetual futures). It combines **real-time market microstructure analysis**, **Smart Money Concepts (SMC/ICT)**, **machine learning veto systems**, **10+ sentinel monitors**, and an **evolutionary gate-weight improvement loop** — all orchestrated through a central intelligence hub.

### What It Does

1. **Ingests real-time data** from Binance, Bybit, OKX, and Hyperliquid via CCXT
2. **Computes 40+ microstructure metrics** — OBI, CVD, VPIN, basis, Z-Score, absorption rate, whale stealth
3. **Evaluates setups across 3 timeframes** — Scalp (1-15m), Intraday (1-8h), Swing (1-7d)
4. **Filters through 4+ adaptive gates** — ML probability, HMM entropy, microstructure, and regime consensus
5. **Self-improves** — evolutionary gate weight adjustment loop from reasoning logs

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     ACTION SERVERS (REST + MCP)                   │
│                                                                   │
│  funding_server (:8080)              hyperliquid_server (:8081)   │
│  ┌─────────────────────┐            ┌───────────────────────┐    │
│  │ 36-40 @action       │            │ 6 @action              │    │
│  │ • funding rates     │            │ • HL funding           │    │
│  │ • OBI/CVD/basis     │            │ • HL scanner           │    │
│  │ • microstructure    │            │ • token deepdive       │    │
│  │ • workflows v3      │            │ • opportunity scanner  │    │
│  │ • institutional     │            └───────────────────────┘    │
│  │ • evolve gates      │                                         │
│  └─────────────────────┘                                         │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                        GATE LAYER                                 │
│                                                                   │
│  ┌──────────────┐  ┌─────────────────┐  ┌────────────────────┐  │
│  │ MLVetoSystem │  │ExecutionGuardian│  │   ShadowTester     │  │
│  │              │  │                 │  │                    │  │
│  │ 4-gate eval  │──▶ evaluate()      │──▶ contingency table  │  │
│  │ VPIN/ML/     │  │ risk state      │  │ win_rate/p_value   │  │
│  │ HMM/Micro    │  │ cooldown/dd     │  │ stats significance │  │
│  └──────────────┘  └─────────────────┘  └────────────────────┘  │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│              INTELLIGENCE HUB (God Node — 41 edges)               │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  • CCXT connection pool (Singleton, Binance/Bybit/OKX/HL)    │ │
│  │  • TTL cache layer                                           │ │
│  │  • Public API: 20+ typed coroutines                          │ │
│  │  • Data snapshots: OBI, CVD, basis, market, toxicity         │ │
│  └─────────────────────────────────────────────────────────────┘ │
└───────┬──────────┬──────────┬───────────┬────────────────────────┘
        │          │          │           │
   ┌────▼───┐ ┌───▼────┐ ┌───▼────┐ ┌───▼──────────┐
   │ ICT    │ │ZScore  │ │SR      │ │Data Integrity │
   │ Engine │ │Engine  │ │Levels  │ │               │
   │ 483L   │ │        │ │246L    │ │Circuit breaker│
   │        │ │Inst MVRV│ │Fractal │ │Sensor health  │
   │FVG/SMT │ │Rolling │ │pivot   │ │Failover proxy │
   │OTE/PO3 │ │48h win │ │Volume  │ │               │
   └────────┘ └────────┘ └────────┘ └───────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                   SENTINEL NETWORK                                │
│                                                                   │
│  ┌─────────┐ ┌────────┐ ┌────────┐ ┌──────────┐ ┌────────────┐ │
│  │   SFP   │ │ Whale  │ │ Spoof  │ │ Squeeze  │ │  Volume    │ │
│  │ Sentinel│ │Monitor │ │Detector│ │ Monitor  │ │  Monitor   │ │
│  └─────────┘ └────────┘ └────────┘ └──────────┘ └────────────┘ │
│                                                                   │
│  ┌─────────────┐ ┌──────────┐ ┌────────────┐ ┌──────────────┐  │
│  │Level Break  │ │Ignition  │ │ Senior     │ │Opportunity   │  │
│  │             │ │Bridge    │ │ Audit      │ │              │  │
│  └─────────────┘ └──────────┘ └────────────┘ └──────────────┘  │
│                                                                   │
│           All orchestrated by SentinelOrchestrator                │
└───────────────────────────────────────────────────────────────────┘
```

**Key insight from the knowledge graph:** SentinelOrchestrator and ExecutionGuardian have **no direct connection**. All signal flow passes exclusively through IntelligenceHub as the single cross-community bridge.

---

## Module Index

### `shared/` — Core Library (16 files)

The brain of the system. Extracted and deduplicated from the original ccxtv2 monolith.

| File | Lines | Purpose |
|------|-------|---------|
| `hub.py` | 919 | **IntelligenceHub** — CCXT singleton, cache, 20+ analysis coroutines (OBI, CVD, VPIN, basis, toxicity, walls, absorption) |
| `redis_bridge.py` | 307 | **RedisBridge** — Thread-safe singleton with JSON failover. CVD velocity, wall velocity, reasoning log, generic cache API |
| `shadow_tester.py` | 469 | **ShadowTester** — Live P&L tracker, contingency table, statistical significance (p-value), ML vs discretionary comparison |
| `veto_system.py` | 276 | **MLVetoSystem** — 4-gate adaptive ML copilot: VPIN, ML probability, HMM entropy, microstructure. Anti-hallucination, regret recalibration |
| `execution_guardian.py` | 215 | **ExecutionGuardian** — Pre-execution risk state machine. Drawdown guard (3-loss cooldown), time-decay exit (70% horizon), Brier score drift |
| `data_integrity.py` | 265 | **DataQuality** — Sensor health validation, circuit breaker (score < 20 blocks), failover informed flow proxy |
| `hub_reader.py` | 76 | **LiveMetrics** — NamedTuple interface for real-time hub metrics (VPIN, basis, OBI, Z-scores, regime) |
| `config.py` | 100 | **Settings** + TickerConfig — YAML or fallback defaults |

### `shared/engines/` — Quantitative Engines

| File | Lines | Capabilities |
|------|-------|-------------|
| `ict_engine.py` | 483 | Valeyre Z-Score, sweeps, FVG/iFVG, OTE zones, SMT divergence, PO3/AMD, breaker blocks, Silver Bullet (10-11 AM NY), CVD correlation via Spearman |
| `sr_levels.py` | 246 | Fractal pivot detection, SR heatmap, volume profile POC, multi-TF confluence, key level extraction |
| `zscore.py` | — | Enhanced Institutional MVRV Z-Score, rolling window (48h default), regime classification |

### `action_servers/` — REST API Layer

#### funding_server (:8080) — 36-40 endpoints

| File | Purpose | Key Endpoints |
|------|---------|---------------|
| `funding_actions.py` (1279L) | CCXT-pure standalone actions | `get-funding-rates-table`, `get-open-int`, `get-orderbook-imbalance`, `get-full-market-snapshot`, `detect-confluence-trigger`, `get-funding-history`, `get-zscore-vs-history` |
| `market_actions.py` (567L) | Market microstructure actions | `get-ob-walls`, `get-basis`, `get-toxicity-index`, `get-cvd-divergence`, `get-trap-score`, `get-htf-zscore`, `get-liquidation-monitor` |
| `insight_actions.py` (624L) | Institutional insight actions v2 | `get-health-score`, `get-flash-alert`, `get-weighted-signals`, `get-tactical-report`, `get-ultra-deep-confluence` |
| `audit_actions.py` | Senior desk microstructure audits | `microstructure-audit`, `eth-ele-audit`, `detect-sfp-confluence`, `senior-desk-universe-audit` |
| `shadow_actions.py` | Shadow tester actions | `get-shadow-stats`, `get-veto-log`, `get-active-shadow-signals`, `get-guardian-health` |
| `absorption_detector.py` (308L) | PhD-level institutional absorption | `absorption-scan`, `absorption-scan-all` — Kyle's Lambda, Iceberg Score, VPIN composite, toxicity index |
| `workflows.py` | Tactical workflow routines | `workflow-scalp`, `workflow-intraday`, `workflow-swing`, `workflow-health` |
| `workflows_advanced.py` (611L) | Institutional workflows v3 | `setup-scalp-institutional`, `setup-intraday-institutional`, `setup-swing-institutional`, `setup-master` |
| `workflows_institutional.py` (920L) | Institutional routines v4 | Evolution + weighted scoring with data integrity |

#### hyperliquid_server (:8081) — 6 endpoints

| File | Purpose |
|------|---------|
| `hl_funding.py` | `get-hl-funding-all`, `get-hl-funding-top`, `get-hl-funding-single` |
| `hl_scanner.py` | `scan-hl-broad`, `scan-hl-opportunities`, `get-hl-token-deepdive` |

### `sentinels/` — Monitoring Network (10 modules)

Replaces the 1211-line `GuardianDaemon.py` monolith. Each sentinel is an independent asyncio coroutine.

| File | Sentinel | Detects |
|------|----------|---------|
| `base.py` | `BaseSentinelTask` | Mixin base with supervised run loop |
| `orchestrator.py` | `SentinelOrchestrator` | Process-level supervisor, graceful shutdown |
| `sfp_sentinel.py` | SFPSentinel | Sweep-Flip-Pump patterns (liquidity grabs) |
| `whale_monitor.py` | WhaleMonitor | Large block orders (>$100K notional) |
| `spoof_detector.py` | SpoofDetector | Ghost walls (placed/removed within snap threshold) |
| `squeeze_monitor.py` | SqueezeMonitor | Short/long squeeze conditions (min score 2) |
| `volume_monitor.py` | VolumeMonitor | Volume anomalies and spikes |
| `level_break.py` | LevelBreak | Support/resistance breach detection (breaks only, never proximity) |
| `ignition_bridge.py` | IgnitionBridge | BTC/ETH coordinated ignition signals |
| `senior_audit.py` | SeniorAudit | Composite microstructure audits |
| `opportunity.py` | Opportunity | Opportunity scoring and ranking |

### `config/` — Central Configuration

| File | Contents |
|------|----------|
| `thresholds.yaml` | **Golden Rules** — VPIN (0.62), basis (-0.05%), CVD accel (0.0), OBI ignition (0.40), slippage (0.15%), cooldown (4h), symbols per exchange |
| `alerts.yaml` | Alert cooldowns, priority levels, squeeze/spoof thresholds, daily digest (08:00 UTC), Telegram format |
| `settings.yaml` | Exchange config, Redis host/port, logging |

### `setup_routines_institutional.py` (1312L)

**Standalone institutional-grade setup routines** that combine ALL data sources:
- Action Server (36 endpoints) — microstructure, OBI, funding, OB walls
- Hyperliquid Server (6 endpoints) — HL funding rates, cross-exchange validation
- Local Engines — SRLevelsEngine, ICTEngine
- Redis Bridge — CVD velocity persistence, wall state, setup results

Implements:
- `setup_scalp_institutional()` — 1-15min, 6-gate architecture
- `setup_intraday_institutional()` — 1-8h, multi-TF bias + SR levels + SFP
- `setup_swing_institutional()` — 1-7d, macro regime + multi-TF SR + funding trend
- `setup_master()` — Runs ALL 3 modalities, returns best opportunity with confidence
- `evolve_gates()` — Reads 24h reasoning log, suggests gate weight adjustments

### `strategies/` — Shell Launchers

| Script | Type | Target Asset |
|--------|------|-------------|
| `strategy_scalp.sh` | SCALP (1-15m) | BTC, ETH, SOL, HYPE, LINK |
| `strategy_sfp.sh` | SFP pattern | Majors |
| `strategy_absorption.sh` | Absorption rate | BTC/ETH |
| `strategy_sweep_liq.sh` | Sweep + Liquidation | All |
| `strategy_hyperliquid_alpha.sh` | HL alpha scanner | Hyperliquid perps |
| `run_all_strategies.sh` | Master launcher | All strategies |

---

## Signal Flow — Market → Decision → Execution

```
  MARKET DATA (CCXT Binance/Bybit/OKX/Hyperliquid)
          │
          ▼
  ┌───────────────────────┐
  │   IntelligenceHub      │ ◄── Process-wide singleton
  │   .connect()           │
  │   .get_market_snapshot │
  │   .get_toxicity_index  │
  │   .get_cvd_divergence  │
  │   .get_basis           │
  └───────────┬───────────┘
              │
    ┌─────────┼─────────┐
    │         │         │
    ▼         ▼         ▼
┌───────┐┌───────┐┌──────────┐
│Sentin.││Workflo││HL Scanner│
│10 mon.││v3/v4  ││6 actions │
└───┬───┘└───┬───┘└────┬─────┘
    │        │         │
    └────────┼─────────┘
             │
             ▼
    ┌─────────────────┐
    │ MLVetoSystem     │
    │ .evaluate_signal │  Gate 1: VPIN > 0.62
    │                  │  Gate 2: ML prob > threshold
    │ 4-gate verdict:  │  Gate 3: HMM entropy < threshold
    │ EXECUTE/REDUCE/  │  Gate 4: Microstructure aligned
    │   NO_TRADE       │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ExecutionGuardian │
    │ .evaluate()      │  Cooldown check (3-loss guard)
    │ .record_trade()  │  Time-decay exit (70% horizon)
    │ .check_drift()   │  Brier score model drift
    └────────┬────────┘
             │
    EXECUTE  │  REJECT
    ┌────────┴────────┐
    ▼                 ▼
 ┌──────┐       ┌──────────┐
 │DISP. │       │Reason Log│ → evolve_gates()
 │ORDER │       │(Redis)   │ → gate weight Δ
 └──────┘       └──────────┘
```

---

## Evolutionary Improvement Loop

The system self-tunes gate weights based on trading outcomes:

```
TRADING SESSION
       │
       ▼
  Reasoning Log ──► Redis key: reason:{modality}:{date}
       │
       │  Daily:   evolve_gates() → reads 24h logs → suggests Δ weights
       │  Weekly:  Gate→PnL correlation → weight *= 0.8 (bad) or *= 1.2 (good)
       │  Manual:  100-setup backtest → vary thresholds ±10/20/30% → find optimal
       │  Future:  Bayesian optimization (gp_minimize) after 500+ setups
       │
       ▼
  Gate Weight Adjustment ──► Redis key: gate:{name}:w
       │
       ▼
  NEXT SESSION (evolved weights)
```

---

## Quick Start

```bash
# 1. Verify imports
cd ccxtv2-next
python3 -c "from shared import IntelligenceHub, RedisBridge, settings; print('OK')"
python3 -c "from shared.redis_bridge import redis; print(redis().health())"

# 2. Start Redis (optional — JSON failover works without it)
sudo service redis-server start

# 3. Start Funding Server (port 8080)
cd action_servers/funding_server
action-server start --port 8080 --dir .

# 4. Start Hyperliquid Server (port 8081) — separate terminal
cd action_servers/hyperliquid_server
action-server start --port 8081 --dir .

# 5. Test an endpoint
curl -X POST http://localhost:8080/api/actions/funding-action-server/get-funding-rates-table/run \
  -H "Content-Type: application/json" \
  -d '{"assets": "BTC,ETH,SOL"}'

# 6. Run a full institutional setup
python3 setup_routines_institutional.py
```

### Production (daemonized)
```bash
setsid action-server start --port 8080 --dir action_servers/funding_server &
setsid action-server start --port 8081 --dir action_servers/hyperliquid_server &
```

---

## Data Flow — Redis & Cross-Request Persistence

```
Request 1                    Request 2
───────────                  ───────────
CVD(t0) computed             CVD(t1) computed
    │                            │
    ▼                            ▼
RedisBridge.set_cvd_velocity  RedisBridge.get_cvd_velocity
    │                            │
    └──── v(t0) stored ─────────► CVD_acceleration = v(t1) - v(t0)
```

Without Redis, CVD velocity resets to 0 between serverless action invocations. **RedisBridge** solves this with:
- `get_cvd_velocity()` / `set_cvd_velocity()` — cross-request persistence (TTL 120s)
- `get_wall_state()` / `set_wall_state()` — anti-spoofing wall velocity
- Generic `get()` / `set()` / `delete()` / `exists()` — cache API
- Automatic JSON failover when Redis is unavailable

---

## Data Integrity System

Sensors can fail silently. The **DataQuality** system detects and handles this:

```
Sensor Value  →  parse (null-safe)  →  DataQuality Enum
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
               VALID                         UNRELIABLE                AMBIGUOUS
            (use value)                 (block execution)          (mark + failover)
                                            │
                                    Circuit Breaker:
                                    health_score < 20 → NO TRADE
                                    VPIN dark → CVD/OBI proxy
```

**Golden Rule:** Never accept a gate result without checking sensor health first.

---

## Cross-Exchange Symbol Mapping

`universal_symbol_mapper(asset, exchange)` in `funding_actions.py`:
- Hardcoded symbols for BTC, ETH, SOL, LINK, HYPE, TON, TST across Binance/Bybit/OKX/HL
- Dynamic fallback via CCXT `load_markets()` for unknown assets
- Hyperliquid uses CCXT natively — no mapper needed

---

## Knowledge Graph Analysis

The entire codebase was analyzed with [graphify](https://github.com/safishamsi/graphifyy):

| Metric | Value |
|--------|-------|
| Nodes | 717 |
| Edges | 1,028 |
| Communities | 68 (50 meaningful) |
| God Node | IntelligenceHub (41 edges, betweenness 0.056) |
| Edge types | contains (348), calls (300), rationale_for (232), method (131), uses (12) |
| Extraction quality | 99% EXTRACTED, 1% INFERRED |

### Key Graph Discoveries

1. **IntelligenceHub connects 9 communities** — the undisputed god node
2. **SentinelOrchestrator and ExecutionGuardian have NO direct path** — everything routes through IntelligenceHub
3. **284 isolated nodes** — mostly docstrings and internal methods with ≤1 connection. Opportunity for better documentation of implicit couplings
4. **7 INFERRED edges involving IntelligenceHub** — model-reasoned connections that need verification (LiveMetrics, VetoResult, ShadowTester)

> Open **[graphify-out/graph.html](graphify-out/graph.html)** in any browser for interactive graph exploration.

---

## MCP Integration

```json
{
  "mcpServers": {
    "ccxtv2-next-funding": {
      "url": "http://localhost:8080/api/mcp/"
    },
    "ccxtv2-next-hyperliquid": {
      "url": "http://localhost:8081/api/mcp/"
    }
  }
}
```

---

## Lessons Learned (from AUDIT_EVOLUTIONARY_LOOP.md)

1. **Never trust a sensor without cross-validation** — VPIN=0 was ambiguous, we now failover to CVD+OBI proxies
2. **Consensus gates protect against false signals** — VPIN/OBI consensus blocked 100% of bad setups when VPIN was dark
3. **Symbol mapping is the silent killer** — TON returned OBI=0 for 3/4 exchanges until we added the dynamic mapper
4. **The evolutionary loop IS the edge** — static thresholds work for 3 months, dynamic ones work forever

---

## Audit Report

Full audit with known issues and priority fix queue: **[AUDIT_EVOLUTIONARY_LOOP.md](AUDIT_EVOLUTIONARY_LOOP.md)**

### Critical Issues (P0)
- VPIN returns 0.0 → failover implemented (OBI gates alone when VPIN dark)
- CVD returns 500 → pending `json.dumps()` wrapper fix
- Shadow endpoints 404 → pending execution module extraction
- Intraday/Swing crash on OHLCV → pending connection pooling

### Verified Alpha Signals (2026-05-10)
| Asset | Setup | Score | Sources |
|-------|-------|-------|---------|
| TST | SHORT, +39%/yr funding, $169M OI | 75/100 | HL + Binance OBI |
| SAGA | SHORT, +57%/yr funding, $97M OI | 75/100 | HL scanner |
| BANANA | LONG, -46%/yr funding | 60/100 | HL funding top |
| BTC | NO_TRADE, regime NEUTRAL | 0/65 | All sources |

---

## Project Status

| Component | Status | Detail |
|-----------|--------|--------|
| Funding Server (8080) | **LIVE** | 36-40 endpoints verified |
| Hyperliquid Server (8081) | **LIVE** | 6 endpoints verified |
| Institutional Routines | **LIVE** | Weighted scoring with data integrity |
| Redis Bridge | **ONLINE** | CVD/wall velocity persistence |
| Data Integrity | **ACTIVE** | Circuit breaker + failover |
| Sentinel Network | **STUB** | Orchestrator live, 8 sentinels to populate (Phase 2) |
| ML Deep Learning | **PLANNED** | Phase 4 — sklearn → PyTorch |
| Sentinel Stubs | **PENDING** | 8 stubs to be populated with original logic |

---

## File Tree

```
.
├── README.md
├── AUDIT_EVOLUTIONARY_LOOP.md
├── setup_routines_institutional.py      # (1312L) Standalone institutional setups
│
├── shared/                              # Core library (no deps on action servers)
│   ├── __init__.py                      # Re-exports: IntelligenceHub, RedisBridge, settings
│   ├── hub.py                           # (919L) IntelligenceHub singleton
│   ├── config.py                        # Settings + TickerConfig
│   ├── redis_bridge.py                  # (307L) Redis with JSON failover
│   ├── hub_reader.py                    # (76L) LiveMetrics NamedTuple
│   ├── shadow_tester.py                 # (469L) P&L tracker, contingency table
│   ├── veto_system.py                   # (276L) 4-gate MLVetoSystem
│   ├── execution_guardian.py            # (215L) Risk state machine
│   ├── data_integrity.py                # (265L) Sensor health + circuit breaker
│   └── engines/
│       ├── ict_engine.py                # (483L) SMC/ICT: FVG, OTE, SMT, Silver Bullet
│       ├── sr_levels.py                 # (246L) Fractal pivots, SR heatmap, volume profile
│       └── zscore.py                    # Institutional MVRV Z-Score
│
├── action_servers/
│   ├── funding_server/                  # Port 8080
│   │   ├── README.md                    # Endpoint map & trading routines
│   │   ├── package.yaml
│   │   ├── conda.yaml
│   │   ├── actions/
│   │   │   ├── funding_actions.py       # (1279L) CCXT-pure standalone
│   │   │   ├── market_actions.py        # (567L) OBI, CVD, basis, toxicity
│   │   │   ├── insight_actions.py       # (624L) Health, flash alerts, deep confluence
│   │   │   ├── audit_actions.py         # Senior desk audits
│   │   │   ├── shadow_actions.py        # Shadow tester endpoints
│   │   │   ├── absorption_detector.py   # (308L) PhD absorption rate
│   │   │   ├── workflows.py             # Tactical scalp/intraday/swing
│   │   │   ├── workflows_advanced.py    # (611L) Institutional v3
│   │   │   └── workflows_institutional.py# (920L) Institutional v4 + evolution
│   │   └── shared/                      # Self-contained copy for Sema4.ai
│   │       └── ...                      # (mirrors shared/)
│   │
│   └── hyperliquid_server/              # Port 8081
│       ├── README.md
│       ├── package.yaml
│       ├── conda.yaml
│       └── actions/
│           ├── hl_funding.py            # Funding rates & premiums
│           ├── hl_scanner.py            # Opportunity scanner
│           └── __init__.py
│
├── sentinels/
│   ├── orchestrator.py                  # (140L) Supervisor (replaces 1211L monolith)
│   ├── base.py                          # (80L) BaseSentinelTask mixin
│   ├── sfp_sentinel.py                  # (STUB) SFP detector
│   ├── whale_monitor.py                 # (STUB) Large order tracker
│   ├── spoof_detector.py                # (STUB) Ghost wall detector
│   ├── squeeze_monitor.py               # (STUB) Squeeze detector
│   ├── volume_monitor.py                # (STUB) Volume anomaly
│   ├── level_break.py                   # (STUB) SR breach
│   ├── ignition_bridge.py               # (STUB) BTC/ETH ignition
│   ├── senior_audit.py                  # (STUB) Composite audits
│   └── opportunity.py                   # (STUB) Opportunity scoring
│
├── config/
│   ├── thresholds.yaml                  # Golden rules (DO NOT MODIFY without backtest)
│   ├── alerts.yaml                      # Alert config, cooldowns, priorities
│   └── settings.yaml                    # Exchange config
│
├── strategies/
│   ├── strategy_scalp.sh
│   ├── strategy_sfp.sh
│   ├── strategy_absorption.sh
│   ├── strategy_sweep_liq.sh
│   ├── strategy_hyperliquid_alpha.sh
│   └── run_all_strategies.sh
│
├── scripts/
│   ├── alpha_scout.py
│   ├── sanity_trading_plan.py
│   └── scalp_analysis.py
│
├── prompts/
│   └── setup_routines_prompt.md         # Claude prompt template
│
├── alerts/                              # Alert gateway (migration pending)
├── ml_deep/                             # Deep learning placeholder (Phase 4)
├── intelligence_core/                   # Legacy core (migrated to shared/)
│
└── graphify-out/                        # Knowledge graph analysis
    ├── graph.html                       # Interactive graph — open in browser
    ├── GRAPH_REPORT.md                   # Full audit report
    └── graph.json                       # Raw data (GraphRAG-ready)
```

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Exchange API | CCXT (Binance, Bybit, OKX, Hyperliquid) |
| Action Server | Sema4.ai / Robocorp Action Server |
| Cache/Persistence | Redis + JSON failover |
| Analytics | NumPy, Pandas, SciPy (Spearman) |
| ML (current) | scikit-learn (HMM via hmmlearn) |
| ML (planned) | PyTorch (Phase 4) |
| HTTP Client | httpx |
| Knowledge Graph | graphify (tree-sitter AST + Louvain clustering) |

---

## Contributing

See the evolutionary audit at **[AUDIT_EVOLUTIONARY_LOOP.md](AUDIT_EVOLUTIONARY_LOOP.md)** for the priority fix/improvement queue.

### Immediate Priorities
1. Fix CVD endpoint return type (dict → json.dumps)
2. Extract execution modules for Shadow endpoints
3. Add OHLCV connection pooling
4. Populate sentinel stubs with original GuardianDaemon logic
5. Implement `get-hl-token-deepdive` logic

---

*Built with: [graphify](https://github.com/safishamsi/graphifyy) knowledge graph · [code-graph-pipeline](https://github.com/raestrada/ai_ops_master_skills) meta-skill · Claude-assisted institutional design*
