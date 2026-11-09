# Ñandu

Software para modelar y analizar redes eléctricas de distribución.

## Versión actual: 0.14.0

Reimplementación independiente de CYME 9.0 para redes de distribución. Importa
bases Access y estudios `.sxst` de CYME a un archivo SQLite propio, conserva las
filas originales y construye un modelo eléctrico por fase. Calcula con un motor
nodal propio:

- **Flujo de carga desbalanceado** (Newton, nodal aumentado), con control de taps.
- **Estimación de estado** WLS con restricciones (Hachtel) e índices de calidad.
- **Distribución de carga** (Load Allocation) por kVA conectado, kVA actual o kWh.

Incluye una interfaz local tipo CYME (`nandu ui`), con edición de la red (tramos, equipos,
cargas, catálogos y lecturas de medidores). No necesita CYME para calcular.

Desde 0.14.0 importa también tablas CYME desde JSON (`import-tables`), con lo que se armó una
base estructural del SIN (500/220/66/23 kV) a partir del esquema unifilar: topología y
maniobras, todavía sin parámetros eléctricos.

**Alcance demostrado y límites.** El flujo se contrastó con CYME en dos
alimentadores (CZU06/CZU08): 2.690 tensiones, 11,2 V de diferencia máxima. Es un
resultado de magnitudes de tensión en esas redes, no una equivalencia general.
No hay transformadores, generación distribuida, cortocircuito ni protecciones.
Cada informe declara su cobertura (`model_coverage`), el residuo de las ecuaciones
en el estado publicado (`final_residual`) y el balance P/Q por red. Un cálculo que
converge pero dejó fuera algo que CYME calcula termina con código 3. El historial
por versión sigue abajo; las cifras de versiones anteriores son históricas.

## Ejecutar

Requiere Python 3.11 o posterior. Para importar Access se necesita Windows y el
controlador Microsoft Access ODBC de la misma arquitectura que Python.
En este equipo, `pyodbc==5.1.0` ya está disponible en Python 3.11.

Desde esta carpeta:

```powershell
py -3.11 -m nandu --version
py -3.11 -m nandu networks --database "C:\Users\moise\Documents\020_CYM_MCP\data\BD_Centro0926.mdb"
py -3.11 -m nandu import-cyme --database "C:\Users\moise\Documents\020_CYM_MCP\data\BD_Centro0926.mdb" --networks CZU06 CZU08 --output "data\czu06-czu08.nandu.sqlite"
py -3.11 -m nandu inspect "data\czu06-czu08.nandu.sqlite"
py -3.11 -m nandu topology "data\czu06-czu08.nandu.sqlite"
py -3.11 -m unittest discover -s tests -v
```

Instalación opcional como comando `nandu`:

```powershell
py -3.11 -m pip install -e ".[access]"
```

El comando `inspect` funciona con la biblioteca estándar de Python, sin CYME,
sus DLL, Access ni pyodbc. El destino de importación debe ser nuevo.

## Procedencia y alcance

- Access se abre en modo de solo lectura.
- Se registra SHA-256 de la base antes y después; si cambia, se aborta la publicación.
  Esta comprobación no sustituye una instantánea transaccional. Utilizar una copia
  estable cuando otro programa esté editando la base.
- Se importan las tablas CYM con `NetworkId`, filtradas por las redes seleccionadas.
- Se incorporan también los nodos referenciados por extremos de tramos aunque
  pertenezcan a otra red o tengan `NetworkId` nulo. Se conservan como nodos de
  frontera pendientes de mapeo, sin inventar pertenencia ni conexión eléctrica.
- Se conservan completos los catálogos `CYMEQ*` y metadatos globales seleccionados.
- Las tablas excluidas se enumeran con motivo en la procedencia.
- Los códigos de fase, estado, conexión y tipo se mantienen como datos originales.
  Las unidades y el sistema de coordenadas todavía requieren mapeo explícito.
- Unidades, parámetros y referencias de equipos se conservan en `source_records`.
- Los números decimales exactos, fechas y binarios usan etiquetas JSON para evitar
  pérdida de precisión o conversiones implícitas.
- Se conservan duplicados y referencias faltantes para auditarlos. Un informe
  estructural correcto no significa que el modelo esté listo para calcular.
- La conectividad representada es estructural; no se deduce energización sin
  interpretar maniobras y estados por fase.

## Archivo `.nandu.sqlite`

| Tabla | Contenido |
|---|---|
| `metadata` | Formato, procedencia, auditoría y semántica pendiente |
| `source_schema` | Columnas y ámbito de las tablas importadas |
| `source_records` | Filas originales consultables como JSON |
| `model_objects` | Entidades del modelo estructural independiente |

## Siguientes hitos

1. Mapear fases, estados, unidades, impedancias, conexiones y referencias de equipos.
2. Construir la topología energizada por fase y detectar componentes aislados.
3. Integrar un motor independiente, evaluando OpenDSS para la primera versión.
4. Comparar casos con cargas y taps fijos contra CYME mediante el MCP existente.
5. Incorporar estimación de demanda y controles de reguladores/capacitores.
6. Desarrollar mapa/unifilar, edición y comparación de escenarios.

El proyecto hermano `020_CYM_MCP` sirve como adaptador de referencia para la
validación. Ñandu no importa su código ni necesita cargarlo para leer sus proyectos.

## Verificación inicial — 18 de octubre de 2026

Importación real guardada en `data/czu06-czu08.nandu.sqlite`:

- 2 redes, 1.307 nodos (incluidos 8 nodos de frontera sin NetworkId).
- 1.309 tramos, 1.962 asociaciones de dispositivos, 2 fuentes y 797 filas de cargas.
- 230 tablas importadas; 87 tablas fuera del alcance enumeradas en la procedencia.
- Auditoría: 0 errores y 21 advertencias, correspondientes a los 8 nodos de
  frontera y sus 13 referencias desde extremos de tramo. Requieren mapeo antes
  de construir la topología eléctrica.
- SHA-256 de la base: `e3d76da076e4fd588aff5c495feaf2c51bbb687908b4eff525086fdf64b0f1e0`,
  idéntico antes y después de la importación.
- 8 pruebas automatizadas correctas; `PRAGMA integrity_check` devuelve `ok`.
- Lectura del proyecto verificada con `python -S`, sin cargar paquetes externos.

Estos resultados verifican importación y almacenamiento, no cálculo eléctrico.

## Conectividad por fase — 0.2.0

```powershell
py -3.11 -m nandu topology "data\czu06-czu08.nandu.sqlite" --output "data\topologia-nueva.json"
```

El resumen aparece en consola. `--details` muestra además cada nodo, tramo,
componente y dispositivo de maniobra. `--output` guarda el informe completo en un
archivo nuevo. El cálculo funciona sin dependencias externas (`py -3.11 -S`).

### Reglas y evidencias

- Fases CYME: 1=A, 2=B, 3=C, 4=AB, 5=AC, 6=BC, 7=ABC. Se contrastaron con
  `CymPhase` en las bibliotecas COM instaladas. No son máscaras binarias.
- Los códigos Access de dispositivos se contrastaron con sus instancias en las
  tablas correspondientes. **No se usa `CymEquipmentType` de COM**, que tiene
  una numeración diferente.
- Los nodos sin red se resuelven solo si hay una única fila `CYMNODE` y una
  asociación explícita `(NetworkId, NodeId)` en `CYMNODEINTER`.
- `ClosedPhase` limita las fases conectadas del tramo. En este perfil, 0 significa
  ninguna fase cerrada. `NormalStatus` se conserva como referencia, sin reemplazar
  el estado guardado de las fases.
- Se intersectan los estados de todos los dispositivos serie del tramo. Una
  combinación de maniobras monofásicas no se convierte en una conexión trifásica.
- Tipos desconocidos, referencias ambiguas, conectores distintos de 0 y estados
  no admitidos excluyen el tramo y producen un resultado con cobertura parcial.
- **Hipótesis pendiente:** `Status=0` de los dispositivos serie se admite como
  ausencia de restricción adicional. No se ha demostrado su correspondencia con
  los enums de la API; el informe registra esta hipótesis. Otros valores se excluyen.
- Se calcula alcanzabilidad desde fuentes con tensión declarada positiva, número
  de componentes, ciclos y componentes con más de una fuente, por fase.
- No se calcula tensión ni alimentación de cargas internas al tramo; no se modela
  acoplamiento delta. Un nodo no alcanzado no se declara físicamente desenergizado.

`scripts/inspect_typelibs.py` permite repetir la lectura estática de enumeraciones
con `comtypes`, sin registrar bibliotecas ni iniciar CYME. Evidencia local:
`data/cyme-typelib-enums.json`. La aplicación no depende de ese script ni de COM.

### Resultado sobre la importación inicial

