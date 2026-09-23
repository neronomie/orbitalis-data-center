from pathlib import Path

import duckdb

BASE_DIR = Path(__file__).resolve().parent
CLEAN_DIR = BASE_DIR / "data" / "cleaned"
SQL_DIR = BASE_DIR / "modelisation" / "sql"
CONCEPTUAL_DB = BASE_DIR / "data" / "analytics" /"conceptual.db"
ANALYTICS_DB = BASE_DIR / "data" / "analytics" /"analytics.db"


def _csv(nom):
    return str(CLEAN_DIR / nom).replace("'", "''")


def _executer_script(con, path):
    con.execute(path.read_text())


def _compte(con, table):
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# --------------------------------------------------------------------------- #
# conceptual.db : intégration des CSV nettoyés dans le schéma relationnel
# --------------------------------------------------------------------------- #
def build_conceptual():
    if CONCEPTUAL_DB.exists():
        CONCEPTUAL_DB.unlink()
    con = duckdb.connect(str(CONCEPTUAL_DB))
    _executer_script(con, SQL_DIR / "create_conceptuel.sql")

    # site : sites.csv + catalogue_references_sites.csv (LEFT JOIN sur site_id,
    # les 4 sites sont présents des deux côtés mais on reste tolérant si ce n'est
    # plus le cas un jour)
    con.execute(f"""
        INSERT INTO site
        SELECT
            s.site_id, s.nom, s.orbite_type, s.altitude_km, s.inclination_deg,
            s.date_mise_en_service, s.statut,
            r.description, r.capacite_max_kw, r.zone_orbitale
        FROM read_csv_auto('{_csv("sites.csv")}') s
        LEFT JOIN read_csv_auto('{_csv("catalogue_references_sites.csv")}') r
            USING (site_id)
    """)

    con.execute(f"""
        INSERT INTO modele_equipement
        SELECT modele_id, type_equipement, fabricant, puissance_nominale_w,
               rendement_nominal, duree_vie_annees, masse_kg
        FROM read_csv_auto('{_csv("catalogue_modeles.csv")}')
    """)

    con.execute(f"""
        INSERT INTO equipement
        SELECT equipement_id, site_id, type, modele, puissance_nominale_w,
               date_installation, statut
        FROM read_csv_auto('{_csv("equipements.csv")}')
    """)

    con.execute(f"""
        INSERT INTO seuil_alarme
        SELECT type_alarme, seuil_warning, seuil_critical, unite, description
        FROM read_csv_auto('{_csv("catalogue_seuils_alarmes.csv")}')
    """)
    # 'inconnu' : sentinelle pour un type_alarme hors nomenclature (cf.
    # nettoyer_alarmes dans etl_nettoyage.py) -> nécessaire pour que la FK
    # alarme.type_alarme -> seuil_alarme.type_alarme reste satisfaite.
    con.execute("""
        INSERT INTO seuil_alarme VALUES
            ('inconnu', NULL, NULL, NULL, 'Type d''alarme hors nomenclature (rejeté au nettoyage ETL)')
    """)

    con.execute(f"""
        INSERT INTO mesure_telemetrie (equipement_id, horodatage, puissance_w,
            temperature_c, rayonnement, tension_v, courant_a)
        SELECT equipement_id, horodatage, puissance_w, temperature_c,
               rayonnement, tension_v, courant_a
        FROM read_csv_auto('{_csv("telemetrie.csv")}')
    """)

    con.execute(f"""
        INSERT INTO mesure_orbitale (site_id, horodatage, phase,
            rayonnement_solaire_w_m2, temperature_ambiante_c)
        SELECT site_id, horodatage, phase, rayonnement_solaire_w_m2, temperature_ambiante_c
        FROM read_csv_auto('{_csv("orbite.csv")}')
    """)

    con.execute(f"""
        INSERT INTO alarme
        SELECT alarme_id, equipement_id, type_alarme, horodatage, severite, message, acquittee
        FROM read_csv_auto('{_csv("alarmes.csv")}')
    """)

    con.execute(f"""
        INSERT INTO operation_maintenance
        SELECT maintenance_id, equipement_id, date_debut, date_fin,
               type_intervention, technicien, cout_eur, commentaire
        FROM read_csv_auto('{_csv("maintenance.csv")}')
    """)

    return con


