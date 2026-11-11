# TMS Estrella Roja — reglas para trabajar en este repositorio

Este repositorio es el TMS en producción. Hasta 25 personas lo usan al mismo tiempo en la página de la nube
(Streamlit Cloud, que publica la rama `main`). Cualquier cambio que llegue a `main` reinicia la app para todos.

## Antes de modificar cualquier cosa (OBLIGATORIO)

1. **Recuérdale al usuario que haga un respaldo** y espera a que confirme que ya lo hizo:
   TMS → entrar como MASTER → pestaña **🔐 Usuarios y bitácora** → **💾 Respaldos** → **Hacer respaldo ahora**.
   Si algo sale mal, desde esa misma pantalla se **restaura** (escribir RESTAURAR y dar clic en Restaurar).
2. Trabaja en una rama (no directo en `main`) y prueba antes de publicar.
3. Pasa los cambios a `main` **solo cuando el usuario lo autorice** y junta varios cambios en una sola
   publicación: cada publicación reinicia la app de la nube.

## Nunca

- Nunca subas `tms.db`, la carpeta `respaldos/` ni `configuracion_local.py` (tiene contraseñas): el repositorio es público.
- Nunca pegues ni pidas tokens o contraseñas en el chat.

## Cómo está armado

- `app.py` pantalla · `base_datos.py` acceso a datos (todo pasa por `conexion()`) · `columnas.py` columnas y permisos.
- `nube.py` base de datos en internet (Turso): cada cambio guardado en `tms.db` se manda a internet; los cambios de
  otras computadoras se bajan solos. Se activa con `TURSO_URL` y `TURSO_TOKEN` (Secrets de Streamlit o `configuracion_local.py`).
- `respaldos.py` respaldo automático diario de madrugada (2:00–5:00 hora centro de México), máximo 150 días (5 meses).
- `estatus_clientes.py` estatus para clientes (perfil TC) · `importar_plan.py` poner al día el plan desde Excel.
- `usuarios_excel.py` carga masiva de usuarios (solo MASTER) · `catalogos_excel.py` catálogos desde Excel.
- El manual de uso NO incluye la carga masiva de usuarios ni los respaldos (son solo para MASTER).