- 1.309 tramos admitidos, sin exclusiones bajo el perfil condicional.
- 67 dispositivos de maniobra; 7 sin fases efectivamente cerradas.
- 10 asociaciones de frontera resueltas, correspondientes a los 8 nodos originales.
- Declaración de coordenadas en la base: `EPSG:32721` (sin transformación aplicada).

| Fase | Nodos del grafo | Alcanzados desde fuentes | No alcanzados | Rango de ciclos |
|---|---:|---:|---:|---:|
| A | 917 | 914 | 3 | 0 |
| B | 951 | 948 | 3 | 0 |
| C | 825 | 822 | 3 | 0 |

Hay 5 componentes por fase y ningún componente conecta más de una fuente.
Estos valores son condicionales a las reglas anteriores; todavía no constituyen
una comparación con resultados de CYME ni una validación eléctrica.

Informe completo: `data/czu06-czu08-topology-verified.json`.
La suite de 0.2.0 tiene **19 pruebas correctas**. Incluye apertura con estado normal
cerrado, maniobras monofásicas, dispositivos serie, nodos compartidos con y sin
asociación, ciclos, fuentes múltiples, códigos desconocidos y tensión no finita.

## Preparación eléctrica — 0.3.0

Resolver catálogos conservando valores nativos:

```powershell
py -3.11 -m nandu electrical "data\czu06-czu08.nandu.sqlite"
```

Normalizar provisionalmente mediante un perfil explícito:

```powershell
py -3.11 -m nandu electrical "data\czu06-czu08.nandu.sqlite" --profile "profiles\cyme9-si-candidate.json" --output "data\electrical-nuevo.json"
```

El perfil incluido es una **hipótesis de conversión**, no una certificación de
las unidades Access. Su contenido y SHA-256 quedan registrados. Sin perfil no se
convierten números ni se interpretan códigos de potencia. Los enums COM tienen
numeraciones distintas y no se aplican directamente a Access.

El módulo:

- Resuelve líneas, cables, conductores, geometrías, reguladores, protecciones,
  capacitores, fuentes, clases de consumo y modelos de carga.
- Señala referencias ausentes o duplicadas, sin elegir silenciosamente un equipo.
- Convierte longitud a metros, impedancia serie a ohmios del tramo, admitancias
  a siemens del tramo, tensión de fuente a voltios fase-neutro y potencia a W/var.
- Conserva impedancias de secuencia como tales. No inventa una matriz ABC con
  hipótesis implícitas de transposición o secuencia negativa.
- Para catálogos por fase selecciona las fases del tramo y conserva los términos
  propios/mutuos. Los signos de admitancia quedan pendientes de verificar antes
  de construir la matriz nodal.
- Conserva la potencia de cada registro ABC sin dividirla ni triplicarla. Para
  registros monofásicos asigna la potencia candidata a la etiqueta de fase;
  esto no es aún una inyección nodal: falta interpretar conexión y ubicación.
- Señala las impedancias derivadas que deben recalcularse o validarse. La marca
  `LockImpedance` se conserva sin tratarla como prueba de vigencia.
- Devuelve siempre `solver_ready: false` hasta implementar y validar el modelo
  eléctrico completo. No suma automáticamente la demanda trifásica.

### Resultado real de preparación

- 1.308 líneas: 83 subterráneas, 609 aéreas de catálogo de secuencia y 616 aéreas
  con parámetros por fase. Todas generaron candidatos numéricos bajo el perfil.
- 74 dispositivos con catálogo y 2 fuentes.
- 797 registros de carga: 640 monofásicos y 157 ABC pendientes de reparto.
- Frecuencia de los catálogos utilizados: 50 Hz.
- Falta el equipo `DEFAULT` en `CYMEQFUSE` para `TRAMO_CZU06_7193` (CZU08).
- `ANDE_19787` (CZU08) y `ANDE_2424` (CZU06) tienen fases de carga no contenidas
  íntegramente en la fase del tramo. Requieren revisión del modelo original.

El informe usa `nandu-electrical-v1`: cada referencia apunta a `references` por
tabla y ordinal original, evitando repetir bibliotecas por cada línea. Conserva
datos originales, valores candidatos, perfil y lista de impedimentos.

Informe: `data/czu06-czu08-electrical-final-v030.json`.

Verificación de 0.3.0: **35 pruebas correctas** y preparación real ejecutada con
`python -S`. Las pruebas cubren factores de potencia, signos capacitivos, unidades
de longitud/admitancia, selección de fase C, referencias duplicadas, conservación
de potencia ABC, conflictos de fases y trazabilidad de referencias compactadas.

## Motor de flujo de carga — 0.4.0

El entorno `.venv` de este equipo contiene DSS-Python 0.15.7 y DSS C-API 0.14.5,
basado en OpenDSS SVN 3723. El solver no utiliza la instalación de CYME.

Ejecutar el caso incluido:

```powershell
.\.venv\Scripts\python.exe -m nandu solve "examples\radial-balanced.json"
.\.venv\Scripts\python.exe -m nandu solve "examples\radial-balanced.json" --output "data\flujo-nuevo.json"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Para reproducir el entorno en otro equipo, con Python 3.11:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-solver.txt
```

También existe el extra instalable `.[solver]`. Las operaciones de importación
Access requieren el extra `.[access]` y su controlador ODBC; el entorno del motor
no necesita Access. Sin el motor, las pruebas de integración se omiten explícitamente.

### Formato de entrada

`examples/radial-balanced.json` muestra todos los campos de
`nandu-electrical-network-v1`:

- `frequency_hz`: frecuencia común de la red.
- `source`: barra ABC, tensión interna fase-neutro en V, ángulo en grados e
  impedancias de Thévenin de secuencia positiva y cero en ohmios. Se usa la
  convención del modelo Vsource de OpenDSS; no representa una barra ideal.
- `buses`: identificadores y fases presentes. Todas las barras comparten la base
  de tensión de la fuente en esta versión.
- `lines`: extremos, orden explícito de fases y matrices simétricas del **tramo
  completo**: resistencia e inductancia expresada como reactancia a la frecuencia
  declarada, ambas en ohmios, y capacitancia nodal en nF. No son valores por km.
  El motor recibe longitud 1 y matrices ya escaladas. `enabled` declara la maniobra.
- `loads`: cada entrada es una carga monofásica `wye_grounded`, modelo
  `constant_power`, con P en W y Q en var. Q negativo representa inyección
  capacitiva. P negativo y conexiones delta no están admitidos todavía.
- `solver`: tolerancia y máximo de iteraciones opcionales.

Se rechazan campos desconocidos, fases incompatibles, referencias faltantes,
matrices no simétricas/no semidefinidas positivas y cargas sin camino por su fase
hasta la fuente. Los identificadores se traducen a nombres internos; no se
interpretan como comandos DSS. Cada corrida usa un contexto nuevo.

### Resultados y criterios

El informe `nandu-load-flow-v1` registra versión del motor, SHA-256 del modelo,
comandos generados, convergencia, iteraciones, tensiones complejas y pu, corrientes
y potencias por conductor/terminal, pérdidas de red y balance de potencia.

Las potencias de terminal son positivas entrando al elemento. El campo
`net_power_absorbed_w_var` de una carga representa su consumo, no una pérdida de
línea. El suministro se toma en los terminales externos de la fuente; la pérdida
interna de Thévenin no se suma a las pérdidas de la red aguas abajo.

Después de converger se comprueban el balance de potencia y la reproducción de
P/Q especificados. Fuera de la banda técnica de 0,01–100 pu se rechazan los
resultados como no soportados; esa banda no es un límite operativo de calidad.
Convergencia fallida devuelve `not_converged` sin tensiones reutilizables; si fallan
las verificaciones devuelve `invalid_results`. Ambos estados producen salida CLI 2.

### Verificación independiente

La fuente del ejemplo tiene 230 V LN y R de Thévenin de 0,01 ohm por fase; la
línea tiene R=0,2 ohm por fase, sin acoplamiento ni capacitancia, y cada carga
consume 1.000 W. La solución de alta tensión es:

`V = (230 + sqrt(230² - 4 × 0,21 × 1000)) / 2 = 229,083302898 V`

OpenDSS reprodujo esa tensión y pérdidas de línea de **11,433109901 W**. El
residuo activo del balance fue aproximadamente **−4,71 × 10⁻⁸ W**.

Verificación de 0.4.0: **47 pruebas correctas, ninguna omitida** en `.venv`,
incluyendo 7 pruebas contra el motor real. También se compara un caso desequilibrado
de 500/1000/1500 W con tres soluciones analíticas independientes.

Resultado persistido: `data/radial-balanced-flow-v040.json`.
Esta validación cubre los casos indicados; no demuestra todavía equivalencia
de CZU06/CZU08 con CYME ni incluye reguladores, transformadores o estimación de demanda.

## Cargas delta y compensación reactiva — 0.5.0

```powershell
.\.venv\Scripts\python.exe -m nandu solve "examples\delta-compensated.json"
```

