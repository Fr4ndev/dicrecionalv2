# Hyperliquid Funding Server

Sema4.ai Action Server para monitoreo de funding rates en Hyperliquid perpetuals. Escanea TODOS los perps de Hyperliquid buscando oportunidades de funding extremo en altcoins.

## Acciones (6 endpoints)

### Scanner
| Acción | Descripción |
|--------|-------------|
| `scan_hyperliquid_funding` | Escanea funding rates de TODOS los perpetuals Hyperliquid |
| `scan_hyperliquid_oi` | Open Interest + cambios en Hyperliquid |
| `scan_hyperliquid_volume` | Volumen 24h + anomalías |

### Funding Detail
| Acción | Descripción |
|--------|-------------|
| `get_hl_funding_detail` | Funding detallado para un símbolo específico |
| `get_hl_funding_extremes` | Top N funding rates extremos (positivos y negativos) |
| `get_hl_opportunity_report` | Reporte completo de oportunidades (funding + OI + volumen) |

## Arranque

```bash
cd action_servers/hyperliquid_server
action-server start --port 8081 --dir .
```

## Endpoints

| URL | Descripción |
|-----|-------------|
| `http://localhost:8081/` | UI del Action Server |
| `http://localhost:8081/api/mcp/` | MCP endpoint para agentes AI |
| `http://localhost:8081/openapi.json` | Spec OpenAPI completa |

## Diferencias con funding_server

| Aspecto | funding_server | hyperliquid_server |
|---------|---------------|-------------------|
| Exchanges | Binance, Bybit, OKX, HL | Solo Hyperliquid |
| Assets | BTC, ETH, SOL, LINK, HYPE | TODOS los perps |
| Enfoque | Majors + microstructure | Altcoin funding opportunities |
| Puerto | 8080 | 8081 |

## Estructura

```
hyperliquid_server/
├── package.yaml
├── conda.yaml
├── README.md
└── actions/
    ├── hl_funding.py    # 3 @action: detalle, extremos, oportunidades
    ├── hl_scanner.py    # 3 @action: scan funding, OI, volumen
    └── __init__.py
```
