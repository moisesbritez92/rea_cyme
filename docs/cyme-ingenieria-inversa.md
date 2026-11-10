# CYME 9.0 r4: hallazgos de ingeniería inversa

Fecha: 19 de octubre de 2026. Instalación: `C:\Program Files\CYME\CYME`.
Cada hallazgo indica su fuente para poder repetirlo.

## Fuentes y cómo leerlas

| Fuente | Cómo se leyó | Qué aporta |
|---|---|---|
| `*.chm` (PowerFlow, StateEstimator, LoadAllocation, Equipment, SteadyState) | `hh.exe -decompile <dir> <archivo>.chm` y conversión HTML→texto | Métodos, parámetros, fórmulas (imágenes PNG) |
| Estudios `.sxst` | Son XML UTF-8 plano; `xml.etree` | Red, catálogos usados, parámetros de cálculo **con enums en texto** |
| `cymData.ini`, `cymEnum.ini`, `cymUnit.ini` | Texto | Nombres de propiedades y enums por tipo de dato |
| `cympy\*.py` | Envoltorios SWIG en texto | Clases y métodos de la API (`sim.LoadFlow`, `sim.DistributionStateEstimator`, `sim.Meter`) |
| `CymMANAloadFlow9064.dll` | `objdump -p`, `strings` (MSYS2) | Exportaciones, dependencias MKL, mensajes internos |
| `CymAPI.dll` | `rea` `inspect_managed_members` | Shim .NET mínimo: 5 tipos, 28 métodos, 5 P/Invoke |
| `runs\*\result.json` del MCP | JSON | Resultados reales de reguladores, cabeceras y reconectadores |

`rea` no tiene configurado ningún proveedor nativo profundo en este equipo: falta
`GHIDRA_INSTALL_DIR` (Ghidra 12.1 requiere Java 21; hay Java 11), y no hay IDA
ni Kuna. Por eso las DLL nativas solo se leyeron por exportaciones y cadenas.
Evidencia `rea` exportada: `data/rea-cyme-20261019-evidence.json`.

## Motor de flujo y estimador: `CymMANAloadFlow9064.dll`

- Exportaciones: `Init`, `SetDevices`, `SetListNodes`, `SetAnalysisParametersData`,
  `Solve`, `ContinueLF`, `GetResultOnNode`, `GetResultOnDevice`, `GetRegulatorFinalTap`,
  `GetTransfoFinalTap`, `GetQualityIndices`, `GetUncertaintiesNode/Device`,
  `GetDSEGenMotResults`, `SetOutputDebug`, entre otras.
- Importa de MKL: `pardiso`, `dss_*` (solver disperso directo), `zgetrf/zgetri`,
  `zgemm` (bloques complejos densos), `dcsr*` (álgebra dispersa).
- Cadenas internas: `buildFixedPointMatrix`, `solveFixedPointSystem`,
  `buildNewtonMatrix`, `solveNewtonSystem`, `Line Search Approach`,
  `Detect Loop of Zero Impedance`, `commonNodeDetection`, `setMasterSlave`,
  `Fictive@Regulator@For@Source`, `Iter(%d) - Regulator(%s): Old Tap:%.1f New Tap:%.1f`,
  `Fix PQ / Unfix PQ`. Archivos de depuración: `MANA_LF_Output_NewtonMatrix.txt`,
  `MANA_LF_Output_FixedPoint.txt`, `MANA_DSE_MultipliersCovariance.txt`.
  Secuencia inferida: inicialización por punto fijo, luego Newton con búsqueda lineal.
- Clases de datos importadas de `CymMANAData9064.dll`: `CMANALoadData`,
  `CMANARegulatorLFData`, `CMANASourceData`, `CMANAOverheadLine{Balanced,Unbalanced,ByPhase}Data`,
  `CMANACableBalancedData`, `CStateEstimatorMANAMeasurementData`,
  `CStateEstimatorMANALagrangeData`, `CStateEstimatorMANAStandardDeviationData`.