# --------------------------------------------------------------------------- #
# analytics.db : schéma en étoile dérivé de conceptual.db
# --------------------------------------------------------------------------- #
def _peupler_dim_date(con):
    bornes = con.execute("""
        SELECT MIN(d), MAX(d) FROM (
            SELECT MIN(CAST(horodatage AS DATE)) AS d FROM src.mesure_telemetrie
            UNION ALL SELECT MAX(CAST(horodatage AS DATE)) FROM src.mesure_telemetrie
            UNION ALL SELECT MIN(CAST(horodatage AS DATE)) FROM src.mesure_orbitale
            UNION ALL SELECT MAX(CAST(horodatage AS DATE)) FROM src.mesure_orbitale
            UNION ALL SELECT MIN(date_debut) FROM src.operation_maintenance
            UNION ALL SELECT MAX(COALESCE(date_fin, date_debut)) FROM src.operation_maintenance
        )
    """).fetchone()
    date_min, date_max = bornes

    con.execute("""
        INSERT INTO dim_date
        SELECT
            CAST(strftime(d, '%Y%m%d') AS INTEGER),
            CAST(d AS DATE),
            CAST(EXTRACT(year FROM d) AS SMALLINT),
            CAST(EXTRACT(month FROM d) AS SMALLINT),
            CAST(EXTRACT(day FROM d) AS SMALLINT),
            CAST(EXTRACT(isodow FROM d) AS SMALLINT),
            CASE EXTRACT(isodow FROM d)
                WHEN 1 THEN 'lundi' WHEN 2 THEN 'mardi' WHEN 3 THEN 'mercredi'
                WHEN 4 THEN 'jeudi' WHEN 5 THEN 'vendredi' WHEN 6 THEN 'samedi'
                ELSE 'dimanche'
            END,
            CAST(EXTRACT(week FROM d) AS SMALLINT),
            CAST((EXTRACT(month FROM d) - 1) / 3 + 1 AS SMALLINT),
            EXTRACT(isodow FROM d) IN (6, 7)
        FROM generate_series(CAST(? AS DATE), CAST(? AS DATE), INTERVAL 1 DAY) AS t(d)
    """, [date_min, date_max])


def _peupler_dimensions(con):
    con.execute("""
        INSERT INTO dim_site (site_id, nom, orbite_type, altitude_km, inclination_deg,
            date_mise_en_service, statut, description, capacite_max_kw, zone_orbitale)
        SELECT site_id, nom, orbite_type, altitude_km, inclination_deg,
               date_mise_en_service, statut, description, capacite_max_kw, zone_orbitale
        FROM src.site
    """)

    con.execute("""
        INSERT INTO dim_type_equipement
        SELECT
            type_equipement,
            ROUND(AVG(rendement_nominal), 4),
            CAST(ROUND(AVG(duree_vie_annees)) AS SMALLINT),
            ROUND(AVG(masse_kg), 2),
            CAST(count(*) AS SMALLINT)
        FROM src.modele_equipement
        GROUP BY type_equipement
    """)

    con.execute("""
        INSERT INTO dim_equipement (equipement_id, site_key, type_equipement,
            modele_declare, puissance_nominale_w, date_installation, statut)
        SELECT e.equipement_id, ds.site_key, e.type, e.modele,
               e.puissance_nominale_w, e.date_installation, e.statut
        FROM src.equipement e
        JOIN dim_site ds ON ds.site_id = e.site_id
    """)

    con.execute("""
        INSERT INTO dim_type_alarme
        SELECT type_alarme, seuil_warning, seuil_critical, unite, description
        FROM src.seuil_alarme
    """)


