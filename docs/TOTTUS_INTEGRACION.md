# Integración autorizada Tottus × Radar de Precios

Documento para el equipo de TI / Seguridad de Tottus. Describe cómo habilitar el acceso del radar
de precios de forma controlada, sin desactivar la protección de Cloudflare para nadie más.

## Qué hace el radar

- Lee **solo páginas públicas de listado de categorías** de `www.tottus.cl`
  (`/tottus-cl/lista/<CATG>/<nombre>?sortBy=derived.currentPrice,asc&page=N`), igual que un cliente.
- No usa carrito, login, checkout ni datos personales. Respeta `robots.txt`.
- Volumen: **1 petición a la vez**, con al menos 4 segundos entre peticiones (≈ 12–15 por minuto como
  máximo). Unas 15 categorías, barrido completo cada pocas horas.
- Origen: GitHub Actions (las IP **cambian** en cada ejecución, por eso la autorización es por llave
  y no por IP).

## Lo que necesitamos de Tottus (una regla en Cloudflare)

Crear una **llave secreta compartida** (cadena aleatoria larga, por ejemplo de 40 caracteres) y
una regla que omita el desafío de bots solo para las peticiones que la traen:

1. Cloudflare Dashboard → zona `tottus.cl` → **Security → WAF → Custom rules → Create rule**.
2. Nombre: `Radar precios (socio)`.
3. Expresión:

   ```
   (http.host eq "www.tottus.cl"
    and starts_with(http.request.uri.path, "/tottus-cl/lista/")
    and any(http.request.headers["x-radar-partner"][*] eq "<LLAVE_SECRETA>"))
   ```

4. Acción: **Skip** → marcar *All remaining custom rules*, *Super Bot Fight Mode* / *Bot Fight Mode*
   y *Managed rules* según el plan.
5. (Recomendado) Una regla de **Rate limiting** para el mismo header: máximo 30 peticiones por minuto.

Así la excepción aplica **solo** a los listados y **solo** a quien tenga la llave. Para revocarla
basta con borrar la regla o cambiar la llave.

> Alternativa equivalente: un feed o API de productos (CSV/JSON con SKU, EAN, precio, precio normal,
> precio CMR y stock). Si existe, el radar puede leerlo directamente.

## Del lado del radar

- La llave se guarda en el repositorio como **GitHub Secret** `TOTTUS_PARTNER_TOKEN` (nunca en código
  ni en logs) y se envía en el header `X-Radar-Partner`.
- Sin la llave, las fuentes de Tottus no se activan.
- Si Cloudflare igual muestra el desafío (llave equivocada o regla desactivada), el radar **pausa
  Tottus automáticamente** con espera creciente y no reintenta en bucle.

## Contacto

Responsable del radar: (completar).
