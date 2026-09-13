# PROJECT MEMORY

> Fuente de verdad del proyecto. Solo información comprobada en código,
> tests o prueba técnica (`deriv_prueba/RESULTADOS.md`). Lo no confirmado se
> marca `PENDIENTE_DE_CONFIRMAR`. Sin secretos reales.

## 1. Objetivo del proyecto

Plataforma de trading DEMO en Python: datos reales de Deriv → velas
construidas propias → estrategias EMA deterministas → broker simulado →
dashboard web. Arquitectura lista para `DerivBroker` posterior sin tocar
estrategias ni dashboard. Sin trading real, sin IA decidiendo operaciones.

## 2. Arquitectura actual

```
create_provider() (factoría; la estrategia no conoce el proveedor)
├── DerivProvider [PRINCIPAL] (usa bot/market_data.py DerivClient)
│     ↓ ticks / velas OHLC (WS clásico wss://ws.derivws.com/websockets/v3)
└── CoinMarketCapProvider [EXPERIMENTAL] (bot/providers_coinmarketcap.py)
      ↓ polling HTTP (quotes + historical con API key, sin streaming)
CandleEngine (bot/candles.py: CandleBuilder por symbol+timeframe)
  ↓ evento CLOSED CANDLE (uid = symbol|timeframe|open_epoch)
IndicatorEngine (bot/indicators.py: EMA incremental, semilla SMA)
  ↓
StrategyEngine (bot/strategies.py: Ema72_150, Ema30_72_150 + registro)
  ↓ Signal (BUY/SELL/EXIT, solo vela cerrada)
BrokerInterface (bot/brokers.py) → SimulatedBroker (activo)
  ↓ fills a precio de mercado + comisión/spread
AccountManager = SimulatedBroker.get_account (balance/equity/P-L)
  ↓
Database SQLite (bot/database.py: Store) ←→ API FastAPI (bot/api.py)
  ←→ Frontend estático (bot/frontend/index.html)
Orquestador (bot/main.py) · Arranque (bot/run.py)
```

Decisión: Binance quedó descartado del flujo (`bot/providers_binance_discarded.py`,
ningún módulo lo importa). Fuente oficial = Deriv.

## 3. Estado actual

- Terminado/funcionando: CandleBuilder multi-TF, EMAs, 2 estrategias con
  cruce confirmado + épsilon, anti-duplicados (`processed` en DB),
  SimulatedBroker (comisión/spread/qty/equity/IDs únicos/restauración),
  seed + resync de historial, reconexión con backoff + ping + resuscripción,
  FastAPI (`/health`, markets, strategies, trades, signals, candles, config,
  `/ws/ui`), dashboard responsive con gráfico de velas+EMAs+marcadores,
  render.yaml, 24/24 tests OK, arranque vivo verificado contra Deriv real.
- Parcial: Deriv opera en modo `poll` (one-shot) sin auth; streaming push
  y catálogo `active_symbols` = `PENDIENTE_DE_CONFIRMAR` (requieren auth).
- Pendiente: token clásico DEMO → validar auth → probar streaming
  autenticado; registrar App ID propio (`1089` es el de pruebas, no definitivo).
- Roto: nada conocido. (Histórico: `run.py` usaba `Settings.mode`
  eliminado — corregido; comparación de cruce sensible a float —
  corregido con épsilon.)

## 4. Decisiones importantes

1. Deriv como única fuente (evidencia: `deriv_prueba/RESULTADOS.md`).
   Motivo: objetivo del proyecto + símbolos verificados.
2. Estrategias solo sobre velas cerradas; ticks jamás generan señales.
   Motivo: evitar look-ahead bias e intrabar.
3. Cruce = transición prev→curr con tolerancia épsilon, no `fast > slow`.
   Motivo: falsos positivos + ruido float.
4. Estado independiente por bot (`BotInstance`/`BotRuntime`): builder, EMAs,
   broker, posiciones, stats y última vela procesada separados.
5. `SimulatedBroker` detrás de `BrokerInterface`; `DerivBroker` stub que
   lanza `NotImplementedError` (cero llamadas ficticias).
6. Reconexión que nunca genera señales: resync solo aporta historial.
7. Secretos solo en entorno (`.env` git-ignorado); PAT nunca como token WS.

## 5. Estrategias

Regla absoluta: **solo velas CERRADAS; ticks = datos, nunca decisiones.**

- **Index Range Killer** (`irk` → clase `Ema72_150` con fast=75 slow=150;
  nombre público auto `"Index Range Killer {tf}"`):
  BUY = EMA75 cruza hacia ARRIBA EMA150 en vela cerrada;
  SELL = cruce hacia ABAJO en vela cerrada; una señal por cruce.
- **Index Range Killer +** (`irk_plus` → `Ema30_72_150` con
  entry_fast=30 mid=75 slow=150; `"Index Range Killer + {tf}"`):
  entrada BUY = EMA30 cruza ARRIBA EMA150 (cerrada);
  entrada SELL = EMA30 cruza ABAJO EMA150 (cerrada);
  salida LONG = EMA30 cruza ABAJO EMA75 (cerrada);
  salida SHORT = EMA30 cruza ARRIBA EMA75 (cerrada).
  EMA75 NO determina la entrada.
