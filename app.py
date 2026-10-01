import json
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
ANALYTICS_DB = BASE_DIR / "data" / "analytics" / "analytics.db"
CLEANED = BASE_DIR / "data" / "cleaned"
MODELE_JSON = BASE_DIR / "data" / "models" / "modele_risque.json"
DECISIONS_CSV = BASE_DIR / "data" / "decisions.csv"
JOURS_RECENTS = 7

st.set_page_config(page_title="Pilotage maintenance", layout="wide")


@st.cache_data
def charger():
    meta = json.loads(MODELE_JSON.read_text())
    con = duckdb.connect(str(ANALYTICS_DB), read_only=True)
    scores = con.execute("SELECT equipement_id, score_risque FROM risque_equipement").df()
    production = con.execute("""
        SELECT e.equipement_id, f.horodatage, f.puissance_w, f.puissance_attendue_w, f.performance
        FROM fait_production f JOIN dim_equipement e USING (equipement_key)
    """).df()
    con.close()
    equipements = pd.read_csv(CLEANED / "equipements.csv")
    alarmes = pd.read_csv(CLEANED / "alarmes.csv", parse_dates=["horodatage"])
    alarmes["horodatage"] = alarmes["horodatage"].dt.tz_localize(None)
    maintenance = pd.read_csv(CLEANED / "maintenance.csv", parse_dates=["date_debut", "date_fin"])
    orbite = pd.read_csv(CLEANED / "orbite.csv", parse_dates=["horodatage"])
    orbite["horodatage"] = orbite["horodatage"].dt.tz_localize(None)
    return meta, scores, production, equipements, alarmes, maintenance, orbite


meta, scores, production, equipements, alarmes, maintenance, orbite = charger()
couts = meta["couts_eur"]
t_ref = production["horodatage"].max()
debut_recent = t_ref - pd.Timedelta(days=JOURS_RECENTS)

perf = (production[production["horodatage"] > t_ref - pd.Timedelta(days=1)]
        .groupby("equipement_id")["performance"].mean().rename("derniere_performance"))
derniere_maint = maintenance.groupby("equipement_id")["date_fin"].max()
jours_maint = ((t_ref - derniere_maint).dt.days).rename("jours_depuis_maintenance")
nb_alarmes = (alarmes[alarmes["horodatage"] > debut_recent]
              .groupby("equipement_id").size().rename("nb_alarmes_recentes"))

table = (equipements[["equipement_id", "site_id", "type"]]
         .merge(scores, on="equipement_id", how="left")
         .join(perf, on="equipement_id").join(jours_maint, on="equipement_id")
         .join(nb_alarmes, on="equipement_id"))
table["nb_alarmes_recentes"] = table["nb_alarmes_recentes"].fillna(0).astype(int)

st.sidebar.header("Filtres et paramètres")
sites = st.sidebar.multiselect("Site", sorted(table["site_id"].unique()))
types = st.sidebar.multiselect("Type d'équipement", sorted(table["type"].unique()))
niveaux = st.sidebar.multiselect("Niveau de risque", ["élevé", "moyen", "faible"])
seuil_metier = meta["seuil_decision"]
seuil = st.sidebar.slider("Seuil de décision", 0.0, 1.0, float(seuil_metier), 0.01)
if seuil != seuil_metier:
    st.sidebar.warning(f"Seuil modifié temporairement (seuil métier retenu : {seuil_metier}).")

table["niveau_risque"] = pd.cut(table["score_risque"], [-1, seuil / 2, seuil, 2],
                                labels=["faible", "moyen", "élevé"], right=False).astype(str)
filtre = table
if sites:
    filtre = filtre[filtre["site_id"].isin(sites)]
if types:
    filtre = filtre[filtre["type"].isin(types)]
if niveaux:
    filtre = filtre[filtre["niveau_risque"].isin(niveaux)]
ids = filtre["equipement_id"]

onglet_dash, onglet_liste, onglet_fiche, onglet_decision = st.tabs(
    ["Vue d'ensemble", "Équipements à risque", "Fiche équipement", "Aide à la décision"])

