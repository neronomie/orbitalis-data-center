# Orbitalis

Prototype de chaîne Data pour le datacenter orbital ORBITALIS : nettoyage des sources, schéma en étoile DuckDB, analyses SQL, maintenance prédictive et application Streamlit de pilotage.

```
data/raw ─▶ data/cleaned ─▶ data/analytics (conceptual.db, analytics.db) ─▶ modèle ─▶ app Streamlit
```

# Comment lancer le projet

Prérequis : Python ≥ 3.13, [uv](https://docs.astral.sh/uv/), sources brutes déposées dans `data/raw/` (non versionnées).

```bash
uv sync
uv run python run_pipeline.py     # ETL : raw -> cleaned -> analytics
uv run python run_prediction.py   # modèle + scores de risque
uv run streamlit run app.py       # application de pilotage
```

# Rapport briefs

## 1. Exploration

Synthèse des sources, de leur grain, de leurs relations et des problèmes à traiter : [SYNTHESE.md](SYNTHESE.md).

## 2. ETL et qualité

- Nettoyage (raw → cleaned) dans [etl_nettoyage.py](etl_nettoyage.py) et ingestion (cleaned → analytics) dans [etl_ingestion.py](etl_ingestion.py), orchestrés par [run_pipeline.py](run_pipeline.py).
- Les lignes rejetées sont conservées avec leur raison dans `data/rejets/`, et les indicateurs de qualité sont écrits dans `data/qualite_indicateurs.csv`.
- Une puissance nulle en éclipse est considérée comme normale ; hors éclipse, elle est traitée comme suspecte.

## 3. Modélisation

- Modèle conceptuel : [modelisation/mcd/](modelisation/mcd/) et [create_conceptuel.sql](modelisation/sql/create_conceptuel.sql).
- Schéma en étoile (`fait_production` et ses dimensions) : [create_analytique.sql](modelisation/sql/create_analytique.sql). Il calcule notamment `performance = puissance_reelle / puissance_attendue` et la perte de production.
- Contexte orbital : chaque ligne de fait porte la phase orbitale. En éclipse, la puissance attendue d'un panneau tend vers 0, donc l'éclipse n'est jamais comptée comme une panne.

## 4. SQL et analyse

- Requêtes 1 à 9 : [requetes_decisionnels.sql](modelisation/sql/requetes_decisionnels.sql).
- Optimisation (EXPLAIN, index, gain) : [optimisation_requetes.txt](modelisation/sql/optimisation_requetes.txt).

## 5. Maintenance prédictive

- Code : [run_prediction.py](run_prediction.py). Figures (coût selon le seuil, matrices de confusion, importance des variables) : [rapports/prediction/](rapports/prediction/).
- Cible : performance sur les 9 h suivantes inférieure à 0,65 × la performance de référence de l'équipement, ou alarme critique liée à la puissance ou à la température.
- Méthode : découpage temporel train / validation / test. Trois modèles comparés : régression logistique, Random Forest et Gradient Boosting.
- Seuil : celui qui minimise le coût métier sur la validation, appliqué une seule fois au test pour ne pas biaiser l'évaluation finale.
- Résultat : le Gradient Boosting est retenu avec un seuil de 0,18, mais il ne bat pas la stratégie « ne rien faire » sur le test. Le prototype fonctionne de bout en bout, mais le modèle n'est pas exploitable en l'état.

## Bonus : application Streamlit

[app.py](app.py) propose une vue d'ensemble, la liste des équipements à risque, une fiche par équipement et une aide à la décision avec le coût estimé de chaque option et l'historique des décisions. Les filtres et le seuil modifiable sont dans la barre latérale.