Se mantiene el formato `nandu-electrical-network-v1` y la compatibilidad con los
casos Yg de 0.4.0. Cada carga delta tiene `connection: "delta"` y `phases` con
exactamente dos fases, por ejemplo `["A", "B"]`, en lugar del campo `phase`.
Sus `p_w` y `q_var` son la potencia **de esa rama**, sin división entre fases.
Un banco delta completo se expresa con tres ramas AB, BC y CA.

La lista opcional `capacitors` acepta ramas con estos campos:

| Campo | Significado |
|---|---|
| `id`, `bus` | Identificador único y barra |
| `connection` | `wye_grounded` o `delta` |
| `phase` / `phases` | Una fase para Yg / dos fases distintas para delta |
| `rated_voltage_v` | Tensión nominal de la rama: LN en Yg, LL en delta |
| `rated_q_var` | Potencia capacitiva nominal positiva, por rama |
| `enabled` | Estado booleano de conexión |

Son capacitores fijos ideales sin control automático ni pérdidas resistivas.
Su potencia reactiva absorbida, con signo negativo para inyección, se verifica
después del cálculo mediante:

`Q_absorbida = −Q_nominal × (|V_rama| / V_nominal)²`

Para delta se obtiene la tensión de rama restando los fasores de las dos fases,
sin asumir `sqrt(3)` sobre una tensión real desequilibrada.

El balance separa consumo de cargas, aporte capacitivo y potencia neta absorbida
por las líneas. `engine_losses_w_var` conserva el valor directo del motor;
`network_losses_w_var` se obtiene sumando los elementos de línea habilitados.
Los capacitores desconectados no aportan corriente ni potencia.

### Resultado del ejemplo

- Tres cargas delta: 3.000 W y 1.500 var en total.
- Tres capacitores de 300 var nominales: aporte real **892,839560 var**.
- Potencia reactiva suministrada por la fuente: **607,160440 var**.
- Pérdidas activas de línea: **11,901423 W**.
- Convergencia en 4 iteraciones, balance y ley de tensión del capacitor verificados.

Resultado: `data/delta-compensated-flow-v050.json`.

Verificación: **55 pruebas correctas, ninguna omitida**, incluyendo 12 pruebas
con OpenDSS real. Se comprueba equivalencia Yg/delta a igual potencia total en un
caso equilibrado, rama AB sin corriente C, ley de tensión del capacitor, apertura
del banco, reducción de reactiva de cabecera y rechazo de fases incompatibles.

Pendiente: reguladores delta de taps fijos y su validación, conversión revisada
de cargas ABC de Access y contraste de los alimentadores reales contra CYME.

## Motor nodal y estimador de estado — 0.6.0

Primera versión que calcula los alimentadores reales importados de CYME. Requiere
`numpy` y `scipy` (extra `.[engine]`, incluidos en `requirements-solver.txt`).

```powershell
.\.venv\Scripts\python.exe -m nandu loadflow "data\czu06-czu08.nandu.sqlite"
.\.venv\Scripts\python.exe -m nandu estimate "data\czu06-czu08.nandu.sqlite" --output "data\estimacion-nueva.json"
.\.venv\Scripts\python.exe -m nandu loadflow "data\czu06-czu08.nandu.sqlite" --compare-cyme "<redes>_node_voltages.csv"
```

`--networks` limita las redes; `--details` o `--output` incluyen nodos, ramas,
reguladores y maniobras. `--compare-cyme` compara las tensiones con el CSV que
genera `cyme_export_scenario` del MCP (`VLNA/B/C` en kV). Código de salida 2 si
no converge.

### Qué hace CYME (ingeniería inversa)

Detalle y evidencias en [docs/cyme-ingenieria-inversa.md](docs/cyme-ingenieria-inversa.md).

- El flujo configurado en el estudio es **VoltageDropUnbalanced** (barrido
  regresivo-progresivo), tolerancia 0,1 % de tensión, 60 iteraciones, arranque
  plano, **sin impedancia de fuente**, con carga capacitiva de línea.
- `CymMANAloadFlow9064.dll` implementa Newton-Raphson desbalanceado sobre MANA
  (análisis nodal aumentado modificado; Kocar 2014, Therrien 2017) con Intel MKL
  PARDISO, y también el **estimador de estado**.
- El estimador es **WLS con restricciones (Hachtel)**: cargas como pseudomedidas,
  precisión declarada = 3σ, multiplicadores de Lagrange para las ecuaciones de red.
  Valores por defecto del estudio: cargas 50 % (kW y kvar), medidores 3 %
  (A, FP, kW, kvar), tensión 1 %, fuente 2 %.

### Modelo y motor

- `nandu/cyme_model.py`: tablas CYME → modelo por fase. Cada convención está en
  `CONVENTIONS` con su evidencia y autoridad (`verified`, `fitted_to_cyme_results`,
  `hypothesis`) y se copia a cada informe.
- `nandu/nodal.py`: tensiones por terminal más corrientes auxiliares de fuentes,
  maniobras cerradas y reguladores (estructura MANA); Newton-Raphson en
  coordenadas rectangulares con LU disperso. Cargas ZIP Yg/delta, capacitores,
  líneas PI nominal, cargas a mitad de tramo con la línea partida.
- **Regulador delta cerrado**: tipo A, `Vsal_X = Vent_X + n_X (Vent_X − Vent_Y)`,
  `n = paso × tap`. Se ajustó a 15 resultados reales de reguladores guardados por el
  MCP: residuo ≤ 2,6 V sobre 13,6 kV. El tipo B y la conexión alternativa dejan
  residuos de 6 a 180 V.
- `nandu/estimator.py`: estado = tensiones, corrientes auxiliares y potencia de
  cada carga. Medidores AMP-FP (|I| y FP) en la cabecera y en maniobras.

### Resultado sobre CZU06/CZU08 (base actual)

| Red | Fuente (kW) | Cargas (kW) | Pérdidas (kW) | V mín (pu) | V máx (pu) | I cabecera A/B/C (A) |
|---|---:|---:|---:|---:|---:|---|
| CZU06 | 6.708,5 | 5.799,7 | 908,8 | 0,9131 | 1,0672 | 191,2 / 165,3 / 160,1 |
| CZU08 | 7.150,7 | 6.005,0 | 1.145,8 | 0,8834 | 1,0996 | 160,8 / 202,1 / 181,8 |

Converge en 4 iteraciones (0,14 s; 3.213 terminales). El balance fuente = cargas +
pérdidas cierra al vatio. Medidores de la base: CZU06 188,1/158,7/161,0 A,
CZU08 159,9/199,1/195,0 A, FP 95 %. El estimador converge en 5 iteraciones con
costo J = 31,5, residuos de medidores menores que 2σ y cargas estimadas de
5.733 kW (CZU06) y 6.069 kW (CZU08). *Histórico (0.6.0, medidor como |I| y FP):
desde 0.9.0 el modelo por defecto es |I|, P y Q y da J = 95,925.*
Informes: `data/czu06-czu08-loadflow-v060.json`, `data/czu06-czu08-estimate-v060.json`.

Verificación: **73 pruebas correctas** en `.venv`. El motor nuevo reproduce la
solución analítica (229,083302897 V; 11,433109901 W), el caso delta con capacitores
(607,160440 var) y OpenDSS en un caso desbalanceado con acoplamiento mutuo.
También se prueban la relación y la conservación de potencia del regulador, la
orientación por la fuente, la corriente por maniobras, la desenergización por fase
abierta, la consistencia del estimador y la regla de CYME «las cargas grandes
absorben más corrección» (ΔP ∝ P²).

### Contraste con CYME en vivo — 19 de octubre de 2026

Corrida `nandu-ref-lf-20261019-01` en CYME 9.0 r4, por el MCP. Redes CZU06 y CZU08
cargadas desde la base, sin modificaciones pendientes y con la configuración
DEFAULT (VoltageDropUnbalanced, 0,1 %). La base se reimportó a
`data/czu06-czu08-20261019.nandu.sqlite`: SHA-256 `c7a2248f…`, mismos datos que la
importación anterior salvo la tabla de bloqueo `CYMNETWORKLOCK`. Los archivos de
referencia de CYME están en `data/cyme-ref/nandu-ref-lf-20261019-01/`.

| Magnitud | CYME | Ñandu |
|---|---:|---:|
| Pérdidas CZU06 (kW) | 908,06 | 908,82 |
| Pérdidas CZU08 (kW) | 1.145,37 | 1.145,79 |
| Potencia de cabecera CZU06 (kW / kvar) | 6.707 / 2.023 | 6.708,5 / 2.026,5 |
| Tensión de 2.690 terminales: máx. / p95 / mediana / RMS de \|ΔV\| (V) | — | 17,4 / 14,7 / 5,0 / 8,0 |
| Potencia de 1.011 cargas por fase (informe «Loads») | — | \|ΔS\| ≤ 0,09 kVA |
| Potencia de paso de 1.308 tramos | — | mediana \|ΔP\| 0,3 kW |