with onglet_dash:
    a_risque = filtre[filtre["niveau_risque"] == "élevé"]
    prod = production[production["equipement_id"].isin(ids) & (production["horodatage"] > debut_recent)]
    critiques = alarmes[alarmes["equipement_id"].isin(ids) & (alarmes["severite"] == "critical")
                        & ~alarmes["acquittee"]]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Équipements à risque", len(a_risque))
    c2.metric(f"Production {JOURS_RECENTS} j (kWh)", f"{prod['puissance_w'].sum() * 5 / 60 / 1000:,.0f}")
    c3.metric(f"Production attendue {JOURS_RECENTS} j (kWh)",
              f"{prod['puissance_attendue_w'].sum() * 5 / 60 / 1000:,.0f}")
    c4.metric("Alarmes critiques non acquittées", len(critiques))
    g1, g2 = st.columns(2)
    g1.subheader("Risques par site")
    g1.bar_chart(filtre.groupby(["site_id", "niveau_risque"]).size().unstack(fill_value=0))
    g2.subheader("Risques par type")
    g2.bar_chart(filtre.groupby(["type", "niveau_risque"]).size().unstack(fill_value=0))

with onglet_liste:
    couleurs = {"élevé": "background-color: #f8b4b4", "moyen": "background-color: #fde68a",
                "faible": "background-color: #bbf7d0"}
    st.dataframe(
        filtre.sort_values("score_risque", ascending=False).style.apply(
            lambda ligne: [couleurs.get(ligne["niveau_risque"], "")] * len(ligne), axis=1),
        hide_index=True, width="stretch")

with onglet_fiche:
    eq = st.selectbox("Équipement", ids, key="fiche")
    if eq:
        ligne = table[table["equipement_id"] == eq].iloc[0]
        st.write(f"Site {ligne['site_id']} · {ligne['type']} · score {ligne['score_risque']:.3f} "
                 f"({ligne['niveau_risque']})")
        st.subheader("Historique de performance")
        st.line_chart(production[production["equipement_id"] == eq]
                      .set_index("horodatage")["performance"].resample("1h").mean())
        st.subheader("Historique des alarmes")
        st.dataframe(alarmes[alarmes["equipement_id"] == eq].sort_values("horodatage", ascending=False),
                     hide_index=True)
        st.subheader("Historique des maintenances")
        st.dataframe(maintenance[maintenance["equipement_id"] == eq].sort_values("date_debut", ascending=False),
                     hide_index=True)
        st.subheader("Variables les plus contributives (importance globale du modèle)")
        st.bar_chart(pd.Series(meta["importances"]).sort_values(ascending=False).head(10))
        st.subheader("Contexte orbital récent (48 h)")
        orb = orbite[(orbite["site_id"] == ligne["site_id"])
                     & (orbite["horodatage"] > t_ref - pd.Timedelta(hours=48))]
        st.line_chart(orb.set_index("horodatage")["rayonnement_solaire_w_m2"])
        st.write(orb["phase"].value_counts(normalize=True).rename("part du temps"))

with onglet_decision:
    eq = st.selectbox("Équipement", ids, key="decision")
    if eq:
        p = float(table.loc[table["equipement_id"] == eq, "score_risque"].iloc[0])
        cout_inspection = p * couts["tp"] + (1 - p) * couts["fp"]
        cout_rien = p * couts["fn"] + (1 - p) * couts["tn"]
        st.write(f"Score de risque : {p:.3f} (seuil {seuil:.2f})")
        c1, c2 = st.columns(2)
        c1.metric("Coût estimé : planifier une inspection", f"{cout_inspection:,.0f} €")
        c2.metric("Coût estimé : ne rien faire", f"{cout_rien:,.0f} €")
        decision = st.radio("Décision", ["Planifier une inspection", "Ne rien faire"])
        if st.button("Enregistrer la décision"):
            pd.DataFrame([{
                "date": datetime.now().isoformat(timespec="seconds"), "equipement_id": eq,
                "score_risque": round(p, 4), "seuil": seuil, "decision": decision,
                "cout_estime_eur": round(cout_inspection if decision == "Planifier une inspection" else cout_rien),
            }]).to_csv(DECISIONS_CSV, mode="a", header=not DECISIONS_CSV.exists(), index=False)
            st.success("Décision enregistrée.")
    st.subheader("Historique des décisions")
    if DECISIONS_CSV.exists():
        st.dataframe(pd.read_csv(DECISIONS_CSV).iloc[::-1], hide_index=True)
