import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "data" / "raw"
CLEAN_DIR = BASE_DIR / "data" / "cleaned"

TYPES_EQUIPEMENT_VALIDES = {
    "panneau_solaire", "convertisseur_DC", "batterie", "onduleur", "radiateur",
}
STATUTS_EQUIPEMENT_VALIDES = {"operationnel", "maintenance", "degrade"}
TYPES_INTERVENTION_VALIDES = {
    "inspection", "preventive", "calibration", "corrective", "remplacement",
}
SEVERITES_VALIDES = {"info", "warning", "critical"}
TYPES_ALARME_VALIDES = {
    "sous_tension", "surchauffe", "perte_puissance",
    "surcharge", "capteur_defaillant", "vibration_anormale",
}


def _new_report():
    return {}


def _log(report, anomalie, modifiees=0, supprimees=0):
    """Cumule le nombre de lignes modifiées/supprimées pour un type d'anomalie."""
    entry = report.setdefault(anomalie, {"lignes_modifiees": 0, "lignes_supprimees": 0})
    entry["lignes_modifiees"] += int(modifiees)
    entry["lignes_supprimees"] += int(supprimees)


DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"]
TIMESTAMP_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S",
]


def _parse_dates_multi(serie, formats):
    """Essaie chaque format strict dans l'ordre (pas de devinette dayfirst/monthfirst
    qui pourrait corrompre des dates déjà valides) ; renvoie une Series datetime64 naïve."""
    resultat = pd.Series(pd.NaT, index=serie.index, dtype="datetime64[ns]")
    restant = serie.notna()
    for fmt in formats:
        if not restant.any():
            break
        candidat = pd.to_datetime(serie, format=fmt, errors="coerce")
        if candidat.notna().any() and candidat.dt.tz is not None:
            candidat = candidat.dt.tz_convert("UTC").dt.tz_localize(None)
        trouve = restant & candidat.notna()
        resultat.loc[trouve] = candidat.loc[trouve]
        restant &= ~trouve
    return resultat


def _dates_heterogenes(serie, formats=DATE_FORMATS, sortie="%Y-%m-%d"):
    """Parse une colonne de dates à formats mixtes -> ISO, renvoie (iso, masque_modifie)."""
    parsed = _parse_dates_multi(serie, formats)
    iso = parsed.dt.strftime(sortie)
    modifie = parsed.notna() & (iso != serie)
    return iso, modifie


# --------------------------------------------------------------------------- #
# sites.csv
# --------------------------------------------------------------------------- #
def nettoyer_sites(path=RAW_DIR / "sites.csv"):
    df = pd.read_csv(path)
    report = _new_report()

    # Valeurs manquantes (altitude/inclinaison) -> imputation par la médiane des autres sites
    for col in ["altitude_km", "inclination_deg"]:
        n_manquant = int(df[col].isna().sum())
        if n_manquant:
            df[col] = df[col].fillna(df[col].median())
            _log(report, "valeurs_manquantes", modifiees=n_manquant)

    return df.reset_index(drop=True), report