- La ayuda (`PowerFlow.chm`, Newton-Raphson unbalanced) cita Kocar et al., IEEE
  Trans. Power Del. 29(2), 2014, y Therrien et al., PVSC 2017. Además describe la matriz
  MANA con `Yn`, `Vadj`, `Dbdep`, `Sadj`, `Sz` e incógnitas `Vn`, `IVs`, `IVd`, `ISW`.

## Parámetros reales del estudio (`.sxst`)

`LoadFlowParametersConfig DEFAULT`: `AnalysisMode=VoltageDropUnbalanced`,
`VoltageTolerance=0.1`, `MaximumIterations=60`, `Flatstart=1`,
`IncludeSourceImpedance=0`, `IncludeLineCharging=1`, `AssumeLineTransposition=1`,
`RegulatorTapOperationMode=Normal`. `GlobalParameters/Frequency=60` (los catálogos
de líneas declaran 50 Hz).

`StateEstimatorParametersConfig DEFAULT`: `KWLoadPrecision=50`,
`KVARLoadPrecision=50`, `KW/KVAR/AMP/PFGlobalMeterPrecision=3`,
`VoltageGlobalMeterPrecision=1`, `SourceVoltageGlobalPrecision=2`,
`InitializationMode=None`, `LoadAllocationMethod=KVAMethod`,
`RegulatorTapOperationMode=Infinite`, `VariableEquivalentSource=0`,
`AggregateLoadsOnSameNode=0`, `LinearLoadScalingMode=None`, `UseLineSearchApproach=0`.

## Estimador (StateEstimator.chm)

WLS con restricciones: min ½ rᵀR⁻¹r, con r − z + h(x) = 0 y c(x) = 0. Se resuelve
iterativamente con el sistema aumentado
`[[R, H, 0], [Hᵀ, 0, Cᵀ], [0, C, 0]] [μ; Δx; −λ] = [z − h; 0; −c]`.
La precisión declarada de cargas y medidores equivale a 3σ. El modelo de
sensibilidad de las cargas se aplica solo en el posproceso. Los índices de calidad
son: costo J (χ²), residuos ponderados, residuos normalizados, multiplicadores de
Lagrange normalizados y desajustes totales por tipo de medida.

## Códigos Access verificados

