"""
Enrichissement des offres via une API jobs externe (provider configurable).
Gère un cache Google Sheets pour éviter de re-requêter les offres déjà enrichies.

Provider actif : Jobs API (by Patrick) — rapidapi.com
Pour revenir à Fantastic.Jobs, voir src/_fantastic_jobs_api.py
"""

import os
import re
import json
import time
import requests

# ---------------------------------------------------------------------------
# Cache Sheets — onglet "Cache API"
# ---------------------------------------------------------------------------

CACHE_TAB = "Cache API"
CACHE_COLUMNS = ["linkedin_url", "api_raw_json", "enriched_at"]


def get_api_cache(sheets_service, spreadsheet_id: str) -> dict[str, dict]:
    """
    Charge le cache depuis l'onglet 'Cache API'.
    Retourne un dict {linkedin_url: enriched_fields}.
    """
    if not sheets_service or not spreadsheet_id:
        return {}

    try:
        # Crée l'onglet s'il n'existe pas
        meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        tab_names = [s["properties"]["title"] for s in meta["sheets"]]
        if CACHE_TAB not in tab_names:
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": CACHE_TAB}}}]}
            ).execute()
            # Écrit l'en-tête
            sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{CACHE_TAB}!A1",
                valueInputOption="RAW",
                body={"values": [CACHE_COLUMNS]},
            ).execute()
            return {}

        # Lit le cache existant
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{CACHE_TAB}!A2:C10000"
        ).execute()
        rows = result.get("values", [])
        cache = {}
        for row in rows:
            if len(row) >= 2 and row[0] and row[1]:
                try:
                    cache[row[0]] = json.loads(row[1])
                except Exception:
                    pass
        return cache
    except Exception as e:
        print(f"[Cache API] Erreur lecture cache: {e}")
        return {}


def save_to_cache(sheets_service, spreadsheet_id: str, linkedin_url: str, enriched: dict):
    """Ajoute ou met à jour une entrée dans le cache Sheets."""
    if not sheets_service or not spreadsheet_id:
        return
    try:
        from datetime import datetime, timezone
        row = [linkedin_url, json.dumps(enriched, ensure_ascii=False), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")]
        sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{CACHE_TAB}!A2",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
    except Exception as e:
        print(f"[Cache API] Erreur écriture cache: {e}")


# ---------------------------------------------------------------------------
# Provider : Jobs API (by Patrick) — à remplacer dès réception de la doc
# Placeholder : toutes les fonctions sont définies mais retournent vide
# ---------------------------------------------------------------------------

def _extract_fields_jobs_api(job: dict) -> dict:
    """
    Extrait les champs utiles depuis un objet job de l'API Jobs API (Patrick).
    À adapter selon la doc exacte de l'API.
    """
    description = (
        job.get("description", "")
        or job.get("job_description", "")
        or job.get("jobDescription", "")
        or ""
    )
    salary = (
        job.get("salary", "")
        or job.get("salary_range", "")
        or job.get("compensation", "")
        or ""
    )
    company_size = str(job.get("company_size", "") or job.get("companySize", "") or "")
    company_industry = str(job.get("industry", "") or job.get("company_industry", "") or "")
    seniority_level = str(job.get("seniority_level", "") or job.get("experience_level", "") or "")
    company_description = str(job.get("company_description", "") or job.get("companyDescription", "") or "")
    company_funding = str(job.get("company_type", "") or job.get("funding", "") or "")

    return {
        "description": description,
        "salary": salary,
        "company_size": company_size,
        "company_industry": company_industry,
        "seniority_level": seniority_level,
        "company_description": company_description,
        "company_funding": company_funding,
    }


def fetch_job_details(linkedin_url: str) -> dict:
    """
    Récupère les détails d'une offre LinkedIn via Jobs API.
    À implémenter une fois la doc reçue.
    """
    # TODO: implémenter avec la doc Jobs API
    return {}


# ---------------------------------------------------------------------------
# Fonction publique principale
# ---------------------------------------------------------------------------

def enrich_jobs_with_api(
    jobs: list[dict],
    max_jobs: int = None,
    sheets_service=None,
    spreadsheet_id: str = "",
) -> list[dict]:
    """
    Enrichit une liste de job dicts via l'API.
    - Vérifie d'abord le cache Sheets pour éviter les re-requêtes
    - Sauvegarde chaque résultat dans le cache
    - max_jobs : limite les appels API (pour les tests)
    - sheets_service + spreadsheet_id : requis pour le cache
    """
    if not os.environ.get("RAPIDAPI_KEY"):
        print("[Job API] RAPIDAPI_KEY non défini — enrichissement ignoré")
        return jobs

    # Charge le cache
    cache = get_api_cache(sheets_service, spreadsheet_id)
    cache_hits = sum(1 for j in jobs if j.get("url") in cache)
    if cache_hits:
        print(f"[Job API] Cache : {cache_hits} offre(s) déjà enrichie(s), skip API")

    api_call_count = 0
    jobs_to_enrich = jobs[:max_jobs] if max_jobs is not None else jobs

    for job in jobs_to_enrich:
        url = job.get("url", "")
        if not url:
            continue

        # Utilise le cache si disponible
        if url in cache:
            result = cache[url]
            _apply_enrichment(job, result)
            job["api_enriched"] = True
            job["from_cache"] = True
            continue

        # Limite à max_jobs appels API réels
        if max_jobs is not None and api_call_count >= max_jobs:
            job["api_enriched"] = False
            continue

        # Appel API réel
        result = fetch_job_details(url)
        api_call_count += 1

        if result.get("description"):
            _apply_enrichment(job, result)
            job["api_enriched"] = True
            job["from_cache"] = False
            save_to_cache(sheets_service, spreadsheet_id, url, result)
            print(f"[Job API] OK : {job.get('title')} @ {job.get('company')} ({len(result['description'])} chars)")
        else:
            job["api_enriched"] = False
            print(f"[Job API] Non trouvé : {job.get('title')} @ {job.get('company')}")

        if api_call_count < (max_jobs or 999):
            time.sleep(1)  # respecte le rate limit

    enriched = sum(1 for j in jobs_to_enrich if j.get("api_enriched"))
    print(f"[Job API] {enriched}/{len(jobs_to_enrich)} offre(s) enrichie(s) ({api_call_count} appels API, {cache_hits} cache)")
    return jobs


def _apply_enrichment(job: dict, result: dict):
    """Applique les champs enrichis sur un job dict."""
    if result.get("description"):
        job["description"] = result["description"]
    if result.get("salary") and not job.get("salary"):
        job["salary"] = result["salary"]
    if result.get("company_size"):
        job["company_size"] = result["company_size"]
    if result.get("company_industry"):
        job["company_industry"] = result["company_industry"]
    if result.get("seniority_level"):
        job["seniority_level"] = result["seniority_level"]
    if result.get("company_description"):
        job["company_description"] = result["company_description"]
    if result.get("company_funding"):
        job["company_funding"] = result["company_funding"]