Una diferencia de 17 V sobre 13,4 kV es 0,13 %, el orden de la tolerancia de
convergencia de CYME (0,1 % de variación entre iteraciones). Ñandu converge a 1e-9 pu.
Comprobar que el residuo se debe a esa tolerancia requiere repetir la corrida de
CYME con una tolerancia menor.

Hipótesis cerradas con esta corrida, ahora `verified` en `CONVENTIONS`:

- Reparto ABC en tercios y rama delta A→AB, B→BC, C→CA. CYME reporta la delta por fase de línea.
- Cargas ABC sobre tramos sin esas fases: CYME las deja en 0 kVA.
- Reactancias del catálogo **sin** reescalar por frecuencia: reescalar 60/50 lleva el
  error a 663 V.
- Carga capacitiva de línea incluida: sin ella el error es de 188 V.
- Matrices de fase por secuencias y modelo PI nominal.

Siguen abiertos el estimador (lectura de `DemandType=3` como AMP-FP; medida de FP
frente a P/Q) y los controles automáticos de reguladores (`Terminal`/`LoadCenter`).
Hoy los taps guardados se usan como fijos.
Informe: `data/czu06-czu08-20261019-loadflow-vs-cyme-v060.json`.

## Interfaz gráfica — 0.7.0

```powershell
.\.venv\Scripts\python.exe -m nandu ui                      # abre http://127.0.0.1:8765/
.\.venv\Scripts\python.exe -m nandu ui "data\czu06-czu08-20261019.nandu.sqlite" --port 8765 --no-browser
```

Aplicación local con la disposición de CYME: menú (File, View, Analysis, Reports,
Help), barra de herramientas, **Explorer** a la izquierda, **unifilar
geográfico** en el centro, **Properties** a la derecha y **Simulation Result**
abajo. Usa solo la biblioteca estándar en el servidor y HTML/JS/CSS propios.
Escucha únicamente en `127.0.0.1` y solo abre archivos `.nandu.sqlite`
de la carpeta de datos.

- Unifilar con coordenadas EPSG:32721 de la base. Usa el color de cada red
  (`CYMNETWORK.Color`), tramos trifásicos continuos y mono o bifásicos en trazo-punto,
  como CYME. Tiene símbolos de fuente, regulador, interruptor, reconectador,
  seccionador, fusible, capacitor y carga. Zoom con la rueda, desplazamiento
  arrastrando y doble clic para acercar.
- Selector de fondo **None / OpenStreetMap / Satellite (Esri)** en la barra: convierte
  posiciones UTM WGS84 (EPSG:32601–32660 y 32701–32760) a EPSG:3857 y superpone
  las redes sobre teselas. Requiere internet solo al elegir un fondo; con otros
  sistemas de coordenadas las opciones de mapa quedan deshabilitadas. Cada fondo
  muestra su atribución y `None` vuelve al diagrama sin conexión. Al alternar entre
  callejero y satélite se conserva el zoom y el desplazamiento.
- Capas de color: color de red, fase, nivel de tensión (%) y nivel de carga (%).
  Esta última usa las bandas y la paleta de la capa «Loading level color(%)» de CYME.
  Capas de etiquetas: tensiones por nodo, corrientes por tramo e identificadores.
  Al pasar el cursor aparece la información en un recuadro amarillo, como en CYME.
- Diálogos Load Flow y Distribution State Estimator (también con F5) con sus
  parámetros. Informes: Summary Report (formato de CYME), Abnormal conditions
  (límites 95–105 %), Buses, Lines and Cables, Iteration Report, DSE - Meter y
  DSE - Load Values. Las filas son filtrables y al hacer clic llevan al elemento.
- Properties muestra los resultados y las filas originales de Access del tramo, sus
  dispositivos, equipos y clientes. Solo se traducen a texto los códigos verificados.

Verificación: 77 pruebas correctas, incluidas las del servicio y del servidor HTTP
con una red sintética. Revisión visual en Chrome sobre CZU06/CZU08: en el tramo del
regulador `TRAMO_CZU06_6938` la carga es 65,9 % (CYME: 66,0 %).

## Distribución de carga, calidad del estimador, estudios .sxst y control de taps — 0.8.0

```powershell
.\.venv\Scripts\python.exe -m nandu allocate "data\czu06-czu08-20261019.nandu.sqlite" [--method KVAMethod|ActualKVAMethod|KWHMethod]
.\.venv\Scripts\python.exe -m nandu estimate "data\czu06-czu08-20261019.nandu.sqlite" --initialization LoadAllocation --quality
py -3.11 -m nandu import-sxst --study "<estudio>.sxst" --networks CZU06 CZU08 --output "data\nuevo.nandu.sqlite"
```

**Load Allocation** (`nandu/allocation.py`). Implementa las fórmulas de
`LoadAllocation.chm`: por fase, kW = kW_dem × peso / Σ pesos y kvar = kW × √(1/FP² − 1).
El peso es el kVA conectado, el kVA actual o los kWh, por el factor de utilización.
Con «Adjust loads using load flow» la demanda se corrige con el flujo hasta que la
cabecera calculada coincide con el medidor (tolerancia 0,5 %, corrección secante).
Las cargas bloqueadas conservan su valor; las cargas sin dato para el método
reciben 0, como en CYME. En CZU06/CZU08 (KVAMethod) converge en 8 flujos (1,4 s)
con las corrientes de cabecera a menos de 0,5 % de los medidores. 174 cargas sin
kVA conectado quedan en 0.
**Hipótesis abierta:** con medidor desbalanceado, Ñandu reparte cada carga ABC por
fase según la demanda de cada fase. La ayuda de CYME sugiere que mantiene equilibradas
las cargas trifásicas. Hay que contrastarlo con CYME.

**Estimador**: `--initialization LoadAllocation` reproduce el `InitializationMode` de
CYME. Arranca con J = 0,26 frente a 31,5 sin inicialización. `--quality` calcula
los índices de calidad:

- Costo J frente al umbral χ², con grados de libertad = medidas − estados + restricciones.
- Residuos normalizados r_N = |r| / √Ω_ii, con Ω = diag de R·E11·R del sistema de
  Hachtel, obtenida con una sola factorización LU.

En CZU06/CZU08 (modelo de medidor de 0.8.0, histórico), J = 31,5 supera el umbral (21,0 con 12 g.l.): la incoherencia entre
el FP medido (0,95) y el de las cargas (≈0,967) aparece como dato sospechoso. Los
umbrales de residuos (3 y 4) son parámetros, no valores leídos de CYME.

**Estudios .sxst** (`nandu/sxst_import.py`, solo biblioteca estándar). Lee el XML de
CYME, traduce los enums de texto a los códigos Access verificados y convierte los
valores de carga tipados (`KW_KVAR`, `KVA_PF`, `KW_PF`) a kW/kvar. Conserva los
parámetros de cálculo del estudio en la procedencia y genera el mismo
`.nandu.sqlite`, que luego sirve para `loadflow`, `estimate`, `allocate` y la UI.
Los dispositivos no soportados se cuentan en la procedencia, por ejemplo transformadores.

**Control de reguladores** («Normal Tap Operation»). Terminal y LoadCenter/RX mueven
taps discretos cuando la tensión de control sale de SetVoltage ± Bandwidth/2. Las
consignas se leen de `CYMLDCSETTINGS`. La tensión de control usa la base de 120 V del
estudio, con compensación R+jX: es una **hipótesis**. Los mensajes de la UI usan el
formato de la DLL de CYME («Iter(n) - Regulator(X): Old Tap … New Tap …»). La base
actual tiene todos los reguladores en FixedTap y no cambia.

Contraste parcial con el estudio del 6 oct. 2026 (`lf-czu-20261006-01`):

| | CYME | Ñandu |
|---|---:|---:|
| CZU06 pérdidas / Vmín / Vmáx | 2,13 kW / 99,40 % / 100,24 % | 1,77 kW / 99,26 % / 100,09 % |
| CZU08 pérdidas / Vmín / Vmáx | 1.585,5 kW / 83,64 % / 107,14 % | 1.514,0 kW / 87,48 % / 108,21 % |

CZU06 queda cerca y es coherente con que los reguladores en Terminal no hayan movido
taps. La diferencia en CZU08 no está explicada; ese estado tenía reguladores con
tensión nominal incoherente según los mensajes de CYME, y solo hay tres valores
agregados para comparar. No se usa como validación.

Verificación: **94 pruebas correctas**. Cubren las fórmulas de reparto, el bloqueo, el
FP en adelanto, el ajuste por flujo, la diagonal de la inversa frente a la inversa
densa, χ², el control Terminal/LDC/límites en Yg y delta cerrado, y la importación
`.sxst` con un estudio sintético.
Informes: `data/czu06-czu08-20261019-allocation-v080.json`,
`data/czu06-czu08-20261019-estimate-quality-v080.json`,
`data/czu06-czu08-sxst-20261006-loadflow-v080.json`.