| Campo | Código → significado | Evidencia |
|---|---|---|
| `CYMSECTIONDEVICE.Location` | 0 Middle, 1 From, 2 To | 580 cargas Access frente a `.sxst` |
| `CYMLOAD.ConnectionConfiguration` | 0 Yg, 2 D | 580 cargas; enum `CymConnectionConfiguration` |
| `CYMCUSTOMERLOAD.LoadValueType` | 0 KW_KVAR | 637 filas frente a `.sxst` |
| `CYMCUSTOMERLOAD.Phase` | 1 A, 2 B, 3 C, 7 ABC (total) | `.sxst` |
| `CYMREGULATOR.ConnectionConfiguration` | 0 Yg, 4 ClosedDelta | typelib + `.sxst` |
| `CYMREGULATOR.SettingOption` | 2 FixedTap (3 Terminal) | typelib `CymSettingOption` |
| `CYMEQREGULATOR` | paso = MaximumBoost / (NumberOfTaps/2) = 0,625 % | catálogo |
| `CYMINTERMEDIATEPOINT` | forma del tramo: `PointIndex` desde 0, de From a To, sin los extremos | 12.907 tramos de `BD_respaldo_lc.mdb`; 6.934 con sentido claro, todos From → To |
| `CYMSECTIONDEVICE.DeviceType` | 5 Transformer | `CYMTRANSFORMER.DeviceType` = 5 y su fila en `CYMSECTIONDEVICE` (red AUX, `BD_respaldo_lc.mdb`) |
| `CYMSECTIONDEVICE.DeviceType` | 1 Underground, 2 OverheadLine, 5 Transformer, 8 Breaker, 10 Recloser, **12 Sectionalizer**, 13 Switch, 14 Fuse, 17 ShuntCapacitor, **18 ShuntReactor**, 20 SpotLoad, **21 DistributedLoad**, 23 OverheadLineUnbalanced, **36 SynchronousGenerator**, **39 ThreeWindingTransformer**, **47 AutoTransformer**, **48 ThreeWindingAutoTransformer**, **49 SVC**, **62 STATCOM** | tutoriales de CYME, casos IEEE importados a `bd_pruebas.mdb` y equipos creados a mano en `bd_nuevo.mdb`; base Access frente a su `.sxst` equipo por equipo (ver abajo) |
| `CYMSECTIONNODE` | nodo terciario del transformador de tres devanados: `SectionId`, `NodeId` = `TertiaryNodeID`, `NodeConnectorIndex`, `BreakPointIndex` = `TertiaryBreakpointIndex` | sección `TRANSFO` del caso IEEE de tres devanados: (TRANSFO, 5, 0, −1) en ambos |
| Tablas de instancia | AutoTransformer (47) en `CYMTRANSFORMER`; ThreeWindingAutoTransformer (48) en `CYMTHREEWINDINGTRANSFORMER` (terciario también en `CYMSECTIONNODE`); SVC (49) en `CYMSVC` + `CYMSVCGENERATIONMODEL` + `CYMUDM` (`DeviceType` 49, `UDMCategory` 13); STATCOM (62) en `CYMSTATCOM` (sin catálogo `CYMEQSTATCOM`) | `bd_nuevo.mdb`, una instancia de cada uno |
| `CYMSYNCHRONOUSGENERATOR.VoltageControlType` | 0 VoltageControlled (PV), 2 Swing | 57 y 2 de 59 generadores (IEEE 14 y 118) |
| `CYMSYNCHRONOUSGENERATOR` / `CYMSYNCHRONOUSGENERATIONMODEL` | `KVSet` en kV LL; `ActiveGeneration` en kW; `PowerFactor` en %; `Min/MaxReactivePower` en kvar; `Status` 0 = Connected; `StabilityModel` 1 = Type1 | 59 generadores con valores idénticos al `.sxst` |
| `CYMSHUNTREACTOR` | `KVARA/B/C` en kvar por fase, `KVLN` en kV fase-neutro (79,674 = 138/√3); `ConnectionConfiguration` 0 = Yg; `Status` 0 = Connected; sin columna `DeviceType` | 2 reactores del IEEE 118 |
| `CYMNODE.UserDefinedBaseVoltage` | kV LL; 0 = sin base propia | IEEE 118: 118 nodos con 138 en Access y en `.sxst` |
| `CYMSWITCH`/`CYMFUSE`/`CYMRECLOSER`/`CYMSECTIONALIZER.NormalStatus` | 0 Open, 1 Closed | `Tutorial_with_cascaded_substations`: los 58 `Open` del `.sxst` son los 58 `NormalStatus` 0 de Access, equipo por equipo |
| `ClosedPhase` (equipos de maniobra) | estado actual, independiente de `NormalStatus`; 0 = None (abierto), 7 = todas las fases **del tramo** | mismo cruce: 16 Switch (NS 0, CP 0), 39 (0, 7), 45 (1, 0); fusibles en tramos monofásicos con CP 7 en Access salen como `A`, `B`… en el `.sxst` |

