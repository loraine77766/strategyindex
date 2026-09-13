# CHANGELOG_AUDIT — StrategyIndex

## [2026-09-13] Fase 4: Deploy Render + Migración PostgreSQL Neon

- **Migración SQLite → PostgreSQL**: `bot/database.py` reescrito con interfaz dual:
  - `Store(path)` → SQLite (local, sin cambios)
  - `Store(database_url=...)` → PostgreSQL (Neon, `psycopg2-binary`)
  - Helper methods: `_row()`, `_rows()`, `_scalar()` (compatibles con ambos motores)
  - SQL dual: `%s`/`ON CONFLICT` para PG, `?`/`INSERT OR REPLACE` para SQLite
  - `PRAGMA table_info` eliminado (no existe en PG); schema unificado vía CREATE TABLE IF NOT EXISTS
  - ALTER TABLE migrations solo en SQLite (PG crea todo el schema de golpe)
- **requirements.txt**: +`psycopg2-binary==2.9.10`
- **config.py**: +`database_url` (via `DATABASE_URL` env var), +`cors_origins`
- **main.py**: `Store(path, database_url=settings.database_url)`
- **api.py**: +CORS middleware (`CORS_ORIGINS` env var, default `*`)
- **render.yaml**: reescrito para CMC provider, CMC_API_KEY, Neon DATABASE_URL
- **.env.example**: actualizado con todas las variables de entorno necesarias
- **Tests**: 74/74 OK (SQLite path intacto, PostgreSQL listo para `DATABASE_URL`)
- **Server vivo**: localhost:8104 funcionando con CMC provider + BTC 5m bot + 400 velas seeded

### Qué configurar en Render:
1. `DATABASE_URL` = tu URL de Neon PostgreSQL (Secret en Render Dashboard)
2. `CMC_API_KEY` = tu API key de CoinMarketCap (Secret en Render Dashboard)
3. `ADMIN_TOKEN` = token secreto para endpoints admin (Secret en Render Dashboard)
4. Las demás variables ya están en `render.yaml`

### Limitación SQLite en Render:
- Render Free no tiene disco persistente → DB se pierde en redeploy/sleep
- Neon PostgreSQL resuelve esto: datos persistentes sin costo extra en tier free
- SQLite sigue disponible como fallback local (sin `DATABASE_URL`)

## [2026-09-13] Fase de consolidación: cleanup, rate-limit, audit profunda

- **Phase 1 — trading.py cleanup**: eliminado `bot/trading.py` (97 líneas
  de código muerto: `TradeEngine`, `AccountState`, `Position` — superseded
  por `brokers.py` SimulatedBroker). Verificado: 0 imports en todo el repo.
- **Phase 2 — Admin rate-limit**: implementado `_RateLimiter` sliding-window
  por IP en `bot/api.py`. Configurable via `ADMIN_RATE_LIMIT` (default 60)
  y `ADMIN_RATE_WINDOW` (default 60s). Solo aplica a `/api/admin/*` (público
  sin límite). Tests: 4 casos (under limit, 429, 403 sin token, public
  unlimited). Verificado en vivo: 3 OK + 429 tras 3 req con limit=3.
- **Phase 3 — Auditoría profunda CMC + integración completa**:
  - Auditoría automatizada de 8 archivos críticos (brokers, CMC client,
    CMC catalog, CMC provider, main, database, strategies, API).
  - **4 bugs críticos corregidos**:
    1. `_TradeShim.entry = self.price` (exit) → `self.p.entry` (entry):
       P&L% estaba calculado contra precio de cierre, no apertura.
    2. `close_trade` epoch=0 → `int(time.time())`: close_epoch y duration
       siempre eran 0. `open_epoch` de SimPosition también fixeado.
    3. `on_tick` desde executor thread → recolección en lista + deliver
       en event loop: `asyncio.Queue.put_nowait()` NO es thread-safe.
    4. `_resync()` sin dedup → `existing = {c.open_epoch for c in b.closed}`:
       reconexiones podían duplicar velas y corromper EMAs.
  - **2 bugs medium corregidos**:
    5. CMC capabilities: `history=keyed` → `keyed or bool(self.pools)`:
       keyless con pools DEX sí da history pero capability decía False.
    6. CMC client: retry solo 429 → retry 429 + 5xx (500/502/503/504).
  - **1 fix deprecación**: `asyncio.get_event_loop()` → `get_running_loop()`
    en `providers_coinmarketcap.py` y `main.py` (6 ocurrencias).
