# Handoff: Biblioteca Personal

Documento de traspaso para que otra IA (o humano) continúe este proyecto sin perder
contexto. Léelo entero antes de tocar código — recoge no solo qué existe, sino **por
qué** se tomaron ciertas decisiones, para no deshacerlas sin darse cuenta.

## Qué es esto

App casera para catalogar los libros físicos de casa: escaneo de código de barras
(ISBN) con la cámara del móvil, autocompletado de metadatos, ubicación física del
libro en casa, historial de lecturas, y búsqueda. Uso personal/familiar, sin
multiusuario ni autenticación (se apoya en que el acceso remoto será vía red privada,
ver sección Despliegue).

Repo: https://github.com/ajifernandez/biblioteca-personal (público, sin secretos).
Ruta local: `/home/user/personal_projects/biblioteca_personal` — es un repo git
**independiente**, no forma parte del monorepo `personal_projects` (ese monorepo no
tiene commits todavía y contiene proyectos no relacionados; no mezclar).

## Decisiones de diseño (y por qué)

- **Stack deliberadamente simple**: Flask + SQLite + HTMX + Jinja2, un único
  contenedor Docker. El usuario eligió explícitamente esto sobre FastAPI+Next.js+Postgres
  (que sí usa en otro proyecto, `telemed/`) porque esto es una app casera pequeña, no
  quiere la complejidad de un stack multi-servicio.
- **Sin autenticación / sin multiusuario**: a propósito. Es una app de un hogar, el
  control de acceso se delega a la red (LAN / Tailscale), no a la app. No añadir login
  a menos que el usuario lo pida explícitamente.
- **Sin gestión de préstamos** ("quién tiene prestado este libro"): se consideró y se
  descartó por scope creep — el usuario solo pidió catálogo + historial de lectura +
  búsqueda + ubicación. No añadir funciones no pedidas (ver CLAUDE.md del repo padre:
  "No design for hypothetical future requirements").
- **Portada de libro**: dos formas de asignarla — (a) automática vía lookup de ISBN,
  (b) manual, con **subida de foto** (`<input type="file" capture="environment">`,
  abre la cámara en móvil) o pegar una URL. La subida de archivo tiene prioridad sobre
  la URL si ambas se envían. Al editar un libro existente, si no se sube archivo ni se
  escribe URL, se conserva la portada actual (no se puede "vaciar" la portada desde el
  formulario — limitación aceptada, no un bug).
- **Lookup de ISBN con fallback**: Google Books API primero, Open Library como
  fallback si Google Books no tiene el libro o falla. Ninguna de las dos requiere API
  key. Ver `lookup_isbn()` en `app.py`.

## Estado actual — todo funcional y probado

Estructura de archivos:
```
app.py                        # toda la lógica backend (Flask, una sola pieza)
schema.sql                    # books + reading_log (SQLite)
requirements.txt              # flask, requests, gunicorn
Dockerfile                    # python:3.12-slim + gunicorn, VOLUME /data
docker-compose.yml            # build local (context: .)
deploy/dockge-compose.yaml    # build remoto desde GitHub (ver Despliegue)
Makefile                      # run, run-https, docker-up/down, clean
static/style.css              # sistema de diseño CSS a medida, dark mode, responsive
templates/
  base.html                   # nav + layout
  index.html                  # búsqueda + listado
  scan.html                   # escaneo cámara + formulario alta
  book_detail.html            # ficha, editor, historial
  partials/_book_list.html    # resultados búsqueda (HTMX)
  partials/_history.html      # timeline historial lectura (HTMX)
```

Rutas principales (`app.py`):
- `GET /` — página de búsqueda
- `GET /search?q=&field=` — resultados HTMX (field: todo/titulo/autor/isbn/ubicacion)
- `GET /scan` — página de escaneo por cámara
- `GET /api/lookup/<isbn>` — JSON con metadata o `{found: false}`
- `POST /books` — crear libro (multipart, soporta `cover_file` o `cover_url`)
- `GET /books/<id>` — ficha del libro
- `POST /books/<id>` — editar libro (multipart, misma lógica de portada)
- `POST /books/<id>/delete`
- `POST /books/<id>/history` — marcar "leyendo" o "leido" (con rating/notas)
- `GET /uploads/<filename>` — sirve fotos de portada subidas

Base de datos: `DB_PATH` (env var, default `/data/biblioteca.db`, local dev
`./data/biblioteca.db`). Fotos subidas en `<dirname(DB_PATH)>/uploads/`, es decir,
**mismo volumen que la BD** — no hace falta un volumen Docker separado.

Frontend: escaneo por cámara con `html5-qrcode` (CDN, formato EAN_13), HTMX para
búsqueda y formularios de historial sin recargar página. Sin build step, sin
JS framework, todo servido directo por Flask/Jinja2.

Diseño: responsive mobile-first (el uso previsto es desde el móvil). Puntos
importantes ya resueltos en `static/style.css`:
- inputs a `16px` de font-size — evita que iOS Safari haga zoom automático al enfocar
- `@media (max-width: 600px)`: grid de formularios a 1 columna, hero del libro pasa a
  columna, botón de nav colapsa a solo icono, grid de libros a 1 columna
- dark mode automático vía `prefers-color-scheme` (sin toggle manual, no se pidió)

