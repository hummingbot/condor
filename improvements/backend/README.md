# Backlog de mejoras — scope `backend/`

Mejoras acotadas al backend Python: el bot de Telegram (`handlers/`, `main.py`), el core
(`config_manager.py`, `utils/`) y la API del dashboard web (`condor/web/`). Numeración
**independiente** del scope `frontend/`: contador único `NNN` dentro de este scope, contando
`todo/` + `done/`, arrancando en `001` y sin reutilizar.

- `todo/` — pendientes, una por archivo.
- `done/` — implementadas, con commits anotados (las cierra `/ship-improvement`).

Generado por `/improvements` (fan-out por dimensión + verificación adversarial).
Para implementar un item: `/ship-improvement <id>` (ej. `/ship-improvement SEC-019`).
