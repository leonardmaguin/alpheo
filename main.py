"""
Orchestrateur principal du Job Scanner.

Usage:
    python main.py                    # scan des dernières 24h
    python main.py --days 7           # scan des 7 derniers jours
    python main.py --from-day 15 --days 7  # fenêtre glissante J-15 à J-8
    python main.py --test             # test avec offres fictives (sans Gmail)
    python main.py --no-enrich        # sans enrichissement Firecrawl
"""

import os
import sys
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).parent / ".env")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from gmail_collector import collect_jobs_from_gmail
from scorer import score_all_jobs, PRE_ENRICHMENT_THRESHOLD, ENRICHMENT_THRESHOLD
from job_api import enrich_jobs_with_api
from sheets_output import write_jobs_to_sheets, get_sheets_service, get_or_create_spreadsheet, COLUMNS, TAB_NAME, job_to_row, job_to_p2_updates, _col_letter

SPREADSHEET_ID = os.environ.get("GOOGLE_SPREADSHEET_ID", "")


def run_test_mode():
    print("\n=== MODE TEST ===\n")
    return [
        {
            "id": "test_1", "source": "test",
            "title": "Head of Operations", "company": "Mobility Scale-up",
            "location": "Brussels, Belgium", "salary": "110-130k€",
            "description": "Série B, 80 personnes. Cherche Head of Ops pour piloter une équipe de 15, déployer des OKRs et automatiser nos process. Stack: SQL, Notion, Zapier. Mobilité durable.",
            "url": "https://linkedin.com/jobs/view/test1", "collected_at": "",
        },
        {
            "id": "test_2", "source": "test",
            "title": "Senior Software Engineer", "company": "Big Corp SA",
            "location": "Paris, France", "salary": "70-85k€",
            "description": "Développeur Python senior pour rejoindre notre équipe backend. 5 ans d'expérience requis en Django/FastAPI.",
            "url": "https://linkedin.com/jobs/view/test2", "collected_at": "",
        },
        {
            "id": "test_3", "source": "test",
            "title": "Chief of Staff", "company": "AI Startup (confidentiel)",
            "location": "Hybrid - Belgium", "salary": "",
            "description": "Nous cherchons un bras droit du CEO pour une startup IA en hypercroissance. Profil ops + product, à l'aise avec la data et les outils IA.",
            "url": "https://linkedin.com/jobs/view/test3", "collected_at": "",
        },
        {
            "id": "test_4", "source": "test",
            "title": "Country Services Manager - Belgium", "company": "Eaton",
            "location": "Bruxelles", "salary": "",
            "description": "",  # description vide — pour tester la passe 1 → enrichissement → passe 2
            "url": "https://linkedin.com/jobs/view/test4", "collected_at": "",
        },
    ]