def _peupler_fait_production(con):
    # Grain = 1 ligne de télémétrie. Contexte orbital résolu par ASOF JOIN
    # (dernière mesure d'orbite connue, même site, horodatage <= télémétrie) :
    # cadence orbite = 15 min vs télémétrie = 5 min, donc au plus 15 min d'écart.
    con.execute("""
        INSERT INTO fait_production (
            date_key, horodatage, equipement_key, site_key,
            phase, rayonnement_solaire_w_m2, temperature_ambiante_c,
            puissance_w, temperature_c, rayonnement, tension_v, courant_a
        )
        SELECT
            CAST(strftime(t.horodatage, '%Y%m%d') AS INTEGER),
            t.horodatage,
            de.equipement_key,
            de.site_key,
            COALESCE(o.phase, 'inconnu'),
            o.rayonnement_solaire_w_m2,
            o.temperature_ambiante_c,
            t.puissance_w, t.temperature_c, t.rayonnement, t.tension_v, t.courant_a
        FROM src.mesure_telemetrie t
        JOIN src.equipement eq ON eq.equipement_id = t.equipement_id
        JOIN dim_equipement de ON de.equipement_id = t.equipement_id
        ASOF LEFT JOIN src.mesure_orbitale o
            ON o.site_id = eq.site_id AND t.horodatage >= o.horodatage
    """)

    # Alarmes : rattachées par ASOF JOIN à la dernière ligne fait_production
    # connue du même équipement (même logique de rapprochement temporel que
    # pour le contexte orbital), puis agrégées par sévérité sur cette ligne.
    con.execute("""
        CREATE TEMP TABLE alarme_bucket AS
        SELECT fp.equipement_key, fp.horodatage, a.severite, a.type_alarme
        FROM src.alarme a
        JOIN dim_equipement de ON de.equipement_id = a.equipement_id
        ASOF JOIN fait_production fp
            ON fp.equipement_key = de.equipement_key AND a.horodatage >= fp.horodatage
    """)
    con.execute("""
        CREATE TEMP TABLE alarme_agg AS
        SELECT
            equipement_key, horodatage,
            CAST(SUM(CASE WHEN severite = 'info' THEN 1 ELSE 0 END) AS SMALLINT) AS nb_info,
            CAST(SUM(CASE WHEN severite = 'warning' THEN 1 ELSE 0 END) AS SMALLINT) AS nb_warning,
            CAST(SUM(CASE WHEN severite = 'critical' THEN 1 ELSE 0 END) AS SMALLINT) AS nb_critical,
            arg_max(type_alarme, CASE severite
                WHEN 'critical' THEN 3 WHEN 'warning' THEN 2 ELSE 1 END) AS type_alarme_dominant
        FROM alarme_bucket
        GROUP BY equipement_key, horodatage
    """)
    con.execute("""
        UPDATE fait_production fp
        SET nb_alarmes_info = ag.nb_info,
            nb_alarmes_warning = ag.nb_warning,
            nb_alarmes_critical = ag.nb_critical,
            type_alarme = ag.type_alarme_dominant
        FROM alarme_agg ag
        WHERE fp.equipement_key = ag.equipement_key AND fp.horodatage = ag.horodatage
    """)
    con.execute("DROP TABLE alarme_bucket")
    con.execute("DROP TABLE alarme_agg")

    # puissance_attendue_w : formule différenciée par type_equipement.
    # panneau_solaire   : dépend de l'irradiance (~0 en éclipse, physiquement attendu)
    # autres types      : puissance_nominale_w * rendement_nominal_moyen(type),
    #                     indépendant de la phase orbitale
    # type non reconnu  : NULL plutôt que silencieusement faux
    con.execute("""
        UPDATE fait_production fp
        SET puissance_attendue_w = CASE de.type_equipement
                WHEN 'panneau_solaire' THEN
                    de.puissance_nominale_w * dte.rendement_nominal_moyen
                        * LEAST(fp.rayonnement_solaire_w_m2 / 1361.0, 1.0)
                WHEN 'batterie' THEN de.puissance_nominale_w * dte.rendement_nominal_moyen
                WHEN 'convertisseur_DC' THEN de.puissance_nominale_w * dte.rendement_nominal_moyen
                WHEN 'onduleur' THEN de.puissance_nominale_w * dte.rendement_nominal_moyen
                WHEN 'radiateur' THEN de.puissance_nominale_w * dte.rendement_nominal_moyen
                ELSE NULL
            END
        FROM dim_equipement de
        JOIN dim_type_equipement dte ON dte.type_equipement = de.type_equipement
        WHERE fp.equipement_key = de.equipement_key
    """)

    # en_maintenance : true si l'horodatage tombe dans une fenêtre d'intervention
    con.execute("""
        UPDATE fait_production fp
        SET en_maintenance = true
        FROM src.operation_maintenance om
        JOIN dim_equipement de ON de.equipement_id = om.equipement_id
        WHERE fp.equipement_key = de.equipement_key
          AND CAST(fp.horodatage AS DATE) BETWEEN om.date_debut AND COALESCE(om.date_fin, CURRENT_DATE)
    """)

    # Lignes synthétiques : rendent visible un arrêt total (aucune télémétrie)
    # pendant une intervention de maintenance.
    con.execute("""
        INSERT INTO fait_production (
            date_key, horodatage, equipement_key, site_key, phase,
            en_maintenance, ligne_synthetique_arret
        )
        SELECT
            dd.date_key,
            CAST(om.date_debut AS TIMESTAMP),
            de.equipement_key,
            de.site_key,
            'inconnu',
            true,
            true
        FROM src.operation_maintenance om
        JOIN dim_equipement de ON de.equipement_id = om.equipement_id
        JOIN dim_date dd ON dd.date_valeur = om.date_debut
        WHERE NOT EXISTS (
            SELECT 1 FROM fait_production fp
            WHERE fp.equipement_key = de.equipement_key
              AND CAST(fp.horodatage AS DATE) BETWEEN om.date_debut AND COALESCE(om.date_fin, CURRENT_DATE)
        )
        ON CONFLICT (equipement_key, horodatage) DO NOTHING
    """)


