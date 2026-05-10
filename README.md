# CCXTV2-Next — Modular Institutional Trading Stack v0.3

> **Knowledge Graph Analysis** — Powered by [graphify](https://github.com/safishamsi/graphifyy)
>
> 717 nodes · 1028 edges · 68 communities · 99% extracted / 1% inferred

---

## Architecture Overview

A multi-layer institutional trading system designed for crypto derivatives markets (perpetual futures). The stack combines real-time market microstructure analysis, Smart Money Concepts (SMC/ICT), machine learning veto systems, and a sentinel-based monitoring network — all orchestrated through a central intelligence hub.

```
┌─────────────────────────────────────────────────────────────┐
│                    ACTION SERVERS (REST)                      │
│  funding_server (36 endpoints :8080)  hyperliquid (6 :8081)  │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                     GATE LAYER                                │
│  MLVetoSystem (4 gates) ◄──► ExecutionGuardian (risk state)  │
│  ShadowTester (backtest loop)                                 │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│              INTELLIGENCE HUB (41 edges — god node)           │
│  CCXT connection pool · engine singletons · market cache      │
└───────┬──────────┬──────────┬──────────┬────────────────────┘
        │          │          │          │
   ┌────▼───┐ ┌───▼────┐ ┌───▼────┐ ┌───▼──────────┐
   │ICT/SMC │ │ZScore  │ │VPIN    │ │SR Levels     │
   │Engine  │ │Engine  │ │Toxicity│ │Fractal Pivot │
   │(15)    │ │(15)    │ │(23)    │ │(9)           │
   └────────┘ └────────┘ └────────┘ └──────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                SENTINEL NETWORK (10+ monitors)                │
│  SFP · Whale · Spoof · Squeeze · Volume · Ignition Bridge     │
│              Orchestrated by SentinelOrchestrator             │
└──────────────────────────────────────────────────────────────┘
```

---

## God Nodes — Core Abstractions

| Node | Edges | Community | Description |
|------|-------|-----------|-------------|
| **IntelligenceHub** | 41 | 9 | Central nervous system. Process-wide CCXT singleton, engine cache, all market data flows through here |
| **ShadowTester** | 19 | 1 | Backtesting engine — tracks discretionary signals vs ML VetoSystem verdicts, builds contingency tables |
| **MLVetoSystem** | 18 | 11 | 4-gate adaptive ML copilot (VPIN / HMM entropy / TimesFM divergence / microstructure) that vetos trades pre-execution |
| **RedisBridge** | 17 | 2 | Standalone Redis singleton with JSON failover. Wall snapshot persistence for velocity/spoofing calculations |
| **ICTEngine** | 15 | 5 | Smart Money Concepts — FVG detection, Silver Bullet windows, high-frequency sweep analysis, CVD divergence |
| **ZScoreEngine** | 15 | 17 | Enhanced Institutional MVRV Z-Score. Rolling Z-Score for funding rates and OBI with per-symbol window management |
| **SentinelOrchestrator** | 11 | 6 | Coordinates all sentinel tasks as asyncio coroutines with supervised run loops |
| **ExecutionGuardian** | 10 | 14 | Pre-execution risk state machine — drawdown tracking, Brier score drift detection, bar-level state management |

---

## Signal Flow — From Market to Execution

```
Market Data (CCXT)
  │
  ├── IntelligenceHub ──► OBISnapshot, CVDState, BasisSnapshot, ToxicityResult
  │                             │
  ├── Sentinels ────────────────┤
  │   ├── SFPSentinel           │  detection events
  │   ├── WhaleMonitor          │  ───────────────► SentinelOrchestrator
  │   ├── SpoofDetector         │                         │
  │   ├── SqueezeMonitor        │                   alert dispatch
  │   └── IgnitionBridge        │
  │                             │
  ├── Workflows ────────────────┤
  │   ├── Scalp (1-15min)       │  institutional setups
  │   ├── Intraday              │  ───────────────► MLVetoSystem.evaluate_signal()
  │   └── Swing                 │                         │
  │                             │                   4-gate verdict
  │                             │                         │
  │                             │              ┌──────────▼──────────┐
  │                             │              │  ExecutionGuardian   │
  │                             │              │  .evaluate()          │
  │                             │              │  .record_trade_open() │
  │                             │              │  .check_model_drift() │
  │                             │              └──────────────────────┘
  │                             │
  └── ShadowTester ◄────────────┘  (backtest loop: logs vetos vs outcomes)
```

---

## Key Relationships Discovered by Graph

### Surprising Connections

| From | To | Confidence | Type |
|------|----|-----------|------|
| SentinelOrchestrator → IntelligenceHub | INFR | 0.54 | Cross-module bridge |
| LiveMetrics → IntelligenceHub | INFR | 0.54 | Reader-to-hub dependency |
| VetoResult → IntelligenceHub | INFR | 0.54 | Veto system feeds back to hub |
| senior_desk_universe_audit() → get_full_market_snapshot() | INFR | 0.55 | Audit-to-data pipeline |
| _get_toxicity_index_internal() → AbsorptionDetector | INFR | 0.54 | Toxicity uses absorption |

### Path Finding

```
IntelligenceHub ──[uses]──► MLVetoSystem  (1 hop — direct dependency)
```

> No direct path exists between `SentinelOrchestrator` and `ExecutionGuardian` — they communicate through `IntelligenceHub` as the sole mediator.

---

## Module Breakdown

### `shared/` — Core Library (95 nodes in hub.py)
| File | Nodes | Key Components |
|------|-------|----------------|
| `hub.py` | 95 | IntelligenceHub, OBISnapshot, CVDState, BasisSnapshot, ToxicityResult, MarketSnapshot |
| `shadow_tester.py` | 41 | ShadowTester, ContingencyTable, go_win_rate, is_statistically_significant |
| `redis_bridge.py` | 40 | RedisBridge, failover JSON persistence, wall snapshots |
| `veto_system.py` | 20 | MLVetoSystem, VetoResult, 4-gate evaluation pipeline |
| `execution_guardian.py` | 17 | ExecutionGuardian, GuardianDecision, drawdown state machine |
| `engines/ict_engine.py` | 33 | ICTEngine, CVD divergence, Silver Bullet windows |
| `engines/zscore.py` | — | ZScoreEngine, Rolling Z-Score for funding |
| `engines/sr_levels.py` | 17 | Support/Resistance fractal pivots, volume profile |
| `data_integrity.py` | 11 | DataQuality, circuit breaker, failover informed flow |

### `sentinels/` — Monitoring Network
| Sentinel | Purpose |
|----------|---------|
| `sfp_sentinel.py` | Sweep-Flip-Pump detection |
| `whale_monitor.py` | Large order tracking |
| `spoof_detector.py` | Ghost wall / spoofing detection |
| `squeeze_monitor.py` | Short/long squeeze conditions |
| `volume_monitor.py` | Volume anomaly detection |
| `level_break.py` | Support/resistance breach detection |
| `ignition_bridge.py` | Dual-asset rotation signals |
| `senior_audit.py` | Composite microstructure audits |
| `opportunity.py` | Opportunity scoring and ranking |

### `action_servers/` — REST API Layer
| Server | Port | Endpoints |
|--------|------|-----------|
| **funding_server** | 8080 | 36 — funding rates, OBI, basis, CVD, Z-score, absorption, confluence triggers, market snapshots, senior audits |
| **hyperliquid_server** | 8081 | 6 — funding rates, premiums, token scanner, deep-dive analysis |

### `setup_routines_institutional.py` — Trading Setup Workflows
- **Scalp Institutional** (1-15min): OBI + VPIN + ICT Silver Bullet gates
- **Intraday Institutional**: Multi-TF bias + SR levels + SFP confluence
- **Swing Institutional**: Macro regime + multi-TF SR + funding trend

---

## Graph Statistics

| Metric | Value |
|--------|-------|
| Total Nodes | 717 |
| Total Edges | 1,028 |
| Communities | 68 (50 meaningful) |
| Extraction Quality | 99% EXTRACTED |
| Inferred Edges | 14 (avg confidence: 0.54) |
| Isolated Nodes | 284 (missing edges — documentation gaps) |
| Token Cost | 0 (AST-only extraction) |

### Edge Type Distribution
| Relation | Count | 
|----------|-------|
| `contains` | 348 |
| `calls` | 300 |
| `rationale_for` | 232 |
| `method` | 131 |
| `uses` | 12 |
| `inherits` | 3 |
| `imports_from` | 2 |

---

## Interactive Graph

Open **[graphify-out/graph.html](graphify-out/graph.html)** in any browser — no server needed.

- Nodes colored by community
- Drag, zoom, click for details
- Filter by node type, file, community
- 717 interactive nodes with edge confidence tags

---

## Audit & Analysis

Full audit report: **[graphify-out/GRAPH_REPORT.md](graphify-out/GRAPH_REPORT.md)**

- God nodes with centrality scores
- Surprising cross-community connections
- 68 communities with cohesion metrics
- Suggested exploration questions
- 284 isolated nodes flagged for investigation

Raw graph data: **[graphify-out/graph.json](graphify-out/graph.json)** — GraphRAG-ready, compatible with LangChain/LlamaIndex/graphify MCP.

---

## Suggested Exploration Questions

1. **Why does `IntelligenceHub` connect 9 communities?** — It has the highest betweenness centrality (0.056), acting as the single cross-community bridge.

2. **Why are there 284 isolated nodes?** — These represent documentation strings, edge cases, and internal methods with ≤1 connection. Likely missing edges from unparsed dependency patterns.

3. **Are the 7 inferred relationships involving `IntelligenceHub` correct?** — `LiveMetrics`, `VetoResult`, `ShadowTester`, and others are connected via INFERRED edges that need manual verification.

4. **Why is there no direct path between `SentinelOrchestrator` and `ExecutionGuardian`?** — All signal flow passes through `IntelligenceHub` as the central mediator.

---

## Pipeline

Built with the **[code-graph-pipeline](https://github.com/raestrada/ai_ops_master_skills)** meta-skill:

```
Raw Codebase (60 files)
    │
    ├── Phase 1: graphify (AST extraction, 5s)
    │   └── 717 nodes, 1028 edges, 68 communities
    │   Output: graph.html + GRAPH_REPORT.md + graph.json
    │
    ├── Phase 2: CodeGraphContext (call chains, dead code)
    │   └── Symbol-level graph with Neo4j/KuzuDB
    │
    └── Phase 3: Qdrant (vector embeddings)
        └── Semantic search over graph nodes
```

---

> *"The graph is the map. Understanding starts not by reading code, but by seeing structure."*