def run_rescore_p2(min_score: int, enrich_limit: int = None, force: bool = False, only_id: str = ""):
    """
    Récupère depuis le Sheets les lignes éligibles, les enrichit et les rescore en P2.
    only_id : si fourni, cible uniquement la ligne avec cet ID LinkedIn (implique force=True).
    """
    service = get_sheets_service()
    sid = get_or_create_spreadsheet(service, SPREADSHEET_ID)

    if only_id:
        label = f"ID LinkedIn = {only_id}"
    elif force:
        label = "avec ou sans P2"
    else:
        label = "sans P2"
    print(f"\n[Rescore P2] Lecture du Sheets (Score P1 >= {min_score}, {label})...")
    result = service.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB_NAME}!A2:AC5000"
    ).execute()
    rows = result.get("values", [])

    def col(name):
        idx = COLUMNS.index(name)
        return lambda row: row[idx] if idx < len(row) else ""

    get_score_p1   = col("Score P1 /10")
    get_date_p2    = col("Date scoring P2")
    get_url        = col("URL")
    get_title      = col("Titre")
    get_company    = col("Entreprise")
    get_location   = col("Localisation")
    get_salary     = col("Salaire affiché")
    get_email_date = col("Date offre")

    from sheets_output import _linkedin_id

    to_rescore = []
    for i, row in enumerate(rows):
        score_p1_raw = get_score_p1(row)
        date_p2 = get_date_p2(row)
        url = get_url(row)

        if only_id:
            if _linkedin_id(url) != only_id:
                continue
        else:
            if not score_p1_raw:
                continue
            if date_p2 and not force:
                continue
            try:
                score_p1 = int(score_p1_raw)
            except ValueError:
                continue
            if score_p1 < min_score:
                continue
        try:
            score_p1 = int(score_p1_raw) if score_p1_raw else 0
        except ValueError:
            score_p1 = 0
        to_rescore.append({
            "sheet_row": i + 2,  # 1-indexed, +1 pour header
            "id": f"rescore_{i}",
            "score_p1": score_p1,
            "url": url,
            "title": get_title(row),
            "company": get_company(row),
            "location": get_location(row),
            "salary": get_salary(row),
            "email_date": get_email_date(row),
            "description": "",
            "source": "sheets_rescore",
            "collected_at": "",
        })

    if not to_rescore:
        print("[Rescore P2] Aucune offre à rescorer.")
        return

    print(f"[Rescore P2] {len(to_rescore)} offre(s) à traiter :")
    for j in to_rescore:
        print(f"  ligne {j['sheet_row']} | {j['score_p1']}/10 | {j['title']} @ {j['company']}")

    # Enrichissement
    print(f"\n[Rescore P2] Enrichissement ({len(to_rescore)} offres)...")
    to_rescore = enrich_jobs_with_api(
        to_rescore,
        max_jobs=enrich_limit,
        sheets_service=service,
        spreadsheet_id=sid,
    )

    has_desc = [j for j in to_rescore if len(j.get("description", "")) > 150]
    no_desc  = [j for j in to_rescore if len(j.get("description", "")) <= 150]
    if no_desc:
        print(f"[Rescore P2] {len(no_desc)} offre(s) sans description (pas d'appel P2) :")
        for j in no_desc:
            print(f"  {j['title']} @ {j['company']}")

    if not has_desc:
        print("[Rescore P2] Aucune description disponible — arrêt.")
        return

    # Scoring P2
    print(f"\n[Rescore P2] Scoring P2 sur {len(has_desc)} offre(s)...")
    from scorer import score_pass2
    rescored = score_pass2(has_desc, verbose=True)
    p2_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    # Index url → sheet_row pour mise à jour
    url_to_row = {j["url"]: j["sheet_row"] for j in to_rescore}

    last_col = _col_letter(len(COLUMNS) - 1)
    updated = 0
    for scored in rescored:
        d = scored.to_dict()
        d["scored_p2"] = True
        d["date_scoring_p2"] = p2_date
        url = d.get("url", "")
        row_num = url_to_row.get(url)
        if not row_num:
            print(f"[Rescore P2] URL introuvable dans le Sheets : {url}")
            continue
        if d.get("p2_failed"):
            print(f"[Rescore P2] Ligne {row_num} ignorée (erreur P2) — {d['title']} @ {d['company']}")
            continue
        updates = job_to_p2_updates(d, row_num)
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()
        reco = d.get("recommendation", "")
        print(f"[Rescore P2] Ligne {row_num} mise à jour — {d['title']} @ {d['company']} → {d.get('score_total','?')}/10 {reco}")
        updated += 1

    print(f"\n[Rescore P2] {updated} ligne(s) mises à jour dans le Sheets.")


