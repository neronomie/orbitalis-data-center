ATTACH IF NOT EXISTS 'data/analytics/conceptual.db' AS conceptual (READ_ONLY);

-- 1. Production totale par équipement et par jour

SELECT
    de.equipement_id,
    dd.date_valeur,
    ROUND(SUM(fp.puissance_w) * 5.0 / 60 / 1000, 3) AS production_totale_kwh
FROM fait_production fp
JOIN dim_equipement de ON de.equipement_key = fp.equipement_key
JOIN dim_date dd ON dd.date_key = fp.date_key
WHERE fp.puissance_w IS NOT NULL
GROUP BY de.equipement_id, dd.date_valeur
ORDER BY de.equipement_id, dd.date_valeur;


-- 2. Équipements aux performances moyennes les plus faibles (hors éclipse)

SELECT
    de.equipement_id,
    de.type_equipement,
    ROUND(AVG(fp.performance), 4) AS performance_moyenne,
    COUNT(fp.performance) AS nb_mesures
FROM fait_production fp
JOIN dim_equipement de ON de.equipement_key = fp.equipement_key
WHERE fp.phase != 'eclipse'
  AND fp.performance IS NOT NULL
GROUP BY de.equipement_id, de.type_equipement
ORDER BY performance_moyenne ASC
LIMIT 10;


-- 3. Nombre d'alarmes par équipement et par niveau de sévérité
SELECT
    de.equipement_id,
    SUM(fp.nb_alarmes_info) AS nb_info,
    SUM(fp.nb_alarmes_warning) AS nb_warning,
    SUM(fp.nb_alarmes_critical) AS nb_critical,
    SUM(fp.nb_alarmes_info + fp.nb_alarmes_warning + fp.nb_alarmes_critical) AS nb_total
FROM fait_production fp
JOIN dim_equipement de ON de.equipement_key = fp.equipement_key
GROUP BY de.equipement_id
ORDER BY nb_total DESC;


-- 4. Délai moyen entre deux opérations de maintenance pour un même équipement
WITH interventions_ordonnees AS (
    SELECT
        equipement_id,
        date_debut,
        LAG(date_debut) OVER (PARTITION BY equipement_id ORDER BY date_debut) AS date_debut_precedente
    FROM conceptual.operation_maintenance
)
SELECT
    equipement_id,
    COUNT(*) AS nb_intervalles,
    ROUND(AVG(date_debut - date_debut_precedente), 1) AS delai_moyen_jours
FROM interventions_ordonnees
WHERE date_debut_precedente IS NOT NULL
GROUP BY equipement_id
ORDER BY equipement_id;

-- Délai moyen tous équipements confondus
WITH interventions_ordonnees AS (
    SELECT
        equipement_id,
        date_debut,
        LAG(date_debut) OVER (PARTITION BY equipement_id ORDER BY date_debut) AS date_debut_precedente
    FROM conceptual.operation_maintenance
)
SELECT ROUND(AVG(date_debut - date_debut_precedente), 1) AS delai_moyen_jours_global
FROM interventions_ordonnees
WHERE date_debut_precedente IS NOT NULL;


-- 5. Équipements non maintenus depuis plus de 90 jours
WITH date_reference AS (
    SELECT MAX(date_valeur) AS d FROM dim_date
),
derniere_intervention AS (
    SELECT equipement_id, MAX(COALESCE(date_fin, date_debut)) AS derniere_date
    FROM conceptual.operation_maintenance
    GROUP BY equipement_id
)
SELECT
    de.equipement_id,
    de.type_equipement,
    di.derniere_date,
    (SELECT d FROM date_reference) - di.derniere_date AS jours_depuis_derniere_maintenance
FROM dim_equipement de
LEFT JOIN derniere_intervention di ON di.equipement_id = de.equipement_id
WHERE di.derniere_date IS NULL
   OR (SELECT d FROM date_reference) - di.derniere_date > 90
ORDER BY jours_depuis_derniere_maintenance DESC NULLS FIRST;


-- 6. Perte de production estimée par site (kWh)
SELECT
    ds.site_id,
    ds.nom,
    ROUND(SUM(fp.perte_production_w) * 5.0 / 60 / 1000, 2) AS perte_totale_kwh
FROM fait_production fp
JOIN dim_site ds ON ds.site_key = fp.site_key
WHERE fp.perte_production_w IS NOT NULL
GROUP BY ds.site_id, ds.nom
ORDER BY perte_totale_kwh DESC;

-- 7. Évolution de la performance d'un équipement au cours du temps
SELECT
    de.equipement_id,
    fp.horodatage,
    fp.performance,
    ROUND(
        AVG(fp.performance) OVER (
            PARTITION BY fp.equipement_key
            ORDER BY fp.horodatage
            ROWS BETWEEN 11 PRECEDING AND CURRENT ROW
        ), 4
    ) AS performance_moyenne_glissante_1h
FROM fait_production fp
JOIN dim_equipement de ON de.equipement_key = fp.equipement_key
WHERE de.equipement_id = 'EQ-001'
ORDER BY fp.horodatage;


-- 8. Performances en ensoleillement vs en éclipse (éclipse != panne)
SELECT
    de.type_equipement,
    fp.phase,
    COUNT(*) AS nb_mesures,
    COUNT(fp.performance) AS nb_mesures_perf_definie,
    ROUND(AVG(fp.performance), 4) AS performance_moyenne
FROM fait_production fp
JOIN dim_equipement de ON de.equipement_key = fp.equipement_key
WHERE fp.phase IN ('ensoleillement', 'eclipse')
GROUP BY de.type_equipement, fp.phase
ORDER BY de.type_equipement, fp.phase;


-- 9. (Business)  Quel est le coût estimé des pertes de production non anticipées sur la période disponible ?
WITH parametres AS (
    SELECT 0.15 AS cout_par_kwh_eur 
),
pertes_non_anticipees AS (
    SELECT fp.perte_production_w
    FROM fait_production fp
    WHERE fp.perte_production_w IS NOT NULL
      AND fp.en_maintenance = false
)
SELECT
    ROUND(SUM(perte_production_w) * 5.0 / 60 / 1000, 2) AS perte_totale_kwh,
    p.cout_par_kwh_eur,
    ROUND(SUM(perte_production_w) * 5.0 / 60 / 1000 * p.cout_par_kwh_eur, 2) AS cout_estime_eur
FROM pertes_non_anticipees, parametres p
GROUP BY p.cout_par_kwh_eur;