`BD_respaldo_lc.mdb` (9 nov. 2026, SHA-256 `0bd0098e7e8203dadef2ae899045b82b1e7f5037541f214e7e74066b2623cfe1`)
se leyó en Linux con mdbtools 1.0 en solo lectura (el SHA-256 no cambió). Conteo de `DeviceType` en
`CYMSECTIONDEVICE`: 23 (16.825), 2 (16.421), 20 (16.164), 13 (1.174), 1 (969), 10 (150), 12 (94), 14 (86),
8 (64), 4 (37), 17 (14), 22 (2), 5 (1). El 12 resultó ser Sectionalizer (abajo); el 22 sigue sin identificar. La base no tiene
transformadores de tres devanados: `CYMTHREEWINDINGTRANSFORMER` no tiene columna de nodo terciario y existe
una tabla `CYMSECTIONNODE` (SectionId, NodeId, NodeConnectorIndex, BreakPointIndex), vacía, que podría
guardarlo: se verificó después (ver la tabla).

### Tutoriales de CYME (9 nov. 2026)

`C:\Program Files\CYME\CYME\tutorial\Samples` trae cuatro redes en Access y en `.sxst` (exportadas con
CYME 7.1). Se copiaron al scratchpad y se leyeron con pyodbc en solo lectura; el SHA-256 no cambió
(`Feeder1_2.mdb` `4c01f26c…9bff6c`, `LFwithProfiles.mdb` `178107f6…9def04`). Cruce de conteos por
`DeviceType` frente a las etiquetas del `.sxst`:

| Red | Access `CYMSECTIONDEVICE` | `.sxst` |
|---|---|---|
| `Feeder1_2` | 1 (70), 2 (291), 5 (5), 10 (13), 13 (4), 14 (96), 21 (295) | Underground 70, OverheadLine 291, Transformer 5, Recloser 13, Switch 4, Fuse 96, DistributedLoad 295 |
| `Tutorial` | 1 (88), 2 (1.136), 8 (1), 12 (1), 13 (66), 14 (103), 17 (8), 21 (675) | Underground 88, OverheadLine 1.136, Breaker 1, Sectionalizer 1, Switch 66, Fuse 103, ShuntCapacitor 8, DistributedLoad 675 |
| `Tutorial_with_cascaded_substations` | 1 (152), 2 (2.714), 5 (4), 10 (20), 12 (48), 13 (151), 14 (345), 17 (23), 20 (1.353) | Underground 152, OverheadLine 2.714, Transformer 4, Recloser 20, Sectionalizer 48, Switch 151, Fuse 345, ShuntCapacitor 23, SpotLoad 1.353 |

`CYMLOAD.DeviceType` es 20 o 21 según el tipo de carga, y `CYMCUSTOMERLOAD.DeviceType` repite el de su
carga. El cambiador bajo carga va en `CYMLOADTAPCHANGER` con el `DeviceType` del transformador (5) y su
`DeviceNumber`, más `CYMAUTOTAPCHANGINGEXTST`.

**Tres devanados en `.sxst`** (`tutorial\QATestCase\IEEE_Three_Winding_Transformer_Test_System.sxst`):
el terciario es parte de la sección (`TertiaryNodeID`, `TertiaryNodeConnectorIndex`,
`TertiaryBreakpointIndex`). Ni el `CYMSECTION` de los tutoriales ni el de CYME 9 (`BD_Centro0926`) tienen
esas columnas; en Access va en `CYMSECTIONNODE` (verificado abajo).

### Casos IEEE importados a una base de prueba (9 nov. 2026)

El usuario creó `..\020_CYM_MCP\data\bd_pruebas.mdb` (vacía) e importó con **Database > Import > CYME XML
Self-Contained Files** tres casos de `tutorial\QATestCase`: `IEEE_Three_Winding_Transformer_Test_System`,
`IEEE_14_node_test_feeder` e `IEEE_118_Bus_Test_System` (sin IDs de sección repetidos, pero sí de nodo: ver Cuidados). Se leyó
una copia en solo lectura (SHA-256 `9251aeac…bd8c9d6`). `cympy.db.ImportXML` desde la consola Python hizo
caer CYME dos veces (`0xc0000374`, heap de `ntdll.dll`) sin escribir nada: usar el menú.

