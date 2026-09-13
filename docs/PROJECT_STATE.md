# PROJECT STATE (resumen rápido — última actualización: 2026-09-13)

- **Objetivo**: StrategyIndex — plataforma de simulación algorítmica multibot.
- **Arquitectura**: `create_provider()` → DerivProvider + CoinMarketCapProvider → CandleEngine → EMA → StrategyEngine (IRK 75/150, IRK+ 30/75/150) → Signal → SimulatedBroker por bot → SQLite/PostgreSQL → FastAPI → frontend.
- **Estado**: SIMULATION verificado en vivo. 74/74 tests pasando. Server en `localhost:8104`.
  - CoinMarketCapProvider: API key real, quotes/historical, polling, seed on-demand.
  - PostgreSQL: `database.py` soporta SQLite (local) y PostgreSQL (DATABASE_URL).
  - CORS: configurado via `CORS_ORIGINS` env var.
  - Admin rate-limit: sliding window por IP.
  - Server: localhost:8104 con CMC provider + BTC 5m bot + 400 velas seeded.
- **Archivos modificados en Fase 4**:
  - `bot/database.py`: migrado a interfaz dual SQLite/PostgreSQL (_row/_rows/_scalar helpers)
  - `bot/config.py`: +database_url, +cors_origins
  - `bot/main.py`: Store(path, database_url=settings.database_url)
  - `bot/api.py`: +CORS middleware
  - `bot/requirements.txt`: +psycopg2-binary
  - `bot/render.yaml`: reescrito para CMC + Neon
  - `bot/.env.example`: actualizado
  - `bot/trading.py`: eliminado (código muerto)
- **Para deploy en Render**:
  1. Crear Neon PostgreSQL → copiar DATABASE_URL
  2. Configurar en Render Dashboard como Secrets: DATABASE_URL, CMC_API_KEY, ADMIN_TOKEN
  3. Las demás variables ya están en render.yaml
  4. Render deployará automáticamente desde el repo
- **Limitaciones conocidas**:
  - CMC Basic plan: OHLCV Historical endpoint no soportado (403)
  - Deriv sin auth: streaming bloqueado, catálogo incompleto
  - SQLite en Render: se pierde en redeploy → Neon PostgreSQL resuelve esto
