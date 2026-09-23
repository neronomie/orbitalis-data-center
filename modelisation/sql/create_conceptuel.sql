-- Dialecte : DuckDB (fichier data/conceptual.db)
-- Recréation propre du schéma
DROP TABLE IF EXISTS operation_maintenance CASCADE;
DROP TABLE IF EXISTS alarme CASCADE;
DROP TABLE IF EXISTS mesure_orbitale CASCADE;
DROP TABLE IF EXISTS mesure_telemetrie CASCADE;
DROP TABLE IF EXISTS seuil_alarme CASCADE;
DROP TABLE IF EXISTS equipement CASCADE;
DROP TABLE IF EXISTS modele_equipement CASCADE;
DROP TABLE IF EXISTS site CASCADE;

DROP SEQUENCE IF EXISTS seq_mesure_telemetrie;
DROP SEQUENCE IF EXISTS seq_mesure_orbitale;
CREATE SEQUENCE seq_mesure_telemetrie START 1;
CREATE SEQUENCE seq_mesure_orbitale START 1;

-- site : sites.csv + catalogue.db/references_sites (fusionnés, même clé métier)
CREATE TABLE site (
    site_id                 TEXT PRIMARY KEY,           -- ex. SITE-001
    nom                     TEXT NOT NULL,
    orbite_type             TEXT,                        -- LEO, LEO-SSO, ...
    altitude_km             NUMERIC(8,2),                -- nullable (ex. SITE-003)
    inclination_deg         NUMERIC(6,2),                -- nullable (ex. SITE-004)
    date_mise_en_service    DATE,
    statut                  TEXT,
    description             TEXT,                        -- catalogue.db/references_sites, nullable
    capacite_max_kw         NUMERIC(8,2),                -- catalogue.db/references_sites
    zone_orbitale           TEXT                         -- catalogue.db/references_sites
);

-- modele_equipement : catalogue.db/modeles
CREATE TABLE modele_equipement (
    modele_id               TEXT PRIMARY KEY,           -- ex. MOD-PAN-100
    type_equipement          TEXT NOT NULL,               -- panneau_solaire, batterie, convertisseur_DC, onduleur, radiateur
    fabricant                TEXT,
    puissance_nominale_w      NUMERIC(10,2),
    rendement_nominal          NUMERIC(5,4),              -- ratio 0-1
    duree_vie_annees            SMALLINT,
    masse_kg                     NUMERIC(8,2)
);


CREATE TABLE equipement (
    equipement_id            TEXT PRIMARY KEY,           -- ex. EQ-001
    site_id                  TEXT NOT NULL REFERENCES site (site_id),
    type                      TEXT NOT NULL,              -- panneau_solaire, batterie, convertisseur_DC, onduleur, radiateur
    modele                    TEXT,                        -- déclaré, non fiable comme FK (cf. note ci-dessus)
    puissance_nominale_w       NUMERIC(10,2),
    date_installation           DATE,
    statut                       TEXT
);

-- seuil_alarme : catalogue.db/seuils_alarmes
CREATE TABLE seuil_alarme (
    type_alarme     TEXT PRIMARY KEY,          -- surchauffe, sous_tension, perte_puissance, surcharge, capteur_defaillant, vibration_anormale
    seuil_warning   NUMERIC(10,3),             -- nullable (ex. capteur_defaillant)
    seuil_critical  NUMERIC(10,3),             -- nullable
    unite           TEXT,                       -- nullable
    description     TEXT
);

-- mesure_telemetrie : telemetrie.csv
-- Association EQUIPEMENT-GENERE-MESURE_TELEMETRIE : EQUIPEMENT (1,1), MESURE_TELEMETRIE (0,N)
CREATE TABLE mesure_telemetrie (
    mesure_id       BIGINT PRIMARY KEY DEFAULT nextval('seq_mesure_telemetrie'),
    equipement_id   TEXT NOT NULL REFERENCES equipement (equipement_id),
    horodatage      TIMESTAMPTZ NOT NULL,       -- harmonisé UTC / ISO 8601 en zone cleaned (formats hétérogènes en brut)
    puissance_w     NUMERIC(12,3),
    temperature_c   NUMERIC(6,2),
    rayonnement     NUMERIC(8,4),               -- capteur local de l'équipement, bruité (cf. MODELISATION.md §3.2)
    tension_v       NUMERIC(6,2),
    courant_a       NUMERIC(8,3),
    UNIQUE (equipement_id, horodatage)
);

CREATE INDEX idx_mesure_telemetrie_equipement ON mesure_telemetrie(equipement_id);
CREATE INDEX idx_mesure_telemetrie_horodatage ON mesure_telemetrie(horodatage);

-- mesure_orbitale : orbite.csv
CREATE TABLE mesure_orbitale (
    mesure_orbitale_id          BIGINT PRIMARY KEY DEFAULT nextval('seq_mesure_orbitale'),
    site_id                      TEXT NOT NULL REFERENCES site (site_id),
    horodatage                    TIMESTAMPTZ NOT NULL,
    phase                          TEXT NOT NULL CHECK (phase IN ('ensoleillement', 'eclipse', 'inconnu')),
    rayonnement_solaire_w_m2        NUMERIC(10,2),
    temperature_ambiante_c           NUMERIC(6,2),
    UNIQUE (site_id, horodatage)
);

CREATE INDEX idx_mesure_orbitale_site ON mesure_orbitale(site_id);
CREATE INDEX idx_mesure_orbitale_horodatage ON mesure_orbitale(horodatage);


CREATE TABLE alarme (
    alarme_id       TEXT PRIMARY KEY,           -- ex. ALM-0001
    equipement_id   TEXT NOT NULL REFERENCES equipement (equipement_id),
    type_alarme     TEXT NOT NULL REFERENCES seuil_alarme (type_alarme),
    horodatage      TIMESTAMPTZ NOT NULL,
    severite        TEXT NOT NULL CHECK (severite IN ('info', 'warning', 'critical')),
    message         TEXT,
    acquittee       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_alarme_equipement ON alarme(equipement_id);
CREATE INDEX idx_alarme_type ON alarme(type_alarme);
CREATE INDEX idx_alarme_horodatage ON alarme(horodatage);

-- operation_maintenance : maintenance.csv
-- Association EQUIPEMENT-FAIT_L_OBJET_DE-OPERATION_MAINTENANCE : EQUIPEMENT (1,1), OPERATION_MAINTENANCE (0,N)
CREATE TABLE operation_maintenance (
    maintenance_id      TEXT PRIMARY KEY,       -- ex. MAINT-001
    equipement_id        TEXT NOT NULL REFERENCES equipement (equipement_id),
    date_debut             DATE NOT NULL,
    date_fin                 DATE,
    type_intervention          TEXT,             -- inspection, preventive, corrective, remplacement, calibration
    technicien                  TEXT,
    cout_eur                     NUMERIC(10,2),
    commentaire                   TEXT,
    CHECK (date_fin IS NULL OR date_fin >= date_debut)
);

CREATE INDEX idx_operation_maintenance_equipement ON operation_maintenance(equipement_id);
CREATE INDEX idx_operation_maintenance_date_debut ON operation_maintenance(date_debut);