# --------------------------------------------------------------------------- #
# equipements.csv
# --------------------------------------------------------------------------- #
def nettoyer_equipements(path=RAW_DIR / "equipements.csv", sites_valides=None):
    df = pd.read_csv(path)
    report = _new_report()

    if sites_valides is None:
        sites_valides = set(pd.read_csv(RAW_DIR / "sites.csv")["site_id"])

    # 1. Doublons stricts (ligne entièrement dupliquée)
    n_avant = len(df)
    df = df.drop_duplicates()
    _log(report, "doublons", supprimees=n_avant - len(df))

    # 2. site_id manquant ou inconnu (clé étrangère invalide) -> ligne non rattachable, supprimée
    invalide = df["site_id"].isna() | ~df["site_id"].isin(sites_valides)
    _log(report, "cle_etrangere_invalide_site", supprimees=int(invalide.sum()))
    df = df[~invalide]

    # 3. type hors nomenclature -> ligne non exploitable, supprimée
    invalide = ~df["type"].isin(TYPES_EQUIPEMENT_VALIDES)
    _log(report, "categorie_invalide_type", supprimees=int(invalide.sum()))
    df = df[~invalide]

    # 4. statut hors nomenclature -> ligne non exploitable, supprimée
    invalide = ~df["statut"].isin(STATUTS_EQUIPEMENT_VALIDES)
    _log(report, "categorie_invalide_statut", supprimees=int(invalide.sum()))
    df = df[~invalide]

    # 5. modèle manquant -> non critique, valeur par défaut
    n_manquant = int(df["modele"].isna().sum())
    if n_manquant:
        df["modele"] = df["modele"].fillna("INCONNU")
        _log(report, "valeurs_manquantes", modifiees=n_manquant)

    # 6. puissance nominale aberrante (<= 0 W) -> remplacée par la médiane
    aberrant = df["puissance_nominale_w"] <= 0
    if aberrant.any():
        df.loc[aberrant, "puissance_nominale_w"] = df["puissance_nominale_w"].median()
        _log(report, "valeurs_aberrantes_puissance", modifiees=int(aberrant.sum()))

    # 7. formats de date hétérogènes -> ISO (JJ/MM/AAAA vu dans le brut)
    iso, modifie = _dates_heterogenes(df["date_installation"])
    df["date_installation"] = iso
    if modifie.any():
        _log(report, "formats_dates_heterogenes", modifiees=int(modifie.sum()))

    return df.reset_index(drop=True), report


# --------------------------------------------------------------------------- #
# maintenance.csv
# --------------------------------------------------------------------------- #
def nettoyer_maintenance(path=RAW_DIR / "maintenance.csv", equipements_valides=None):
    df = pd.read_csv(path)
    report = _new_report()

    if equipements_valides is None:
        equipements_valides = set(pd.read_csv(RAW_DIR / "equipements.csv")["equipement_id"])

    # 1. equipement_id manquant ou inconnu (clé étrangère invalide)
    invalide = df["equipement_id"].isna() | ~df["equipement_id"].isin(equipements_valides)
    _log(report, "cle_etrangere_invalide_equipement", supprimees=int(invalide.sum()))
    df = df[~invalide]

    # 2. formats de date hétérogènes -> ISO
    for col in ["date_debut", "date_fin"]:
        iso, modifie = _dates_heterogenes(df[col])
        df[col] = iso
        if modifie.any():
            _log(report, "formats_dates_heterogenes", modifiees=int(modifie.sum()))

    # 3. incohérence chronologique (date_fin avant date_debut)
    incoherent = pd.to_datetime(df["date_fin"]) < pd.to_datetime(df["date_debut"])
    _log(report, "incoherence_chronologique", supprimees=int(incoherent.sum()))
    df = df[~incoherent]

    # 4. coût négatif -> valeur absolue
    aberrant = df["cout_eur"] < 0
    if aberrant.any():
        df.loc[aberrant, "cout_eur"] = df.loc[aberrant, "cout_eur"].abs()
        _log(report, "valeurs_aberrantes_cout", modifiees=int(aberrant.sum()))

    # 5. type_intervention hors nomenclature -> ligne non exploitable, supprimée
    invalide = ~df["type_intervention"].isin(TYPES_INTERVENTION_VALIDES)
    _log(report, "categorie_invalide_intervention", supprimees=int(invalide.sum()))
    df = df[~invalide]

    # 6. technicien manquant -> non critique, valeur par défaut
    n_manquant = int(df["technicien"].isna().sum())
    if n_manquant:
        df["technicien"] = df["technicien"].fillna("NON_RENSEIGNE")
        _log(report, "valeurs_manquantes", modifiees=n_manquant)

    return df.reset_index(drop=True), report


