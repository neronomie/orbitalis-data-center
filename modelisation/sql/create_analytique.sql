-- Dialecte : DuckDB (fichier data/analytics.db)
-- Schéma uniquement (dimensions + table de faits). Le peuplement et le calcul
-- différencié de puissance_attendue_w / en_maintenance / lignes synthétiques
-- d'arrêt sont de la logique ETL : voir etl_ingestion.py (populate_analytics).

DROP TABLE IF EXISTS fait_maintenance CASCADE;
DROP TABLE IF EXISTS fait_production CASCADE;
DROP TABLE IF EXISTS dim_type_alarme CASCADE;
DROP TABLE IF EXISTS dim_equipement CASCADE;
DROP TABLE IF EXISTS dim_type_equipement CASCADE;
DROP TABLE IF EXISTS dim_site CASCADE;
DROP TABLE IF EXISTS dim_date CASCADE;

DROP SEQUENCE IF EXISTS seq_dim_site;
DROP SEQUENCE IF EXISTS seq_dim_equipement;
DROP SEQUENCE IF EXISTS seq_fait_production;
CREATE SEQUENCE seq_dim_site START 1;
CREATE SEQUENCE seq_dim_equipement START 1;
CREATE SEQUENCE seq_fait_production START 1;

-- =====================================================================
-- Dimensions
-- =====================================================================

-- dim_date
CREATE TABLE dim_date (
    date_key        INTEGER PRIMARY KEY,        -- format AAAAMMJJ
    date_valeur     DATE NOT NULL UNIQUE,
    annee           SMALLINT NOT NULL,
    mois            SMALLINT NOT NULL CHECK (mois BETWEEN 1 AND 12),
    jour            SMALLINT NOT NULL CHECK (jour BETWEEN 1 AND 31),
    jour_semaine    SMALLINT NOT NULL CHECK (jour_semaine BETWEEN 1 AND 7), -- 1 = lundi
    nom_jour        TEXT NOT NULL,
    semaine_annee   SMALLINT NOT NULL,
    trimestre       SMALLINT NOT NULL CHECK (trimestre BETWEEN 1 AND 4),
    est_weekend     BOOLEAN NOT NULL
);

-- dim_site : sites.csv + catalogue.db/references_sites
CREATE TABLE dim_site (
    site_key                BIGINT PRIMARY KEY DEFAULT nextval('seq_dim_site'),
    site_id                 TEXT NOT NULL UNIQUE,      -- clé métier, ex. SITE-001
    nom                     TEXT NOT NULL,
    orbite_type             TEXT,
    altitude_km             NUMERIC(8,2),
    inclination_deg         NUMERIC(6,2),
    date_mise_en_service    DATE,
    statut                  TEXT,
    description             TEXT,
    capacite_max_kw         NUMERIC(8,2),
    zone_orbitale           TEXT
);

-- dim_type_equipement : catalogue.db/modeles agrégé par type_equipement
-- (contourne la FK equipement.modele -> modele_id invalide, cf. MODELISATION.md §3.2)
CREATE TABLE dim_type_equipement (
    type_equipement              TEXT PRIMARY KEY,        -- panneau_solaire, batterie, convertisseur_DC, onduleur, radiateur
    rendement_nominal_moyen       NUMERIC(5,4),            -- AVG(rendement_nominal) sur les modèles du type
    duree_vie_annees_moyenne       SMALLINT,
    masse_kg_moyenne                NUMERIC(8,2),
    nb_modeles_catalogue             SMALLINT NOT NULL
);

-- dim_equipement : equipements.csv
CREATE TABLE dim_equipement (
    equipement_key          BIGINT PRIMARY KEY DEFAULT nextval('seq_dim_equipement'),
    equipement_id           TEXT NOT NULL UNIQUE,       -- clé métier, ex. EQ-001
    site_key                BIGINT NOT NULL REFERENCES dim_site (site_key),
    type_equipement          TEXT NOT NULL REFERENCES dim_type_equipement (type_equipement),
    modele_declare            TEXT,                       -- valeur brute equipements.csv/modele, indicative uniquement (non fiable, cf. §3.1/§3.2)
    puissance_nominale_w       NUMERIC(10,2),
    date_installation            DATE,
    statut                        TEXT
);

-- dim_type_alarme : catalogue.db/seuils_alarmes
CREATE TABLE dim_type_alarme (
    type_alarme     TEXT PRIMARY KEY,          -- surchauffe, sous_tension, ...
    seuil_warning   NUMERIC(10,3),
    seuil_critical  NUMERIC(10,3),
    unite           TEXT,
    description     TEXT
);

-- =====================================================================
-- Faits
-- =====================================================================