- Tests: 74/74 OK (4 nuevos: rate-limit). Server verificado en vivo en
  localhost:8104 con CMC provider + BTC 5m bot + velas reales.
- Docs: PROJECT_STATE, CHECKPOINT, CHANGELOG actualizados.

## [2026-09-12] Flujo vivo Deriv público sin token + estados de bot

- Objetivo: `python -m bot.run` → Deriv público → velas → IRK → broker
  simulado → stats → dashboard, sin `DERIV_TOKEN`, sin inventar datos.
- Auditados: `market_data.py`, `providers*.py`, `bots.py`, `brokers.py`,
  `main.py`, `run.py`, `api.py`, candles/indicators/strategies, DB,
  frontend. Producción sin mocks (fakes solo en tests).
- Modificados: `bot/bots.py` (estados STARTING/WAITING_FOR_MARKET_DATA/
  RUNNING/RECONNECTING/ERROR/STOPPED), `bot/main.py` (transiciones por
  seed/tick/conexión, logs bot+TF+cierre, fix: avisos de modo ya no marcan
  RECONNECTING, mensaje resync), `bot/api.py` (+state/last_tick/
  last_candle públicos), `bot/frontend/index.html` (badge de estado).
- NO cambió: estrategias, reglas de velas, broker, proveedores, auth, CMC.
- Prueba controlada (sintética, separada): IRK+ BUY@102.01 → EXIT@90.99 →
  P/L -11.0648 → balance 38.94 → stats total 1. OK.
- Prueba viva (DB `live.db`, `R_100` 1m + `frxXAUUSD` 5m/15m, ~12 min):
  seed 126–400 velas/bot; 8 velas 1m cerradas y evaluadas en vivo;
  0 señales y 0 trades (sin cruce real: correcto, nada fabricado);
  estados RUNNING verificados por API; precio fluyendo (R_100 ~545).
- Reinicio: 3 bots recuperados, processed 6→8 sin duplicados (uid único),
  0 señales/0 trades (sin cambios fantasma).
- Tests: 61/61 OK. `context_guard` OK.
- Pendientes: token clásico (auth/streaming), endurecer admin, Render.

## [2026-09-12] Auditoría profunda con ejecución real

- Objetivo: verificar con evidencia real (app viva, HTTP real, DB real).
- Método: app en puerto 8101 con DB temporal `audit.db` + `ADMIN_TOKEN`;
  bots A/B/C creados por `POST /api/admin/bots`; sonda controlada sobre
  clases reales; reinicio real del proceso; escaneos de mocks/dead-code.
- Bugs reales encontrados y corregidos:
  1. `latest_tick` asumía `history.prices` → `KeyError 'prices'` en bucle
     cuando Deriv responde sin historial (causa raíz §2). Fix: validación
     defensiva en `bot/market_data.py`.
  2. El pong del keepalive (30 s) robaba respuestas del poll en `ws.recv()`
     compartido (evidencia: fallos cada ~35 s). Fix: único lector `_pump` +
     apareo por `req_id` + ping por `_call` (`bot/market_data.py`).
     Verificado: 0 errores en 75 s+ con 2 ciclos de ping; precio fluyendo.
  3. `close_position` usaba qty/comisión/spread ACTUALES del broker en vez
     del snapshot de la posición (cambio de config mid-posición corrompía
     P/L). Fix: snapshot en `SimPosition` (commission/spread/qty).
  4. `BotRuntime` creaba el broker con `db=None`: ningún trade persistía.
     Fix: pasa `db` real. Crítico.
  5. El fix §2 rompió seed/resync (sin `_lock`/`_inbox` fuera de `run()`).
     Fix: `_ensure_pump` perezoso. Tests FakeWS migrados al pump real +
     regresión `test_pong_does_not_steal_response`.
