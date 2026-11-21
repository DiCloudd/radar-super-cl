# Radar de precios de supermercados de Chile

Compara precios entre cadenas, detecta ofertas y errores de precio. Corre gratis en
GitHub Actions (ventanas de ~12 min encadenadas) y guarda la BD cifrada en la rama `gh-pages`.

## Cobertura actual

| Cadena | Fuente | Nube (Actions) | Nodo Chile |
|---|---|---|---|
| Santa Isabel | payload RSC de categoría + EAN desde la ficha | ✅ | ✅ |
| Acuenta | payload RSC (Instaleap), trae EAN | ✅ | ✅ |
| Unimarc | `__NEXT_DATA__`, trae EAN | ⏸️ Akamai 403 a IPs de GitHub | ✅ |
| Tottus | — | ❌ Cloudflare bloquea todo cliente HTTP | ❌ |
| Lider | — | ❌ descartada (PerimeterX + robots.txt) | ❌ |

Las fuentes se editan en `sources.toml` (sin tocar código).

## Comandos

```powershell
python -m radar.main scan acuenta-licores          # lee una página y la muestra
python -m radar.main window --minutes 12           # ventana de monitoreo
python -m radar.main stats
python -m pytest -q
```

## Nodo opcional en Chile (para Unimarc)

Unimarc bloquea las IPs de GitHub pero responde normal desde Chile. Un PC viejo o un
Android con Termux puede correr el mismo monitor solo para Unimarc:

```bash
git clone https://github.com/DiCloudd/radar-super-cl && cd radar-super-cl
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
MONITOR_RUNNER=chile RADAR_DB=data/chile.db python -m radar.main window --minutes 12 --only unimarc
```

(La sincronización de su BD con la nube se agrega en una fase posterior.)

## Ética

Respeta robots.txt, 1 petición a la vez por dominio con ≥4 s + jitter, headers normales de
navegador. Nunca resuelve captchas ni evade protecciones: ante un bloqueo, la tienda se pausa
con backoff exponencial y el resto sigue.
