# Radar de precios de supermercados de Chile

Compara precios entre cadenas, detecta ofertas y errores de precio. Corre gratis en
GitHub Actions (ventanas de ~12 min encadenadas) y guarda la BD cifrada en la rama `gh-pages`.

## Cobertura actual

| Cadena | Fuente | Nube (Actions) | Nodo Chile |
|---|---|---|---|
| Lider | `__NEXT_DATA__` de /browse/ (EAN desde usItemId) | ✅ best-effort (pausa si aparece PerimeterX) | ✅ |
| Jumbo | mismo extractor que Santa Isabel (Cencosud) | ✅ | ✅ |
| Santa Isabel | payload RSC de categoría + EAN desde la ficha | ✅ | ✅ |
| Acuenta | payload RSC (Instaleap), trae EAN | ✅ | ✅ |
| Unimarc | `__NEXT_DATA__`, trae EAN | ⏸️ Akamai 403 a IPs de GitHub | ✅ `tools/node_chile.sh` |
| Alvi (mayorista) | mismo extractor que Unimarc (SMU) + precios por cantidad | ⏸️ Akamai 403 | ✅ `tools/node_chile.sh` |
| Super10 | solo catálogo de ofertas para socios (sin catálogo completo) | — | posible a futuro |
| Cugat | — | ❌ Cloudflare | — |
| Tottus | `__NEXT_DATA__` de /lista/ (48/pág) | 🔑 lista; se activa con la llave de socio (`TOTTUS_PARTNER_TOKEN`) | — |

Comparación entre cadenas: EAN primero; si no hay, marca + contenido exacto + nombre casi idéntico
(`radar/matcher.py`). Errores de precio con reglas estrictas en `radar/analysis.py`.

Las fuentes se editan en `sources.toml` (sin tocar código).

## Comandos

```powershell
python -m radar.main scan acuenta-licores          # lee una página y la muestra
python -m radar.main window --minutes 12           # ventana de monitoreo
python -m radar.main stats
python -m pytest -q
```

## Nodo opcional en Chile (para Unimarc y Alvi)

**En Windows (este PC):** `powershell -ExecutionPolicy Bypass -File tools
ode_windows.ps1 -Install`
crea una copia en `%USERPROFILE%adar-node`, su entorno Python y la tarea programada oculta
`RadarSuperNodo` (inicia al iniciar sesión). Log: `%USERPROFILE%adar-node
ode.log`.
Para quitarlo: `... node_windows.ps1 -Uninstall`.

**En Linux / Android (Termux) / Raspberry Pi:**

Unimarc bloquea las IPs de GitHub pero responde normal desde Chile. Un PC viejo o un
Android con Termux puede correr el mismo monitor solo para Unimarc:

```bash
git clone https://github.com/DiCloudd/radar-super-cl && cd radar-super-cl
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
bash tools/node_chile.sh
```

El script `tools/node_chile.sh` hace todo en bucle: ventana de 12 min, exporta y sube a la rama
`node-data`; cada ventana de la nube lo integra automáticamente.

## Ética

Respeta robots.txt, 1 petición a la vez por dominio con ≥4 s + jitter, headers normales de
navegador. Nunca resuelve captchas ni evade protecciones: ante un bloqueo, la tienda se pausa
con backoff exponencial y el resto sigue.

## Acceso privado por dispositivo

Los datos de la app se publican **cifrados** (AES-256-GCM). Solo los dispositivos aprobados
reciben la llave (ECDH P-256 + HKDF), y cada uno genera su propia llave privada en el navegador,
que nunca sale del teléfono. La lista de dispositivos se guarda cifrada en `access/devices.enc`.

1. Abre la app en el dispositivo nuevo: verás su código (ej. `K7Q3-MZ2A`) y el botón **Enviar solicitud**.
2. En la app de GitHub: **Actions → dispositivos → Run workflow**, acción `aprobar`, pega la solicitud
   (`RADAR1...`) y un apodo neutro.
3. Se activa en la próxima actualización de datos (máx. ~25 min).
4. Para quitar acceso: acción `bloquear` con el código. Desde la siguiente actualización ese
   dispositivo ya no puede descifrar los datos nuevos (la llave cambia en cada publicación).

La app instalada en la pantalla de inicio del iPhone tiene almacenamiento propio: cuenta como otro
dispositivo y también hay que aprobarla.

## Tottus (integración autorizada)

El extractor está listo. Se activa cuando Tottus configura en su Cloudflare la regla descrita en
[docs/TOTTUS_INTEGRACION.md](docs/TOTTUS_INTEGRACION.md) y la llave acordada se guarda como GitHub Secret
`TOTTUS_PARTNER_TOKEN`. No se evade ninguna protección.
