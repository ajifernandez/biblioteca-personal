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
local con `python app.py` funciona porque es localhost. Para probar desde el móvil por
IP de la LAN (`http://192.168.x.x:5000`) la cámara no funcionará — usa el despliegue
con Tailscale (más abajo) para tener HTTPS válido también en pruebas.

(Existe `make run-https`, que arranca con un certificado autofirmado vía
`ssl_context="adhoc"`, pero varios navegadores móviles se quedan colgados negociando
TLS con ese certificado por no incluir SAN — no es fiable, mejor usar Tailscale.)

## Despliegue en casa (Raspberry Pi / NAS / mini PC)

```bash
docker compose up -d --build
```

Los datos quedan en el volumen `biblioteca_data` (SQLite), persisten entre reinicios.

### Despliegue con Dockge

Repo: https://github.com/ajifernandez/biblioteca-personal

1. En Dockge, "+ Compose" → nombre `biblioteca-personal`.
2. Pega el contenido de [`deploy/dockge-compose.yaml`](deploy/dockge-compose.yaml) — usa
   `build.context` apuntando directo al repo de GitHub, así Dockge no necesita el código
   clonado en su host, solo Docker con acceso a internet.
3. "Deploy". Docker clona el repo y construye la imagen en el momento.
4. Para actualizar tras un cambio en el repo: botón "Rebuild" en Dockge (vuelve a clonar
   y reconstruir con el último commit).

Los datos (SQLite) persisten en el volumen `biblioteca_data`, independiente del código.

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
