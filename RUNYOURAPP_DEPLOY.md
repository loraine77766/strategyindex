# RunYour.App — Deploy de StrategyIndex

## Qué es esta variante

Un wrapper mínimo en la raíz del repo que expone `main:app` para que RunYour.App detecte la aplicación FastAPI automáticamente. **No duplica lógica**: reutiliza 100% de `bot/`.

## Archivos nuevos (específicos RunYour.App)

| Archivo | Función |
|---------|---------|
| `main.py` (raíz) | Entrypoint FastAPI con lifecycle (startup → provider, shutdown → stop) |
| `requirements.txt` (raíz) | Dependencias para RunYour.App (idéntico a `bot/requirements.txt`) |

**Archivos NO modificados**: toda la lógica en `bot/` permanece igual.

## Cómo arrancar localmente

```bash
# Desde la raíz del repo
uvicorn main:app --host 0.0.0.0 --port 8000

# O con variables de entorno
set DATABASE_URL=postgresql://...
set CMC_API_KEY=tu-key
set ADMIN_TOKEN=tu-token
uvicorn main:app --host 0.0.0.0 --port 8000
```

Verificar: `curl http://localhost:8000/health` → `{"ok":true,"time":...}`

## Variables de entorno necesarias

| Variable | Obligatoria | Descripción |
|----------|-------------|-------------|
| `DATABASE_URL` | Sí (producción) | PostgreSQL Neon URL |
| `CMC_API_KEY` | Sí | CoinMarketCap API key |
| `ADMIN_TOKEN` | Sí | Token para endpoints admin |
| `PORT` | No (auto) | Puerto dinámico (RunYour.App lo asigna) |
| `BOT_PROVIDER` | No (default: `deriv`) | Proveedor de datos |
| `BOT_SYMBOLS` | No (default: `frxXAUUSD,OTC_NDX,R_100`) | Símbolos |
| `CORS_ORIGINS` | No (default: `*`) | Orígenes permitidos |

## Configuración en RunYour.App

1. Conectar repo de GitHub
2. **Build Command**: `pip install -r requirements.txt`
3. **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Agregar environment variables: `DATABASE_URL`, `CMC_API_KEY`, `ADMIN_TOKEN`

## Diferencia con Render

| | Render | RunYour.App |
|---|--------|-------------|
| Entry point | `python -m bot.run` | `uvicorn main:app` |
| Requirements | `bot/requirements.txt` | `requirements.txt` (raíz) |
| Orchestrator | En `run.py` (thread separada) | En `main.py` (event loop de uvicorn) |
| Provider | `asyncio.run()` en main thread | `asyncio.create_task()` en startup event |

## Limitaciones

- Sin WebSocket streaming en tiempo real (RunYour.App no garantiza conexiones persistentes)
- El provider CMC puede tener rate limits en tier gratuito
- Neon free tier: 0.5 GB almacenamiento, 24/7 compute hours

## Qué SÍ funciona

- Todas las rutas públicas (`/api/bots`, `/api/markets`, `/api/candles`)
- Todas las rutas admin (`/api/admin/*`)
- Frontend HTML
- Simulación de trading
- Estadísticas
- Persistencia en PostgreSQL Neon
- CoinMarketCap data