- Clases legacy `ema72_150`/`ema30_72_150` (72/…) conservadas funcionando;
  bots nuevos usan 75. Timeframe fijo por bot (inmutable).
- Cruce alcista: `prev_fast <= prev_slow AND curr_fast > curr_slow`
  (con épsilon); bajista espejo. Bots por defecto: `Index Range Killer 5m`
  (frxXAUUSD), `Index Range Killer + 15m` (frxXAUUSD), `Index Range Killer
  5m` (OTC_NDX); capital $50, lote 0.01, max 1.

## 6. Instrumentos

Defaults (`BOT_SYMBOLS`): `frxXAUUSD`, `OTC_NDX`, `R_100`.
Confirmados (prueba técnica): `frxXAUUSD`, `OTC_NDX`, `frxXAGUSD`,
`frxEURUSD`, `cryBTCUSD`, `cryETHUSD`, `R_100`, `R_50`, `1HZ100V`,
`BOOM1000`, `CRASH1000`, `JD10`, `STPRNG`.
PROHIBIDOS (InvalidSymbol): `XAUUSD`, `USTEC`, `US100`, `NAS100`, `US500`,
`TSLA`, `PAXGUSDT` (era proxy Binance, eliminado del flujo).

## 7. Timeframes

Usados: `1m 5m 15m 30m 1h 4h` (asignados por defecto: 5m y 15m).
Deriv entrega granularidades 60–86400
(60,120,180,300,600,900,1800,3600,7200,14400,28800,86400);
la arquitectura acepta cualquiera (`market_data.GRAN_ALL`).

## 8. Datos de mercado

Proveedor: `DerivProvider` (WS clásico + `ticks_history` ticks/candles).
Sin auth: `active_symbols=[]`, `ticks+subscribe→InvalidSymbol` →
modo `poll` cada `BOT_POLL_SECONDS`. Con App ID registrado + token
clásico: vía OTP a `ws/demo` (código listo en `run_client`, sin probar =
`PENDIENTE_DE_CONFIRMAR`). Reconexión: backoff 5→120 s, ping 30 s,
resuscripción, resync solo-historial. Ticks alimentan mercado/gráfico;
las señales salen solo de `CandleEngine`.

## 9. Broker

`BrokerInterface`: connect/get_account/get_balance/get_positions/
place_order/close_position/get_order_status. Activo: `SimulatedBroker`
POR BOT (capital/lote/max propios; balance/equity/posiciones/entrada/
salida/P-L/pct/duración/comisión/spread/ID `SIM-…`, snapshot inmutable por
trade, restauración desde DB). Sustitución futura por `DerivBroker` sin
modificar estrategias.

## 10. Autenticación y seguridad

Estado REAL (no inventar): API pública de solo lectura (bots, detalle,
stats, historial, mercados, velas sin EMAs; estrategia = "Private");
endpoints `/api/admin/*` exigen `X-Admin-Token: $ADMIN_TOKEN` (403 sin él).
Frontend: vista pública + login admin (token en sessionStorage).
Implementado: dashboard público de solo lectura; un único administrador
(token servidor); escritura/control protegidos también en backend; sin
registro público; sin depender de IP. Pendiente endurecer: rate-limit y
expiración de token.
Secretos: solo nombres de vars en §14; nunca valores en docs/código/logs/git.

## 11. API

Base `/` (StrategyIndex). `GET /health` (público, para Render).
PUBLIC READ (sin estrategia ni EMAs ni señales internas; strategy="Private"):
`/api/bots` (filtros bot/símbolo/timeframe/periodo/estado), `/api/bots/{id}`,
`/stats`, `/history`, `/api/markets`, `/api/candles` (velas+marcadores),
WS `/ws/ui`.
ADMIN (`X-Admin-Token`, 403 sin él): `GET/POST /api/admin/bots`,
`PATCH /api/admin/bots/{id}` (timeframe inmutable→400), `/signals`,
`/candles` (con EMAs 30/75/150 + estrategia), `/status`.

## 12. Frontend

`bot/frontend/index.html` (StrategyIndex, responsive PC/móvil, SVG, sin
emojis): vista pública (tarjetas de bots con rendimiento, filtros, detalle
con equity/drawdown/por-operación e historial, skeletons/empty/error) +
admin (login por token, crear bots, ON/OFF, detalle técnico con parámetros
y señales). Sin lógica de trading (solo fetch + render).

## 13. Base de datos

SQLite (`bot/bot.db`, git-ignorado). Tablas: `kv`, `markets(symbol,onoff,fav)`,
`assignments` (legacy, ya migrada a bots, se conserva), `bots(bot_id,name,
strategy_key,symbol,provider,timeframe,capital,lot,max_positions,status,
created,spread,commission,costs_assumed,config)`,
`trades(+bot_id,lot,spread,commission,pnl_pct,duration,params,provider,
costs_assumed)` (fotografía inmutable), `signals`, `processed(uid)`
(anti-duplicados, sobrevive reinicios), `events`. Posiciones abiertas se
restauran al arrancar.