# --------------------------------------------------------------------------- #
# orbite.csv
# --------------------------------------------------------------------------- #
def nettoyer_orbite(path=RAW_DIR / "orbite.csv", sites_valides=None):
    df = pd.read_csv(path)
    report = _new_report()

    if sites_valides is None:
        sites_valides = set(pd.read_csv(RAW_DIR / "sites.csv")["site_id"])

    # 1. site_id inconnu (clé étrangère invalide)
    invalide = ~df["site_id"].isin(sites_valides)
    _log(report, "cle_etrangere_invalide_site", supprimees=int(invalide.sum()))
    df = df[~invalide]

    # 2. phase orbitale inconnue -> déduite du rayonnement mesuré
    inconnue = df["phase"] == "inconnu"
    if inconnue.any():
        df.loc[inconnue, "phase"] = np.where(
            df.loc[inconnue, "rayonnement_solaire_w_m2"] > 100, "ensoleillement", "eclipse"
        )
        _log(report, "phase_orbitale_inconnue", modifiees=int(inconnue.sum()))

    # 3. rayonnement aberrant (hors [0, 1450] W/m2, constante solaire ~1361 W/m2)
    aberrant = (df["rayonnement_solaire_w_m2"] < 0) | (df["rayonnement_solaire_w_m2"] > 1450)
    if aberrant.any():
        medianes_valides = df.loc[~aberrant].groupby("phase")["rayonnement_solaire_w_m2"].median()
        df.loc[aberrant, "rayonnement_solaire_w_m2"] = df.loc[aberrant, "phase"].map(medianes_valides)
        _log(report, "valeurs_aberrantes_rayonnement", modifiees=int(aberrant.sum()))

    # 4. température manquante -> interpolation temporelle par site
    n_manquant = int(df["temperature_ambiante_c"].isna().sum())
    if n_manquant:
        df = df.sort_values(["site_id", "timestamp"])
        df["temperature_ambiante_c"] = df.groupby("site_id")["temperature_ambiante_c"].transform(
            lambda s: s.interpolate(limit_direction="both")
        )
        _log(report, "valeurs_manquantes", modifiees=n_manquant)

    return df.reset_index(drop=True), report


# --------------------------------------------------------------------------- #
# alarmes.json
# --------------------------------------------------------------------------- #
def nettoyer_alarmes(path=RAW_DIR / "alarmes.json", equipements_valides=None):
    df = pd.read_json(path)
    report = _new_report()

    if equipements_valides is None:
        equipements_valides = set(pd.read_csv(RAW_DIR / "equipements.csv")["equipement_id"])

    # 1. equipement_id manquant ou inconnu (clé étrangère invalide)
    invalide = df["equipement_id"].isna() | ~df["equipement_id"].isin(equipements_valides)
    _log(report, "cle_etrangere_invalide_equipement", supprimees=int(invalide.sum()))
    df = df[~invalide]

    # 2. doublons stricts
    n_avant = len(df)
    df = df.drop_duplicates()
    _log(report, "doublons", supprimees=n_avant - len(df))

    # 2bis. doublons de clé (même alarme_id, contenu différent -> conservation
    # de la première occurrence, la ou les suivantes sont un doublon d'injection)
    n_avant = len(df)
    df = df.drop_duplicates(subset="alarme_id", keep="first")
    _log(report, "doublons_cle_alarme_id", supprimees=n_avant - len(df))

    # 3. sévérité hors nomenclature -> alignée sur le niveau le plus proche
    invalide = ~df["severite"].isin(SEVERITES_VALIDES)
    if invalide.any():
        df.loc[invalide, "severite"] = "critical"
        _log(report, "categorie_invalide_severite", modifiees=int(invalide.sum()))

    # 4. type_alarme hors nomenclature
    invalide = ~df["type_alarme"].isin(TYPES_ALARME_VALIDES)
    if invalide.any():
        df.loc[invalide, "type_alarme"] = "inconnu"
        _log(report, "categorie_invalide_type_alarme", modifiees=int(invalide.sum()))

    return df.reset_index(drop=True), report