El cruce por (sección, `DeviceNumber`) da 2 → OverheadLine (191), 5 → Transformer (13), 17 → ShuntCapacitor (16),
18 → ShuntReactor (2), 20 → SpotLoad (111), 23 → OverheadLineUnbalanced (3), 36 → SynchronousGenerator (59) y
39 → ThreeWindingTransformer (1), sin ningún equipo sin pareja.

`CYMLOADTAPCHANGER` (2 filas, una sola combinación de valores, evidencia débil): `ControlStatus` 1 = `A`,
`TapLocation` 1 = Primary, `InitialTapPositionMode` 1 = TapModeFixed, `ControlType` 0 = VoltagePercent,
`SettingOption` 4 (sin texto en el `.sxst`). `Boost`/`Buck` en %, `UpperBandwidth`/`LowerBandwidth` y `SetPoint`
como en el `.sxst`.

Cuidados:
- El equipo `DEFAULT` del `.sxst` **no se importa**: CYME conserva el suyo (el transformador de tres devanados
  quedó con 1.000 kVA y 69/13,8/13,8 kV en vez de 15 MVA y 69/13,2/2,4 kV). Para cruzar unidades de catálogo
  hace falta un equipo con otro ID.
- Los IDs de nodo son globales en la base, no por red: los nodos 1–6 del caso de tres devanados y 15 del
  IEEE 14 coinciden con nodos del IEEE 118 y quedaron unidos a él (el dibujo de `TEST-FEEDER` se cruza).
  Esos 21 nodos compartidos quedaron con `CYMNODE.NetworkId` nulo y dos filas cada uno en `CYMNODEINTER`
  (42). Los cruces de equipos son por sección y `DeviceNumber` y no se ven afectados. Para importar varios
  casos, revisar antes que no repitan IDs de sección **ni de nodo**.
- El `.sxst` usa enums de texto distintos de Access también en `CTConnection` (3 ↔ `12`, 1 ↔ `1N`).

**Autotransformadores:** el esquema de CYME 9 tiene catálogos `CYMEQAUTOTRANSFORMER` y
`CYMEQTHREEWINDAUTOTRANSFORMER`, pero no tablas de instancia propias: la instancia va en `CYMTRANSFORMER` /
`CYMTHREEWINDINGTRANSFORMER` con `DeviceType` 47 / 48 (verificado abajo). Un lector que filtre esas tablas
solo por nombre confundiría un autotransformador con un transformador común: hay que mirar `DeviceType`.

### Equipos creados a mano (`bd_nuevo.mdb`, 9 nov. 2026)

Base nueva con solo el caso IEEE de tres devanados; el usuario agregó en CYME un autotransformador, uno de
tres devanados, un SVC y un STATCOM (el equipo que hay en SLO) y guardó con Database > Update Network.
Copia en solo lectura, SHA-256 `64bb5738…be54137`.

| `DeviceType` | Sección | Tabla | Datos |
|---|---|---|---|
| 47 | `L3-4` (Location 2 = To) | `CYMTRANSFORMER` | `EquipmentId` DEFAULT, `NormalFeedingNodeId` 3, `Reversible` 1 |
| 48 | `8` (2 → 8, terciario `8-T`) | `CYMTHREEWINDINGTRANSFORMER` + `CYMSECTIONNODE` | taps fijos 100 % |
| 49 | `L3-4` (Location 1 = From) | `CYMSVC`, `CYMSVCGENERATIONMODEL`, `CYMUDM` | `NodeId` 3 (nodo controlado), `DesiredVoltage` 13,8 (kV LL), Q −5.000…5.000 kvar |
| 62 | `9` (6 → `SHUNT~<GUID>`) | `CYMSTATCOM` | `DesiredVoltage` 138 (kV LL), `MaximumShuntCurrent` 2.000, `ShuntTransformerReactance` 1 (unidades sin verificar) |