## Validación en vivo de flujo, estimador y distribución de carga — 0.9.0

Corridas en CYME 9.0 r4 el 19 oct. 2026 por el MCP, con autorización del usuario.
Los parámetros que se cambiaron para cada prueba se restauraron al terminar, y las
cargas modificadas en memoria se deshicieron: CYME quedó con 0 modificaciones,
tolerancias en 0,1 % y taps del estimador en Infinite. Las referencias están en
`data/cyme-ref/` y el resumen en `data/czu06-czu08-20261019-cyme-validation-v090.json`.

**Flujo de carga.** Con la tolerancia de CYME bajada temporalmente a 0,001 %, la
diferencia de tensión en 2.690 terminales baja de 17,4 a **11,2 V máx. / 2,8 V
mediana**. Parte del residuo anterior era la tolerancia de 0,1 % de CYME. Lo que
queda es un patrón por fase que ya aparece en el tronco antes de los reguladores.
No se explica por la impedancia mutua (±10 % lleva a ±100 V), ni por los
capacitores, la posición de las cargas «Middle», el paso de los reguladores o las
cargas de los cables.

**Estimador de estado.** El historial de modificaciones de CYME trae el valor exacto
de cada carga antes y después; `scripts/cyme_dse_changes.py` lo extrae y
`scripts/compare_cyme_dse.py` lo compara. Resultado:

| | Filas | RMS kW | RMS kvar | Suma kW | Suma kvar |
|---|---:|---:|---:|---:|---:|
| CZU06 | 325 | 0,23 | 0,03 | +0,4 | +0,8 |
| CZU08 | 284 | 0,67 | 0,67 | −127 | +126 |

El modelo que reproduce CYME en CZU06, ahora por defecto (`CONVENTIONS['state_estimator']`):

- El medidor A-FP entra como |I|, P y Q, cada uno con precisión 3 % = 3σ. Antes
  entraba como |I| y FP, que difería en 80 kW / 118 kvar.
- La σ de las cargas es la precisión × valor (50 %), sin dividir por 3.
- Las cargas bloqueadas quedan fijas: CYME no modificó ninguna de las 12.
- La estimación es por fase y CYME escribe en cada fila ABC la suma de sus fases.

En CZU08 el sesgo no se explicó con ninguna de las variantes probadas: modo de taps,
tolerancia, cargas bloqueadas, delta como estrella, agrupar filas ABC, variantes
del regulador. Todas las cargas modificadas allí están aguas abajo de reguladores
en delta cerrado. El estimador de CYME deja además la fase B un 5,8 % sobre el
medidor tras reescribir las filas ABC equilibradas.

**Load Allocation.** CYME mantiene **equilibradas** las filas ABC: reciben la asignación
de menor módulo entre sus fases, y las monofásicas absorben el resto por fase. Con
esa regla, el error por carga en CZU06 baja de 6,0 a **0,9 kVA RMS** (388 potencias
por fase; FP a ±1,3 puntos salvo una delta monofásica con convención de signo).
En CZU08 CYME **no converge** («cannot adjust KW after 60 iterations») y deja las
cargas sin cambios; Ñandu sí converge (17 flujos) y lo informa. Esa diferencia de
comportamiento se mantiene a propósito.

Verificación: **98 pruebas correctas** (filas ABC equilibradas, cargas bloqueadas en
el estimador, medidas |I|/P/Q).

## Integridad del modelo y consistencia de resultados — 0.10.0

Responde a la revisión técnica externa de 0.9.0 (19 oct. 2026). Cada hallazgo
reproducido tiene ahora una prueba de regresión con la expectativa corregida.

**Modelo (H1–H3).**

- Un equipo de un `.sxst` sin modelo en Ñandu (p. ej. `Transformer`) ya no
  desaparece. Su asociación se conserva sin código Access, la auditoría estructural
  la marca `unsupported_device` y `cyme_model` excluye el tramo entero en lugar de
  calcularlo como si el equipo no existiera.
- Las exclusiones son atómicas. Un tramo o una fila de cliente se valida entera en
  estructuras locales y solo se publica si nada falla. Antes, un capacitor inválido
  dejaba incorporadas las líneas del tramo, y una carga delta que fallaba en su
  segunda rama dejaba la primera (45 kW).
- Identificadores entre redes (`CONVENTIONS['identity']`, hipótesis). Los `NodeId`
  son globales y un nodo compartido es frontera. Un `SectionId`, un dispositivo
  (tipo, número) o una fuente repetidos entre las redes calculadas se excluyen con
  todos sus implicados y se reportan. Antes se fusionaban en silencio y el sistema
  quedaba singular. Dos redes disjuntas calculadas juntas dan las mismas tensiones
  que por separado (prueba).

**Resultados (H4, H7).**

- Load Allocation decide la convergencia antes de actualizar las potencias. Las
  potencias publicadas son las del último flujo resuelto, y `nodal.solve` copia el
  arreglo que recibe. En CZU08 el residuo `fuente − cargas − pérdidas` pasa de
  −4,5 kW a 2e-11 relativo: la fuente (7.134,232 kW) y las pérdidas (1.209,086 kW) no
  cambian; la carga publicada pasa de 5.929,669 a 5.925,146 kW. Contra CYME en CZU06
  mejora de 0,896 a **0,876 kVA RMS** (máx. 6,98 → 6,65 kVA; 388 potencias por fase).
- `nodal.solve` converge solo si, después del último paso, la corriente no balanceada
  (≤ 1e-6 A) y las restricciones de tensión (≤ 1e-6 V) cumplen sus tolerancias,
  separadas por unidad. El estimador exige además factibilidad del estado publicado.
- Los resultados incluyen `capacitor_totals` y `balance` por red; el informe CLI
  muestra `capacitor_power` y `balance_residual`. En CZU06 la partida que faltaba en
  el resumen era la de capacitores (−325,137 kvar).

**Cobertura (separada del estado del solver).** `model_coverage.status` vale
`complete`, `cyme_equivalent_exclusions` (solo exclusiones que CYME también hace,
como las cargas ABC sobre tramos monofásicos), `approximated` o `incomplete`, con la
potencia excluida en kW/kvar. `loadflow`, `estimate` y `allocate` devuelven
**código 3** si convergen con cobertura `incomplete`. Las cargas sin energía figuran
en `skipped_elements.loads`. `--compare-cyme` cuenta también los terminales de
Ñandu que el CSV no trae (`missing_in_cyme`).

**Estimador (H5, H6).** Las lecturas A-FP se validan (corriente > 0, 0 < |FP| ≤ 100)
y se rechazan con diagnóstico. La σ de P y Q del medidor tiene un piso de 0,1 % de
la potencia aparente medida (`meter_sigma_floor_pct_of_s`, hipótesis de ingeniería):
con FP = 100 % el estimador converge en vez de dividir por cero. En CZU06/CZU08 el
piso no actúa. Los índices de calidad llevan `calibration`: en el modo compatible
con CYME, |I|, P y Q de un mismo par A-FP se ponderan como independientes y los
grados de libertad no verifican rango, así que χ² y la clasificación de residuos
son orientativos. La formulación calibrada (covarianzas propagadas) queda pendiente.

**Interfaz (H8).** La sobrecarga se evalúa por fase (`loading_pct_phase`). Con
120/50/50 A y 100 A de capacidad, solo A figura sobrecargada. La potencia de paso
conserva el signo (positivo en sentido From → To).

**CZU06/CZU08 con 0.10.0** (`data/czu06-czu08-20261019-*-v0100.json`):

| Cálculo | Resultado |
|---|---|
| Flujo frente a CYME (tol. 0,001 %) | 2.690 terminales; 0 faltantes en ambos sentidos; 11,171 V máx., 9,956 V p95, 5,517 V RMS (sin cambios) |
| Residuo final del flujo | 3,9e-8 A; balance P/Q relativo ≤ 6e-11 por red |
| Cobertura | `cyme_equivalent_exclusions` (ANDE_19787 y ANDE_2424) |
| Load Allocation KVAMethod | 17 flujos; 0,423 % de desajuste; 174 cargas sin peso; balance ≤ 2e-11 |
| Estimador `--quality` | 6 iteraciones; J = 95,925 (sin cambios); residuo KCL 3,6e-8 A |

Pendientes de la revisión: covarianzas y rango en el estimador (H6), escalamiento y
búsqueda lineal en Newton, validación en alimentadores externos al ajuste,
transformadores, licencia y CI.

Verificación: **116 pruebas correctas**, ninguna omitida.

## Ventana de informes tipo CYME — 0.11.0

La ventana **Simulation Result** se acerca a la de informes de CYME:

- **Tablas** (Buses, Lines and Cables, Regulators, Shunt Capacitors, Voltage Profile,
  Load Values): orden por columna (clic en el encabezado: ascendente, descendente, sin
  orden; los vacíos quedan al final), selector **Columns** con columnas opcionales
  ocultas por defecto (ángulos, carga por fase, distancia, tap máximo), filtro de texto
  sobre todas las celdas y contador de filas. Se muestran las primeras 3.000 filas; la
  exportación incluye todas.