# --------------------------------------------------------------------------- #
# telemetrie.csv
# --------------------------------------------------------------------------- #
def nettoyer_telemetrie(path=RAW_DIR / "telemetrie.csv", equipements_valides=None):
    df = pd.read_csv(path)
    report = _new_report()

    if equipements_valides is None:
        equipements_valides = set(pd.read_csv(RAW_DIR / "equipements.csv")["equipement_id"])

    # 1. formats d'horodatage hétérogènes -> ISO 8601
    iso, modifie = _dates_heterogenes(
        df["timestamp"], formats=TIMESTAMP_FORMATS, sortie="%Y-%m-%dT%H:%M:%S"
    )
    df["timestamp"] = iso
    if modifie.any():
        _log(report, "formats_dates_heterogenes", modifiees=int(modifie.sum()))

    # 2. equipement_id inconnu (clé étrangère invalide)
    invalide = ~df["equipement_id"].isin(equipements_valides)
    _log(report, "cle_etrangere_invalide_equipement", supprimees=int(invalide.sum()))
    df = df[~invalide]

    # 3. doublons stricts (ligne entièrement dupliquée)
    n_avant = len(df)
    df = df.drop_duplicates()
    _log(report, "doublons_stricts", supprimees=n_avant - len(df))

    # 4. doublons de clé (même horodatage + équipement, valeurs de capteur différentes)
    n_avant = len(df)
    df = df.drop_duplicates(subset=["timestamp", "equipement_id"], keep="first")
    _log(report, "doublons_cle_horodatage_equipement", supprimees=n_avant - len(df))

    # 5. valeurs aberrantes (capteur défaillant : seuils physiquement impossibles)
    #    puissance > 5000 W (max théorique ~3000 W par équipement) ou température hors [-150, 100] °C
    aberrant_p = df["puissance_w"] > 5000
    aberrant_t = (df["temperature_c"] > 100) | (df["temperature_c"] < -150)
    n_lignes_aberrantes = int((aberrant_p | aberrant_t).sum())
    df.loc[aberrant_p, "puissance_w"] = np.nan
    df.loc[aberrant_t, "temperature_c"] = np.nan
    if n_lignes_aberrantes:
        _log(report, "valeurs_aberrantes_capteur", modifiees=n_lignes_aberrantes)

    # 6. valeurs manquantes (capteur en panne ponctuelle) -> interpolation temporelle par équipement
    cols_numeriques = ["puissance_w", "temperature_c", "tension_v", "courant_a"]
    n_lignes_manquantes = int(df[cols_numeriques].isna().any(axis=1).sum())
    if n_lignes_manquantes:
        df = df.sort_values(["equipement_id", "timestamp"])
        df[cols_numeriques] = df.groupby("equipement_id")[cols_numeriques].transform(
            lambda s: s.interpolate(limit_direction="both")
        )
        _log(report, "valeurs_manquantes", modifiees=n_lignes_manquantes)

    return df.reset_index(drop=True), report


# --------------------------------------------------------------------------- #
# catalogue.db (modeles, seuils_alarmes, references_sites)
# --------------------------------------------------------------------------- #
def nettoyer_modeles(df):
    report = _new_report()

    # 1. doublons stricts
    n_avant = len(df)
    df = df.drop_duplicates()
    _log(report, "doublons", supprimees=n_avant - len(df))

    # 2. type_equipement hors nomenclature -> ligne non exploitable, supprimée
    invalide = ~df["type_equipement"].isin(TYPES_EQUIPEMENT_VALIDES)
    _log(report, "categorie_invalide_type", supprimees=int(invalide.sum()))
    df = df[~invalide]

    # 3. rendement_nominal manquant -> non applicable aux radiateurs (dissipation
    #    passive, pas de rendement électrique) : laissé tel quel. Pour les autres
    #    types, imputation par la médiane du même type_equipement.
    manquant = df["rendement_nominal"].isna() & (df["type_equipement"] != "radiateur")
    if manquant.any():
        medianes = df.groupby("type_equipement")["rendement_nominal"].transform("median")
        df.loc[manquant, "rendement_nominal"] = medianes.loc[manquant]
        _log(report, "valeurs_manquantes", modifiees=int(manquant.sum()))

    # 4. puissance nominale aberrante (<= 0 W) hors radiateurs (pas de conso
    #    électrique nominale pour un radiateur passif) -> médiane du même type
    aberrant = (df["puissance_nominale_w"] <= 0) & (df["type_equipement"] != "radiateur")
    if aberrant.any():
        medianes = df.groupby("type_equipement")["puissance_nominale_w"].transform("median")
        df.loc[aberrant, "puissance_nominale_w"] = medianes.loc[aberrant]
        _log(report, "valeurs_aberrantes_puissance", modifiees=int(aberrant.sum()))

    return df.reset_index(drop=True), report


