# SIN en Ñandu: estado y plan

Plan para caracterizar el Sistema Interconectado Nacional (SIN, 500/220/66/23 kV) en Ñandu.
Escrito el 9 nov. 2026 en la máquina Linux (sin CYME). Está pensado para seguir en la máquina
Windows, que tiene CYME 9.0 r4 y el MCP `cyme` (`..\020_CYM_MCP`). Antes de empezar, leer
`CLAUDE.md` (reglas del proyecto) y la sección «Base del SIN desde el esquema unifilar — 0.14.0»
del `README.md`.

## 1. Qué hay hoy

### Origen de los datos

El esquema del SIN se arma en el repo `eventos_ens`, carpeta `esquema/`. Combina el unifilar
`SIN2026-10V1.pdf`, el SCADA, los mímicos y las correcciones a mano, y queda como un grafo
(`grafo.cargar(correcciones=True)`). `esquema/exportar_cyme.py` lo traduce a tablas CYME
(`sin_cyme.json`, formato `nandu-cyme-tables-v1`) y Ñandu lo importa:

```powershell
# en eventos_ens\esquema (networkx + PyYAML)
python exportar_cyme.py                                   # → sin_cyme.json
# en rea_cyme (stdlib)
py -3.11 -m nandu import-tables --tables ..\eventos_ens\esquema\sin_cyme.json --output data\sin-geo-20261109.nandu.sqlite
py -3.11 -m nandu topology data\sin-geo-20261109.nandu.sqlite
py -3.11 -m nandu ui data\sin-geo-20261109.nandu.sqlite
```

> `sin_cyme.json` y `data/` están en `.gitignore`. Para usarlos en Windows hay dos caminos:
> clonar `eventos_ens` y regenerar el JSON, o copiar a mano `sin_cyme.json` (SHA-256
> `021071a5…2db67`) o `data/sin-geo-20261109.nandu.sqlite`. Los cambios de Ñandu 0.14.0 y el
> exportador **todavía no están commiteados** en Linux.

### Contenido de la base (red única `SIN`)

| Tabla | Filas | Qué es |
|---|---:|---|
| `CYMNODE` | 2.006 | UTM 21 S (EPSG:32721): ubicación de la estación + su unifilar del PDF a 10 m/punto |
| `CYMSECTION` / `CYMSECTIONDEVICE` | 2.211 | todos trifásicos (`Phase` 7) |
| `CYMBREAKER` | 1.745 | 186 abiertos (`ClosedPhase` 0, `NormalStatus` 0); 762 son salidas de alimentador |
| `CYMTRANSFORMER` / `CYMEQTRANSFORMER` | 226 / 24 | dos devanados, `DeviceType` 5; solo kV y MVA cuando el mímico la da |
| `CYMTHREEWINDINGTRANSFORMER` | 15 | `DeviceType` **nulo** (código sin verificar); terciario en `CYMSECTIONNODE` (hipótesis) |
| `CYMOVERHEADLINE` | 225 | `Length` en m sobre el trazado del inventario geo; `LineId` nulo (sin conductor) |
| `CYMINTERMEDIATEPOINT` | 3.542 | forma de las líneas sobre el trazado |
| `CYMSOURCE` | 21 | una por barra de MD, AYO, CHACY y ACY, a tensión nominal (supuesto de la ENS) |
| `ESQ_*` | — | procedencia de cada elemento, estaciones, líneas, alimentadores y 299 problemas |

- **Líneas:** 149 de 164 tienen longitud (unos 7.306 km). 83 van por su propio trazado del
  inventario y 68 por la red de trazados.
- **Topología** (`nandu topology`): 15 tramos excluidos, que son los transformadores de tres
  devanados.
- **Contraste con la ENS:** tratando esos 15 como conexión, la base alcanza las mismas 338
  barras que la ENS, más CAT y PCA 66 kV.

### Códigos ya verificados

Se verificaron en `BD_respaldo_lc.mdb`, la base de ANDE, leída con mdbtools en solo lectura:

- `DeviceType`: 2 OverheadLine, 5 Transformer, 8 Breaker.
- `CYMINTERMEDIATEPOINT`: `PointIndex` desde 0, de From a To, sin los extremos.
- `ClosedPhase` 0 = abierto.

Siguen como hipótesis:
- `NormalStatus` 0 = normalmente abierto.
- `Length` en metros.
- El código y el nodo terciario del transformador de tres devanados.

## 2. Tareas que requieren CYME (hacer primero en Windows)

Seguir las precauciones de `CLAUDE.md`:
- Trabajar sobre una **copia** de la base o del estudio.
- Pedir confirmación antes de cualquier herramienta de escritura.
- No usar `study.Save(...)` ni `save_study=True`, porque desconectan CYME de la base.
- Deshacer siempre con `cyme_undo_modifications`.

