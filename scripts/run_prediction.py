import argparse
import json
import sys
from pathlib import Path

import duckdb
import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, confusion_matrix, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).resolve().parent.parent
ANALYTICS_DB = BASE_DIR / "data" / "analytics" / "analytics.db"
CONCEPTUAL_DB = BASE_DIR / "data" / "analytics" / "conceptual.db"
MODELS_DIR = BASE_DIR / "data" / "models"
FIGURES_DIR = BASE_DIR / "rapports" / "prediction"
DUCKDB_CONFIG = {"TimeZone": "UTC"}

HORIZON_H = 9
FENETRE_REFERENCE_H = 24
SEUIL_PERF_RELATIVE = 0.65
TYPES_ALARME_CIBLE = ("perte_puissance", "sous_tension", "surcharge", "surchauffe")
# Onduleurs et radiateurs : aucune télémétrie dans fait_production -> hors périmètre
TYPES_EQUIPEMENT = ("panneau_solaire", "batterie", "convertisseur_DC")

FRACTION_TRAIN = 0.70
FRACTION_VALIDATION = 0.15

COUT_FN = 2500   # dégradation non anticipée
COUT_FP = 150    # inspection inutile
COUT_TP = 300    # dégradation correctement anticipée
COUT_TN = 0

RANDOM_STATE = 42

COULEURS_MODELES = {  # palette catégorielle de référence, ordre fixe
    "regression_logistique": "#2a78d6",
    "random_forest": "#eb6834",
    "gradient_boosting": "#1baf7a",
}


# --------------------------------------------------------------------------- #
# Extraction : agrégation horaire de fait_production
# --------------------------------------------------------------------------- #
def charger_horaire(con):
    types = ", ".join(f"'{t}'" for t in TYPES_EQUIPEMENT)
    alarmes = ", ".join(f"'{t}'" for t in TYPES_ALARME_CIBLE)
    return con.execute(f"""
        WITH base AS (
            SELECT
                e.equipement_id,
                e.type_equipement,
                e.puissance_nominale_w,
                e.date_installation,
                date_trunc('hour', f.horodatage) AS heure,
                f.phase = 'ensoleillement' AND NOT f.en_maintenance AS utile,
                CASE WHEN e.type_equipement = 'batterie' THEN abs(f.puissance_w)
                     ELSE f.puissance_w END
                    / NULLIF(f.puissance_attendue_w, 0) AS perf,
                f.en_maintenance,
                f.temperature_c, f.tension_v, f.courant_a, f.temperature_ambiante_c,
                f.nb_alarmes_info, f.nb_alarmes_warning, f.nb_alarmes_critical,
                f.nb_alarmes_critical > 0 AND f.type_alarme IN ({alarmes}) AS alarme_cible
            FROM fait_production f
            JOIN dim_equipement e USING (equipement_key)
            WHERE NOT f.ligne_synthetique_arret
              AND e.type_equipement IN ({types})
        )
        SELECT
            equipement_id, type_equipement, puissance_nominale_w, date_installation, heure,
            sum(perf) FILTER (WHERE utile AND perf IS NOT NULL)       AS perf_somme,
            count(perf) FILTER (WHERE utile)                            AS perf_n,
            bool_or(en_maintenance)                                     AS en_maintenance,
            avg(temperature_c)                                          AS temp_moy,
            max(temperature_c)                                          AS temp_max,
            avg(tension_v)                                              AS tension_moy,
            min(tension_v)                                              AS tension_min,
            avg(courant_a)                                              AS courant_moy,
            avg(temperature_ambiante_c)                                 AS temp_ambiante_moy,
            sum(nb_alarmes_info)                                        AS nb_info,
            sum(nb_alarmes_warning)                                     AS nb_warning,
            sum(nb_alarmes_critical)                                    AS nb_critical,
            count(*) FILTER (WHERE alarme_cible)                        AS nb_alarme_cible
        FROM base
        GROUP BY ALL
        ORDER BY equipement_id, heure
    """).df()


def charger_maintenances(con):
    con.execute(f"ATTACH '{CONCEPTUAL_DB}' AS src (READ_ONLY)")
    df = con.execute("""
        SELECT equipement_id, CAST(date_fin AS TIMESTAMP) + INTERVAL 1 DAY AS fin_maintenance
        FROM src.operation_maintenance
        WHERE date_fin IS NOT NULL
        ORDER BY fin_maintenance
    """).df()
    con.execute("DETACH src")
    return df


# --------------------------------------------------------------------------- #
# Features (passé uniquement) + cible (futur)
# --------------------------------------------------------------------------- #
def _somme_passee(g, col, n):
    return g[col].transform(lambda s: s.rolling(n, min_periods=1).sum())