def nettoyer_seuils_alarmes(df):
    report = _new_report()

    # 1. doublons stricts
    n_avant = len(df)
    df = df.drop_duplicates()
    _log(report, "doublons", supprimees=n_avant - len(df))

    # 2. type_alarme hors nomenclature -> ligne non exploitable, supprimée
    invalide = ~df["type_alarme"].isin(TYPES_ALARME_VALIDES)
    _log(report, "categorie_invalide_type_alarme", supprimees=int(invalide.sum()))
    df = df[~invalide]

    # 3. seuils/unité manquants -> normal pour capteur_defaillant (alarme déclenchée
    #    par l'absence de remontée du capteur, pas par un franchissement de seuil
    #    numérique) ; pour tout autre type, ligne non exploitable, supprimée
    manquant = df[["seuil_warning", "seuil_critical", "unite"]].isna().any(axis=1)
    manquant_inattendu = manquant & (df["type_alarme"] != "capteur_defaillant")
    _log(report, "valeurs_manquantes_seuils", supprimees=int(manquant_inattendu.sum()))
    df = df[~manquant_inattendu]

    return df.reset_index(drop=True), report


def nettoyer_references_sites(df, sites_valides=None):
    report = _new_report()

    if sites_valides is None:
        sites_valides = set(pd.read_csv(RAW_DIR / "sites.csv")["site_id"])

    # 1. doublons stricts
    n_avant = len(df)
    df = df.drop_duplicates()
    _log(report, "doublons", supprimees=n_avant - len(df))

    # 2. site_id inconnu (clé étrangère invalide) -> ligne non rattachable, supprimée
    invalide = ~df["site_id"].isin(sites_valides)
    _log(report, "cle_etrangere_invalide_site", supprimees=int(invalide.sum()))
    df = df[~invalide]

    # 3. description manquante -> non critique, valeur par défaut
    n_manquant = int(df["description"].isna().sum())
    if n_manquant:
        df["description"] = df["description"].fillna("NON_RENSEIGNE")
        _log(report, "valeurs_manquantes", modifiees=n_manquant)

    # 4. capacité max manquante ou aberrante (<= 0 kW) -> médiane des autres sites
    aberrant = df["capacite_max_kw"].isna() | (df["capacite_max_kw"] <= 0)
    if aberrant.any():
        df.loc[aberrant, "capacite_max_kw"] = df["capacite_max_kw"].median()
        _log(report, "valeurs_aberrantes_capacite", modifiees=int(aberrant.sum()))

    return df.reset_index(drop=True), report


def nettoyer_catalogue(path=RAW_DIR / "catalogue.db", sites_valides=None):
    """Nettoie les 3 tables de catalogue.db, renvoie {table: (df, report)}."""
    con = sqlite3.connect(path)
    try:
        modeles_brut = pd.read_sql("SELECT * FROM modeles", con)
        seuils_brut = pd.read_sql("SELECT * FROM seuils_alarmes", con)
        ref_sites_brut = pd.read_sql("SELECT * FROM references_sites", con)
    finally:
        con.close()

    modeles_df, modeles_report = nettoyer_modeles(modeles_brut)
    seuils_df, seuils_report = nettoyer_seuils_alarmes(seuils_brut)
    ref_sites_df, ref_sites_report = nettoyer_references_sites(ref_sites_brut, sites_valides=sites_valides)

    return {
        "modeles": (modeles_brut, modeles_df, modeles_report),
        "seuils_alarmes": (seuils_brut, seuils_df, seuils_report),
        "references_sites": (ref_sites_brut, ref_sites_df, ref_sites_report),
    }