El STATCOM ocupa una sección propia hacia un nodo generado `SHUNT~<GUID>`: un equipo shunt que no está sobre
una sección existente crea su propio tramo. Un solo ejemplar de cada tipo; los códigos se apoyan en la tabla
donde cayó cada `DeviceNumber` y en lo que el usuario creó. No se crearon catálogos propios (todo `DEFAULT`):
las unidades de `CYMEQTHREEWINDINGTRANSFORMER` siguen sin cruzar (los nombres del `.sxst` sugieren kVA, kV
LL y %).

Copias de `bd_pruebas.mdb` y `bd_nuevo.mdb`, sus SHA-256 y los scripts de cruce: `data/cyme-codigos-20261109/`.

**Barras y conectores** (IEEE 14 y 118, 282 nodos de `bd_pruebas` frente a su `.sxst`):
- `CYMNODE.X/Y` es el primer `Connectors/Point` del nodo.
- `CYMNODECONNECTOR` (NetworkId, NodeId, ConnectorIndex, X, Y) tiene una fila por punto, con el índice
  igual al orden de los puntos, y solo en los nodos con más de un punto (37 de dos, 67 de tres; los 178 de
  un punto no tienen filas).
- Un tramo sale del conector `From/ToNodeConnectorIndex`.
- `CYMBUSDISPLAY` (NetworkId, NodeId, X1, Y1, X2, Y2, Width) es el `BusDisplay` del nodo. `Width` es el
  largo de la barra (111 de 111).
- Los generadores y las cargas del IEEE 118 cuelgan de la barra con una sección propia sin equipo serie
  (`1` → `1-TO`).

**Enums COM:** `CymEquipmentType` de `Cyme64.tlb` no coincide con Access (OverheadLine 64, Transformer 3,
ThreeWindingTransformer 28): no sirve para deducir códigos.

## Regulador en delta cerrado (ajuste a resultados)

Con los resultados `VLN{A,B,C}input`, `VLN{A,B,C}`, `VLL{A,B,C}` y `RegTap{A,B,C}` de 15
corridas reales, se ajustaron dos ángulos libres de entrada por caso:

| Variante | Residuo máximo |
|---|---|
| Tipo A, serie en X, shunt X–Y (A–B, B–C, C–A) | 0,2–2,6 V |
| Tipo A, shunt X–Z | 1,9–31,6 V |
| Tipo B (cualquiera) | 18–180 V |

Script: `scripts/fit_closed_delta_regulators.py`, sobre
`020_CYM_MCP\runs\*\result.json`.

## Load Allocation (LoadAllocation.chm)

Fórmulas por fase k (imágenes `image19_9..16`): TkVA(k) = Σ_s base(s,k)·factor,
kW_asig(s,k) = kW_dem(k)·base(s,k)·factor/TkVA(k) y kvar_asig = kW_asig·√(1/FP(k)² − 1).
La base es Connected kVA (KVAMethod), Actual kVA o kWh. Con varios medidores, el de
aguas abajo asigna a sus cargas y su demanda se resta del de aguas arriba (todavía no
implementado). Las cargas bloqueadas no se modifican y su potencia se resta de la
demanda. Parámetros del estudio: `Method=KVAMethod`, `Tolerance=0,5`,
`RunVoltageDrop=true`, `InitialLossesKW=0,1`.

## Formato .sxst

XML UTF-8 con raíz `<Cyme Version="9.0" Revision="4">`. Contiene:

- `Networks/Network/Topos/Topo`: red, fuentes con `SourceSettings`, `Meter` y
  `EquivalentSource`, y color.
- `Sections/Section`, con `OwnerID` = red y `Devices/<Tipo>`.
- `Nodes/Node/Connectors/Point` (X, Y).
- `Equipments/.../EquipmentDBs`: solo los equipos usados; los campos `*ID` son texto.
- `ConsumerClasses`.
- `Studies/Study/CalculationParameters`: flujo, estimador, `GlobalParameters` con
  `BaseVoltageCustomerSide=120`.

