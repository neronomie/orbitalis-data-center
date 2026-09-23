# ORBITALIS Datacenter

ORBITALIS exploite un datacenter placé en orbite basse (LEO). Son alimentation électrique repose sur des panneaux solaires et plusieurs équipements de conversion et de stockage. Les équipements sont soumis à des conditions particulières :

- Alternance entre périodes d'ensoleillement et d'éclipse
- Variations extrêmes de température
- Rayonnement
- Vieillissement progressif
- Défaillances de capteurs ou de convertisseurs

Les interventions de maintenance sont extrêmement coûteuses et difficiles à planifier. L'objectif est de construire une chaîne Data robuste permettant de :

- Fiabiliser les données
- Analyser les performances
- Anticiper les dégradations avant qu'elles n'impactent la production

Vous travaillez comme une équipe Data chargée de produire un premier prototype exploitable et reproductible.

## Référentiels

- Compétences transversales

## Ressources

- orbitalis_datasets_large.zip

## Contexte du projet

### 1. Prise en main et exploration

Les données fournies proviennent de plusieurs systèmes et ne sont pas parfaitement propres.

**Sources disponibles :**

- sites.csv
- equipements.csv
- telemetrie.csv
- alarmes.json
- maintenance.csv
- orbite.csv
- catalogue.db

**Travail demandé :**

- Explorer chaque source (structure, volume, types, valeurs manquantes, doublons évidents).
- Répondre brièvement aux questions suivantes :
  - Quelles sont les sources disponibles et leur grain ?
  - Quelles données semblent fiables ou problématiques ?
  - Quelles relations peut-on établir entre les sources ?
  - Quelles informations sont nécessaires pour analyser une dégradation de performance ?
- Produire un court document de synthèse (1–2 pages max) identifiant au moins 7 problèmes ou questions à vérifier avant toute transformation.

> Rappel : avant de transformer une donnée, il faut comprendre sa signification, son grain et son origine.

### 2. ETL et qualité des données

Construisez une chaîne ETL permettant de charger des données propres dans une zone d'analyse.

**Travail demandé :**

- Lire et profiler les différentes sources.
- Harmoniser les noms de colonnes, types et formats de dates (tous les timestamps doivent être en UTC, format ISO 8601).
- Traiter les valeurs manquantes et aberrantes de façon documentée.
- Identifier et gérer les doublons.
- Vérifier les clés et relations entre les sources (intégrité référentielle).
- Conserver toutes les lignes rejetées dans une table ou un fichier rejets avec la raison du rejet.
- Produire des indicateurs de qualité pour chaque source :
  - nombre de lignes lues
  - nombre de lignes acceptées
  - nombre de lignes rejetées
  - nombre de doublons détectés

**Point d'attention critique :**

Une puissance nulle n'est pas nécessairement une anomalie. Pendant une phase d'éclipse, la production solaire peut être nulle. Vous devez utiliser le contexte orbital (orbite.csv) pour distinguer une situation normale d'une anomalie potentielle.

**Exigences techniques :**

- Séparer clairement : raw → cleaned → analytics.

### 3. Modélisation des données

#### 3.1 Modèle conceptuel

Réalisez un modèle conceptuel identifiant notamment :

- sites
- équipements
- mesures de télémétrie
- alarmes
- opérations de maintenance
- phases orbitales
- références / catalogue

#### 3.2 Modèle décisionnel (schéma en étoile)

Construisez un schéma en étoile destiné à l'analyse.

Une table de faits devra notamment permettre d'étudier :

- la production électrique réelle
- la puissance attendue / nominale
- les pertes de production
- les alarmes
- les performances des équipements
- les opérations de maintenance
- le contexte orbital (ensoleillement / éclipse)

Définissez clairement :

- les dimensions
- les mesures
- les clés
- au moins deux indicateurs calculés, dont obligatoirement :

```
performance = puissance_reelle / puissance_attendue
```

Expliquez comment le contexte d'orbite doit être intégré aux analyses (ne jamais traiter automatiquement une éclipse comme une panne).

### 4. SQL et analyse

À partir du modèle décisionnel, écrivez des requêtes permettant de répondre aux questions suivantes :

