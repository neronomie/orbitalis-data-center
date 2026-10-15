import sys
import time

import etl_ingestion
import etl_nettoyage


def main():
    debut = time.monotonic()

    print("#" * 78)
    print("# ÉTAPE 1/2 : NETTOYAGE (data/raw -> data/cleaned)")
    print("#" * 78)
    etl_nettoyage.main()

    print("\n" + "#" * 78)
    print("# ÉTAPE 2/2 : INGESTION (data/cleaned -> conceptual.db -> analytics.db)")
    print("#" * 78)
    etl_ingestion.main()

    duree = time.monotonic() - debut
    print(f"\nPipeline terminée en {duree:.1f}s")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nÉchec de la pipeline : {exc}", file=sys.stderr)
        sys.exit(1)