- Comportamiento NO cambiado: estrategias, reglas de velas, API pública
  (sin fugas: escaneo EMA/strategy_key/params/signals limpio), auth admin
  (403/200 verificados), Deriv poll, CMC intacto.
- Tests: 61/61 OK (59 previos + regresión pong; ningún test modificado
  para pasar: los cambios fueron al código).
- Evidencia: bots A/B/C vía API (nombres/TF correctos), TF inmutable (400
  y sigue 1m), independencia (lote B intacto tras cambiar A), CFD exacto
  4/4 (cálculo independiente), max 1/3, inmutabilidad en sqlite3 + API,
  reinicio real (5 bots, balances, config lote 0.05 intactos), frontend
  servido (skeletons/SVG/fetch; sin mocks), sin imports muertos (salvo
  `trading.py` legacy desconectado, conservado), 1 proceso ~62 MB.
- Pendientes/riesgos: Deriv auth/streaming NO VERIFICADO (sin token);
  Weekend: forex con poco movimiento (precio estático observado);
  conexiones TCP no confirmadas por falta de permisos de red local;
  `trading.py` legacy sin uso; resync con error vacío ocasional (mejorar
  mensaje); admin sin rate-limit.

## [2026-09-11] Fase 1 — Auditoría completa (sin modificar código)

- Objetivo: diagnosticar arquitectura y planificar StrategyIndex.
- Archivos auditados: `bot/{providers,main,config,brokers,candles,indicators,
  strategies,database,api,run,market_data,providers_coinmarketcap}.py`,
  `bot/frontend/index.html`, `bot/tests/test_bot.py`, `docs/PROJECT_MEMORY.md`,
  `docs/PROJECT_STATE.md`, `bot/README.md`, `bot/render.yaml`.
- Archivos modificados: ninguno (solo se crea este changelog).
- Funcionalidades existentes: DerivProvider (poll verificado) + CMC experimental,
  CandleBuilder multi-TF, EMA incremental + series, Ema72_150/Ema30_72_150 con
  cruce prev→curr + épsilon, SimulatedBroker (comisión/spread/IDs/restauración),
  SQLite (kv, markets, assignments, trades, signals, processed, events),
  FastAPI + dashboard canvas, 30/30 tests OK.
- Qué debe modificarse: modelo BotInstance por encima de assignments
  (capital/lote/max_positions propios, timeframe fijo, nombre auto);
  estrategias 75/150 y 30/75/150 como variantes paramétricas (clases actuales
  ya son paramétricas: reutilizar sin reescribir); snapshot inmutable en
  trades + columnas nuevas; motor de estadísticas; split API pública/admin
  con auth backend; dashboard público + admin.
- Qué debe conservarse: DerivProvider, CMC, CandleEngine, IndicatorEngine,
  SimulatedBroker, lógica de cruce, anti-duplicados, reconexión, dashboard
  base, DerivBroker stub, 30 tests (migrar solo los acoplados a `assignments`
  interno, justificándolo aquí).
- Riesgos: refactor de Orchestrator rompe tests TestSystem/TestEngine
  (acoplados a `assignments`/`engine` internos) → se migrarán a la API de
  bots documentando el motivo; DB existente `bot/bot.db` trae assignments
  viejos → migración automática a bots una sola vez.
- Pendientes: Fases 2–10 según especificación.

## [2026-09-11] Fases 2–3 — Bots independientes + Index Range Killer

- Objetivo: BotInstance/BotRuntime + estrategias 75/150 y 30/75/150.
- Auditados: `strategies.py` (clases paramétricas reutilizables),
  `brokers.py`, `main.py`, `database.py`.
