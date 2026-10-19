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