def _somme_future(g, col, n):
    # somme sur les n lignes STRICTEMENT suivantes ; NaN si la fenêtre est incomplète
    return g[col].transform(lambda s: s[::-1].rolling(n, min_periods=n).sum()[::-1].shift(-1))


def construire_jeu(horaire, maintenances):
    df = horaire.sort_values(["equipement_id", "heure"]).reset_index(drop=True)
    df["t_reference"] = df["heure"] + pd.Timedelta(hours=1)
    df["perf_somme"] = df["perf_somme"].fillna(0.0)
    df["perf_h"] = df["perf_somme"] / df["perf_n"].replace(0, np.nan)
    g = df.groupby("equipement_id", sort=False)

    # Référence propre à l'équipement : médiane des perfs horaires hors éclipse
    df["reference"] = g["perf_h"].transform(
        lambda s: s.rolling(FENETRE_REFERENCE_H, min_periods=FENETRE_REFERENCE_H // 2).median()
    )

    for n in (3, 9, 24):
        somme = _somme_passee(g, "perf_somme", n)
        effectif = _somme_passee(g, "perf_n", n)
        df[f"perf_{n}h"] = somme / effectif.replace(0, np.nan)
        df[f"perf_{n}h_rel"] = df[f"perf_{n}h"] / df["reference"]
    df["perf_std_24h_rel"] = g["perf_h"].transform(
        lambda s: s.rolling(24, min_periods=6).std()
    ) / df["reference"]
    df["tendance_perf"] = df["perf_9h_rel"] - df["perf_24h_rel"]

    for col, agg, n in [
        ("temp_moy", "mean", 9), ("temp_max", "max", 24),
        ("tension_moy", "mean", 9), ("tension_min", "min", 24),
        ("courant_moy", "mean", 9), ("temp_ambiante_moy", "mean", 9),
    ]:
        df[f"{col}_{n}h"] = g[col].transform(lambda s: getattr(s.rolling(n, min_periods=1), agg)())

    for col in ("nb_info", "nb_warning", "nb_critical", "nb_alarme_cible"):
        df[f"{col}_24h"] = _somme_passee(g, col, 24)
    df["nb_critical_72h"] = _somme_passee(g, "nb_critical", 72)

    df["age_jours"] = (df["t_reference"] - pd.to_datetime(df["date_installation"])).dt.days
    df = pd.merge_asof(
        df.sort_values("t_reference"), maintenances,
        left_on="t_reference", right_on="fin_maintenance",
        by="equipement_id", direction="backward",
    )
    df["jours_depuis_maintenance"] = (
        (df["t_reference"] - df["fin_maintenance"]).dt.total_seconds() / 86400
    ).fillna(df["age_jours"])  # jamais maintenu -> ancienneté depuis l'installation

    for t in TYPES_EQUIPEMENT:
        df[f"type_{t}"] = (df["type_equipement"] == t).astype(int)

    # Cible
    df = df.sort_values(["equipement_id", "heure"]).reset_index(drop=True)
    g = df.groupby("equipement_id", sort=False)
    perf_future = _somme_future(g, "perf_somme", HORIZON_H) / _somme_future(
        g, "perf_n", HORIZON_H).replace(0, np.nan)
    alarme_future = _somme_future(g, "nb_alarme_cible", HORIZON_H)
    df["ratio_perf_futur"] = perf_future / df["reference"]
    df["cible_perf"] = (df["ratio_perf_futur"] < SEUIL_PERF_RELATIVE).astype(int)
    df["cible_alarme"] = (alarme_future > 0).astype(int)
    df["cible"] = (df["cible_perf"] | df["cible_alarme"]).astype(int)
    df["cible_disponible"] = alarme_future.notna()
    return df


FEATURES = [
    "perf_3h_rel", "perf_9h_rel", "perf_24h_rel", "perf_std_24h_rel", "tendance_perf",
    "temp_moy_9h", "temp_max_24h", "tension_moy_9h", "tension_min_24h",
    "courant_moy_9h", "temp_ambiante_moy_9h",
    "nb_info_24h", "nb_warning_24h", "nb_critical_24h", "nb_alarme_cible_24h",
    "nb_critical_72h", "jours_depuis_maintenance", "age_jours", "puissance_nominale_w",
    *[f"type_{t}" for t in TYPES_EQUIPEMENT],
]


def observations_valides(df):
    # Observation exploitable : référence connue, pas en maintenance à t
    return df[df["reference"].notna() & (df["reference"] > 0) & ~df["en_maintenance"]]


def decouper_temporellement(obs):
    heures = np.sort(obs["t_reference"].unique())
    coupure_val = heures[int(len(heures) * FRACTION_TRAIN)]
    coupure_test = heures[int(len(heures) * (FRACTION_TRAIN + FRACTION_VALIDATION))]
    purge = pd.Timedelta(hours=HORIZON_H)
    t = obs["t_reference"]
    train = obs[t <= coupure_val - purge]
    val = obs[(t > coupure_val) & (t <= coupure_test - purge)]
    test = obs[t > coupure_test]
    return train, val, test


# --------------------------------------------------------------------------- #
# Modèles, coût métier, seuil
# --------------------------------------------------------------------------- #
def creer_modeles():
    return {
        "regression_logistique": make_pipeline(
            SimpleImputer(strategy="median"), StandardScaler(),
            LogisticRegression(max_iter=2000, class_weight="balanced"),
        ),
        "random_forest": make_pipeline(
            SimpleImputer(strategy="median"),
            RandomForestClassifier(
                n_estimators=300, min_samples_leaf=20, class_weight="balanced_subsample",
                n_jobs=-1, random_state=RANDOM_STATE,
            ),
        ),
        "gradient_boosting": HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.05, class_weight="balanced",
            random_state=RANDOM_STATE,
        ),
    }


