# Orbitalis DataCenter

L'objectif de ce projet est de sur la bases de données de télémetrie d'une station auto-alimentée, de produire un premier prototype exploitable et reproductible pour fiabiliser les données, analyser les performances, anticiper les dégradations avant qu’elles n’impactent la production.

# 1. Prise en main et exploration

### Quelles sont les sources disponibles et leurs grains ?

| Source | description | type grain | explication grain |
| --- | --- | --- | --- |
| [alarmes.json](data/alarmes.json) | Occurence des évenements de type alarmes | grain fin | +400 mesures effectués sur une seule journée |
| [equipements.csv](data/equipements.csv) | Inventaire d'équipements | grain épais | ... |
| [maintenance.csv](data/maintenance.csv) | Occurence des évenements de type maintenance | grain fin | 97 occ pour avril, mai, juin 2025 |
| [orbite.csv](data/orbite.csv) | 

### Quelles données semblent fiables ou problématiques ?

### Quelles relations peut-on établir entre les sources ?

### Quelles informations sont nécessaires pour analyser une dégradation de performance ?


Voici les anomalies soulevés dans les documents fournis 

> alarmes.json
