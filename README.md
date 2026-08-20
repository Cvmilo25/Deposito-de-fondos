# Dashboard de nuevos depósitos de Fondos Mutuos — CMF Chile

Monitorea los nuevos fondos (mutuos y de inversión) depositados ante la Comisión
para el Mercado Financiero (CMF), usando como única fuente el
[Registro Público de Depósito de Reglamentos Internos](https://www.cmfchile.cl/institucional/inc/deposito_fondos_mutuos.php)
de la CMF.

## Estructura del proyecto

```
scripts/
  common.py                    Normalización de columnas + construcción de la URL del reglamento
  fetch_cmf.py                 Descarga el Excel desde cmfchile.cl (requests + fallback Playwright)
  process.py                   Normaliza el Excel descargado y lo integra al histórico
  validate_reglamento_links.py Valida contra el sitio real qué % de links de reglamento resuelven
data/
  historico_depositos.csv      Histórico completo, versionado en git (fuente de verdad)
  last_update.json             Metadata de la última corrida (para el dashboard)
  raw/                         Excel crudo descargado (no versionado, se regenera cada corrida)
dashboard/
  index.html / app.js / styles.css   Dashboard estático, sin build step ni dependencias externas
.github/workflows/
  update-and-deploy.yml        Cron diario: descarga -> procesa -> commit -> publica en Pages
```

## Cómo se define "nuevo fondo"

Un fondo se considera "nuevo" en un período si su **Fecha de Depósito** (informada
directamente por la CMF, no la fecha de inicio de operaciones ni la de última
modificación) cae dentro de ese período. El dashboard evalúa esto contra la fecha
actual real (no contra la fecha del último dato cargado), así que "últimos 30 días"
siempre significa los 30 días previos a hoy.

## Campos del histórico (`data/historico_depositos.csv`)

| Columna | Origen en el Excel de la CMF |
|---|---|
| `id_registro` | N° de registro (informativo — no siempre es único, ver nota abajo) |
| `fecha_deposito` | Fecha de depósito |
| `run_fondo` | RUN del Fondo Mutuo |
| `nombre_fondo` | Nombre del Fondo Mutuo |
| `administradora` | Nombre Administradora |
| `estado_registro` | Estado (Vigente/No Vigente) |
| `estado_liquidacion` | Estado (indica si fondo está liquidado) |
| `tipo_fondo` | Tipo Fondo (`Fondo Mutuo`, `Fondo Inversión Rescatable`, `Fondo Inversión No Rescatable`) |
| `reglamento_codigo` | Reglamento Interno Vigente (código numérico) |
| `reglamento_url` | Construida: `https://www.cmfchile.cl/documentos/rfm/rfm_{codigo}.pdf` |
| `fecha_ultima_modificacion` | Fecha última modificación |
| `primera_deteccion` | Fecha en que este pipeline detectó el registro por primera vez |

### Sobre la clave única de cada fondo

El histórico se deduplica por `run_fondo` (RUN del fondo), no por `id_registro`. La CMF
reutiliza el N° de registro entre fondos distintos en al menos un caso confirmado en los
datos reales (`FM110545` aparece para dos fondos con RUN 8912 y 8756), mientras que el RUN
es único en las 2.639 filas del histórico inicial. `scripts/process.py` re-detecta esto en
cada corrida vía `merge_into_history()`.

### Sobre el link al reglamento interno

El patrón de URL fue inferido por evidencia externa (varios PDFs reales de
reglamentos de fondos mutuos publicados por la CMF siguen exactamente
`documentos/rfm/rfm_{codigo}.pdf`, donde `{codigo}` es el mismo número que trae la
columna "Reglamento Interno Vigente" del Excel), **no por inspección directa del
sitio**. Está confirmado para `Fondo Mutuo`; para los dos tipos de Fondo de
Inversión es una hipótesis razonable (mismo formato de código) pero no verificada
al 100%. Ejecuta `scripts/validate_reglamento_links.py` (necesita salida a
internet real hacia cmfchile.cl, por eso corre en CI, no en este entorno de
desarrollo) para medir la tasa de éxito real por tipo de fondo y ajustar
`build_reglamento_url()` en `scripts/common.py` si hace falta.

## Correr el pipeline localmente

```bash
pip install -r requirements.txt
python scripts/fetch_cmf.py --output data/raw/deposito_fondos_mutuos_latest.xlsx
python scripts/process.py --input data/raw/deposito_fondos_mutuos_latest.xlsx
```

Para ver el dashboard con los datos actuales:

```bash
mkdir -p /tmp/site/data
cp dashboard/* /tmp/site/
cp data/historico_depositos.csv data/last_update.json /tmp/site/data/
cd /tmp/site && python3 -m http.server 8000
# abrir http://localhost:8000
```

## Automatización (GitHub Actions)

`.github/workflows/update-and-deploy.yml` corre diariamente (cron `0 12 * * *`,
~08-09 hora Chile), además de en cada push a `main` que toque `dashboard/` o
`scripts/`, y manualmente vía "Run workflow". En cada corrida:

1. Descarga el Excel desde la CMF (`fetch_cmf.py`).
2. Lo normaliza e integra al histórico (`process.py`).
3. Commitea el CSV/JSON actualizados directamente a la rama.
4. Publica `dashboard/` + los datos frescos en GitHub Pages.

### Paso manual único requerido (solo la primera vez)

GitHub Pages debe activarse una vez en la configuración del repositorio:

**Settings → Pages → Source: "GitHub Actions"**

Sin este paso el job de deploy fallará aunque el resto del pipeline funcione bien.

### Estado actual: Actions bloqueado por revisión anti-abuso de GitHub

Esta cuenta/repo es nueva y GitHub aún no habilita la ejecución de workflows definidos por
el usuario (síntoma: `https://github.com/<owner>/<repo>/actions/workflows/update-and-deploy.yml`
muestra **"This workflow does not exist"** aunque el archivo esté presente y sea válido).
Esto es una restricción del lado de GitHub, no del código de este proyecto — suele
levantarse verificando la cuenta (teléfono/email) o esperando a que GitHub complete la
revisión; si persiste, hay que escribirle a soporte de GitHub.

**Mientras tanto, el dashboard igual está publicado**, usando el modo clásico de Pages
que no depende de Actions:

**Settings → Pages → Source: "Deploy from a branch" → Branch: `main` → Folder: `/dashboard`**

`scripts/process.py` ya deja una copia de `historico_depositos.csv` y `last_update.json`
dentro de `dashboard/data/` en cada corrida (parámetro `--publish-dir`, activado por
defecto) para que este modo funcione sin pasos extra. La única desventaja: la
actualización diaria automática no corre hasta que Actions quede habilitado — mientras
tanto, se actualiza corriendo el pipeline localmente y haciendo push.

Una vez que GitHub habilite Actions para la cuenta, cambia el Source de Pages de vuelta a
**"GitHub Actions"** y el workflow `update-and-deploy.yml` retoma la actualización diaria
automática (incluyendo el deploy, que sobreescribe lo publicado por el modo clásico).

## Alcance de datos

El registro de la CMF incluye tres tipos de fondo bajo el mismo depósito de
reglamentos: `Fondo Mutuo`, `Fondo Inversión Rescatable` y
`Fondo Inversión No Rescatable`. El dashboard los incluye todos, con "Tipo de
Fondo" como filtro y como dimensión del gráfico de nuevos depósitos por tipo.