La forma de verificar un código es la de siempre: crear el equipo en CYME, guardarlo en una base
Access de prueba o exportar un `.sxst` (que trae los enums en texto) y cruzar con las tablas
Access.

1. **Transformador de tres devanados:**
   - `DeviceType` en `CYMSECTIONDEVICE` y en `CYMTHREEWINDINGTRANSFORMER`;
   - dónde va el nodo terciario: ¿`CYMSECTIONNODE`? ¿`CYMNODECONNECTOR`? ¿un `ConnectorIndex`?;
   - unidades de `PrimaryRatedCapacity` y de las Z.

   Con eso se completan `exportar_cyme.py` (hoy deja `DeviceType` nulo) y `topology.DEVICES`.
2. **Autotransformador** (`CYMEQAUTOTRANSFORMER`, `CYMEQTHREEWINDAUTOTRANSFORMER`): su
   `DeviceType`. Hoy VAL AT01 y AT02 (500/220) son transformadores comunes.
3. **Reactor shunt, SVC, generador síncrono y cambiador bajo carga**
   (`CYMSHUNTREACTOR`, `CYMSVC`, `CYMSYNCHRONOUSGENERATOR`, `CYMLOADTAPCHANGER`): su `DeviceType`
   y las columnas que usa CYME. En `BD_respaldo_lc.mdb` aparecen los códigos 12 y 22 sin
   identificar.
4. **`CYMNODE.UserDefinedBaseVoltage` y `RatedVoltage`:** unidad (¿kV LL?) y si CYME los usa
   como base de tensión del nodo para los informes en %.
5. **`NormalStatus`:** confirmar que 0 = normalmente abierto con un `.sxst` que tenga un
   interruptor `NormalStatus=Open`.
6. **Frecuencia:** el SIN es de 50 Hz. Ver dónde se fija en CYME (`GlobalParameters/Frequency`
   del estudio es 60 y los catálogos de línea declaran 50) y cómo afecta la susceptancia.
7. **Opcional:** si CYME puede abrir un estudio de transmisión con generadores PV, preparar un
   caso chico (IEEE 9 o 14) en CYME, correr el flujo con el MCP y guardar el resultado. Sería la
   referencia para validar el motor de Ñandu con transformadores y barras PV.

Cada hallazgo va a la tabla de códigos de `docs/cyme-ingenieria-inversa.md`, con su evidencia,
y la convención a `CONVENTIONS` con su `authority`.

## 3. Qué necesita Ñandu para caracterizar el SIN

### 3.1 Barras

- **Tensión nominal por nodo.** Ya existe en `ESQ_NODO.KV`. Pasarla a `CYMNODE` cuando se sepa
  cómo la usa CYME (tarea 2.4). Sin ella no hay tensiones en %, ni se detecta un transformador
  conectado a la barra equivocada.
- **Barra frente a unión:** saber qué nodo es barra, a qué estación pertenece y cuáles son sus
  secciones y acoples. CYME tiene `CYMBUSDISPLAY`, `CYMZONE` y `CYMNETWORKAREA` para agruparlos.
- **Procesador topológico** (nodo-interruptor → barra-rama). Con el estado de los interruptores,
  reduce los 2.006 nodos a unas pocas centenas de barras eléctricas antes de calcular. Es lo
  normal en transmisión y mejora el condicionamiento: cada interruptor cerrado es hoy una
  ecuación más (`ISW` en el nodal aumentado). Propuesta: un módulo `nandu/bus_branch.py` (stdlib)
  que devuelva el mapa nodo → barra y se pueda deshacer para publicar los resultados por nodo.

### 3.2 Transformadores (lo más urgente)

- **Dos devanados:** MVA, kV, Z % y X/R (más Z0), grupo de conexión (YNd1, Dyn1…, con su desfase
  de 30°) y taps (rango, paso y posición). El nodal ya trata reguladores en delta cerrado; el
  transformador se monta sobre esa base. Validarlo contra pandapower y OpenDSS
  (`nodal.from_native` frente a `powerflow`).
- **Cambiador bajo carga** (`CYMLOADTAPCHANGER`): barra controlada, tensión de consigna y banda.
- **Tres devanados** (los 15 excluidos: PBO, PSA, LAM, LPA, BRO, ALT, BMO, VAU, PCA, ELA,
  VMI2…) y **autotransformadores**, cuando estén las tareas 2.1 y 2.2. Hoy, por los de tres
  devanados, 50 barras quedan sin camino a una central.

### 3.3 Generación

Hoy solo hay fuentes de tensión fija (Thévenin), que sirven para alimentadores. Para el SIN hace
falta:
- **Generadores con barra PV:** P y |V| fijadas, con límites de Q. Son Itaipú (MD), Yacyretá
  (AYO) y Acaray (ACY/CHACY).
- **Una barra slack.**

### 3.4 Líneas

