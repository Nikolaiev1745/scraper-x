# Scraper de X — guía rápida

Descarga los tweets de una cuenta de X a Excel, CSV y JSON.

Necesitás **Python 3.10 o superior** instalado. Nada más: la herramienta se
instala sola la primera vez.

> El manual completo está en `README.md`. Esta guía es solo para arrancar.

---

## 1. Destrabar el ZIP (antes de descomprimir)

Windows bloquea los archivos que vienen de la red o del correo. Si descomprimís
sin destrabar, después hay que hacerlo archivo por archivo.

**Botón derecho sobre el ZIP → Propiedades → tildar "Desbloquear" → Aceptar.**

Recién ahí, descomprimir.

## 2. Copiarla a tu disco, en una ruta corta

Poné la carpeta en tu disco local y **cerca de la raíz** — por ejemplo
`C:\Scraper`.

Dos motivos, los dos hacen fallar la instalación:

- **No la dejes en una carpeta sincronizada** (OneDrive, Drive) ni de red: la
  instalación falla o queda lentísima.
- **No la dejes muy adentro de otras carpetas.** Windows no admite rutas de más
  de 260 caracteres, y las librerías que se instalan usan buena parte de esos.
  Algo como `C:\Users\tu.nombre\OneDrive - ...\Documentos\Herramientas\...` es
  demasiado. Si pasa, la herramienta te lo avisa y te dice qué hacer.

## 3. Abrirla

Doble clic en **`run_web.bat`**.

La primera vez se abre una ventana negra que instala lo que necesita: **tarda
unos minutos y no hay que cerrarla**. Cuando termina, se abre el navegador solo.
Las veces siguientes arranca en segundos.

Es siempre el mismo archivo, para instalar y para usar.

## 4. Pegar tus cookies de X

La herramienta entra a X con tu sesión, así que la primera vez hay que darle dos
valores del navegador. **Tienen que ser de tu cuenta, no de otra persona.**

1. Abrí [x.com](https://x.com) en el navegador, con tu sesión iniciada.
2. Apretá **F12** (se abren las herramientas de desarrollo).
3. Pestaña **Application** → panel izquierdo → **Cookies** → `https://x.com`.
4. Buscá en la lista `auth_token` y copiá su valor.
5. Buscá `ct0` y copiá su valor.
6. Pegá cada uno en su campo del formulario.

Quedan guardados: no te los va a pedir de nuevo.

**Cada tanto vencen.** Cuando pase, la herramienta dice *"NO SE PUDO CONECTAR
CON X"* — no es que la cuenta no tenga tweets. Se arregla repitiendo este paso
con valores nuevos.

## 5. Usarla

1. **Elegir la carpeta del caso** (botón `Elegir...`). Ahí van los archivos.
2. **Poner la cuenta** a descargar, sin la arroba.
3. Mirar lo que informa de esa carpeta y apretar **Ejecutar**.

Queda un solo juego de archivos por cuenta (`cuenta_all.xlsx`, `.csv`, `.json`)
que se va actualizando en cada corrida, en vez de descargas sueltas por fecha.

---

## Si algo falla

**"La version de Python es muy vieja"** — instalá Python desde
[python.org](https://www.python.org/downloads/). En el instalador, **tildá "Add
Python to PATH"** antes de darle a instalar.

**Se abre la ventana negra y se cierra sola** — no tenés Python instalado, o no
quedó en el PATH. Mismo paso de arriba.

**"La carpeta esta demasiado anidada"** — mové la carpeta a `C:\Scraper` (o
similar, cortito) y volvé a abrir `run_web.bat`. Ver el paso 2.

**"No se pudieron instalar las dependencias"** — si el detalle menciona
`WinError 206`, es el problema de la ruta larga de arriba. Si no, la red del
trabajo está bloqueando `pypi.org`: eso no se arregla desde acá, avisá y lo vemos.

**"NO SE PUDO CONECTAR CON X"** — cookies vencidas. Volvé al paso 4.

**El botón `Elegir...` no hace nada** — escribí la ruta a mano en el campo, que
funciona siempre. Si el diálogo no aparece, puede haber quedado detrás de la
ventana del navegador.

**Para cerrarla** — `Ctrl+C` en la ventana negra, o cerrala directamente.

---

## Cuando X cambia

X modifica su sitio cada tanto y rompe la herramienta. Cuando pase, hay que
actualizar una librería:

```
.venv\Scripts\python.exe -m pip install -U twscrape
```

Si eso no alcanza, hace falta una versión nueva de la herramienta: pedila.

**Al reportar un problema, decí el nombre del ZIP que te pasaron** (tiene la
fecha y la versión adentro, por ejemplo `scrapeador_tw_2026-08-18_a432d17.zip`).
Con eso se sabe exactamente qué versión estás usando.