-- fait_production : grain = 1 équipement x 1 horodatage de télémétrie (pas 5 min)
CREATE TABLE fait_production (
    production_key              BIGINT PRIMARY KEY DEFAULT nextval('seq_fait_production'),
    date_key                    INTEGER NOT NULL REFERENCES dim_date (date_key),
    horodatage                  TIMESTAMP NOT NULL,
    equipement_key               BIGINT NOT NULL REFERENCES dim_equipement (equipement_key),
    site_key                     BIGINT NOT NULL REFERENCES dim_site (site_key), -- dénormalisé depuis dim_equipement : évite une jointure pour filtrer/agréger par site

    -- Contexte orbital (orbite.csv), résolu à l'ETL : horodatage le plus proche, même site
    phase                         TEXT NOT NULL CHECK (phase IN ('ensoleillement', 'eclipse', 'inconnu')),
    rayonnement_solaire_w_m2      NUMERIC(10,2),             -- irradiance du site (source fiable, cf. §3.2 vs telemetrie.rayonnement)
    temperature_ambiante_c        NUMERIC(6,2),

    -- Production réelle / attendue / performance (telemetrie.csv + calcul ETL)
    puissance_w                    NUMERIC(12,3),             -- puissance réelle mesurée
    puissance_attendue_w           NUMERIC(12,3),             -- calculé à l'ETL, formule différenciée PAR type_equipement,
                                                                -- cf. populate_analytics() dans etl_ingestion.py :
                                                                -- panneau_solaire   : dépend de l'irradiance (~0 en éclipse, attendu)
                                                                -- autres types      : puissance_nominale_w * rendement_nominal_moyen(type),
                                                                --                     indépendant de la phase orbitale
    performance                     NUMERIC(6,4) GENERATED ALWAYS AS (
                                         ROUND(puissance_w / NULLIF(puissance_attendue_w, 0), 4)
                                     ) VIRTUAL,                 -- indicateur obligatoire : performance = puissance_reelle / puissance_attendue
    perte_production_w              NUMERIC(12,3) GENERATED ALWAYS AS (
                                         GREATEST(puissance_attendue_w - puissance_w, 0)
                                     ) VIRTUAL,                 -- indicateur : perte de production instantanée (0 si production >= attendue)

    -- Mesures capteur complémentaires (telemetrie.csv)
    temperature_c                    NUMERIC(6,2),
    rayonnement                       NUMERIC(8,4),            -- capteur local de l'équipement, bruité (diagnostic uniquement, ≠ rayonnement_solaire_w_m2)
    tension_v                         NUMERIC(6,2),
    courant_a                          NUMERIC(8,3),

    -- Alarmes (alarmes.json) : comptage par sévérité sur cette ligne (pas de répétition -> sommable)
    nb_alarmes_info                     SMALLINT NOT NULL DEFAULT 0,
    nb_alarmes_warning                   SMALLINT NOT NULL DEFAULT 0,
    nb_alarmes_critical                   SMALLINT NOT NULL DEFAULT 0,
    type_alarme                           TEXT REFERENCES dim_type_alarme (type_alarme), -- NULL si aucune alarme, sinon type de l'alarme la plus sévère du bucket

    -- Maintenance (operation_maintenance, zone conceptuelle) : indicateurs de contexte
    -- uniquement, pas d'attributs dégénérés. Voir populate_analytics() dans etl_ingestion.py
    -- pour la logique de peuplement.
    en_maintenance                        BOOLEAN NOT NULL DEFAULT false, -- true si horodatage tombe dans [date_debut, date_fin] d'une intervention pour cet équipement
    ligne_synthetique_arret                BOOLEAN NOT NULL DEFAULT false, -- true si cette ligne a été insérée par l'ETL (pas de télémétrie réelle) pour rendre visible une intervention où l'équipement était totalement à l'arrêt

    UNIQUE (equipement_key, horodatage)
);

CREATE INDEX idx_fait_production_date ON fait_production(date_key);
CREATE INDEX idx_fait_production_equipement ON fait_production(equipement_key);
CREATE INDEX idx_fait_production_site ON fait_production(site_key);
CREATE INDEX idx_fait_production_phase ON fait_production(phase);
-- Idéalement un index partiel (WHERE type_alarme IS NOT NULL / WHERE en_maintenance) :
-- non supporté par DuckDB (CREATE INDEX ... WHERE) à ce jour, donc index pleins.
CREATE INDEX idx_fait_production_type_alarme ON fait_production(type_alarme);
-- Index clé pour les questions 4 et 5 (délai entre interventions / non-maintenus depuis 90j) :
-- filtrer en_maintenance puis regrouper par épisodes contigus (LAG/gap) sur (equipement_key, horodatage).
CREATE INDEX idx_fait_production_en_maintenance ON fait_production(equipement_key, horodatage);
