# EducaMadrid substitution sync

Servicio que inicia sesión en EducaMadrid, descarga las respuestas nuevas del formulario de
sustituciones (LimeSurvey), las guarda localmente y las importa en el Teacher Substitution
Manager vía su API (`POST /absences`). Es un servicio más de `docker-compose.yml`: usa
Playwright con un Chromium real (instalado en su propia imagen) para completar el login SSO
de EducaMadrid, y expone un pequeño endpoint HTTP (`POST /run`) para dispararlo bajo demanda
— desde el botón de administración de la app, o desde una tarea programada.

## Configuración

1. Añade en el `.env` de la raíz del repo (junto a `JWT_SECRET`, `ADMIN_EMAIL`, etc.):

   ```
   EDUCAMADRID_USERNAME=tu-usuario
   EDUCAMADRID_PASSWORD=tu-contraseña
   EDUCAMADRID_SURVEY_URL=https://formularios.educa.madrid.org/index.php/admin/survey/sa/view/surveyid/266389
   EDUCAMADRID_SURVEY_ID=266389
   SYNC_TRIGGER_TOKEN=un-secreto-largo-y-aleatorio
   ```

   `SYNC_TRIGGER_TOKEN` es un secreto compartido entre `api` y `sync`: solo la propia
   aplicación (autenticada como administrador) puede disparar una ejecución. La API propia
   se autentica reutilizando `ADMIN_EMAIL`/`ADMIN_PASSWORD`, ya presentes en el `.env` raíz.
   `APP_API_URL` no hace falta tocarlo: `docker-compose.yml` lo apunta automáticamente a
   `http://api:8000` (la red interna de Docker).

2. `config/field_mapping.json` ya está verificado contra una ejecución real del formulario
   266389: las columnas del CSV exportado son las cabeceras en español que LimeSurvey usa
   por defecto (`Usuario`, `Fecha`, `Sesión`, `Grupo`, `Aula`, `Tareas`,
   `ID de respuesta`, `Fecha de envío`), no códigos de pregunta. El día de la semana se
   calcula solo a partir de `Fecha`, no hace falta una columna aparte. Este fichero está
   montado como volumen (`sync/config`), así que puedes editarlo y solo hace falta
   reiniciar el contenedor (`docker compose restart sync`), no reconstruirlo. Si cambias de
   formulario o LimeSurvey cambia el idioma de exportación, copia de nuevo
   `config/field_mapping.example.json` y ajusta los nombres tras revisar una fila real (ver
   `data/pending_responses.json` después de una ejecución).

## Arranque

```powershell
docker compose up --build
```

Esto construye la imagen de `sync` (incluye `playwright install --with-deps chromium`, por
lo que la primera build tarda unos minutos) y la deja escuchando internamente en el puerto
8080, solo accesible desde la red interna de Docker — no se publica ningún puerto al host.

## Cómo se dispara una ejecución

- **Desde la app**: un administrador pulsa "Sincronizar EducaMadrid" en el Dashboard. El
  backend llama a `POST http://sync:8080/run` con el `SYNC_TRIGGER_TOKEN` y devuelve el
  resultado (respuestas descargadas, completadas, importadas y pendientes) a la interfaz.
- **De forma desatendida**: programa una tarea (Task Scheduler en Windows, cron en Linux)
  que haga la misma llamada HTTP, por ejemplo:

  ```powershell
  Invoke-RestMethod -Uri "http://localhost:8000/admin/sync-educamadrid" -Method Post -Headers @{Authorization="Bearer $token"}
  ```

  usando un token de administrador obtenido previamente de `POST /auth/login`, ya que el
  endpoint público es el del backend (`/admin/sync-educamadrid`), que a su vez reenvía la
  petición al servicio `sync`. Documenta este script si lo automatizas, igual que
  cualquier otra credencial de administrador.

## Primera ejecución (supervisada)

Antes de fiarte del botón, comprueba una vez con el navegador visible que los selectores
del login de Keycloak y de la pantalla de exportación de LimeSurvey son correctos (pueden
variar del valor por defecto asumido en `educamadrid_sync/browser_login.py` y
`educamadrid_sync/limesurvey_export.py`). Esto requiere una instalación local temporal
(fuera de Docker, porque necesitas ver la ventana del navegador):

```powershell
cd sync
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
$env:EDUCAMADRID_HEADLESS = "false"
python -m educamadrid_sync.run
```

Revisa `data/sync.log` y `data/pending_responses.json` tras la ejecución. Las respuestas
que no se puedan mapear (profesor o sesión no encontrados) se quedan en
`pending_responses.json` con un aviso en el log — no se importan a ciegas, y se reintentan
solas en la siguiente ejecución.

## Qué hace cada ejecución

1. Inicia sesión en EducaMadrid (Playwright + Keycloak SSO).
2. Descarga el CSV de respuestas del formulario 266389 reutilizando esa sesión.
3. Guarda las respuestas nuevas en `data/pending_responses.json` (formato local
   persistente, montado como volumen para que sobreviva a reinicios del contenedor)
   *antes* de tocar la API, para no perder nada si el resto falla.
4. Se autentica en la API propia y, por cada respuesta pendiente, la traduce a un
   `AbsenceIn` y hace `POST /absences` (que ya asigna sustituto y envía el email, sin
   duplicar esa lógica aquí).
5. Cada importación exitosa se borra de `pending_responses.json` y su id pasa a
   `data/imported_ids.json` (ledger que evita reimportar la misma respuesta dos veces).

## Tests

```powershell
pytest sync/tests
```

Cubren `mapping.py` y `state_store.py` con datos de ejemplo, sin necesidad de red ni
credenciales.

## Seguridad

- Las credenciales de EducaMadrid solo viven en `.env` (no se comitean). No las pegues en
  logs ni en tickets.
- `data/` contiene datos de profesores y sustituciones (PII); está en `.gitignore`.
- El contenedor `sync` no publica ningún puerto al host: solo `api` puede alcanzarlo, y
  además debe presentar `SYNC_TRIGGER_TOKEN`.
- Confirma que este uso de automatización cumple los términos de uso de EducaMadrid en tu
  centro antes de dejarlo desatendido en producción.
