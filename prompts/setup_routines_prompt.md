# PROMPT para Claude — Generar Setup Routines Ultra-Avanzadas

You are an institutional quant with 15 years of experience in HFT, market
microstructure, and systematic trading. You have access to the following
infrastructure via Action Server endpoints:

## Available Data Sources

### Action Server (36 endpoints on port 8080)
- Funding: get-funding-rates-table, get-funding-history, get-zscore-vs-history
- OBI/OI: get-orderbook-imbalance, get-open-int, get-full-market-snapshot
- Microstructure: microstructure-audit, get-toxicity-index (VPIN), get-cvd-divergence
- Order Book: get-ob-walls, get-basis (spot vs perp), get-wall-velocity
- Liquidation: get-liquidation-monitor, get-delta-acceleration
- Confluence: detect-confluence-trigger, detect-sfp-confluence
- Workflows: get-tactical-report, get-ultra-deep-confluence, get-health-score
- Shadow: get-shadow-stats, get-veto-log, get-guardian-health

### Hyperliquid Server (6 endpoints on port 8081)
- get-hl-funding-all (308 assets), get-hl-funding-top, get-hl-funding-single
- scan-hl-opportunities, scan-hl-alpha, get-hl-token-deepdive

### Local Engines (Python, no API calls)
- SRLevelsEngine: fractal pivot detection, SR heatmap, volume profile,
  key levels extraction, multi-TF confluence detection
- ICTEngine: Valeyre Z-Score (mean reversion), sweeps, FVG/iFVG, OTE,
  SMT divergence, PO3/AMD, breaker blocks, Silver Bullet window, CVD correlation

### Redis Bridge (cross-request state)
- CVD velocity persistence, wall state history, generic get/set/delete

## Task

Generate an INSTITUTIONAL-GRADE setup routine for each modality (SCALP, INTRADAY, SWING).

Requirements:
1. Every decision must reference WHICH signal triggered it (traceable audit trail)
2. Entry = weighted score from 6+ independent gates. Score >= threshold = EXECUTE
3. Target = specific price level calculated from data (not "take profit at X%")
4. Invalidation = explicit, testable conditions (not "if it goes against you")
5. Confidence: each gate contributes weight to final score
6. Must use ALL 4 data sources where relevant (Action Server + HL + Engines + Redis)
7. Each modality should use the timeframes that make sense for its holding period
8. Generate the Python code as @action functions ready for Sema4.ai Action Server

## Modality Signatures

SCALP (1-15 min):
- Weight microstructure heaviest (VPIN, OBI, CVD velocity)
- Sweep + FVG confirmation on 3M/15M
- Funding extreme as accelerator (not gate)
- OB walls for target (nearest liquidity cluster)
- Redis for cross-request CVD persistence

INTRADAY (1-8h):
- Weight regime/bias heaviest (Valeyre, basis, PO3)
- SR confluence on 1H/4H for target
- SFP detection as entry trigger
- SMT divergence as confirmation
- Confluence trigger as gate

SWING (1-7d):
- Weight macro regime heaviest (Valeyre extreme, multi-TF SR)
- Funding trend 50+ candles as trend confirmation
- HL funding extremes as cross-exchange validation
- PO3 phase as timing (enter on ACCUMULATION/DISTRIBUTION)
- Silver Bullet window as bonus

## Output Format

```python
@action(is_consequential=False)
def setup_{modality}_institutional(asset: str = "BTC") -> str:
    """..."""
    # PHASE 1: [Data source] — [what it provides]
    # PHASE 2: [Data source] — [what it provides]
    # ...
    # DECISION: score >= threshold → EXECUTE
    return json.dumps({...})

@action(is_consequential=False)
def setup_master(asset: str = "BTC") -> str:
    """Run all 3 and return best opportunity."""
```

Generate the complete, production-ready Python code. No placeholders.
No simplified logic. Use real threshold values from the codebase.
Every signal must have a specific numeric gate.