- Modificados: `bot/bots.py` (nuevo: BotInstance, nombre auto
  "Index Range Killer {tf}" / "+ {tf}", capital 50/lote 0.01/max 1,
  timeframe fijo, CATALOG irk/irk_plus), `bot/main.py` (orquestador
  multibot, migración única assignments→bots, 1 fallo no detiene otros).
- NO cambió: `strategies.py`, `candles.py`, `indicators.py`, DerivProvider,
  regla velas cerradas, cruce prev→curr+épsilon, anti-duplicados, reconexión.
- Tests: 43/43 (migrados 4 tests acoplados a `assignments` interno:
  test_03/09/10/11/14 → API de bots; motivo: modelo sustituido).
- Problemas: `bots` INSERT con 14 placeholders/15 columnas → corregido.
- Pendientes: resto de fases.

## [2026-09-11] Fases 4–5 — CFD robusto + historial inmutable + stats

- Modificados: `bot/database.py` (tabla `bots`, migración columnas snapshot
  en `trades`: bot_id, lot, spread, commission, pnl_pct, duration, params,
  provider, costs_assumed), `bot/brokers.py` (SimPosition.bot_id,
  `place_order(...,bot_id,extra)`, qty en shim, restore con lot/bot_id),
  `bot/stats.py` (nuevo: 24 métricas + curvas + drawdown + rachas).
- NO cambió: fills, comisiones, interfaz BrokerInterface, DerivBroker stub.
- Tests: broker/P&L/recovery existentes OK + nuevos (métricas, inmutabilidad).
- Riesgos: spread/comisión marcados `costs_assumed` (supuestos, no reales).

## [2026-09-11] Fase 6 — API pública vs admin

- Modificados: `bot/api.py` (reescrito; rutas viejas `/api/strategies`,
  `/api/trades/*`, `/api/config` eliminadas por exponer internos sin auth —
  motivo documentado), `bot/requirements.txt` (+httpx para tests).
- Público: `/api/bots`, `/api/bots/{id}`, `/stats`, `/history`,
  `/api/markets`, `/api/candles` (sin EMAs/estrategia/señales/provider).
- Admin (`X-Admin-Token=$ADMIN_TOKEN`, 403 sin él): `/api/admin/bots`,
  POST/PATCH (timeframe inmutable → 400), signals, candles con EMAs 30/75/150,
  status. Sin ADMIN_TOKEN configurado todo admin es 403.
- Tests: 6 casos auth exigidos OK. Bug detectado por tests: `require_admin()`
  como default se evaluaba al definir rutas → pasado a `Depends`; snapshot
  sin estrategia → `open_trade` usa `extra.strategy/timeframe`.
- Verificado en vivo: público sin token (strategy Private), admin 403 sin
  token y 200 con token.

## [2026-09-11] Fases 7–9 — Dashboards + gráficos + UX

- Modificados: `bot/frontend/index.html` (reescrito: StrategyIndex público
  con tarjetas, filtros bot/símbolo/TF/periodo, detalle con equity/drawdown/
  por-operación, skeletons shimmer, empty/error; admin con login por token,
  crear bots, ON/OFF, detalle técnico, señales; SVG, sin emojis, responsive).
- NO cambió: ninguna lógica de trading en frontend (solo fetch+render).
- Verificado en vivo: tarjetas públicas y login admin funcionales.

## [2026-09-11] Fase 10 — Tests + re-auditoría + docs

- Tests: 59/59 OK (30 previos + IRK/bots/stats/auth nuevos).
- Re-auditoría: `context_guard` OK; imports flujo principal OK; arranque
  vivo OK (migración 3 assignments→bots, Deriv poll, API pública+admin).
- Docs: PROJECT_MEMORY/STATE/CHECKPOINT actualizados.
- Riesgos: `trading.py` (TradeEngine viejo) quedó sin uso — no se elimina
  (histórico); DB `bot/bot.db` migrada in situ; admin sin rate-limit
  (fase local); persistencia Render efímera (pendiente).