## 14. Variables de entorno

Solo nombres: `BOT_PROVIDER`, `BOT_BROKER`, `BOT_SYMBOLS`, `BOT_TIMEFRAMES`,
`BOT_BALANCE`, `BOT_QTY`, `BOT_COMMISSION`, `BOT_SPREAD`, `BOT_POLL_SECONDS`,
`BOT_DB`, `BOT_LOG`, `HOST`, `PORT`, `DERIV_APP_ID`, `DERIV_TOKEN`,
`CMC_IDS`, `CMC_POLL_SECONDS`, `ADMIN_TOKEN`.

## 15. Tests

`bot/tests/test_bot.py` — 30 tests, sin red (FakeWS para DerivClient),
sin Binance en el flujo: construcción/cierre de velas (2), cruces 72/150 ± (2),
entrada 30/150 ± (2), salida 30/72 (1), sin falsos + intrabar-revierte→0 y
cruce-al-cierre→1 (2), anti-duplicados broker y vela (2), IDs únicos (1),
P/L con comisión/spread (1), recovery (1), stub Deriv sin fakes (1),
multi-símbolo/TF/estrategia (3), reconnect sin señal (1), interfaz
provider (1), cliente Deriv válido/inválido/OHLC/sub-poll/sub-stream/error
(6), multi-proveedor: factoría/stubs/capabilities/PairScanner/TF (6),
CMC Keyless: quotes/listados/429/timeout/JSON/activo-inexistente/OHLC/
conversión/polling/backoff/cierre/factoría (12), IRK 75/150 ± + entrada/
salida 30/75/150 + auto-nombres + defaults + TF inmutable + independencia
capital/lote + max_positions (7), stats métricas + inmutabilidad (2),
API auth 6 casos (público sin estrategia/EMAs/señales, sin modificar,
sin cambiar TF; admin con acceso).
Estado: 59/59 OK.

## 16. Problemas conocidos

1. Sin token clásico: solo modo `poll`; streaming push y catálogo =
   `PENDIENTE_DE_CONFIRMAR`.
2. PAT `pat_…` → `InvalidToken` en WS clásico (no reutilizar ahí).
3. `app_id=1089` de pruebas; propio sin registrar.
4. `NDX` existe pero sin datos (mercado/O TC); usar `OTC_NDX`.
5. DB local efímera en Render (sin disco persistente) — pendiente de
   decidir si importa para la fase demo.
6. CMC Keyless: sin streaming (solo polling), sin OHLC CEX (403), K-line
   solo pools DEX (no verificado con pool real); límites IP (429+backoff).

## 17. Próximos pasos

1. Obtener token clásico DEMO (`app.deriv.com → API Token`).
2. Re-ejecutar `deriv_prueba/prueba_deriv.py` con él (auth + catálogo).
3. Registrar App ID propio; probar OTP → `ws/demo` → streaming.
4. Solo entonces: evaluar `DerivBroker` (sin órdenes reales aún).
5. Endurecer admin (rate-limit, expiración de token). Hecho: split
   público/admin con `ADMIN_TOKEN` en backend + tests.
6. Decidir persistencia en Render. NO: trading real, Docker, estrategias
   nuevas, IA decidiendo.

## 18. Historial de cambios

- 2026-09-11: Prueba técnica Deriv (sonda, informe A–F, PAT→InvalidToken).
- 2026-09-11: Núcleo bot (candles/EMAs/2 estrategias/trade paper/API/
  dashboard/tests 13/13; fix épsilon float).
- 2026-09-11: Fase provider público Binance + SimulatedBroker (tests 18/18,
  streaming verificado) — DESCARTADA después por el usuario.
- 2026-09-11: Corrección a Deriv+SimulatedBroker (DerivProvider, símbolos
  válidos, stub NOT IMPLEMENTED, tests 24/24, arranque vivo Deriv) +
  sistema de memoria persistente.
- 2026-09-11: Abstracción multi-proveedor (factoría `create_provider`,
  `ProviderCapabilities` honestas, stubs Kraken/OKX/Bybit + `PairScanner`
  como interfaz, Binance evaluado pero NO reincorporado; Deriv intacto,
  tests 30/30, arranque vivo Deriv verificado).
- 2026-09-11: CoinMarketCapProvider Keyless experimental (polling 60 s,
  quotes/listados/map verificados en vivo; OHLC CEX 403; tests 42/42).
  Deriv intacto como principal.
- 2026-09-11: StrategyIndex Fases 1–10 (BotInstance/BotRuntime, IRK 75/150
  y + 30/75/150, CFD con snapshot inmutable, stats 24 métricas, API
  pública/admin con auth backend, dashboards+skeletons, tests 59/59,
  migración assignments→bots verificada en vivo).
