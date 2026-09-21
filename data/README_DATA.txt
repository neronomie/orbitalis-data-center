ORBITALIS - Jeu de données synthétiques (version large)
=======================================================

Données SYNTHÉTIQUES générées pour le TP ORBITALIS DATA CENTER.

Période : 2025-05-01 → 2025-06-14 (45 jours)

Volumes approximatifs :
- sites.csv          : 4 sites
- equipements.csv    : ~70 équipements (+ lignes sales)
- telemetrie.csv     : ~350 000+ lignes (pas de 5 min)
- orbite.csv         : ~17 000 lignes (pas de 15 min)
- alarmes.json       : ~520 alarmes
- maintenance.csv    : ~100 interventions
- catalogue.db       : modèles, seuils, références sites

Problèmes de qualité injectés volontairement :
- formats de dates hétérogènes
- valeurs manquantes
- valeurs aberrantes
- doublons
- clés étrangères invalides
- phases orbitales (ensoleillement / éclipse)
- puissance nulle en éclipse = comportement normal

Ces données ne sont PAS les fichiers officiels du TP.
Elles sont destinées à l'entraînement et au prototypage.