def cout_metier(y, pred):
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return tp * COUT_TP + fp * COUT_FP + fn * COUT_FN + tn * COUT_TN


SEUILS = np.round(np.arange(0.0, 1.0001, 0.01), 2)


def courbe_cout(y, proba):
    return np.array([cout_metier(y, (proba >= s).astype(int)) for s in SEUILS])


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #
def _style_axes(ax):
    for cote in ("top", "right"):
        ax.spines[cote].set_visible(False)
    ax.spines["left"].set_color("#b5b4ae")
    ax.spines["bottom"].set_color("#b5b4ae")
    ax.tick_params(colors="#52514e")
    ax.grid(axis="y", color="#e6e5e0", linewidth=0.8)
    ax.set_axisbelow(True)


def figure_cout_seuil(courbes, retenu, seuil):
    fig, ax = plt.subplots(figsize=(9, 5))
    for nom, couts in courbes.items():
        ax.plot(SEUILS, couts / 1000, color=COULEURS_MODELES[nom], linewidth=2,
                label=nom.replace("_", " "))
    ax.axvline(seuil, color="#52514e", linestyle="--", linewidth=1)
    ax.annotate(f"seuil retenu = {seuil:.2f}\n({retenu.replace('_', ' ')})",
                xy=(seuil, courbes[retenu].min() / 1000), xytext=(10, 25),
                textcoords="offset points", color="#0b0b0b", fontsize=9)
    ax.set_xlabel("Seuil de décision (probabilité)")
    ax.set_ylabel("Coût métier total (k€)")
    ax.set_title("Coût métier en fonction du seuil - jeu de validation", loc="left")
    ax.legend(frameon=False)
    _style_axes(ax)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "cout_seuil_validation.png", dpi=150)
    plt.close(fig)


def figure_matrices(matrices, retenu, seuil):
    fig, axes = plt.subplots(1, len(matrices), figsize=(5 * len(matrices), 4.4))
    for ax, (jeu, cm) in zip(axes, matrices.items()):
        ax.imshow(cm, cmap="Blues")
        seuil_texte = cm.max() / 2
        for i in range(2):
            for j in range(2):
                ax.text(j, i, f"{cm[i, j]:,}".replace(",", " "), ha="center", va="center",
                        fontsize=13, color="white" if cm[i, j] > seuil_texte else "#0b0b0b")
        ax.set_xticks([0, 1], ["pas d'inspection", "inspection"])
        ax.set_yticks([0, 1], ["pas de dégradation", "dégradation"])
        ax.set_xlabel("Décision du modèle")
        ax.set_ylabel("Réalité")
        ax.set_title(f"{jeu} - coût {cout_depuis_matrice(cm):,} €".replace(",", " "), loc="left")
    fig.suptitle(f"Matrices de confusion - {retenu.replace('_', ' ')}, seuil {seuil:.2f}",
                 x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "matrices_confusion.png", dpi=150)
    plt.close(fig)


def cout_depuis_matrice(cm):
    (tn, fp), (fn, tp) = cm
    return int(tp * COUT_TP + fp * COUT_FP + fn * COUT_FN + tn * COUT_TN)


