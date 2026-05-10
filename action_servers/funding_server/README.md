# CCXTV2-Next Action Server — Endpoint Map & Trading Routines

**36 endpoints activos | 3 modalidades de trading | Redis ONLINE**

## Mejor Ruta por Modalidad

### 🔥 SCALP (1-15 min)

**Entry:** `workflow-scalp` o cadena manual:
```
get-full-market-snapshot → detect-confluence-trigger → get-ob-walls → get-cvd-divergence
```

| Condición | Endpoint | Gate |
|-----------|----------|------|
| Confluence trigger | `detect-confluence-trigger` | SENSITIVE o CONSERVATIVE |
| OBI pressure | `get-orderbook-imbalance` | \|OBI\| > 0.40 |
| Funding extreme | `get-funding-rates-table` | \|funding\| > 0.05% |
| CVD aggression | `get-cvd-divergence` | acceleration > 0 |

**Entry confirmation:** Todos TRUE.
**Target:** Nearest wall cluster del `get-ob-walls`.
**Invalidation:** OBI flip, CVD neg, timeout 15min, max loss -0.5%.

---

### 📊 INTRADAY (1-8 horas)

**Entry:** `workflow-intraday` o cadena manual:
```
get-zscore-vs-history → get-basis → get-funding-history(10 velas) → detect-confluence-trigger
```

| Condición | Endpoint | Gate |
|-----------|----------|------|
| Regime Z-Score | `get-zscore-vs-history` | OVERHEATED o MEAN_REVERT_RISK |
| Basis spot vs perp | `get-basis` | < -0.05% (LONG) o > +0.05% (SHORT) |
| Funding trend | `get-funding-history` | 5/10 velas misma dirección |
| Confluence | `detect-confluence-trigger` | SENSITIVE |

**Target:** Z-Score retorna a NEUTRAL.
**Invalidation:** Regime flip, basis flip, timeout 8h, max loss -2%.

---

### 🐋 SWING (1-7 días)

**Entry:** `workflow-swing` o cadena manual:
```
get-ultra-deep-confluence → get-zscore-vs-history → get-funding-history(50 velas) → get-orderbook-imbalance(depth=100)
```

| Condición | Endpoint | Gate |
|-----------|----------|------|
| Deep confluence | `get-ultra-deep-confluence` | INFORMED_FLOW o ACCUMULATION |
| HTF Z-Score | `get-zscore-vs-history` | \|z\| > 1.5 |
| Funding trend 50 velas | `get-funding-history` | >80% misma dirección |
| OBI macro | `get-orderbook-imbalance` | \|OBI\| > 0.30 |

**Target:** HTF Z-Score cruza zona neutral.
**Invalidation:** Regime HTF flip, 3 velas funding opuestas, timeout 7d, max DD -5%.

---

## Estado de Endpoints (36 total)

### ✅ Probados y funcionales (12/36)

| Endpoint | Respuesta real |
|----------|---------------|
| `get-funding-rates-table` | BTC: binance 0.006%, bybit 0.002% |
| `get-open-int` | OI + ΔOI multi-exchange |
| `get-orderbook-imbalance` | OBI con datos reales |
| `get-full-market-snapshot` | Funding + OI + OBI consolidado |
| `detect-confluence-trigger` | Reglas SENSITIVE/CONSERVATIVE |
| `get-funding-history` | Histórico N velas |
| `get-zscore-vs-history` | Z-Score 48h + regime |
| `get-tactical-report` | Reporte táctico scalp/swing |
| `get-ultra-deep-confluence` | Confluencia multi-timeframe |
| `get-ob-walls` | Muros bid/ask live |
| `get-basis` | Spot vs perp con datos reales (BTC basis=-0.034%) |
| `microstructure-audit` | Auditoría PhD por símbolo |

### 🆕 Nuevos (4/36) — TESTEADOS HOY

| Endpoint | Modalidad | Estado |
|----------|-----------|--------|
| `workflow-scalp` | SCALP 1-15min | ✅ Live |
| `workflow-intraday` | INTRADAY 1-8h | ✅ Live |
| `workflow-swing` | SWING 1-7d | ✅ Live |
| `workflow-health` | System health | ✅ Live (Redis ONLINE) |

### ⚠️ Parciales (necesitan fix de return type: dict→str)

| Endpoint | Issue |
|----------|-------|
| `get-cvd-divergence` | Retorna dict, Sema4ai requiere str |
| `get-trap-score` | Idem |
| `get-health-score` | Idem (datos reales: BTC score=65.7) |
| `get-flash-alert` | Idem |
| `get-system-health` | NameError (import legacy) |
| `get-htf-zscore` | NameError (import legacy) |

### ❓ No registrados (shadow actions — dependen de execution modules)

`get-shadow-stats`, `get-veto-log`, `get-active-shadow-signals`, `get-guardian-health`

---

## Ejemplo real de decisión (BTC hoy 2026-05-10)

```
SCALP:    NO_TRADE — confluence=NONE, sin presión institucional
INTRADAY: NO_TRADE — regime=NEUTRAL (zscore=0.20), sin edge direccional
SWING:    NO_TRADE — HTF zscore=0.20 < 1.5, funding 50/50, sin tendencia macro

Conclusión: Mercado en régimen NEUTRAL. Esperar a que VPIN > 0.62
            o Z-Score cruce ±2.0 para entrada institucional.
            Redis ONLINE — los datos persisten entre requests.
```

## Arranque

```bash
cd ~/Escritorio/ccxtv2-next/action_servers/funding_server

# Con Redis (recomendado)
sudo service redis-server start
action-server start --port 8080 --dir .

# Sin Redis (failover JSON automático)
action-server start --port 8080 --dir .

# Hyperliquid server (otro puerto)
cd ../hyperliquid_server
action-server start --port 8081 --dir .
```

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