def main():
    parser = argparse.ArgumentParser(description="Job Scanner — trouve les offres qui te correspondent")
    parser.add_argument("--days", type=int, default=1, help="Nombre de jours à couvrir (défaut: 1)")
    parser.add_argument("--from-day", type=int, default=0,
                        help="Décalage de départ en jours (ex: --from-day 15 --days 7 = J-15 à J-8)")
    parser.add_argument("--test", action="store_true", help="Mode test avec offres fictives")
    parser.add_argument("--test-one", type=str, default="", metavar="URL",
                        help="Teste le pipeline complet sur une seule URL LinkedIn")
    parser.add_argument("--no-enrich", action="store_true", help="Désactive l'enrichissement via API")
    parser.add_argument("--enrich-limit", type=int, default=None, metavar="N",
                        help="Limite le nombre d'appels API réels (ex: --enrich-limit 1 pour tester)")
    parser.add_argument("--no-sheets", action="store_true", help="N'écrit pas dans Google Sheets")
    parser.add_argument("--output-json", type=str, help="Sauvegarde les résultats en JSON")
    parser.add_argument("--rescore-p2", action="store_true",
                        help="Enrichit et rescore en P2 toutes les lignes Sheets sans Date scoring P2")
    parser.add_argument("--rescore-min-score", type=int, default=6, metavar="N",
                        help="Score P1 minimum pour --rescore-p2 (défaut: 6)")
    parser.add_argument("--rescore-force", action="store_true",
                        help="Rescore même les lignes qui ont déjà une P2")
    parser.add_argument("--rescore-id", type=str, default="", metavar="LINKEDIN_ID",
                        help="Rescore uniquement l'offre avec cet ID LinkedIn (implique --rescore-force)")
    args = parser.parse_args()

    print("=" * 60)
    print("JOB SCANNER — Léonard Maguin")
    print("=" * 60)

    if args.rescore_p2 or args.rescore_id:
        run_rescore_p2(
            min_score=args.rescore_min_score,
            enrich_limit=args.enrich_limit,
            force=args.rescore_force or bool(args.rescore_id),
            only_id=args.rescore_id,
        )
        return

    # --- ÉTAPE 1 : Collecte ---
    if args.test_one:
        # Mode test sur une seule URL : injecte une offre factice avec l'URL fournie
        import re as _re
        m = _re.search(r"/jobs/view/(\d+)", args.test_one)
        job_id = m.group(1) if m else "test"
        raw_jobs = [{
            "id": f"test_{job_id}", "source": "test_one",
            "title": "?", "company": "?", "location": "?",
            "description": "", "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
            "salary": "", "email_date": "", "collected_at": "",
        }]
        print(f"\n=== MODE TEST ONE — {raw_jobs[0]['url']} ===\n")
    elif args.test:
        raw_jobs = run_test_mode()
    else:
        end_day = args.from_day + args.days
        if args.from_day > 0:
            print(f"\n[1/4] Collecte fenêtre glissante : J-{end_day} à J-{args.from_day}...")
        else:
            print(f"\n[1/4] Collecte des {args.days} dernier(s) jour(s)...")
        raw_jobs = [j.to_dict() for j in collect_jobs_from_gmail(
            days_back=end_day,
            skip_days=args.from_day,
        )]

    if not raw_jobs:
        print("Aucune offre collectée.")
        return
    print(f"     → {len(raw_jobs)} offre(s) collectée(s)")

    # --- ÉTAPE 2a : Scoring passe 1 (sans description) ---
    print(f"\n[2/4] Scoring passe 1 (titre + entreprise + localisation)...")
    scored_p1 = score_all_jobs(raw_jobs, verbose=True, pass2=False)

    # Offres à enrichir : non-rejetées avec score passe 1 >= seuil pré-enrichissement
    to_enrich = [j for j in scored_p1 if not j.hard_reject and j.score_total >= PRE_ENRICHMENT_THRESHOLD]
    hard_rejected = [j for j in scored_p1 if j.hard_reject]
    low_score = [j for j in scored_p1 if not j.hard_reject and j.score_total < PRE_ENRICHMENT_THRESHOLD]

    print(f"     → {len(to_enrich)} offre(s) retenues pour enrichissement (score P1 >= {PRE_ENRICHMENT_THRESHOLD})")
    print(f"     → {len(low_score)} offre(s) score trop bas, {len(hard_rejected)} rejetées définitivement")

    # --- ÉTAPE 2b : Enrichissement API (description complète) ---
    # Sauvegarde le score P1 avant que la passe 2 ne l'écrase
    to_enrich_dicts = []
    for j in to_enrich:
        d = j.to_dict()
        d["score_p1"] = j.score_total
        to_enrich_dicts.append(d)

    if not args.no_enrich and os.environ.get("RAPIDAPI_KEY") and to_enrich_dicts:
        enrich_limit = args.enrich_limit
        if args.test or args.test_one:
            enrich_limit = enrich_limit or 1  # toujours limité à 1 en mode test
        print(f"\n[3/4] Enrichissement API ({len(to_enrich_dicts)} offres{f', max {enrich_limit} appels' if enrich_limit else ''})...")
        # Initialise le service Sheets pour le cache (réutilise la connexion existante)
        try:
            _sheets_svc = get_sheets_service()
            _sid = get_or_create_spreadsheet(_sheets_svc, SPREADSHEET_ID)
        except Exception:
            _sheets_svc, _sid = None, ""
        to_enrich_dicts = enrich_jobs_with_api(
            to_enrich_dicts,
            max_jobs=enrich_limit,
            sheets_service=_sheets_svc,
            spreadsheet_id=_sid,
        )
    else:
        if not args.no_enrich and to_enrich_dicts:
            print(f"\n[3/4] Enrichissement ignoré (RAPIDAPI_KEY non défini)")

    # --- ÉTAPE 2c : Scoring passe 2 (avec description si disponible) ---
    has_description = [j for j in to_enrich_dicts if len(j.get("description", "")) > 150]
    no_description  = [j for j in to_enrich_dicts if len(j.get("description", "")) <= 150]

    final_scored = []
    if has_description:
        print(f"\n[3b/4] Scoring passe 2 ({len(has_description)} offres avec description)...")
        rescored = score_all_jobs(has_description, verbose=True, pass2=True)
        p2_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        for j in rescored:
            d = j.to_dict()
            if d.get("p2_failed"):
                # Erreur Claude (ex: rate limit) — traiter comme sans P2
                d["scored_p2"] = False
                d["date_scoring_p2"] = ""
            else:
                d["scored_p2"] = True
                d["date_scoring_p2"] = p2_date
            final_scored.append(d)
    # Offres sans description : conserve le score passe 1, pas de P2
    for j in no_description:
        j["scored_p2"] = False
        j["date_scoring_p2"] = ""
        final_scored.append(j)

    # Rassemble tout pour le Sheets
    # Pour les offres non enrichies, score_p1 = score_total (passe 1 est le score final)
    def _with_p1(scored_job):
        d = scored_job.to_dict()
        d["score_p1"] = scored_job.score_total
        return d

    all_jobs_dict = (
        final_scored
        + [_with_p1(j) for j in low_score]
        + [_with_p1(j) for j in hard_rejected]
    )

    # --- ÉTAPE 4 : Output ---
    if not args.no_sheets:
        print(f"\n[4/4] Export vers Google Sheets...")
        sheets_url = write_jobs_to_sheets(all_jobs_dict, spreadsheet_id=SPREADSHEET_ID)
        print(f"     → {sheets_url}")
    else:
        print(f"\n[4/4] Export Sheets ignoré (--no-sheets)")

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(all_jobs_dict, f, ensure_ascii=False, indent=2)
        print(f"     → JSON sauvegardé : {args.output_json}")

    # --- Résumé final ---
    print("\n" + "=" * 60)
    print("RÉSUMÉ — Offres shortlistées (Accepté = TRUE)")
    print("=" * 60)
    top = [j for j in all_jobs_dict if j.get("score_total", 0) >= ENRICHMENT_THRESHOLD and not j.get("hard_reject")]
    top.sort(key=lambda x: x.get("score_total", 0), reverse=True)

    if top:
        for j in top:
            score = j.get("score_total", 0)
            bar = "█" * score + "░" * (10 - score)
            print(f"  {bar} {score}/10 — {j.get('title')} @ {j.get('company')}")
            print(f"           {j.get('summary', '')[:120]}")
            print(f"           {j.get('url', '')}")
            print()
    else:
        print("  Aucune offre shortlistée.")

    print("=" * 60)


if __name__ == "__main__":
    main()