def build_analytics():
    if ANALYTICS_DB.exists():
        ANALYTICS_DB.unlink()
    con = duckdb.connect(str(ANALYTICS_DB))
    _executer_script(con, SQL_DIR / "create_analytique.sql")
    con.execute(f"ATTACH '{str(CONCEPTUAL_DB)}' AS src (READ_ONLY)")

    _peupler_dim_date(con)
    _peupler_dimensions(con)
    _peupler_fait_production(con)

    con.execute("DETACH src")
    return con


# --------------------------------------------------------------------------- #
# Orchestration + rapport
# --------------------------------------------------------------------------- #
def main():
    print("=" * 78)
    print(" RAPPORT D'INGESTION ETL - ORBITALIS")
    print("=" * 78)

    print("\n--- conceptual.db ---")
    con_c = build_conceptual()
    for table in ["site", "modele_equipement", "equipement", "seuil_alarme",
                  "mesure_telemetrie", "mesure_orbitale", "alarme", "operation_maintenance"]:
        print(f"  {table:<22} {_compte(con_c, table):>8} lignes")
    con_c.close()

    print("\n--- analytics.db ---")
    con_a = build_analytics()
    for table in ["dim_date", "dim_site", "dim_type_equipement", "dim_equipement",
                  "dim_type_alarme", "fait_production"]:
        print(f"  {table:<22} {_compte(con_a, table):>8} lignes")
    n_synthetiques = con_a.execute(
        "SELECT count(*) FROM fait_production WHERE ligne_synthetique_arret"
    ).fetchone()[0]
    n_avec_alarme = con_a.execute(
        "SELECT count(*) FROM fait_production WHERE type_alarme IS NOT NULL"
    ).fetchone()[0]
    print(f"    dont lignes synthétiques (arrêt maintenance) : {n_synthetiques}")
    print(f"    dont lignes avec alarme rattachée             : {n_avec_alarme}")
    con_a.close()

    print(f"\nBases écrites : {CONCEPTUAL_DB} et {ANALYTICS_DB}")


if __name__ == "__main__":
    main()
