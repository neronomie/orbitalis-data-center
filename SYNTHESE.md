
# Orbitalis DataCenter - Synthèse d'exploration
Ce document vise à produire une synthèse d'exploration des données initial du projet Orbitalis DataCente afin d'identifier au moins 7 problèmes ou questions à vérifier avant toute transformation.

## Questions : 

### 1.a Quelles sont les sources disponibles et leurs grains ?

| Source | description | type grain | explication grain |
| --- | --- | --- | --- |
| [alarmes.json](data/alarmes.json) | Occurence des évenements de type alarmes | grain fin | 521 alarmes horodatées à la minute, sur la période du 2025-05-01 au 2025-06-14 (45 jours) |
| [equipements.csv](data/equipements.csv) | Inventaire d'équipements | grain épais | 67 équipements, référentiel statique (un enregistrement par équipement, pas de série temporelle) |
| [maintenance.csv](data/maintenance.csv) | Occurence des évenements de type maintenance | grain fin | 97 interventions horodatées (date_debut/date_fin) sur avril, mai, juin 2025 |
| [orbite.csv](data/orbite.csv) | Données orbitales et environnementales par site (phase, rayonnement solaire, température ambiante) | grain fin | 17 280 mesures au pas de 15 min, pour 4 sites, sur 45 jours |
| [sites.csv](data/sites.csv) | Inventaire des sites (station) | grain épais | 4 sites, référentiel statique (un enregistrement par site) |
| [telemetrie.csv](data/telemetrie.csv) | Télémetrie des équipements (puissance, température, tension, courant) | grain très fin | 635 340 mesures au pas de 5 min, pour ~67 équipements, sur 45 jours |
| [catalogue.db](data/catalogue.db) | Base de référence (modèles d'équipements, seuils d'alarmes, infos sites) | grain épais | 3 tables de référentiel statique : `modeles` (12 lignes), `seuils_alarmes` (6 lignes), `references_sites` (4 lignes) |

### 1.b Quelles données semblent fiables ou problématiques ?

**Fiables** — sites.csv, equipements.csv (hors 2 lignes sales) et catalogue.db : référentiels statiques, peu de bruit.

**Problématiques** :
- `telemetrie.csv` : 4 formats de date différents, ~10% de valeurs manquantes, 13 240 doublons (couples timestamp/équipement), températures aberrantes (-250°C → 999°C sur 11 000 lignes), ~7,7% de puissances négatives
- `equipements.csv` : 2 lignes sales (site_id vide/inconnu, date "2025-99-99", puissance négative) + 1 doublon exact (EQ-006)
- `alarmes.json` : 1 alarme sans equipement_id, sévérité "URGENCE" hors nomenclature (info/warning/critical), type_alarme incohérent avec le message dans 84% des cas, 1 type "UNKNOWN_TYPE"
- `maintenance.csv` : FK invalides (site vide, "EQ-XXX"), 1 intervention avec date_fin < date_debut

### 1.c Quelles relations peut-on établir entre les sources ?

- `sites.csv` ↔ `equipements.csv` via `site_id`
- `equipements.csv` ↔ `telemetrie.csv` / `alarmes.json` / `maintenance.csv` via `equipement_id`
- `sites.csv` ↔ `orbite.csv` via `site_id` (contexte environnemental)
- `equipements.csv` ↔ `catalogue.db` (`modeles`) via `modele` (puissance/rendement nominaux)
- `alarmes.json` ↔ `catalogue.db` (`seuils_alarmes`) via `type_alarme`

Schéma logique : site → équipements → (télémétrie + alarmes + maintenance), confrontés aux seuils du catalogue et au contexte orbital.

### 1.d Quelles informations sont nécessaires pour analyser une dégradation de performance ?

- Télémétrie nettoyée (puissance, tension, courant, température) par équipement
- Puissance nominale / rendement attendu (equipements.csv + catalogue.db) pour comparer réel vs attendu
- Phase orbitale (orbite.csv) pour ne pas confondre puissance nulle en éclipse avec une panne
- Historique des alarmes pour corréler avec les baisses de perf
- Historique de maintenance pour exclure les périodes d'intervention
- Seuils d'alarme (catalogue.db) pour objectiver ce qui est "dégradé"
