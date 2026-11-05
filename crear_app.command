#!/bin/bash
# Crea "Scraper de X.app" para abrir la herramienta con doble clic, sin que
# aparezca una ventana de Terminal.
#
# Se corre UNA sola vez. El .app no copia el codigo: apunta a esta carpeta, asi
# que despues de un "git pull" ya levanta la version nueva sin regenerarlo.
#
# Uso: doble clic, o ./crear_app.command desde la terminal.
set -euo pipefail
cd "$(dirname "$0")"

PROYECTO="$(pwd)"
APP="$PROYECTO/Scraper de X.app"
CONTENTS="$APP/Contents"

echo "Creando la aplicacion en:"
echo "  $APP"
echo

rm -rf "$APP"

# El bundle se genera con osacompile en vez de armarlo a mano. Un .app cuyo
# ejecutable es un shell script arranca bien, pero el proceso Python NO se
# registra como aplicacion: no aparece en el Dock y no hay Cmd+Q. Un .app de
# AppleScript si aparece, y ademas permite cerrar el servidor al salir.
APPLESCRIPT="$(mktemp -t scraper).applescript"
cat > "$APPLESCRIPT" <<AS
property pid : ""

on run
	set proyecto to "$PROYECTO"
	if not existeCarpeta(proyecto) then
		mostrarError("No se encontro la carpeta del proyecto:

" & proyecto & "

Si la moviste o le cambiaste el nombre, volve a correr crear_app.command desde su ubicacion actual.")
		quit
		return
	end if
	if not existeArchivo(proyecto & "/.venv/bin/python") then
		mostrarError("Falta el entorno virtual en:

" & proyecto & "/.venv

Crealo con estos comandos, parado en esa carpeta:

  python3 -m venv .venv
  ./.venv/bin/python -m pip install -r requirements.txt

Mas detalles en el README.md")
		quit
		return
	end if
	-- 'exec' hace que el shell SE CONVIERTA en el python, asi \$! guarda el PID
	-- real. Sin eso queda el PID de un subshell intermedio y al cerrar la app el
	-- servidor sobrevive huerfano, ocupando el puerto.
	set pid to do shell script "cd " & quoted form of proyecto & " && { nohup sh -c 'exec ./.venv/bin/python run_web.py' >/dev/null 2>&1 & echo \$!; }"
end run

-- Si el servidor se apago solo (boton Salir de la interfaz), la app se cierra
-- tambien y el icono desaparece del Dock.
on idle
	if pid is "" then
		quit
		return 5
	end if
	try
		do shell script "kill -0 " & pid
	on error
		quit
	end try
	return 2
end idle

on quit
	try
		do shell script "kill " & pid & " 2>/dev/null; true"
	end try
	continue quit
end quit

on existeCarpeta(ruta)
	try
		do shell script "test -d " & quoted form of ruta
		return true
	on error
		return false
	end try
end existeCarpeta

on existeArchivo(ruta)
	try
		do shell script "test -x " & quoted form of ruta
		return true
	on error
		return false
	end try
end existeArchivo

on mostrarError(mensaje)
	display dialog mensaje with title "Scraper de X" buttons {"Entendido"} default button 1 with icon caution
end mostrarError
AS

# -s: "stay open", para que la app siga viva mientras corre el servidor.
osacompile -s -o "$APP" "$APPLESCRIPT"
rm -f "$APPLESCRIPT"

# osacompile escribe su propio Info.plist: le ajustamos nombre e icono.
/usr/libexec/PlistBuddy -c "Set :CFBundleName 'Scraper de X'" "$CONTENTS/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string 'Scraper de X'" "$CONTENTS/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier ar.forense.scrapeador-tw" "$CONTENTS/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile icono" "$CONTENTS/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string icono" "$CONTENTS/Info.plist" 2>/dev/null || true

# Icono (opcional): si falla, el .app queda con el generico y todo funciona igual.
mkdir -p "$CONTENTS/Resources"
if [ -x "$PROYECTO/.venv/bin/python" ] && [ -f "$PROYECTO/crear_icono.py" ]; then
    "$PROYECTO/.venv/bin/python" "$PROYECTO/crear_icono.py" "$CONTENTS/Resources/icono.icns" \
        && echo "  icono generado" \
        || echo "  (sin icono propio: se usa el generico)"
fi

# Sin el atributo de cuarentena, Gatekeeper no molesta al abrirla.
xattr -cr "$APP" 2>/dev/null || true
touch "$APP"

echo
echo "Listo."
echo
echo "  - Doble clic en \"Scraper de X\" para abrir la herramienta."
echo "  - Arrastrala al Dock para tenerla a mano."
echo "  - Para cerrarla: el boton Salir de la interfaz, o Cmd+Q desde el Dock."
echo
