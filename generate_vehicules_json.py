#!/usr/bin/env python3
"""Génère vehicules.json (base véhicules de LaWatture) à partir d'OpenEV Data.

Usage :
    git clone --depth 1 https://github.com/open-ev-data/open-ev-data-dataset.git
    python3 generate_vehicules_json.py open-ev-data-dataset vehicules.json

Comme pour la surcouche péages, le calcul est fait ici, une fois pour toutes :
l'application lit le fichier tel quel, sans appel réseau à OpenEV Data.

Colonnes de chaque version (voir "colonnes" dans le fichier produit) :
  id            identifiant stable (marque/modele/annee/version)
  annee, version
  kwh_net       capacité utile (kWh)
  kwh_brut      capacité brute (kWh), peut être null
  conso_ref     capacité utile ÷ autonomie homologuée × 100 (kWh/100 km)
  cycle         cycle de l'autonomie utilisée : WLTP, sinon EPA
  p_10_80       puissance moyenne de recharge DC sur 10→80 % (kW)
  p_10_80_est   1 si p_10_80 est estimée (P_DC_RATIO × puissance DC max), 0 si mesurée
  dc_max, ac_max  puissances maximales de recharge (kW)
  prises        prises de charge, ex. "ccs2,type2"

Données : OpenEV Data, licence CDLA-Permissive-2.0 (https://open-ev-data.github.io).
"""
import copy, datetime, glob, json, os, subprocess, sys

# Puissance moyenne 10→80 % estimée quand OpenEV ne donne pas de temps de charge :
# en pratique la moyenne se situe vers 55-65 % de la puissance DC maximale.
P_DC_RATIO = 0.6


def merge(a, b):
    out = copy.deepcopy(a)
    for k, v in b.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def records(root):
    """Fusionne base.json (modèle) + fichier de l'année + fichier de la version."""
    for base in sorted(glob.glob(os.path.join(root, 'src/*/*/base.json'))):
        d = os.path.dirname(base)
        B = json.load(open(base, encoding='utf-8'))
        for yd in sorted(glob.glob(d + '/*/')):
            names = [os.path.basename(f)[:-5] for f in sorted(glob.glob(yd + '*.json'))]
            if not names:
                continue
            yb = min(names, key=len)  # fichier de l'année = nom le plus court
            Y = merge(B, json.load(open(yd + yb + '.json', encoding='utf-8')))
            for n in names:
                r = Y if n == yb else merge(Y, json.load(open(yd + n + '.json', encoding='utf-8')))
                yield n, r


def row(slug, r):
    b = r.get('battery', {})
    net = b.get('pack_capacity_kwh_net')
    if not net:
        return None
    rated = r.get('range', {}).get('rated', [])
    rng, cycle = None, None
    for cyc in ('wltp', 'epa'):
        rng = next((x.get('range_km') for x in rated if x.get('cycle') == cyc and x.get('range_km')), None)
        if rng:
            cycle = cyc.upper()
            break
    conso = round(net / rng * 100, 1) if rng else None

    ch = r.get('charging', {})
    dc_max = (ch.get('dc') or {}).get('max_power_kw')
    ac_max = (ch.get('ac') or {}).get('max_power_kw')
    p, est = None, 0
    for t in (ch.get('charging_time') or {}).get('dc', []):
        f, to, mins = t.get('from_soc_percent'), t.get('to_soc_percent'), t.get('time_min')
        if f is not None and to and mins and to > f:
            p = round(net * (to - f) / 100 / (mins / 60))
            if (f, to) == (10, 80):
                break
    if p is None and dc_max:
        p, est = round(dc_max * P_DC_RATIO), 1

    prises = ','.join(sorted({c['connector'] for c in r.get('charge_ports', []) if c.get('connector')}))
    variant = (r.get('variant') or {}).get('name') or (r.get('trim') or {}).get('name') or 'Base'
    vid = '/'.join([r['make']['slug'], r['model']['slug'], str(r.get('year')), slug])
    return [vid, r.get('year'), variant, net, b.get('pack_capacity_kwh_gross'), conso, cycle,
            p, est, dc_max, ac_max, prises]


def main(root, dest):
    marques = {}
    n = 0
    for slug, r in records(root):
        x = row(slug, r)
        if not x:
            continue
        mk, md = r['make']['name'], r['model']['name']
        versions = marques.setdefault(mk, {}).setdefault(md, [])
        if any(v[1:] == x[1:] for v in versions):  # doublon exact (même données, autre fichier)
            continue
        versions.append(x)
        n += 1
    for mk in marques:
        for md in marques[mk]:
            marques[mk][md].sort(key=lambda v: (-(v[1] or 0), v[2]))
    try:
        commit = subprocess.check_output(['git', '-C', root, 'rev-parse', '--short', 'HEAD'], text=True).strip()
    except Exception:
        commit = None
    out = {
        'source': 'OpenEV Data (https://github.com/open-ev-data/open-ev-data-dataset)',
        'licence': 'CDLA-Permissive-2.0',
        'commit': commit,
        'genere': datetime.date.today().isoformat(),
        'p_dc_ratio': P_DC_RATIO,
        'colonnes': ['id', 'annee', 'version', 'kwh_net', 'kwh_brut', 'conso_ref', 'cycle',
                     'p_10_80', 'p_10_80_est', 'dc_max', 'ac_max', 'prises'],
        'marques': dict(sorted(marques.items(), key=lambda kv: kv[0].lower())),
    }
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    nm = sum(len(m) for m in marques.values())
    print(f'{len(marques)} marques, {nm} modèles, {n} versions -> {dest} ({os.path.getsize(dest) // 1024} Ko)')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'open-ev-data-dataset',
         sys.argv[2] if len(sys.argv) > 2 else 'vehicules.json')