- **Exportación** de cualquier informe, con el filtro, el orden y las columnas
  visibles: **Copy** (separado por tabuladores, para pegar en Excel), **CSV** (UTF-8
  con BOM, punto decimal) y **Excel** (`.xlsx` Office Open XML generado en el
  navegador, sin dependencias; los números se guardan como números y no como texto).
- **Altura del panel**: se arrastra la barra «Simulation Result» hacia arriba o abajo;
  doble clic la maximiza y vuelve a la altura anterior. La altura se recuerda en el
  navegador. El perfil de tensión se dibuja al tamaño del panel o de la ventana
  desacoplada.
- **Detach**: abre el informe en una ventana propia del navegador, que se actualiza
  con cada cálculo. Se pueden tener varias a la vez, y al hacer clic en una fila se
  selecciona el elemento en el unifilar principal.
- Informes nuevos, calculados en `ui/service.py` a partir del mismo estado publicado
  por el motor:
  - **Regulators**: uno por fase. Incluye tap final (después del control de taps),
    tensiones de entrada y salida, corriente y, si hay control, la tensión de control
    sobre la base de 120 V (`nodal.control_voltages`) con su consigna y ancho de banda.
  - **Shunt Capacitors**: kvar nominales y entregados a la tensión calculada
    (Q·|V|²/Vn², el mismo modelo de impedancia constante del motor), y «Loading (%)»
    con la definición de CYME (kvar entregados / nominales).
  - **Voltage Profile**: tensión (%) por fase en función de la distancia a la fuente.
    Muestra todos los nodos como puntos y el camino fuente → nodo seleccionado (o el
    más lejano) como líneas por fase. La distancia es la suma de longitudes de línea
    (Dijkstra desde las fuentes) y no cruza maniobras abiertas en todas sus fases.
    Los colores de fase son los del unifilar (convención CYME). Como rojo y verde no
    se distinguen con deuteranopía, cada fase tiene además su propio trazo y una
    etiqueta directa.
- Connection y Tap Mode usan los textos de CYME (`Yg`, `D`, `ClosedDelta`,
  `FixedTap`, `LoadCenter`, `RXSettings`, `Terminal`). El código Access 4
  (FixedTapPrimary) se informa como `FixedTap`, porque el modelo lo trata igual.

Verificación: **120 pruebas correctas**. Cuatro son nuevas:
- Informes de regulador y perfil en la red sintética: la distancia final es la suma de
  longitudes, los taps por fase son los del modelo y la potencia aparente de entrada
  es igual a la de salida.
- Perfil sin cruzar un enlace abierto y sin contar dos veces una línea partida.
- kvar entregados y «Loading (%)» de un capacitor a 1,05 Vn (110,25 %).

El `.xlsx` lo abrió Microsoft Excel por COM: números como `Double`, encabezados en
negrita y caracteres `<&>"Ñ` correctos. Revisión en Chrome: orden, columnas y perfil,
sin errores de página. La ventana desacoplada se abre (evento de popup), pero su
contenido no se capturó.

**Contraste con CYME en vivo — 27 de octubre de 2026.** CZU06/CZU08 se cargaron desde
`BD_Centro0926.mdb` sin modificaciones (corrida `nandu-reports-czu-20261027-01`) y se
exportaron sin guardar el estudio (`data/cyme-czu06-czu08-20261027/`). El SHA-256 de la
base ya no era el de la importación del 18 de octubre, así que se reimportó a
`data/czu06-czu08-20261027.nandu.sqlite`. Las 230 tablas importadas resultaron
idénticas a las de las importaciones del 18 y del 19 de octubre: el hash cambió por
bytes internos de Access, no por los datos. Detalle en
`data/czu06-czu08-20261027-ui-reports-v0110.json` y `...-ui-capacitors-v0110.json`.

| Informe | Resultado frente a CYME |
|---|---|
| Regulators: taps | 12/12 iguales (4 reguladores × 3 fases) |
| Regulators: V in / V out | ≤ 13,1 V (mismo orden que el residuo del flujo) |
| Regulators: corriente | ≤ 0,4 A; CYME informa la corriente del lado de entrada |
| Shunt Capacitors: kvar nominales | 3/3 iguales |
| Shunt Capacitors: kvar entregados | 9/9 fases ≤ 0,56 kvar; el «Loading (%)» de CYME es Q/Qn = (V/Vn)², no V/Vn |
| Voltage Profile: distancia | 1.304 nodos, ≤ 0,05 m (redondeo); los 3 nodos sin energía faltan en ambos |
| Tensiones nodo a nodo | 2.690 terminales; 17,4 V máx. con la tolerancia por defecto de CYME (0,1 %); 11,171 V frente a la corrida de CYME a 0,001 % del 19 oct., sin cambios |

El contraste corrigió tres errores de la primera versión:
- La entrada y la salida se tomaban del sentido del tramo. En `RT_CZU_06_100A_3` el
  tramo apunta contra el flujo y salían invertidas (1,4 kV). Ahora se usan
  `input_bus`/`output_bus`, los mismos que orienta el motor.
- La corriente era la del lado de salida y quedaba entre 4 y 15 % por debajo de la de
  CYME (el cociente es Vout/Vin). Ahora `I (A)` es la de entrada, Mᵀ·I_salida, y la
  de salida queda en la columna opcional `I out (A)`.
- La distancia contaba dos veces las líneas partidas por cargas al medio: 14,9 km de
  error a 87 km de la fuente.

Las tensiones de CYME de hoy son idénticas (0,0 V) a las de su corrida del 19 de
octubre con la tolerancia por defecto. La corrida a 0,001 % se separa de ellas hasta
7,3 V. Los 17,4 V incluyen ese error de convergencia de CYME, y las diferencias de
tensión de los reguladores (≤ 13 V) también. El contraste exacto sigue siendo el de
0,001 % (11,171 V, pendiente sin explicar).

## Edición de red en la interfaz — 0.12.0

La interfaz permite editar la red como en CYME. Las ediciones se hacen sobre las mismas
filas de Access (`CYMSECTION`, `CYMSECTIONDEVICE`, `CYMNODE`, tablas de equipos,
`CYMCUSTOMERLOAD`, catálogos `CYMEQ*`), de modo que la red editada se recalcula con el
mismo `cyme_model.build` y se puede guardar como proyecto Ñandu.

- **Modo edición** (botón **Edit** o menú Edit). En Properties, los campos editables
  pasan a ser controles y cada grupo tiene **Apply** (también Enter) y **Reset**. Los
  campos con dominio conocido son listas: fases 1..7, ClosedPhase, Location,
  ConnectionConfiguration, SettingOption y LoadValueType con los códigos verificados,
  y los identificadores de equipo (LineId, CableId, EquipmentId, ConsumerClassId) con
  las filas de su catálogo. Los números se escriben con punto o coma decimal y se
  guardan con el tipo que ya tiene la columna. Identidad y conectividad (NetworkId,
  SectionId, DeviceNumber, FromNodeId, ToNodeId...) no se editan como campos: tienen
  operaciones propias. Un grupo «Equipment» edita la fila del catálogo, compartida por
  todos los equipos que la usan (el título indica cuántos).
- **Mover nodos**: arrastrar un nodo en modo edición. Con fondo de mapa, la posición se
  convierte de EPSG:3857 al UTM del proyecto (ida y vuelta < 0,1 mm). La longitud
  eléctrica (`Length`) no cambia al mover nodos, como en CYME: es un dato del tramo.
- **Menú contextual** (clic derecho en modo edición): en un nodo, *Add section from this
  node*; en un tramo, *Add device ▸* (Switch, Fuse, Breaker, Recloser, Regulator, Shunt
  Capacitor, Spot Load), *Delete <equipo>*, *Add section from To node* y *Delete
  section* (también la tecla Supr).
- **Alta de tramo**: desde un nodo se hace clic en otro nodo o en un punto vacío (nodo
  nuevo). El diálogo pide identificador (automático si se deja vacío), fase, tipo de
  conductor, equipo del catálogo y longitud (por defecto, la distancia dibujada).
- **Undo** (Ctrl+Z) deshace operación por operación; un grupo de propiedades aplicado de
  una vez se deshace junto.
- **Save As** (Ctrl+S): guarda la red editada como un `.nandu.sqlite` nuevo en la
  carpeta de datos y lo abre. El original no se modifica y un destino existente no se
  reemplaza. La procedencia del archivo nuevo guarda el archivo de origen con su
  SHA-256 y el diario completo de cambios (valores antes y después, plantillas usadas).

Cualquier edición borra el resultado calculado: tensiones y flujos de la red anterior
no corresponden a la editada. Las ediciones viven en la memoria del servidor hasta
guardarlas; recargar la página las conserva, cerrar el servidor las pierde.

