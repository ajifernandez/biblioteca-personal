# Biblioteca personal

App para catalogar los libros de casa: escaneo de código de barras con la cámara del
móvil, autocompletado de título/autor/portada, ubicación física y historial de lecturas.
Búsqueda por título, autor, ISBN o ubicación.

Stack: Flask + SQLite + HTMX. Un único contenedor, sin dependencias externas en runtime
aparte de las APIs públicas de metadatos (Google Books, Open Library).

## Desarrollo local

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
DB_PATH=./data/biblioteca.db python app.py
```

Abre http://localhost:5000

Nota: el escaneo por cámara (`getUserMedia`) solo funciona en `localhost` o HTTPS. En
local con `python app.py` funciona porque es localhost; para acceso remoto necesitas
HTTPS (ver despliegue).

## Despliegue en casa (Raspberry Pi / NAS / mini PC)

```bash
docker compose up -d --build
```

Los datos quedan en el volumen `biblioteca_data` (SQLite), persisten entre reinicios.

### Acceso remoto con Tailscale (recomendado)

Necesario porque el escaneo con cámara exige HTTPS fuera de localhost.

1. Instala Tailscale en el servidor de casa y en tu móvil/portátil, mismo tailnet:
   https://tailscale.com/download
2. En el servidor, expón la app con certificado HTTPS automático:
   ```bash
   sudo tailscale serve --bg 5000
   ```
3. Tailscale te da una URL tipo `https://tu-maquina.tu-tailnet.ts.net`. Úsala desde
   cualquier dispositivo dentro de tu tailnet — funciona la cámara porque es HTTPS válido.

No necesitas abrir puertos en el router ni exponer nada a internet público.

### Alternativa: Caddy + dominio propio

Si prefieres no usar Tailscale, pon Caddy delante como reverse proxy con un dominio
propio (Let's Encrypt automático) y abre el puerto 443 en el router hacia el servidor.
Más expuesto que Tailscale; solo recomendable si ya tienes ese setup.

## Backup

Copia el volumen o simplemente el fichero SQLite:

```bash
docker cp biblioteca_personal:/data/biblioteca.db ./backup-$(date +%F).db
```
