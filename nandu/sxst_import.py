"""Importación de estudios CYME autónomos (.sxst, XML) al formato Ñandu.

El XML de CYME guarda los enums como texto. Se traducen a los códigos Access ya
verificados (docs/cyme-ingenieria-inversa.md) para que el resto de Ñandu use las
mismas tablas que con una base Access. Los valores de carga tipados (LoadValueKW_KVAR,
LoadValueKVA_PF, ...) se convierten a kW/kvar porque su tipo es explícito en el XML.
"""
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from . import __version__
from .model import build_model, validate
from .storage import save

PHASE = {'None': 0, 'A': 1, 'B': 2, 'C': 3, 'AB': 4, 'AC': 5, 'BC': 6, 'ABC': 7}
LOCATION = {'Middle': 0, 'From': 1, 'To': 2}
CONNECTION = {'Yg': 0, 'Y': 1, 'D': 2, 'OpenDelta': 3, 'ClosedDelta': 4}
SETTING = {'LoadCenter': 0, 'RXSettings': 1, 'FixedTap': 2, 'Terminal': 3, 'FixedTapPrimary': 4, 'PythonScript': 5}
STATUS = {'Connected': 0, 'Disconnected': 1}
DEVICE_CODE = {'Underground': 1, 'OverheadLine': 2, 'Regulator': 4, 'Breaker': 8, 'Recloser': 10, 'Switch': 13,
               'Fuse': 14, 'ShuntCapacitor': 17, 'SpotLoad': 20, 'OverheadLineUnbalanced': 23}
SWITCH_TABLE = {'Breaker': 'CYMBREAKER', 'Recloser': 'CYMRECLOSER', 'Switch': 'CYMSWITCH', 'Fuse': 'CYMFUSE'}
SEQUENCE_FIELDS = {'PositiveSequenceResistance': 'PositiveSequenceResistance',
                   'PositiveSequenceReactance': 'PositiveSequenceReactance',
                   'ZeroSequenceResistance': 'ZeroSequenceResistance', 'ZeroSequenceReactance': 'ZeroSequenceReactance',
                   'PositiveSequenceShuntSusceptance': 'PosSeqShuntSusceptance',
                   'PositiveSequenceShuntConductance': 'PosSeqShuntConductance',
                   'ZeroSequenceShuntSusceptance': 'ZeroSequenceShuntSusceptance',
                   'ZeroSequenceShuntConductance': 'ZeroSequenceShuntConductance',
                   'Frequency': 'Frequency', 'NominalRating': 'NominalRating', 'FirstRating': 'FirstRating'}


def _text(el, tag, default=None):
    found = el.find(tag)
    return default if found is None or found.text is None else found.text.strip()


def _num(el, tag, default=None):
    value = _text(el, tag)
    if value in (None, ''):
        return default
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'{tag}: valor no finito')
    return number


def _int(value):
    return int(float(value)) if value not in (None, '') else None


def load_value(el):
    """kW, kvar desde un <LoadValue xsi:type=...> de CYME. FP en %, negativo = adelanto."""
    kind = el.get('Type') or ''
    if kind == 'LoadValueKW_KVAR':
        return _num(el, 'KW', 0.0), _num(el, 'KVAR', 0.0)
    if kind in ('LoadValueKVA_PF', 'LoadValueKW_PF'):
        pf = _num(el, 'PF', 100.0) / 100
        if not 0 < abs(pf) <= 1:
            raise ValueError(f'FP no admitido: {pf}')
        kw = _num(el, 'KVA', 0.0) * abs(pf) if kind == 'LoadValueKVA_PF' else _num(el, 'KW', 0.0)
        return kw, kw * math.sqrt(1 / pf ** 2 - 1) * (1 if pf > 0 else -1)
    raise ValueError(f'tipo de carga no admitido: {kind}')