**Reglas de Ñandu, no semántica de CYME.** Las siguientes decisiones son de la
interfaz y no se contrastaron con CYME:
- Un equipo nuevo copia una fila existente del mismo tipo (de la misma red si hay) y
  cambia identidad, red y ubicación; las maniobras quedan cerradas en las fases del
  tramo, y la carga nueva arranca en 0 kW / 0 kvar con las fases del tramo. Si la base
  no tiene ninguna maniobra de ese tipo, se crea una fila mínima con las columnas que
  lee `cyme_model` (en servicio, cerrada). Reguladores, capacitores y cargas necesitan
  una fila existente como plantilla.
- Un tramo nuevo copia un conductor existente del tipo elegido y toma la fase del tramo
  conectado al nodo de partida (ABC si no hay).
- Borrar un tramo borra sus equipos, sus clientes, los ajustes LDC de sus reguladores y
  sus puntos intermedios. Los nodos que quedan sin tramos, fuente ni medidor también se
  borran.
- El conductor no se borra solo: para quitarlo se borra el tramo.

Toda operación es atómica: si algo falla (código no verificado, equipo inexistente en el
catálogo, identificador repetido), las tablas quedan como estaban y el error se muestra.

Verificación: **127 pruebas correctas**. Siete son nuevas (`tests/test_ui.py`,
`EditingTests`):
- Propiedades con destino de edición y opciones verificadas.
- Edición que reconstruye el modelo, borra el resultado y se deshace.
- Valores inválidos sin efecto, incluso dentro de un lote.
- Alta de tramo, carga y seccionador, y borrado con limpieza de nodos.
- El conductor no se borra solo.
- Coordenadas de mapa guardadas en el UTM del proyecto.
- Save As con diario de cambios, sin tocar el original y sin reemplazar un destino.

Sobre CZU06/CZU08 (copia en una carpeta temporal) se agregaron los siete tipos de
equipo en un tramo y un tramo nuevo con una carga de 100 kW. El flujo converge, sin
avisos nuevos del modelo, y el archivo guardado reabre con su diario. La API se probó
por HTTP: alta, código inválido (400), deshacer, nombre de archivo con `../` (400) y
Save As. La página carga con el menú Edit y los botones nuevos. La interacción con el
mouse (arrastre, menú contextual, diálogos) no se pudo automatizar: la herramienta de
captura del navegador no respondió.

## Lecturas de medidor modificables — 0.13.0

El estimador y Load Allocation se pueden correr con lecturas de medidor distintas de la base,
para ver qué estado resulta con otra demanda de cabecera. Se reemplazan las lecturas A-FP de
`CYMNETWORKMETER` (`Val1A/B/C` = A, `Val2A/B/C` = FP %) antes de construir el modelo. La
lectura de `DemandType=3` como A-FP sigue siendo la hipótesis de `meter_convention`.

- **CLI** (`estimate` y `allocate`): `--meter RED:FASES=A[/FP]`, repetible. Las fases van
  juntas (`ABC`, `A`, `BC`...); sin FP se conserva el de la base; FP negativo = adelanto.
  La corriente y el FP se separan con `/` porque dentro de cada número se admite la coma
  decimal (`CZU06:A=100,5/95`).

  ```powershell
  .\.venv\Scripts\python.exe -m nandu estimate data\czu06-czu08.nandu.sqlite --networks CZU06 --meter CZU06:ABC=210/95
  .\.venv\Scripts\python.exe -m nandu allocate data\czu06-czu08.nandu.sqlite --meter CZU06:A=188/90 --meter CZU08:C=200
  ```

- **Interfaz**: el explorador tiene un grupo *Meters*. Al seleccionar el nodo del medidor,
  Properties muestra el grupo *Meter <red> (A-PF)*, y en modo edición se cambian corriente y
  FP por fase, con Undo, diario y Save As como cualquier otra edición. Los diálogos de Load
  Allocation y del estimador muestran las lecturas que se usarán y marcan con `*` las que
  difieren del archivo abierto.

Solo se editan esas seis lecturas. `DemandType`, las precisiones (`Precision*`) y los totales
no se tocan, porque el cálculo no los usa o no los admite.

**Trazabilidad.** Un informe con lecturas reemplazadas incluye `meter_overrides` (red, nodo,
fase, valores antes y después) y `meter_overrides_note`: el resultado no es comparable con una
corrida de CYME sobre la base sin cambios. En la interfaz, el resultado lleva el mismo
`meter_overrides` y un aviso al inicio de Messages.

**Rechazos** (código 1 en la CLI y error sin efecto en la interfaz), en lugar de elegir:
- red sin medidor o con más de uno;
- `DemandType` distinto de A-FP o demanda total;
- red fuera de `--networks`;
- la misma fase reemplazada dos veces;
- corriente negativa o no numérica;
- FP fuera de (0, 100] en una fase con corriente.

Una corriente de 0 deja la fase sin medir, como ya hacía `meters_from_model`.

Con los cambios de demanda, Load Allocation puede no converger. Mantiene sus filas ABC
equilibradas (regla de 0.9.0), así que un medidor muy desbalanceado no siempre se puede
cumplir en las tres fases. En ese caso el informe lo dice (`status`, código 2).

Verificación: **139 pruebas correctas**. Doce son nuevas (`tests/test_meters.py`):
- lectura de `--meter` y rechazo de textos ambiguos;
- reemplazo sin modificar las tablas de entrada, con el diario igual a `meter_differences`;
- modelo con los valores nuevos, y rechazos;
- fase con corriente 0;
- propiedades y explorador del medidor;
- edición marcada como modificada, que se deshace;
- valores y campos inválidos sin efecto;
- aviso en los resultados de la interfaz;
- informe de `allocate`, cuya potencia de fuente escala con la corriente (60/50 A);
- CLI de `estimate` y error de FP.

Sobre CZU06 (base de 188/159/161 A, FP 95 %):
- **Estimador sin cambios**: fuente 6.559 kW.
- **Estimador con `--meter CZU06:ABC=210/95`**: fuente 8.118 kW, Vmín 83,5 %.
- **Estimador con FP 90 % en la fase A**: kvar de fuente de 2.143 a 2.432.
- **Load Allocation con 210 A**: converge, con 210,1/210,2/209,4 A en la fuente.


## Base del SIN desde el esquema unifilar — 0.14.0

Ñandu importa ahora tablas con el formato de CYME desde un JSON (`nandu-cyme-tables-v1`).
El primer uso es el **Sistema Interconectado Nacional**: el esquema armado en
`eventos_ens/esquema` (unifilar `SIN2026-10V1.pdf` + SCADA + mímicos + correcciones a mano)
se traduce a una red CYME `SIN` y se guarda como cualquier otro `.nandu.sqlite`.

```bash
# en eventos_ens/esquema (networkx + PyYAML): grafo corregido → tablas CYME
../.venv/bin/python exportar_cyme.py                      # → sin_cyme.json
# en rea_cyme (solo stdlib)
python -m nandu import-tables --tables ../eventos_ens/esquema/sin_cyme.json --output data/sin-geo-20261109.nandu.sqlite
python -m nandu topology data/sin-geo-20261109.nandu.sqlite
python -m nandu ui data/sin-geo-20261109.nandu.sqlite
```

El exportador vive con el esquema, que es dueño de su semántica (extracción del PDF, correcciones,
abiertos). Ñandu no importa su código: solo comprueba la forma del documento (formato, filas
escalares, números finitos, convenciones con `authority` admitida), audita la estructura con
`model.validate` y guarda las tablas sin reinterpretarlas. La procedencia registra el SHA-256 del
JSON, el del PDF, una huella de los 129 archivos de entrada y las convenciones declaradas.

### Traducción

| Esquema | Tablas CYME |
|---|---|
| barras y uniones del dibujo | `CYMNODE` en UTM 21 S (`CoordinateSystem` EPSG:32721, como la base CYME): la ubicación de la estación (`geojson/estaciones.geojson`) y, alrededor, su unifilar del PDF a 10 m por punto. Las del PDF quedan en `ESQ_NODO` |
| interruptor | tramo con Breaker (8); abierto en el dibujo o en `abiertos_manual.yaml` → `ClosedPhase` 0, `NormalStatus` 0 |
| transformador de 2 devanados | tramo con Transformer (5), de la tensión mayor a la menor; `CYMEQTRANSFORMER` con kV y MVA cuando el mímico la da |
| transformador de 3 devanados | `CYMTHREEWINDINGTRANSFORMER` con `DeviceType` nulo; terciario en `CYMSECTIONNODE` |
| línea (o líneas unidas por las correcciones) | OverheadLine (2) entre sus extremos, uno por estación; con derivaciones, estrella desde el punto de derivación. `Length` en m y forma (`CYMINTERMEDIATEPOINT`) sobre el trazado del inventario georreferenciado; sin conductor (`LineId` nulo) |
| alimentador de 23 kV (SCADA) | interruptor desde su barra hasta una cabecera `EST_ALnn` |
| centrales MD, AYO, CHACY y ACY | `CYMSOURCE` en cada barra, a tensión nominal (supuesto de la ENS) |

