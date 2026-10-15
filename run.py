import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Ordre imposé : la pipeline recrée analytics.db (et supprime donc la table
# risque_equipement), la prédiction doit repasser derrière pour que l'app
# Streamlit retrouve ses scores.
ETAPES = [
    ("pipeline ETL (raw -> cleaned -> conceptual.db -> analytics.db)", "scripts.run_pipeline"),
    ("prédiction (modèle + scores de risque)", "scripts.run_prediction"),
]


def main():
    debut = time.monotonic()
    for i, (libelle, module) in enumerate(ETAPES, 1):
        print("=" * 78)
        print(f" ÉTAPE {i}/{len(ETAPES)} : {libelle}")
        print("=" * 78, flush=True)
        retour = subprocess.run([sys.executable, "-m", module], cwd=BASE_DIR)
        if retour.returncode != 0:
            print(f"\nÉchec de l'étape {i} ({module}), code {retour.returncode}", file=sys.stderr)
            sys.exit(retour.returncode)
        print()

    print(f"Tout est à jour en {time.monotonic() - debut:.1f}s. "
          f"Lancer l'application : uv run streamlit run app.py")


if __name__ == "__main__":
    main()
