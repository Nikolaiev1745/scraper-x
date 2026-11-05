# scrapeador_tw

Descarga los tweets, retweets y respuestas de una cuenta de X (Twitter) a JSON, CSV y Excel.

Pensado para trabajo de investigación: cada cuenta se mantiene como un **único juego de archivos
consolidado** (`<usuario>_all.json` / `.csv` / `.xlsx`) que se va actualizando corrida tras corrida,
en vez de dejar descargas sueltas por fecha. Las descargas se pueden apuntar directo a la carpeta
del caso, así el código queda libre de datos.

Funciona en **macOS, Windows y Linux**. Usa [twscrape](https://github.com/vladkens/twscrape), que
pega contra la API de X directamente: no hace falta navegador ni driver.

---

## Requisitos

- **Python 3.10 o superior.** Recomendado 3.11 o 3.12: son las versiones con wheels ya compiladas
  para todas las dependencias, así que la instalación no tiene que compilar nada.
  - macOS: `brew install python@3.11`
  - Windows: instalador de [python.org](https://www.python.org/downloads/) (marcá "Add to PATH")
- Una **sesión activa de X** en el navegador, para copiar las cookies (ver abajo).

---

## Instalación

**No hay que hacer nada.** La primera vez que abrís el lanzador (`run_web.bat` en
Windows, `run_web.command` en macOS y Linux), crea el entorno virtual e instala
las dependencias solo. Tarda unos minutos y muestra el avance en la consola; las
veces siguientes arranca directo.

Es el mismo archivo para instalar y para usar, así que no hay que elegir cuál
abrir la primera vez. Si `requirements.txt` cambia después de un `git pull`, la
reinstalación también se dispara sola.

En macOS/Linux puede hacer falta darle permiso de ejecución al lanzador, una vez:

```bash
chmod +x run_web.command
```

<details>
<summary>Instalación a mano (si preferís controlar el proceso)</summary>

```bash
# macOS / Linux
python3 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt
```

```
:: Windows
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

El `.env` no hace falta crearlo: las cookies se pegan en el formulario web la
primera vez y se guardan solas. Para armarlo a mano, `cp .env.example .env`.

</details>

> **No copies un `.env` de otra máquina entre sistemas distintos.** Si trae
> `SCRAPER_OUTPUT_DIR=C:\Casos\...` y lo abrís en Mac, la ruta no es válida: la herramienta lo
> detecta, avisa por consola y usa `output/` en vez de crear una carpeta con un nombre raro.
> Configurá la carpeta de nuevo desde el formulario web.

---

## Configurar la sesión de X

La herramienta necesita dos cookies de tu sesión: `auth_token` y `ct0`.

1. Abrí [x.com](https://x.com) logueado, en el navegador.
2. Abrí las DevTools (F12 en Windows, `Cmd+Option+I` en Mac).
3. Pestaña **Application** (o **Storage** en Firefox) → **Cookies** → `https://x.com`.
4. Copiá los valores de `auth_token` y de `ct0`.

Se pegan en el formulario web (campos separados) o, a mano, en el `.env`:

```
X_COOKIES=auth_token=XXXX; ct0=YYYY
```

Se guardan solos en el `.env` la primera vez, así que las corridas siguientes ya no las piden.
El `.env` está en el `.gitignore` y **no se comitea**: contiene tu sesión.

Las cookies vencen cada tanto. Cuando pase, la herramienta lo dice explícitamente
("NO SE PUDO CONECTAR CON X") en vez de reportar cero tweets: repetí estos pasos y pegá las nuevas.

---

## Uso

### Interfaz web (lo habitual)

- **macOS**: doble clic en `Scraper de X.app` (ver abajo cómo crearla), o en `run_web.command` si
  querés ver la consola.
- **Windows**: doble clic en `run_web.bat`.
- **Cualquier sistema**: `python3 run_web.py`

Levanta un servidor local y abre el navegador solo en la dirección correcta. Para detenerlo,
`Ctrl+C` en la terminal (o cerrar la ventana).

Normalmente usa <http://127.0.0.1:5000>, pero si ese puerto está ocupado toma el siguiente libre
(5001, 5002…) y te lo dice al arrancar. **En macOS el puerto 5000 suele estar tomado por el receptor
de AirPlay**, que además responde `403 Forbidden` a `http://localhost:5000` — por eso conviene
guiarse siempre por la dirección que imprime el lanzador y no por un favorito viejo. Si preferís
liberar el 5000, se apaga en Ajustes del Sistema → General → AirDrop y Handoff → "Receptor de
AirPlay", pero no hace falta.

Si el scraper ya estaba abierto, un segundo lanzamiento no arranca otra copia: te abre el navegador
en la que ya está corriendo.

El flujo es: elegir la carpeta del caso → poner la cuenta → revisar qué trae → Ejecutar.

### Aplicación de macOS (sin terminal)

Si preferís abrirla como cualquier otra app del Mac, sin ventana de Terminal, generá el ejecutable
una sola vez:

```bash
./crear_app.command
```

Eso crea **`Scraper de X.app`** en la carpeta del proyecto. Doble clic y listo; arrastrala al Dock
para tenerla a mano.

Para cerrarla, cualquiera de las dos:

- el botón **Salir** de la interfaz;
- **Cmd+Q** con la app seleccionada en el Dock.

Las dos apagan el servidor de verdad y liberan el puerto.

**No hace falta regenerarla después de un `git pull`.** La app no copia el código: apunta a esta
carpeta, así que siempre levanta la versión actual. Solo hay que volver a correr `crear_app.command`
si **movés o renombrás la carpeta del proyecto** — si eso pasa, la app te lo dice con un cartel en
vez de fallar en silencio. Lo mismo si falta el `.venv`.

La app no se comitea (está en el `.gitignore`): es un artefacto generado, no código.

### Línea de comandos

```bash
./.venv/bin/python main.py --target-user nasa --max-tweets 200 --formats json,csv,xlsx
```

En Windows, cambiá `./.venv/bin/python` por `.\.venv\Scripts\python.exe`.

Opciones principales: `--target-user`, `--types tweets,retweets,replies`, `--max-tweets`,
`--since` / `--until` (`YYYY-MM-DD`), `--update`, `--historical` + `--window-days`, `--formats`,
`--output-dir`. El detalle completo está en `main.py --help`.

---

## Elegir la carpeta del caso

Es el primer paso de cada corrida, arriba de todo en el formulario.

- **`Elegir...`** abre el selector de carpetas del sistema (Finder en macOS, el
  explorador en Windows). Es un diálogo nativo lanzado por la herramienta, no por el
  navegador: los navegadores no pueden dar una ruta absoluta a la aplicación, por diseño de
  seguridad.
- **`Usadas recientemente`** lista las últimas 5 carpetas, la más reciente primero, para saltar
  entre causas sin volver a navegar el disco. Se guardan en `recientes.json` (ignorado por git,
  porque son nombres de causas) y las que ya no existen se descartan solas.
- También podés **pegar la ruta a mano** en el campo. Es el camino de respaldo si el selector no
  está disponible.

Apenas elegís una carpeta, la herramienta te dice qué hay ahí: qué cuentas ya se descargaron y,
para la cuenta objetivo, cuántos registros y de cuándo es el último tweet.

**Si detecta descargas previas de esa cuenta, pasa sola a "solo lo nuevo"** y lo avisa en pantalla.
Si ya elegiste un modo a mano, no te lo cambia.

> **Permisos en macOS.** La primera vez que la herramienta escriba en Escritorio, Documentos o
> Descargas, macOS va a pedir autorización a nombre de la terminal desde la que la lanzaste. Es un
> permiso por carpeta y por aplicación: se acepta una vez. Elegir la carpeta en el diálogo no
> otorga ese permiso por sí solo.

---

## Los tres modos

| Modo | Qué hace | Cuándo usarlo |
|---|---|---|
| **Descarga completa** | Recorre el timeline del perfil. Se puede acotar con "Desde" / "Hasta". | Primera bajada de una cuenta. |
| **Solo lo nuevo** (`--update`) | Lee lo ya bajado en la carpeta de datos y trae solo lo posterior al último tweet. Incluye retweets. | Seguimiento periódico de una cuenta. |
| **Historial por fechas** (`--historical`) | Va por la búsqueda de X en ventanas de N días, para pasar el tope de ~3200 tweets del timeline. Requiere "Desde". **No trae retweets** y tarda más. | Cuentas muy grandes, o para ir bien atrás en el tiempo. |

### El tope de ~3200 y qué hace la herramienta

X solo deja recorrer por el timeline los **~3200 tweets más recientes** de un perfil. Si pedís
"Desde" una fecha más vieja que eso, el timeline se agota antes de llegar y la corrida termina
igual, sin error: en el pasado eso dejaba un hueco de meses sin avisar.

Ahora la descarga completa **detecta ese corte**. Cuando pide un rango hacia atrás y no llega a la
fecha "Desde", completa sola por búsqueda histórica (la misma técnica del modo `--historical`, en
ventanas de N días) y avisa por pantalla:

```
[fallback] El timeline de @fulano se cortó antes de 2026-01-01 (tope ~3200 de X).
Completo por búsqueda histórica; los retweets quedan solo hasta el tramo que alcanzó el timeline.
```

La única limitación que queda: **la búsqueda de X no devuelve retweets**, así que en ese tramo
completado llegan tweets, respuestas y citas, pero no los retweets. Si en esa corrida solo
pediste retweets, no hay forma de llegar más atrás con esta API y la herramienta te lo dice en vez
de entregar un resultado incompleto en silencio.

Para esto no hace falta que hagas nada: es el comportamiento por defecto de la descarga completa
cuando el rango no entra en el timeline.

También hay un **"Agregar por id"** en la web: pegando ids o URLs de tweets sueltos (esos que X a
veces no lista, como algunas respuestas), los recupera uno por uno y los suma al consolidado del
autor.

---

## Los archivos de salida

Por cada cuenta queda un solo juego de archivos en la carpeta de datos:

```
output/
  nasa_all.json     <- fuente de verdad, siempre se escribe
  nasa_all.csv      <- con BOM, para que Excel respete los acentos
  nasa_all.xlsx
```

Cada corrida fusiona lo nuevo ahí: deduplica por id de tweet, ordena por fecha descendente, y las
métricas de los tweets ya conocidos se actualizan al último valor visto. Después de escribir el
consolidado, se borran los archivos sueltos previos de esa cuenta (`nasa_2024-01-05.json` y
similares), que ya quedaron absorbidos.

### El nombre de los archivos importa

La herramienta reconoce una descarga **solo si se llama `<cuenta>_all.json`**. Es lo que usa para
listar las cuentas de la carpeta y, sobre todo, lo que busca el modo "solo lo nuevo" para saber
desde dónde continuar.

Si traés una descarga vieja con otro nombre (`data.json`, `backup.json`), copiala con el nombre que
corresponde y la va a tomar:

```bash
cp data.json nasa_all.json
```

La interfaz te avisa cuando encuentra un `.json` que parece una descarga pero no sigue esa
convención, así no parece que la carpeta está vacía cuando no lo está. Conviene **copiar y no
renombrar**: el archivo original queda como estaba en el expediente.

La carpeta se elige en el campo "Carpeta de datos" de la web (o con `--output-dir`) y se recuerda
para la próxima. Conviene apuntarla a la carpeta del caso.

> **Ojo en macOS y Linux:** esa limpieza de archivos sueltos borra igual aunque tengas el archivo
> abierto en Excel o Numbers — a diferencia de Windows, donde el archivo queda bloqueado y se
> saltea. El consolidado `_all.*` se escribe *antes* de limpiar, así que no se pierde información,
> pero cerrá los archivos viejos antes de correr si los tenés abiertos.

> La limpieza solo toca archivos que empiezan con `<usuario>_` y terminan en `.json`, `.csv` o
> `.xlsx`. Si apuntás la carpeta de datos a una carpeta de caso con archivos propios, revisá que
> ninguno coincida con ese patrón.

---

## Estructura del proyecto

| Archivo | Rol |
|---|---|
| `run_web.py` | Lanzador multiplataforma: instala si hace falta, abre el navegador, levanta Flask |
| `run_web.command` / `run_web.bat` | Envoltorios de doble clic para macOS y Windows |
| `crear_app.command` | Genera `Scraper de X.app` para abrir sin terminal (macOS) |
| `crear_icono.py` | Dibuja el ícono de la app, sin dependencias externas |
| `app.py` | Interfaz web (Flask, HTML embebido) |
| `main.py` | Interfaz de línea de comandos |
| `runner.py` | Orquestador compartido por la web y la CLI |
| `auth.py` | Alta de la cuenta en twscrape (cookies o credenciales) |
| `scraper.py` | Recolección: timeline y búsqueda por fechas |
| `parser.py` | Normaliza el JSON crudo de X a registros planos |
| `export.py` | Escribe y fusiona el consolidado JSON / CSV / Excel |
| `config.py` | Rutas, variables de entorno y constantes |
| `recientes.json` | Últimas carpetas usadas (se crea solo, ignorado por git) |
| `GUIA.md` | Guía de una página para quien solo usa la herramienta |
| `empaquetar.ps1` | Arma el ZIP para entregarle la herramienta a otra persona |

---

## Entregársela a otra persona

`empaquetar.ps1` arma el ZIP, desde Windows:

```
powershell -ExecutionPolicy Bypass -File .\empaquetar.ps1
```

Deja un `scrapeador_tw_<fecha>_<commit>.zip` en la carpeta que contiene al
proyecto. Quien lo recibe solo necesita Python 3.10+; el resto lo instala el
lanzador. La `GUIA.md` que va adentro le explica los pasos.

El ZIP se arma con `git archive`, que exporta **únicamente archivos versionados**.
No es un detalle de comodidad: copiar la carpeta a mano se lleva el `.env` (tu
sesión de X), `accounts.db` (tu sesión ya activada), `recientes.json` (nombres de
causas) y `output/`. Con `git archive` no pueden colarse, porque nunca estuvieron
versionados — y el script igual revisa el ZIP ya armado y aborta si encuentra
alguno.

Dos cosas que conviene tener presentes:

- **Las cookies tienen que ser de quien la usa.** Pasarle tu `.env` lo deja
  operando X con tu cuenta, lo que contamina la atribución de quién hizo la
  recolección.
- El script **se niega a empaquetar con cambios sin commitear**, porque
  `git archive` exporta `HEAD`: entregaría una versión distinta de la que estás
  probando. `-Force` lo saltea si es a propósito.

---

## Solución de problemas

**"NO SE PUDO CONECTAR CON X"** — es un fallo de sesión o de red, *no* significa que no haya tweets
nuevos, y no se tocó nada de lo ya descargado. Por orden: pegá cookies nuevas; si sigue, actualizá
twscrape (X cambia su frontend seguido):

```bash
./.venv/bin/python -m pip install -U twscrape
```

**No se abre la carpeta al terminar** — la interfaz lo dice y muestra la ruta para que la abras a
mano. En Linux hace falta `xdg-utils` instalado.

**El botón `Elegir...` no hace nada** — pegá la ruta a mano en el campo, que funciona siempre. En
Linux el selector necesita `zenity` (GNOME) o `kdialog` (KDE) instalado; en macOS y Windows viene
incluido con el sistema. Si el diálogo aparece detrás de la ventana del navegador, buscalo en el
selector de ventanas.

**"La version de Python es muy vieja"** — las dependencias piden 3.10 o superior. Instalá una
versión más nueva desde python.org (en Windows, tildando "Add Python to PATH") y volvé a abrir el
lanzador.

**"No se pudieron instalar las dependencias"** — casi siempre es la red bloqueando `pypi.org`
(proxy o firewall corporativo). El detalle de pip queda impreso arriba del mensaje. Para
descartarlo: `py -3 -m pip download twscrape -d %TEMP%\pruebapip`.

**"No se pudo preparar el entorno"** — no hay permiso de escritura donde está la herramienta.
Suele pasar en carpetas de red o sincronizadas: copiala al disco local.

**"La carpeta esta demasiado anidada"** (Windows) — el límite de 260 caracteres por ruta. Las
dependencias instalan archivos a ~140 caracteres de profundidad dentro del `.venv`, así que una
carpeta muy adentro de otras hace fallar a pip con un `WinError 206` que no explica la causa. Se
chequea antes de instalar. La solución es mover la herramienta a algo como `C:\Scraper`.

**El navegador dice "Forbidden" o no carga** — fijate la dirección exacta que imprimió el lanzador.
En macOS, `http://localhost:5000` lo contesta el receptor de AirPlay con un 403, no el scraper.

**"Address already in use"** — ya no debería pasar: el lanzador detecta una instancia corriendo y
te abre esa. Si igual aparece, cerrá las ventanas del scraper y volvé a lanzarlo.

**Se descargó en una carpeta que no esperabas** — mirá el aviso `[config]` en la consola. Es el
caso del `.env` traído de otro sistema operativo.
