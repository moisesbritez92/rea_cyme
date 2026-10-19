# Proyecto futuro: transmisión y generación en Ñandu

Propuesta (19 oct. 2026) para agregar a Ñandu una capa de transmisión y generación
con vista unifilar, acoplada a la capa de distribución existente. Nada de esto está
implementado.

## Punto de partida

`BD_Centro0926.mdb` (consultada en solo lectura) no contiene transmisión ni generación:

- Las 75 redes son alimentadores de distribución (`NetworkType` = 0 en todas).
- No hay generadores, barras de transmisión ni subestaciones modeladas.
- `CYMTRANSFORMER` tiene solo 2 transformadores de distribución de 100 kVA (23/0,38 kV,
  red `AUX`).
- Aguas arriba de cada alimentador solo hay un equivalente de Thévenin
  (`CYMEQUIVALENTSOURCE`, `CYMEQSOURCE`): 11 subestaciones (ABA, CDO, COV, CYO, CZA,
  CZU, NAR, PPE, SES, VAQ, VIL) a 23 kV, con R/X de secuencias 1, 2 y 0 en dos
  "niveles". Qué representa cada nivel es una **hipótesis** no verificada.

Los datos de transmisión deben venir de fuera de CYME. El formato preferido es
PSS/E `.raw` (habitual en empresas de transmisión; falta confirmar qué usa ANDE).

## Herramienta de validación: pandapower

Python puro, licencia BSD, instalable con pip, red en JSON, barras PV, límites de
reactiva, transformadores de 2 y 3 devanados, cortocircuito IEC 60909 y OPF. Usa
numpy/scipy como Ñandu. Su unifilar es básico: lo dibuja Ñandu. GridCal queda como
referencia visual opcional (trae editor de unifilares e importa `.raw`).

## Integración

### 1. Importación

`nandu/psse_import.py` (solo stdlib): lee el `.raw` (barras, líneas, transformadores,
generadores, cargas) y lo guarda en el mismo `.nandu.sqlite`, en tablas `tx_buses`,
`tx_branches`, `tx_transformers` y `tx_generators`. Las filas originales se conservan
en `source_records`.

```powershell
py -3.11 -m nandu import-psse --raw ande.raw --output data\nuevo.nandu.sqlite
```

### 2. Modelo

`nandu/tx_model.py` (stdlib): modelo de secuencia positiva con barras PQ, PV y slack,
límites de reactiva y taps. Cada convención se registra en `CONVENTIONS` con su
`authority` y su evidencia.

### 3. Cálculo

- **Validador:** `nandu/pf_pandapower.py`, el equivalente de `powerflow.py` (OpenDSS)
  para transmisión. pandapower se importa solo dentro del comando.
- **Motor propio:** barras PV y control de reactiva en `nodal.py`, contrastados con
  pandapower y con casos IEEE de solución publicada.

### 4. Acople con distribución

En cada una de las 11 subestaciones se vincula la barra de transmisión que alimenta
al transformador de potencia con la fuente de cada alimentador. La tabla de vínculos
es explícita; los vínculos ambiguos se excluyen y se reportan en `issues`.

- **Desacoplado:** se resuelve la transmisión; su tensión e impedancia de Thévenin
  reemplazan a `CYMEQUIVALENTSOURCE`. Se comparan los equivalentes calculados con los
  de CYME.
- **Iterativo:** las demandas de cabecera del flujo de distribución vuelven como
  cargas en transmisión hasta que ambas capas convergen.

### 5. Interfaz

Vista **Schematic** en `nandu/ui/static`: unifilar de transmisión con disposición
automática. Doble clic en una subestación abre sus alimentadores en la vista
geográfica existente. `service.py` agrega endpoints de geometría esquemática y de
resultados por barra.

### 6. CLI, informes y pruebas

- Comandos `nandu txflow` (flujo de transmisión) y `nandu cosim` (capas acopladas).
- Informes inmutables `data/<red>-txflow-v<versión>.json`.
- Pruebas `unittest` con IEEE 9 o 14 barras frente a la solución publicada y
  frente a pandapower.

## Orden sugerido

1. pandapower en `.venv` y caso IEEE 14 con solución publicada.
2. Importador PSS/E `.raw`.
3. Vista esquemática en la interfaz.
4. Barras PV en el motor nodal propio, contrastadas con pandapower.
5. Acople con distribución (primero desacoplado, después iterativo).

## Preguntas abiertas

- ¿En qué formato están los datos de transmisión de ANDE y quién los provee?
- ¿El unifilar es solo de visualización o también debe calcular flujo?
- Significado exacto de los niveles 1 y 2 de `CYMEQUIVALENTSOURCE` (verificar con la
  ayuda `.chm` o con CYME).
