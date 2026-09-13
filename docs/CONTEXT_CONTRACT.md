# CONTEXT CONTRACT (reglas obligatorias para OpenCode en este proyecto)

1. Nunca asumir que algo existe sin comprobarlo (símbolos, endpoints,
   respuestas): verificar contra la API o el código y citar evidencia.
2. Leer `docs/PROJECT_STATE.md` antes de comenzar una tarea importante.
3. Leer `docs/PROJECT_MEMORY.md` cuando se necesite contexto amplio.
4. No modificar arquitectura sin registrar la decisión en la memoria.
5. No cambiar reglas de trading sin autorización explícita del usuario.
6. Nunca introducir lógica de trading dentro del frontend.
7. Nunca permitir que el frontend sea la única barrera de seguridad.
8. Toda operación administrativa debe estar protegida también en backend.
9. Nunca utilizar secretos reales en archivos de documentación.
10. No borrar funcionalidades existentes para solucionar un problema sin
    justificarlo; conservar lo que funciona (ej. Binance quedó como
    archivo descartado, no se destruyó nada útil).
11. Antes de modificar código crítico, comprobar dependencias.
12. Ejecutar tests relacionados después de cambios importantes.
13. Reglas de trading inviolables: solo velas cerradas; jamás señales
    intrabar; vela = symbol+timeframe+timestamp; una vela nunca dos veces;
    sin look-ahead bias; StrategyEngine independiente del broker;
    MarketData sin lógica de estrategia.
14. Seguridad objetivo: dashboard público solo lectura; un único admin;
    login solo `/admin`; sin registro público; control protegido en
    backend; no usar IP como autorización.
15. Si falta contexto (alguna de: objetivo, arquitectura, qué funciona,
    qué falta, reglas de trading, seguridad, archivos a tocar), NO
    programar: ejecutar `context:restore` primero.