def _catalogs(root):
    out = {t: [] for t in ('CYMEQOVERHEADLINE', 'CYMEQCABLE', 'CYMEQOVERHEADLINEUNBALANCED', 'CYMEQREGULATOR',
                           'CYMEQSHUNTCAPACITOR', 'CYMEQSOURCE', 'CYMEQSWITCH', 'CYMEQRECLOSER', 'CYMEQBREAKER',
                           'CYMEQFUSE')}
    dbs = root.find('Equipments/Equipments/EquipmentDBs')
    if dbs is None:
        return out
    simple = lambda db: {c.tag: (c.text or '').strip() for c in db if len(c) == 0}
    # Los identificadores (EquipmentID, PhaseConductorID, ...) son texto aunque parezcan números.
    numeric = lambda row: {k: (float(v) if _is_number(v) and not k.endswith('ID') else v) for k, v in row.items()}
    for db in dbs:
        raw = numeric(simple(db))
        ident = raw.get('EquipmentID')
        if db.tag in ('OverheadLineDB', 'CableDB'):
            row = {'EquipmentId': ident, **{dst: raw.get(src, 0.0) for src, dst in SEQUENCE_FIELDS.items()},
                   'UserDefinedImpedances': raw.get('UserDefinedImpedances'), 'LockImpedance': raw.get('LockImpedance')}
            out['CYMEQOVERHEADLINE' if db.tag == 'OverheadLineDB' else 'CYMEQCABLE'].append(row)
        elif db.tag == 'OverheadLineUnbalancedDB':
            out['CYMEQOVERHEADLINEUNBALANCED'].append({**{k: v for k, v in raw.items() if k != 'EquipmentID'},
                                                        'EquipmentId': ident})
        elif db.tag == 'RegulatorDB':
            out['CYMEQREGULATOR'].append({**{k: v for k, v in raw.items() if k != 'EquipmentID'}, 'EquipmentId': ident,
                                          'NumberOfTaps': _int(raw.get('NumberOfTaps'))})
        elif db.tag == 'ShuntCapacitorDB':
            out['CYMEQSHUNTCAPACITOR'].append({**{k: v for k, v in raw.items() if k != 'EquipmentID'}, 'EquipmentId': ident})
        elif db.tag == 'SubstationDB':
            out['CYMEQSOURCE'].append({**{k: v for k, v in raw.items() if k != 'EquipmentID'}, 'EquipmentId': ident})
        elif db.tag in ('SwitchDB', 'RecloserDB', 'BreakerDB', 'FuseDB'):
            out['CYMEQ' + db.tag[:-2].upper()].append({**{k: v for k, v in raw.items() if k != 'EquipmentID'},
                                                        'EquipmentId': ident})
    return out