Las cargas van por `CustomerLoadModel` (LoadModelID) con `LoadValue xsi:type`
explícito. Las consignas de reguladores están en `ForwardSettings`/`ReverseSettings`,
equivalentes a `CYMLDCSETTINGS` con Forward 1/0 en Access.

## Contraste en vivo del 19 oct. 2026

- Flujo con tolerancia de 0,001 %: residuo de 11,2 V máx. y 2,8 V mediana (con 0,1 %: 17,4 / 5,0 V).
- Estimador: el historial de modificaciones de CYME («State Estimator») detalla cada carga con
  «Change 'Real Power' from X kW to Y kW» (3 decimales). Las filas ABC guardan un único valor.
  Las cargas bloqueadas no se modifican. Modelo ajustado: medidor A-FP como |I|, P y Q; σ de
  carga = precisión × valor. CZU06 queda con RMS de 0,23 kW / 0,03 kvar; CZU08 con un sesgo
  de -127/+126 sin explicar. Cambiar el modo de taps (Infinite/Normal) o la tolerancia
  (0,1 / 0,001 %) no cambia el resultado de CYME.
- Load Allocation: el historial solo dice «Load allocation on Network X», sin detalle; se usa
  el informe «Load Flow - Loads». Las filas ABC quedan equilibradas con la asignación de menor
  módulo (RMS 0,9 kVA en CZU06). CZU08: CYME no converge en 60 iteraciones.

## Informes de resultados (contraste del 27 oct. 2026)

Corrida `nandu-reports-czu-20261027-01`, CZU06/CZU08 desde la base, tolerancia por defecto.

- **Regulador, corriente:** `IA/IB/IC` de `cyme_regulator_inventory` es la corriente del lado de
  entrada (fuente). Su cociente con la corriente de salida es Vout/Vin, y coincide con Mᵀ·I_salida
  del motor en ≤ 0,4 A. `VLNxinput`/`VLNx` siguen la orientación eléctrica, no el sentido del tramo.
- **Capacitor, «Loading (%)»** (informes Abnormal conditions y Detailed) = Q entregada / Q nominal
  = (V/Vn)² por fase, no V/Vn. En 9 de 9 fases la diferencia con Ñandu es ≤ 0,3 puntos (0,56 kvar).
  La hipótesis de corriente fallaría por 3 a 10 puntos. El informe «Shunt Capacitors and Reactors»
  da en «Total Cap. kvar» el valor nominal.
- **Capacitor, «From Node»** del informe es el nodo From del tramo, no el de conexión
  (BC_100KVAR_3 tiene `Location` = To).
- **Nodo, `Distance`** (km): suma de longitudes desde la fuente. Coincide en ≤ 0,05 m en 1.304
  nodos; los nodos sin energía dan 0.

## Pendiente de contraste con CYME en vivo

Resueltos el 19 oct. 2026 con la corrida `nandu-ref-lf-20261019-01` (ver README):
tensiones nodo a nodo (|ΔV| ≤ 17,4 V), reparto ABC, rama delta, cargas sobre fases
ausentes (0 kVA) y frecuencia (sin reescalar). Pendientes:

1. Residuo de 5 a 17 V: confirmar que se debe a la tolerancia de 0,1 % de VoltageDrop
   repitiendo CYME con una tolerancia menor.
2. Medidas AMP-FP del estimador (|I| + FP, o P/Q derivadas) y cargas estimadas
   (informe «Distribution State Estimator - Load Values»).
3. Control automático de reguladores. Implementado con la hipótesis de tensión de
   control sobre la base de 120 V del estudio (`BaseVoltageCustomerSide`), no V/PT: en
   la corrida del 6 oct. los reguladores Yg Terminal con PT 200 no fueron al tap máximo.
   Modo `Infinite` pendiente.
4. Load Allocation con medidor desbalanceado y cargas ABC (¿CYME las mantiene equilibradas?).