Las tablas `ESQ_*` guardan lo que CYME no tiene: estación, número, bahía SCADA, `id_pdf`, confianza
y motivo de revisión de cada equipo (`ESQ_EQUIPO`), nombres e inventario georreferenciado de líneas
(`ESQ_LINEA`), estaciones con su ubicación (`ESQ_ESTACION`, de la base maestra), alimentadores y su
red CYME candidata (`ESQ_ALIMENTADOR`: CZU + AL06 → CZU06), nodos con tensión y nombres del grafo
(`ESQ_NODO`), las 182 correcciones aplicadas y los problemas (`ESQ_PROBLEMA`).

**Códigos.** `DeviceType` 5 = Transformer se verificó en `BD_respaldo_lc.mdb` (CYME 9.0 r4), leída con
mdbtools en solo lectura (SHA-256 `0bd0098e…cfe1`, igual antes y después): `CYMTRANSFORMER.DeviceType`
= 5 y su fila en `CYMSECTIONDEVICE`. La base no tiene transformadores de tres devanados, así que su
código y la ubicación del terciario siguen sin verificar. `NormalStatus` 0 = normalmente abierto es una
hipótesis (26 filas de `CYMSWITCH` con `ClosedPhase` 0 y `NormalStatus` 0); el análisis usa `ClosedPhase`.

**Decisiones del exportador** (cada caso queda en `ESQ_PROBLEMA`):
- Se usa el grafo con los abiertos incluidos y marcados, no quitados: así entran los interruptores que
  el mímico agrega aunque estén abiertos (CZU 89).
- En un devanado, el trazo entre los círculos (toca los dos devanados) y los muñones (solo tocan el
  transformador) son del dibujo y se descartan (45 transformadores). Si quedan dos nodos externos, el
  transformador se excluye: no se funden barras.
- Una línea de las correcciones que el dibujo reduce a un punto (BMO 71 y PBO 88 se tocan en N152) se
  separa: los equipos de la otra estación pasan a un extremo propio y la línea va entre los dos (27).
- Alimentadores sin barra en el mímico: si la estación tiene una sola barra de su tensión, va ahí (30);
  si no, sin tramo (67). CAP tiene dos interruptores SCADA por salida (`52_AL01` y `52_AL01G`): uno por
  cabecera.
- Interruptores sin ningún lado reconocido (12) y transformadores con un solo devanado propio (12) quedan
  en `ESQ_EQUIPO` con `Incluido` falso.

### Ubicación y longitudes

- **Estaciones**: con la regla de `visor.py` (MD en Itaipú, adonde llegan sus líneas del inventario; sin el
  número final: CON1 → CON), 120 de 122 en la base maestra; CAIASA y CDM1 en la media de sus vecinas por
  línea (`estacion_ubicacion_estimada`). Contraste con la base CYME: en 68 cabeceras de alimentador, la
  ubicación de la estación queda a 72 m (mediana) del nodo fuente de `BD_respaldo_lc.mdb`; máximo 528 m
  (CYO). La conversión WGS84 → UTM da 7.235.052 m N para (−57°, −25°), como la teórica.
- **Extremos de línea**: uno por estación. Varios interruptores de la misma estación (interruptor y
  medio) llegan al mismo punto, así una línea VAL–VHA con cinco interruptores es un tramo, no una estrella
  de cinco. Una barra enganchada a la línea por una corrección (`barra_kv`, «interruptor del otro extremo
  no identificado», LM26 en COV) queda como extremo propio: fundida, dejaba en bucle a COV 81 y 82.
- **Longitud** (`Length`, metros: hipótesis del perfil `cyme9-si-candidate`): los trazados existentes de
  la misma tensión forman una red (vértices a lo largo de cada trazado; una punta a ≤ 300 m de otro
  trazado lo continúa o deriva). Cada estación se ancla a los trazados que pasan a ≤ 3 km, sumando esa
  distancia. Dos extremos: camino más corto, sin atravesar otras estaciones. Derivación: el punto de la
  red con menor suma de distancias es el centro, y cada brazo mide hasta ahí. Antes que la red se prueba el
  trazado que `lineas.yaml` nombra (`inventario_geo`), uno distinto por circuito (IPU–YZU I y II para L018 y
  L017), y después la red de los nombrados: 83 líneas van por su trazado del inventario y 68 por la red
  (`ESQ_LINEA.MetodoLongitud`). 71 trazados del inventario no son de ninguna línea de la base: 15 en
  construcción, 28 con estaciones fuera del esquema y 28 entre estaciones del esquema que el unifilar no
  une (IPU–MD 1 a 4, COV–PPE, LIM–ELK, SES–IRO, SLO–VAU…). El visor los dibuja; la base no los modela. Se rechaza un camino por
  varios trazados de más del doble de la recta (VAL–YZU, LM01/02: 511 km por MD y VHA, porque el
  inventario no tiene ese trazado); de más de 1,5 veces queda marcado. Una línea a una estación de
  ubicación estimada queda sin longitud (CAIASA cae sobre VTA, su única vecina). Las anclas son puntos
  del segmento, que queda partido en ellas: dos estaciones sobre el mismo segmento se unen por el tramo
  entre ellas, no yendo hasta un vértice y volviendo. 149 de 164 líneas tienen longitud, 7.306 km en
  total; trazado/recta: mediana 1,14.
- **Forma** (`CYMINTERMEDIATEPOINT`): el mismo camino, como puntos intermedios. Convención verificada en
  `BD_respaldo_lc.mdb` (37.762 puntos en 12.907 tramos): `PointIndex` desde 0, de From a To, sin los
  extremos; de los 6.934 tramos con dos puntos o más y sentido claro, ninguno va de To a From.
  3.542 puntos; el largo de la poligonal dibujada es 1,016 veces `Length` (mediana). La interfaz dibuja,
  selecciona, ubica los símbolos y hace zoom por esos puntos; antes unía From y To en recta.

### Resultado (9 nov. 2026)

`sin_cyme.json` → `data/sin-geo-20261109.nandu.sqlite`: 1 red, 2.006 nodos, 2.211 tramos (1.745
interruptores, 186 abiertos; 226 transformadores de 2 devanados y 15 de 3; 225 tramos de línea en 164
líneas, 3.542 puntos intermedios; 762 alimentadores con tramo de 829), 21 fuentes, 299 problemas. Auditoría estructural: 15
errores, los 15 transformadores de tres devanados (`unsupported_device`). La primera versión, con
coordenadas del PDF y un extremo por interruptor, quedó en `data/sin-20261109.nandu.sqlite`.

`topology`: 15 tramos excluidos (los mismos), 1.551 de 2.005 nodos alcanzados desde las centrales por
fase, 162 componentes. **Contraste con la ENS** (`grafo.cargar(solo_cerrados=True, correcciones=True)`,
camino a las barras de las centrales): tratando en memoria los transformadores de tres devanados como
conexión, las 338 barras que la ENS alcanza también se alcanzan en la base, y se suman CAT y PCA 66 kV,
por los interruptores del mímico que la ENS omite al quitar los abiertos. Sin esa prueba, 50 de las barras
que la ENS alcanza quedan sin camino por los tres devanados (PBO, PSA, LAM, LPA, MRA, SMI, REP…).

La interfaz abre la base sobre el mapa (OpenStreetMap o satélite) y muestra Breaker, Transformer y
Source en el explorador; las líneas siguen su trazado y cada tramo muestra su `Length`. **No hay parámetros eléctricos**: ni
impedancias de línea (falta el conductor de cada una), ni de transformador, ni cargas. `loadflow` no
corresponde todavía.

Pendientes:
1. Código Access de `ThreeWindingTransformer` y dónde guarda CYME el nodo terciario: crear uno en
   CYME y leer la base. Hasta entonces, 15 transformadores quedan fuera de la topología.
2. Impedancias: conductor (o R/X/B por km) de cada línea y, de cada transformador, MVA, Z %, X/R,
   grupo de conexión y taps (¿PSS/E de ANDE? ver `proyecto_futuro.md`). Revisar las 14 líneas sin
   longitud y las 6 marcadas.
3. Unir las cabeceras de alimentadores con las redes de distribución de la base CYME.

Verificación: **145 pruebas correctas** con Python 3.11 (en este equipo, Python 3.10: las 2 de
`test_sxst_import` que usan `hashlib.file_digest` fallan por la versión). Cinco son nuevas
(`tests/test_tables_import.py`): importación con procedencia y semántica declarada, destino
existente, transformador que conduce e interruptor abierto que corta, transformador con
`Status` ≠ 0 excluido, y rechazo de formato, valores no escalares o no finitos y convenciones
sin autoridad. La sexta (`test_ui`): puntos intermedios ordenados por `PointIndex`, filtrados por red y
proyectados al mapa.