- **Impedancias:** conductor y estructura, o directamente R1, X1, B1, R0, X0 y B0 por km. Las
  longitudes ya están.
- **50 Hz** (tarea 2.6). La carga capacitiva pesa mucho en 500 kV.
- **π exacto** en líneas largas (AYO–VAL, unos 234 km); Ñandu usa π concentrado.
- **Dobles circuitos:** acoplamiento mutuo (`CYMDOUBLECIRCUIT`) para la secuencia cero
  (cortocircuito).

### 3.5 Compensación reactiva

Aparece en las bahías del SCADA: reactores de línea (VHA REA1 y REA2, AYO REA2), bancos de
capacitores (PJC BC01/BC02) y el CER de GUA. Ñandu solo modela capacitores; faltan reactores y
SVC.

### 3.6 Cargas y mediciones

- **Demanda** por alimentador o por transformador. Sale del SCADA, o de enlazar cada cabecera
  con su red de distribución (`ESQ_ALIMENTADOR.RedCymeCandidata`, por ejemplo CZU + AL06 →
  `CZU06`).
- **Mediciones del SCADA** (MW, MVAr y kV en líneas y transformadores) para un estimador de
  estado de transmisión. Hoy el estimador solo usa corriente y FP en la cabecera
  (`CYMNETWORKMETER`). CYME tiene `CYMMETER`, `CYMINSTWATTMETER`, `CYMINSTVARMETER` y
  `CYMINSTVOLTAGEMETER`. El mapa de medidas a equipos está en
  `eventos_ens/esquema/geojson/sin_topologia_medidas.json`.

### 3.7 Datos a pedir a ANDE

Si existe un caso PSS/E (`.raw`) o equivalente del SIN, cubre de una vez transformadores,
líneas, generación y compensación (ver `proyecto_futuro.md`). Si no, hacen falta:
- protocolos o placas de los transformadores (MVA, Ucc %, pérdidas, grupo, taps);
- el conductor y la estructura de cada línea;
- los datos de los generadores (MVA, Xd, Xd'', curva de capacidad);
- la potencia de los reactores, los bancos y el CER.

## 4. Orden de trabajo

| # | Paso | Depende de | Validación |
|---|---|---|---|
| 0 | Tareas CYME 2.1–2.6 | máquina Windows + MCP | tablas Access / `.sxst` |
| 1 | Tensión nominal por barra + procesador topológico | 2.4 (solo para escribir `CYMNODE`) | `topology` igual antes y después; barras = componentes por interruptores cerrados |
| 2 | Transformador de dos devanados (Z, grupo, taps) en `cyme_model` + `nodal` | — | pandapower / OpenDSS / caso analítico |
| 3 | Generadores PV + slack | — | IEEE 9/14 con solución publicada y pandapower |
| 4 | Tres devanados y autotransformadores | 2.1, 2.2 | pandapower |
| 5 | Reactores shunt y SVC | 2.3 | balance de Q |
| 6 | Impedancias de línea (π exacto, 50 Hz) | datos de ANDE | OpenDSS |
| 7 | Cargas por alimentador / transformador | SCADA | balance P/Q |
| 8 | Mediciones del SCADA y estimador de transmisión | 7 | χ², residuos normalizados |

Los pasos 1 a 3 se pueden hacer sin datos de ANDE, con casos de prueba. Cada uno sube la versión
y documenta en el README el número de pruebas y la referencia independiente (regla de
`CLAUDE.md`: «converge» no es «igual a CYME»).

## 5. Pendientes del esquema (en `eventos_ens`, no en Ñandu)

En `ESQ_PROBLEMA` de la base. Los más importantes:

- **28 trazados del inventario entre estaciones del esquema que el unifilar no une:** IPU–MD 1 a
  4, COV–PPE, LIM–ELK, SES–IRO, SES–BSP–SRO, SLO–VAU, VAU–BPA, ACY–K15, PPE–CZA, CBO–SPP,
  FIL–MES, GDI–CEN, VTA–CAIASA, IRY–VAQ–CYO, PBO–BMO, segundos circuitos (ACY–PFO I, VHA–PBO I,
  AYO–CBO II, K30–YZU I/II, YZU–COV I)… Confirmar con operación y agregarlos a
  `correcciones_mimico.yaml`.
- **12 líneas sin longitud** (extremos sin estación o en CAIASA) y **8 mucho más largas que la
  recta** (PBO–VAU, LAM–REP, CBO–CAM, PIR–PAR…).
- **VAL–YZU 500 kV (LM01/02):** el inventario no tiene su trazado.
- **Ubicación de CAIASA y CDM1:** no están en `estaciones.geojson`.
- **67 alimentadores sin barra** y **30 con barra supuesta**.
- **CAP:** dos interruptores SCADA por salida (`52_AL01` y `52_AL01G`).
- **24 transformadores** con nodos del dibujo descartados o con un solo devanado propio.