1. Quelle est la production totale par équipement et par jour ?
2. Quels équipements présentent les performances moyennes les plus faibles (hors périodes d'éclipse) ?
3. Combien d'alarmes chaque équipement a-t-il générées, par niveau de sévérité ?
4. Quel est le délai moyen entre deux opérations de maintenance pour un même équipement ?
5. Quels équipements n'ont pas été maintenus depuis plus de 90 jours ?
6. Quelle est la perte de production estimée par site (en kWh) ?
7. Comment évolue la performance d'un équipement au cours du temps ? (utiliser au moins une fonction de fenêtre)
8. Comparer les performances pendant les phases d'ensoleillement et les phases d'éclipse sans considérer l'éclipse comme une panne.
9. (Question business) Quel est le coût estimé des pertes de production non anticipées sur la période disponible ?

**Optimisation :**

Sur la table de télémétrie (volumineuse) :

- Identifiez une requête coûteuse
- Utilisez EXPLAIN QUERY PLAN (ou équivalent)
- Proposez au moins un index pertinent
- Expliquez le gain attendu

### 5. Maintenance prédictive

#### Problème

À une date de référence *t*, le modèle doit estimer si un équipement risque d'entrer dans un état dégradé au cours des prochaines 6 périodes orbitales (ou des 9 prochaines heures).

#### Définition obligatoire de la variable cible

Vous devez définir précisément la variable cible.

Exemple de définition acceptable (vous pouvez en proposer une autre, mais elle doit être claire et mesurable) :

> Un équipement est considéré comme « à risque de dégradation » à la date *t* si, dans les 9 heures suivantes :
>
> - sa performance moyenne (hors éclipse) tombe en dessous de 0,65
>
> OU
>
> - il génère au moins une alarme de sévérité critical liée à la puissance ou à la température.

#### Modélisation

- Ne mélangez jamais aléatoirement les observations dans le temps.
- Comparez au minimum : régression logistique, Random Forest ou Gradient Boosting.

#### Évaluation

Présentez obligatoirement :

- Matrice de confusion

#### Coût métier

| Situation | Coût |
|---|---|
| Dégradation non anticipée | 2 500 € |
| Inspection inutile | 150 € |
| Dégradation correctement anticipée | 300 € |
| Pas de dégradation / pas d'inspection | 0 € |

À partir de ces éléments :

- Recherchez sur l'ensemble de validation un seuil qui minimise le coût métier total.
- Appliquez une seule fois ce seuil sur le jeu de test.
- Expliquez pourquoi le choix du seuil doit être réalisé avant l'évaluation finale.

#### Interprétation

- Identifiez les variables les plus importantes.

## Modalités pédagogiques

### Bonus : Application Streamlit de pilotage de la maintenance

Développez une application Streamlit permettant à un opérateur de maintenance de piloter les décisions à partir des résultats du modèle et des données nettoyées.

L'application doit être utilisable (pas uniquement démonstrative) et s'appuyer sur les données et le modèle produits dans les parties précédentes.

#### Fonctionnalités minimales attendues

**Vue d'ensemble (Dashboard)**

- Nombre d'équipements à risque (selon le seuil métier retenu)
- Répartition des risques par site / type d'équipement
- Production totale vs production attendue sur la période récente
- Nombre d'alarmes critiques non acquittées

**Liste des équipements à risque**

- Tableau filtrable et triable
  - Colonnes suggérées : equipement_id, site, type, score de risque, dernière performance, jours depuis dernière maintenance, nb alarmes récentes
- Code couleur selon le niveau de risque

**Fiche équipement détaillée**

- Historique de performance (courbe)
- Historique des alarmes
- Historique des maintenances
- Variables les plus contributives au score de risque (si disponible)
- Contexte orbital récent (ensoleillement / éclipse)

**Aide à la décision**

- Possibilité de simuler une décision : « Planifier une inspection » / « Ne rien faire »
- Affichage du coût estimé selon la matrice métier
- Historique des décisions prises dans l'application (même simple, en session ou fichier)

**Filtres et paramètres**

- Filtre par site, type d'équipement, niveau de risque
- Possibilité de modifier temporairement le seuil de décision (avec avertissement)

## Modalités d'évaluation

### Rendu

#### Livrables

- Github + documentation

#### Critères de performance

- La qualité de la documentation correspond à un vrai rendu pro