# --------------------------------------------------------------------------- #
# Orchestration + rapport
# --------------------------------------------------------------------------- #
def _imprimer_rapport(nom_fichier, n_avant, n_apres, report):
    print(f"\n--- {nom_fichier} : {n_avant} lignes brutes -> {n_apres} lignes nettoyées ---")
    if not report:
        print("  aucune anomalie détectée")
        return
    for anomalie, compte in report.items():
        print(
            f"  {anomalie:<40} {compte['lignes_modifiees']:>6} modifiée(s)  "
            f"{compte['lignes_supprimees']:>6} supprimée(s)"
        )


def main():
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print(" RAPPORT DE NETTOYAGE ETL - ORBITALIS")
    print("=" * 78)

    sites_df, sites_report = nettoyer_sites()
    _imprimer_rapport("sites.csv", n_avant=len(pd.read_csv(RAW_DIR / "sites.csv")),
                       n_apres=len(sites_df), report=sites_report)

    eq_df, eq_report = nettoyer_equipements(sites_valides=set(sites_df["site_id"]))
    _imprimer_rapport("equipements.csv", n_avant=len(pd.read_csv(RAW_DIR / "equipements.csv")),
                       n_apres=len(eq_df), report=eq_report)

    equipements_valides = set(eq_df["equipement_id"])

    maint_df, maint_report = nettoyer_maintenance(equipements_valides=equipements_valides)
    _imprimer_rapport("maintenance.csv", n_avant=len(pd.read_csv(RAW_DIR / "maintenance.csv")),
                       n_apres=len(maint_df), report=maint_report)

    orbite_df, orbite_report = nettoyer_orbite(sites_valides=set(sites_df["site_id"]))
    _imprimer_rapport("orbite.csv", n_avant=len(pd.read_csv(RAW_DIR / "orbite.csv")),
                       n_apres=len(orbite_df), report=orbite_report)

    alarmes_df, alarmes_report = nettoyer_alarmes(equipements_valides=equipements_valides)
    _imprimer_rapport("alarmes.json", n_avant=len(pd.read_json(RAW_DIR / "alarmes.json")),
                       n_apres=len(alarmes_df), report=alarmes_report)

    telem_df, telem_report = nettoyer_telemetrie(equipements_valides=equipements_valides)
    _imprimer_rapport("telemetrie.csv", n_avant=len(pd.read_csv(RAW_DIR / "telemetrie.csv")),
                       n_apres=len(telem_df), report=telem_report)

    catalogue = nettoyer_catalogue(sites_valides=set(sites_df["site_id"]))
    for table, (df_brut, df_propre, rep) in catalogue.items():
        _imprimer_rapport(f"catalogue.db::{table}", n_avant=len(df_brut),
                           n_apres=len(df_propre), report=rep)

    print("\n" + "-" * 78)
    total_modifiees = total_supprimees = 0
    tous_reports = [sites_report, eq_report, maint_report, orbite_report, alarmes_report, telem_report]
    tous_reports += [rep for _, _, rep in catalogue.values()]
    for report in tous_reports:
        for compte in report.values():
            total_modifiees += compte["lignes_modifiees"]
            total_supprimees += compte["lignes_supprimees"]
    print(f"TOTAL toutes sources : {total_modifiees} lignes modifiées, {total_supprimees} lignes supprimées")
    print("-" * 78)

    sites_df.to_csv(CLEAN_DIR / "sites.csv", index=False)
    eq_df.to_csv(CLEAN_DIR / "equipements.csv", index=False)
    maint_df.to_csv(CLEAN_DIR / "maintenance.csv", index=False)
    orbite_df.to_csv(CLEAN_DIR / "orbite.csv", index=False)
    alarmes_df.to_csv(CLEAN_DIR / "alarmes.csv", index=False)
    telem_df.to_csv(CLEAN_DIR / "telemetrie.csv", index=False)
    catalogue["modeles"][1].to_csv(CLEAN_DIR / "catalogue_modeles.csv", index=False)
    catalogue["seuils_alarmes"][1].to_csv(CLEAN_DIR / "catalogue_seuils_alarmes.csv", index=False)
    catalogue["references_sites"][1].to_csv(CLEAN_DIR / "catalogue_references_sites.csv", index=False)
    print(f"\nFichiers nettoyés écrits dans {CLEAN_DIR}")


if __name__ == "__main__":
    main()