def figure_importances(importances, retenu):
    top = importances.head(15)[::-1]
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.barh(top.index, top["moyenne"], xerr=top["ecart_type"], color="#2a78d6",
            height=0.6, error_kw={"ecolor": "#52514e", "linewidth": 1})
    ax.set_xlabel("Baisse de PR-AUC quand la variable est permutée (validation)")
    ax.set_title(f"Importance des variables - {retenu.replace('_', ' ')}", loc="left")
    _style_axes(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#e6e5e0", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "importance_variables.png", dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Publication des scores courants dans analytics.db
# --------------------------------------------------------------------------- #
def publier_scores(df, modele, nom_modele, seuil):
    dernier = observations_valides(df).sort_values("t_reference").groupby("equipement_id").tail(1)
    scores = dernier[["equipement_id", "t_reference"]].copy()
    scores["score_risque"] = modele.predict_proba(dernier[FEATURES])[:, 1]
    scores["seuil_decision"] = seuil
    scores["a_risque"] = scores["score_risque"] >= seuil
    scores["modele"] = nom_modele
    scores["horizon_h"] = HORIZON_H

    con = duckdb.connect(str(ANALYTICS_DB), config=DUCKDB_CONFIG)
    con.register("scores_df", scores)
    con.execute("CREATE OR REPLACE TABLE risque_equipement AS SELECT * FROM scores_df")
    con.close()
    return scores


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _repartition(nom, d):
    return (f"  {nom:<11} {len(d):>6} obs  {d['t_reference'].min():%Y-%m-%d %H:%M} -> "
            f"{d['t_reference'].max():%Y-%m-%d %H:%M}  taux de positifs {d['cible'].mean():.1%}")


def main():
    global ANALYTICS_DB, CONCEPTUAL_DB
    parser = argparse.ArgumentParser(description="Entraînement et scoring du modèle de maintenance prédictive")
    parser.add_argument("--analytics-db", type=Path, default=ANALYTICS_DB)
    parser.add_argument("--conceptual-db", type=Path, default=CONCEPTUAL_DB)
    args = parser.parse_args()
    ANALYTICS_DB, CONCEPTUAL_DB = args.analytics_db, args.conceptual_db
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print(" MAINTENANCE PRÉDICTIVE - ORBITALIS")
    print("=" * 78)

    con = duckdb.connect(str(ANALYTICS_DB), read_only=True, config=DUCKDB_CONFIG)
    horaire = charger_horaire(con)
    maintenances = charger_maintenances(con)
    con.close()

    df = construire_jeu(horaire, maintenances)
    obs = observations_valides(df)
    obs = obs[obs["cible_disponible"]]
    train, val, test = decouper_temporellement(obs)

    print(f"\n--- Jeu de données (équipement x heure, horizon {HORIZON_H} h) ---")
    print(f"  {obs['equipement_id'].nunique()} équipements ({', '.join(TYPES_EQUIPEMENT)})")
    print(f"  positifs dus à la performance : {obs['cible_perf'].mean():.1%}  |  "
          f"dus aux alarmes critiques : {obs['cible_alarme'].mean():.1%}")
    for nom, d in [("train", train), ("validation", val), ("test", test)]:
        print(_repartition(nom, d))

    X_train, y_train = train[FEATURES], train["cible"]
    X_val, y_val = val[FEATURES], val["cible"]
    X_test, y_test = test[FEATURES], test["cible"]

    print("\n--- Comparaison des modèles (validation) ---")
    print(f"  {'modèle':<24}{'ROC-AUC':>9}{'PR-AUC':>9}{'seuil*':>8}{'coût* (€)':>12}")
    modeles, courbes, resultats = creer_modeles(), {}, {}
    for nom, modele in modeles.items():
        modele.fit(X_train, y_train)
        proba = modele.predict_proba(X_val)[:, 1]
        courbes[nom] = courbe_cout(y_val, proba)
        i = int(np.argmin(courbes[nom]))
        resultats[nom] = {
            "roc_auc_val": roc_auc_score(y_val, proba),
            "pr_auc_val": average_precision_score(y_val, proba),
            "seuil": float(SEUILS[i]),
            "cout_val": int(courbes[nom][i]),
        }
        r = resultats[nom]
        print(f"  {nom:<24}{r['roc_auc_val']:>9.3f}{r['pr_auc_val']:>9.3f}"
              f"{r['seuil']:>8.2f}{r['cout_val']:>12,}".replace(",", " "))

    # Modèle ET seuil choisis sur la validation uniquement
    retenu = min(resultats, key=lambda n: resultats[n]["cout_val"])
    seuil = resultats[retenu]["seuil"]
    modele = modeles[retenu]
    print(f"\n  -> modèle retenu : {retenu}, seuil = {seuil:.2f} (coût validation minimal)")

    # Application UNIQUE du seuil figé sur le test
    pred_val = (modele.predict_proba(X_val)[:, 1] >= seuil).astype(int)
    proba_test = modele.predict_proba(X_test)[:, 1]
    pred_test = (proba_test >= seuil).astype(int)
    cm_val = confusion_matrix(y_val, pred_val, labels=[0, 1])
    cm_test = confusion_matrix(y_test, pred_test, labels=[0, 1])
    cout_test = cout_depuis_matrice(cm_test)
    n_pos, n_neg = int(y_test.sum()), int((1 - y_test).sum())
    cout_rien = n_pos * COUT_FN
    cout_tout = n_pos * COUT_TP + n_neg * COUT_FP

    print(f"\n--- Évaluation finale sur le test (seuil {seuil:.2f} appliqué une seule fois) ---")
    print(f"  ROC-AUC {roc_auc_score(y_test, proba_test):.3f}  |  "
          f"PR-AUC {average_precision_score(y_test, proba_test):.3f}")
    print("  Matrice de confusion (lignes = réalité, colonnes = décision) :")
    print(f"    {'':<20}{'pas inspect.':>14}{'inspection':>12}")
    print(f"    {'pas de dégradation':<20}{cm_test[0, 0]:>14}{cm_test[0, 1]:>12}")
    print(f"    {'dégradation':<20}{cm_test[1, 0]:>14}{cm_test[1, 1]:>12}")
    print(f"  Coût modèle             : {cout_test:>10,} €".replace(",", " "))
    print(f"  Coût « ne rien faire »  : {cout_rien:>10,} €".replace(",", " "))
    print(f"  Coût « tout inspecter » : {cout_tout:>10,} €".replace(",", " "))
    print(f"  Économie vs ne rien faire : {cout_rien - cout_test:,} €".replace(",", " "))

    imp = permutation_importance(
        modele, X_val, y_val, scoring="average_precision", n_repeats=5,
        random_state=RANDOM_STATE, n_jobs=-1,
    )
    importances = pd.DataFrame(
        {"moyenne": imp.importances_mean, "ecart_type": imp.importances_std}, index=FEATURES,
    ).sort_values("moyenne", ascending=False)
    print("\n--- Variables les plus importantes (permutation, validation) ---")
    for var, ligne in importances.head(10).iterrows():
        print(f"  {var:<26} {ligne['moyenne']:+.4f} ± {ligne['ecart_type']:.4f}")

    figure_cout_seuil(courbes, retenu, seuil)
    figure_matrices({"Validation": cm_val, "Test": cm_test}, retenu, seuil)
    figure_importances(importances, retenu)

    joblib.dump(modele, MODELS_DIR / "modele_risque.joblib")
    meta = {
        "modele": retenu,
        "seuil_decision": seuil,
        "horizon_h": HORIZON_H,
        "features": FEATURES,
        "types_equipement": list(TYPES_EQUIPEMENT),
        "cible": {
            "seuil_perf_relative": SEUIL_PERF_RELATIVE,
            "fenetre_reference_h": FENETRE_REFERENCE_H,
            "types_alarme_critical": list(TYPES_ALARME_CIBLE),
        },
        "couts_eur": {"fn": COUT_FN, "fp": COUT_FP, "tp": COUT_TP, "tn": COUT_TN},
        "split": {
            nom: [str(d["t_reference"].min()), str(d["t_reference"].max())]
            for nom, d in [("train", train), ("validation", val), ("test", test)]
        },
        "validation": resultats,
        "test": {"matrice_confusion": cm_test.tolist(), "cout_eur": cout_test,
                 "cout_ne_rien_faire_eur": cout_rien, "cout_tout_inspecter_eur": cout_tout},
        "importances": importances["moyenne"].round(5).to_dict(),
    }
    (MODELS_DIR / "modele_risque.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    scores = publier_scores(df, modele, retenu, seuil)
    print(f"\n--- Scores de risque courants ({scores['t_reference'].max():%Y-%m-%d %H:%M}) ---")
    print(f"  {int(scores['a_risque'].sum())} / {len(scores)} équipements à risque "
          f"-> table risque_equipement")

    print(f"\nModèle : {MODELS_DIR / 'modele_risque.joblib'}")
    print(f"Figures : {FIGURES_DIR}")


if __name__ == "__main__":
    try:
        main()
    except duckdb.IOException as exc:
        print(f"\nBase DuckDB inaccessible (verrouillée par un autre process ?) : {exc}",
              file=sys.stderr)
        sys.exit(1)