## Despliegue — lo importante para no repetir errores ya resueltos

**Problema resuelto: cámara no funciona por IP+HTTP.** `getUserMedia` (acceso a
cámara) lo bloquean los navegadores fuera de `localhost` o HTTPS. Acceder por
`http://192.168.x.x:5000` desde el móvil deja ver la página pero la cámara no arranca.

**Camino descartado: HTTPS con certificado autofirmado (`ssl_context="adhoc"` de
Flask/pyOpenSSL).** Existe como `make run-https` pero **no es fiable**: el certificado
que genera carece de SAN (Subject Alternative Name), y varios navegadores móviles
(Chrome/Safari en Android/iOS) se quedan colgados negociando TLS en vez de mostrar un
aviso claro de "no seguro". No merece la pena seguir depurando esto — no perder tiempo
aquí si se vuelve a topar con el mismo síntoma.

**Camino recomendado y en curso: Tailscale.**
```bash
sudo tailscale up          # autenticación interactiva (login por navegador)
sudo tailscale serve --bg 5000   # expone la app con HTTPS válido, sin abrir puertos
```
Da una URL `https://<maquina>.<tailnet>.ts.net` con certificado válido, accesible desde
cualquier dispositivo con Tailscale instalado y la misma cuenta (incluido el móvil).
Sin exponer nada a internet público, sin tocar el router.

Estado a fecha de este documento: Tailscale **está instalado** en la máquina de
desarrollo pero **no autenticado** (`tailscale status` devolvía "Logged out"). Pendiente
que el usuario ejecute `sudo tailscale up` y complete el login — es un paso que debe
hacer él mismo (requiere su cuenta), no delegable a la IA sin credenciales.

**Despliegue en producción: Dockge.** El usuario tiene Dockge corriendo en
`192.168.1.136:5001` (gestor de stacks docker-compose vía UI web) y quiere la app ahí,
no en la máquina de desarrollo.

Truco usado para evitar tener que clonar el repo a mano en el host de Dockge: Docker
Compose soporta `build.context` como **URL de git directamente**. Validado
funcionando end-to-end (`docker build https://github.com/ajifernandez/biblioteca-personal.git`
construye y el contenedor arranca correctamente con gunicorn). El archivo
`deploy/dockge-compose.yaml` ya tiene esto listo — solo hay que pegarlo en la UI de
Dockge ("+ Compose" → pegar → Deploy). Para actualizar tras un push nuevo: botón
"Rebuild" en Dockge (vuelve a clonar y reconstruir).

Pendiente de confirmar con el usuario: si ya desplegó en Dockge y si ya completó el
login de Tailscale. No asumir que estos pasos están hechos — pregunta antes de dar por
sentado que la app ya es accesible remotamente.

## Limitaciones conocidas (aceptadas, no bugs)

- Sin límite de tamaño en la subida de fotos de portada, ni redimensionado — para uso
  personal de bajo volumen no se consideró necesario, pero si el usuario reporta que
  el disco crece mucho o subidas lentas desde móvil con fotos grandes, esto sería lo
  primero a mirar (añadir `MAX_CONTENT_LENGTH` en Flask y/o redimensionar con Pillow).
- No se puede vaciar la portada de un libro desde el formulario de edición (ver arriba).
- Búsqueda es `LIKE` simple sobre SQLite, sin full-text search ni tolerancia a tildes
  distintas — suficiente para una biblioteca casera, no se ha pedido más.
- `reading_log`: un libro puede tener múltiples ciclos de lectura (releer), el sistema
  de "abrir/cerrar" entradas usa la última entrada `leyendo` sin `finished_at` — si se
  pulsa "empezar a leer" varias veces sin cerrar, puede haber más de una entrada
  abierta simultánea (edge case no bloqueado, poco probable en uso real).

## Cómo probar en local

```bash
make run        # venv + flask dev server en http://localhost:5000
make run-https  # variante con SSL adhoc — no fiable en móvil, ver arriba
make docker-up  # build + run vía docker compose local
make clean      # borra venv/data/__pycache__
```

Al probar manualmente con curl/scripts: **cuidado con no borrar `venv/` o `data/`
mientras el servidor sigue corriendo** — en una sesión anterior esto corrompió la venv
(`ModuleNotFoundError: No module named 'flask.debughelpers'`) por una condición de
carrera. Si pasa, `rm -rf venv && python3 -m venv venv && pip install -r
requirements.txt` desde cero lo arregla; no es un bug del código.

## Estilo de trabajo esperado por el usuario

- Español, respuestas concisas.
- CLAUDE.md del repo padre aplica: pensar antes de actuar, no reescribir archivos
  enteros si se puede editar, no añadir abstracciones/features no pedidas, probar antes
  de dar por terminado.
- El usuario prefiere que se pruebe de verdad (curl end-to-end, no solo "debería
  funcionar") antes de reportar algo como hecho — así se ha trabajado hasta ahora y ha
  detectado varios problemas reales antes de que llegaran al usuario (el fallo de venv
  corrupta, validar el build de Dockge antes de dárselo).
- Antes de acciones con efecto visible hacia fuera (crear repos, hacer push, decidir
  visibilidad pública/privada) se ha preguntado al usuario en vez de asumir.