def _is_number(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def read(path, network_ids=None, load_model_id='1'):
    """Lee un .sxst y devuelve (tablas tipo Access, informe de importación)."""
    path = Path(path).resolve(strict=True)
    root = ET.parse(path).getroot()
    if root.tag != 'Cyme':
        raise ValueError('No es un estudio CYME (.sxst)')
    wanted = set(network_ids or [])
    t = {name: [] for name in ('CYMNETWORK', 'CYMNODE', 'CYMSECTION', 'CYMSECTIONDEVICE', 'CYMSOURCE',
                               'CYMEQUIVALENTSOURCE', 'CYMHEADNODE', 'CYMNETWORKMETER', 'CYMREGULATOR', 'CYMLOAD',
                               'CYMCUSTOMERLOAD', 'CYMCONSUMERCLASS', 'CYMSHUNTCAPACITOR', 'CYMOVERHEADLINE',
                               'CYMOVERHEADLINEUNBALANCED', 'CYMUNDERGROUNDLINE', 'CYMDBPARAMETERS', 'CYMLDCSETTINGS',
                               *SWITCH_TABLE.values())}
    t.update(_catalogs(root))
    unsupported, issues = {}, []
    topos = {}
    for topo in root.iter('Topo'):
        net = _text(topo, 'NetworkID')
        topos[net] = topo
    sections = [s for s in root.iter('Section') if not wanted or _text(s, 'OwnerID') in wanted]
    nets = sorted({_text(s, 'OwnerID') for s in sections})
    for net in nets:
        topo = topos.get(net)
        color = _int(_text(topo, 'Color')) if topo is not None else None
        t['CYMNETWORK'].append({'NetworkId': net, 'Color': color,
                                'NetworkType': _text(topo, 'NetworkType') if topo is not None else None})
        if topo is None:
            issues.append({'code': 'network_without_topo', 'network_id': net})
            continue
        for src in topo.iter('Source'):
            node = _text(src, 'SourceNodeID')
            settings = src.find('SourceSettings')
            if settings is None:
                continue
            t['CYMSOURCE'].append({'NetworkId': net, 'NodeId': node, 'DeviceNumber': _text(settings, 'DeviceNumber'),
                                   'EquipmentId': _text(settings, 'SourceID'), 'ConnectorIndex': 0,
                                   **{f'OperatingVoltage{p}': _num(settings, f'OperatingVoltage{p}') for p in 'ABC'}})
            t['CYMHEADNODE'].append({'NetworkId': net, 'NodeId': node})
            eq = src.find('EquivalentSourceModels/EquivalentSourceModel/EquivalentSource')
            if eq is not None:
                t['CYMEQUIVALENTSOURCE'].append({'NodeId': node, **{c.tag: float(c.text) for c in eq
                                                                    if len(c) == 0 and _is_number(c.text)}})
            meter = src.find('Meter/MeterModels/MeterModel')
            if meter is not None:
                row = {'NetworkId': net, 'NodeId': node, 'IsTotalDemand': 0, 'MeterLocation': 1,
                       **{f'Val{i}{p}': 0.0 for i in (1, 2) for p in 'ABC'}}
                kinds = set()
                for p in 'ABC':
                    lv = meter.find(f'DemandValue{p}/LoadValue')
                    if lv is None:
                        continue
                    kinds.add(lv.get('Type'))
                    if lv.get('Type') == 'LoadValueAMP_PF':
                        row[f'Val1{p}'], row[f'Val2{p}'] = _num(lv, 'AMP', 0.0), _num(lv, 'PF', 0.0)
                    else:
                        kw, kvar = load_value(lv)
                        row[f'Val1{p}'], row[f'Val2{p}'] = kw, kvar
                row['DemandType'] = 3 if kinds == {'LoadValueAMP_PF'} else None
                row['DemandTypeText'] = ','.join(sorted(k for k in kinds if k))
                row.update({'Val1Total': 0.0, 'Val2Total': 0.0})
                if any(row.get(f'Val1{p}') for p in 'ABC'):
                    t['CYMNETWORKMETER'].append(row)
    owner_of_node = {}
    for sec in sections:
        net = _text(sec, 'OwnerID')
        sid = _text(sec, 'SectionID')
        row = {'NetworkId': net, 'SectionId': sid, 'FromNodeId': _text(sec, 'FromNodeID'),
               'ToNodeId': _text(sec, 'ToNodeID'), 'Phase': PHASE[_text(sec, 'Phase')],
               'FromNodeConnectorIndex': _int(_text(sec, 'FromNodeConnectorIndex', '0')),
               'ToNodeConnectorIndex': _int(_text(sec, 'ToNodeConnectorIndex', '0'))}
        t['CYMSECTION'].append(row)
        owner_of_node.setdefault(row['FromNodeId'], net)
        owner_of_node.setdefault(row['ToNodeId'], net)
        devices = sec.find('Devices')
        for dev in (devices if devices is not None else []):
            code = DEVICE_CODE.get(dev.tag)
            number = _text(dev, 'DeviceNumber')
            if code is None:
                unsupported[dev.tag] = unsupported.get(dev.tag, 0) + 1
                continue
            t['CYMSECTIONDEVICE'].append({'NetworkId': net, 'SectionId': sid, 'DeviceNumber': number, 'DeviceType': code,
                                          'Location': LOCATION.get(_text(dev, 'Location'), 0)})
            status = STATUS.get(_text(dev, 'ConnectionStatus', 'Connected'), 1)
            base = {'NetworkId': net, 'DeviceNumber': number, 'Status': status}
            if dev.tag in ('OverheadLine', 'OverheadLineUnbalanced'):
                t['CYMOVERHEADLINE' if dev.tag == 'OverheadLine' else 'CYMOVERHEADLINEUNBALANCED'].append(
                    {**base, 'LineId': _text(dev, 'LineID'), 'Length': _num(dev, 'Length')})
            elif dev.tag == 'Underground':
                t['CYMUNDERGROUNDLINE'].append({**base, 'CableId': _text(dev, 'CableID'), 'Length': _num(dev, 'Length'),
                                                'NumberOfCableInParallel': _int(_text(dev, 'NumberOfCableInParallel', '1'))})
            elif dev.tag in SWITCH_TABLE:
                t[SWITCH_TABLE[dev.tag]].append({**base, 'EquipmentId': _text(dev, 'DeviceID'),
                                                 'ClosedPhase': PHASE[_text(dev, 'ClosedPhase', 'ABC')],
                                                 'NormalStatus': 1 if _text(dev, 'NormalStatus') == 'Closed' else 0,
                                                 'NormalFeedingNodeId': _text(dev, 'NormalFeedingNodeID')})
            elif dev.tag == 'Regulator':
                row = {**base, 'EquipmentId': _text(dev, 'DeviceID'),
                       'ConnectionConfiguration': CONNECTION[_text(dev, 'ConnectionConfiguration')],
                       'SettingOption': SETTING[_text(dev, 'SettingOption')],
                       'NormalFeedingNodeId': _text(dev, 'NormalFeedingNodeID'),
                       'LoadCenterId': _text(dev, 'LoadCenterID'),
                       **{f'TapPosition{p}': _num(dev, f'TapPosition{p}', 0.0) for p in 'ABC'},
                       **{k: _num(dev, k) for k in ('PTRatio', 'CTPrimaryRating', 'BoostPercent', 'BuckPercent')}}
                for forward, tag in ((1, 'ForwardSettings'), (0, 'ReverseSettings')):
                    settings = dev.find(tag)
                    if settings is not None:
                        t['CYMLDCSETTINGS'].append({'NetworkId': net, 'DeviceNumber': number, 'DeviceType': 4,
                                                    'Forward': forward, **{c.tag: float(c.text) for c in settings
                                                                           if _is_number(c.text)}})
                t['CYMREGULATOR'].append(row)
            elif dev.tag == 'ShuntCapacitor':
                t['CYMSHUNTCAPACITOR'].append({
                    **base, 'EquipmentId': _text(dev, 'DeviceID'),
                    'ConnectionConfiguration': CONNECTION[_text(dev, 'ConnectionConfiguration')],
                    'KVLN': _num(dev, 'KVLN'), 'CapacitorControlType': 0,
                    **{f'KVAR{p}': _num(dev, f'FixedKVAR{p}', 0.0) for p in 'ABC'},
                    **{f'SwitchedKVAR{p}': _num(dev, f'SwitchedKVAR{p}', 0.0) for p in 'ABC'}})
            elif dev.tag == 'SpotLoad':
                t['CYMLOAD'].append({'NetworkId': net, 'DeviceNumber': number, 'DeviceType': 20,
                                     'ConnectionConfiguration': CONNECTION[_text(dev, 'ConnectionConfiguration', 'Yg')]})
                for cl in dev.iter('CustomerLoad'):
                    cstatus = STATUS.get(_text(cl, 'ConnectionStatus', 'Connected'), 1)
                    models = [m for m in cl.iter('CustomerLoadModel') if _text(m, 'LoadModelID') == load_model_id]
                    for model in models:
                        locked = 1 if _text(model, 'LockDuringLoadAllocation') == 'Locked' else 0
                        for value in model.iter('CustomerLoadValue'):
                            try:
                                kw, kvar = load_value(value.find('LoadValue'))
                            except (ValueError, AttributeError) as exc:
                                issues.append({'code': 'load_value_skipped', 'device_id': number, 'reason': str(exc)})
                                continue
                            t['CYMCUSTOMERLOAD'].append({
                                'NetworkId': net, 'DeviceNumber': number, 'DeviceType': 20,
                                'CustomerNumber': _text(cl, 'CustomerNumber'), 'ConsumerClassId': _text(cl, 'CustomerType'),
                                'Status': cstatus, 'Phase': PHASE[_text(value, 'Phase')], 'LoadValueType': 0,
                                'LoadValue1': kw, 'LoadValue2': kvar, 'ConnectedKVA': _num(value, 'ConnectedKVA', 0.0),
                                'KWHUsage': _num(value, 'KWH', 0.0), 'LoadModelId': _int(load_model_id),
                                'LockDuringLoadAllocation': locked,
                                'NumberOfCustomer': _num(value, 'NumberOfCustomer', 0.0)})
    used = set(owner_of_node)
    for node in root.iter('Node'):
        ident = _text(node, 'NodeID')
        if ident not in used:
            continue
        point = node.find('Connectors/Point')
        t['CYMNODE'].append({'NetworkId': owner_of_node[ident], 'NodeId': ident,
                             'X': _num(point, 'X') if point is not None else None,
                             'Y': _num(point, 'Y') if point is not None else None})
    for cc in root.iter('ConsumerClass'):
        t['CYMCONSUMERCLASS'].append({
            'ConsumerClassId': _text(cc, 'Name'),
            **{k: _num(cc, k, 0.0) for k in ('ConstantPower', 'ConstantCurrent', 'ConstantImpedance', 'PowerFactor')},
            'UtilizationFactor': _num(cc, 'UtilizationFactor', 100.0),
            'IsExponentialModel': _int(_text(cc, 'IsExponentialModel', '0')),
            'LoadFlowVoltPercentOfNominal': _num(cc, 'LoadFlowVoltagePercentOfNominal', 0.0)})
    calc = root.find('Studies/Study/CalculationParameters')
    parameters = {}
    if calc is not None:
        lf = calc.find('LoadFlowParameters/ParametersConfigurations/LoadFlowParametersConfig')
        se = calc.find('StateEstimatorParameters/ParametersConfigurations/StateEstimatorParametersConfig')
        for name, el in (('load_flow', lf), ('state_estimator', se)):
            if el is not None:
                parameters[name] = {c.tag: c.text for c in el if len(c) == 0}
    report = {'cyme_version': f'{_text(root, "Version")} r{_text(root, "Revision")}', 'networks': nets,
              'unsupported_devices': unsupported, 'issues': issues, 'calculation_parameters': parameters}
    return {k: v for k, v in t.items() if v}, report


def import_study(path, output, network_ids=None):
    path = Path(path).resolve(strict=True)
    with path.open('rb') as stream:
        sha_before = hashlib.file_digest(stream, 'sha256').hexdigest()
    started = datetime.now(timezone.utc).isoformat()
    tables, report = read(path, network_ids)
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != sha_before:
            raise RuntimeError('El estudio cambió durante la lectura')
    schemas = {}
    for name, rows in tables.items():
        cols = sorted({k for r in rows for k in r})
        schemas[name] = {'columns': [{'name': c, 'type': 'VARIANT'} for c in cols], 'scope': 'sxst_study'}
    model = build_model(tables)
    validation = validate(model)
    provenance = {'importer_version': __version__, 'source_format': 'cyme-sxst', 'source_path': str(path),
                  'source_sha256': sha_before, 'source_hash_unchanged': True, 'source_read_only': True,
                  'network_ids': report['networks'], 'started_at': started,
                  'completed_at': datetime.now(timezone.utc).isoformat(), 'cyme_version': report['cyme_version'],
                  'unsupported_devices': report['unsupported_devices'], 'import_issues': report['issues'],
                  'calculation_parameters': report['calculation_parameters'], 'excluded_tables': []}
    save(output, provenance, schemas, tables, model, validation)
    return {'file': str(Path(output).resolve()), 'networks': report['networks'],
            'counts': {k: len(v) for k, v in tables.items()}, 'source_sha256': sha_before,
            'unsupported_devices': report['unsupported_devices'], 'issue_count': len(report['issues']),
            'validation_status': validation['status']}
