"""
Récupère les descriptions complètes des offres LinkedIn via LinkedIn Job Search API (Fantastic.Jobs).
API : linkedin-job-search-api.p.rapidapi.com
Plan gratuit : 200,000 jobs/mois, 25,000 requests/mois
"""

import os
import re
import time
import requests

RAPIDAPI_HOST = "linkedin-job-search-api.p.rapidapi.com"
RAPIDAPI_BASE = "https://linkedin-job-search-api.p.rapidapi.com"


def _headers() -> dict:
    return {
        "x-rapidapi-host": RAPIDAPI_HOST,
        "x-rapidapi-key": os.environ.get("RAPIDAPI_KEY", ""),
    }


def _get_with_retry(url: str, params: dict, max_retries: int = 3) -> dict | list | None:
    """GET avec retry exponentiel sur 429."""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=_headers(), params=params, timeout=20)
            if response.status_code == 429:
                wait = 15 * (attempt + 1)
                print(f"[LinkedIn API] Rate limit — attente {wait}s...")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code in (404, 422):
                return None
            raise
    return None


def get_job_by_id(job_id: str) -> dict:
    """
    Cherche un job par son ID LinkedIn dans la base 7 jours.
    Retourne le premier résultat ou un dict vide.
    """
    try:
        data = _get_with_retry(
            f"{RAPIDAPI_BASE}/active-jb-7d",
            params={
                "id": job_id,
                "description_type": "text",
                "include_ai": "true",
                "limit": "1",
            },
        )
        if isinstance(data, list) and data:
            return data[0]
    except Exception as e:
        print(f"[LinkedIn API] Erreur get_job_by_id {job_id}: {e}")
    return {}


def search_job(title: str, company: str, location: str = "Belgium") -> dict:
    """
    Fallback : cherche un job par titre + entreprise si l'ID ne fonctionne pas.
    Retourne le meilleur résultat ou un dict vide.
    """
    if not title or not company or title == "?":
        return {}
    try:
        data = _get_with_retry(
            f"{RAPIDAPI_BASE}/active-jb-7d",
            params={
                "title_filter": f'"{title}"',
                "organization_filter": company,
                "location_filter": location,
                "description_type": "text",
                "include_ai": "true",
                "limit": "5",
            },
        )
        if not isinstance(data, list) or not data:
            return {}
        company_lower = company.lower()
        for job in data:
            org = (job.get("organization") or "").lower()
            if company_lower in org or org in company_lower:
                return job
        return data[0]
    except Exception as e:
        print(f"[LinkedIn API] Erreur search_job '{title}' @ '{company}': {e}")
    return {}


def _extract_fields(job: dict) -> dict:
    """Extrait les champs utiles depuis un objet job Fantastic.Jobs."""
    description = job.get("description_text", "") or ""

    # Salaire : d'abord les champs AI, puis salary_raw
    salary = ""
    ai_min = job.get("ai_salary_minvalue")
    ai_max = job.get("ai_salary_maxvalue")
    ai_val = job.get("ai_salary_value")
    ai_unit = job.get("ai_salary_unittext", "YEAR")
    ai_currency = job.get("ai_salary_currency", "EUR")
    if ai_min and ai_max:
        salary = f"{int(ai_min):,}-{int(ai_max):,} {ai_currency}/{ai_unit}"
    elif ai_val:
        salary = f"{int(ai_val):,} {ai_currency}/{ai_unit}"
    elif job.get("salary_raw"):
        raw = job["salary_raw"]
        if isinstance(raw, dict):
            mn = raw.get("minValue", "")
            mx = raw.get("maxValue", "")
            cur = raw.get("currency", "EUR")
            unit = raw.get("unitText", "YEAR")
            if mn and mx:
                salary = f"{mn}-{mx} {cur}/{unit}"
            elif mn:
                salary = f"{mn}+ {cur}/{unit}"

    # Taille entreprise
    company_size = (
        job.get("linkedin_org_size", "")
        or (f"{job['linkedin_org_employees']} employees" if job.get("linkedin_org_employees") else "")
    )

    # Secteur
    company_industry = job.get("linkedin_org_industry", "") or ""

    # Séniorité
    seniority_level = job.get("seniority", "") or job.get("ai_experience_level", "") or ""

    # Description entreprise (bonus)
    company_description = job.get("linkedin_org_description", "") or ""

    return {
        "description": description,
        "salary": salary,
        "company_size": str(company_size),
        "company_industry": str(company_industry),
        "seniority_level": str(seniority_level),
        "company_description": company_description,
        "company_funding": job.get("linkedin_org_type", "") or "",
    }


def get_job_description(linkedin_url: str) -> dict:
    """
    Point d'entrée principal : récupère les détails d'une offre via son URL LinkedIn.
    Essaie d'abord par job_id, puis par recherche si l'ID ne retourne rien.
    """
    if not os.environ.get("RAPIDAPI_KEY"):
        return {}

    match = re.search(r"/jobs/view/(\d+)", linkedin_url)
    if not match:
        return {}
    job_id = match.group(1)

    job = get_job_by_id(job_id)
    if job.get("description_text"):
        return _extract_fields(job)
    return {}


def fetch_belgium_jobs_bulk(titles: list[str], limit: int = 100) -> list[dict]:
    """
    Récupère en 1 seul appel API les offres Belgique correspondant aux titres fournis.
    Économise les crédits : 1 requête pour N offres au lieu de N requêtes.
    """
    if not titles:
        return []

    # Construit un filtre OR sur les titres (max 5 pour éviter les timeouts)
    title_sample = titles[:5]
    title_filter = " OR ".join(f'"{t}"' for t in title_sample)

    try:
        data = _get_with_retry(
            f"{RAPIDAPI_BASE}/active-jb-7d",
            params={
                "title_filter": title_filter,
                "location_filter": "Belgium",
                "description_type": "text",
                "include_ai": "true",
                "limit": str(min(limit, 100)),
            },
        )
        if isinstance(data, list):
            return data
    except Exception as e:
        print(f"[LinkedIn API] Erreur fetch_belgium_jobs_bulk: {e}")
    return []


def _match_job(api_jobs: list[dict], title: str, company: str, job_id: str) -> dict | None:
    """Trouve le meilleur match dans une liste d'offres API."""
    title_lower = title.lower()
    company_lower = company.lower()

    # Match exact par job_id dans l'URL
    for j in api_jobs:
        url = j.get("url", "") or ""
        if job_id and job_id in url:
            return j

    # Match fuzzy titre + entreprise
    best = None
    best_score = 0
    for j in api_jobs:
        t = (j.get("title") or "").lower()
        o = (j.get("organization") or "").lower()
        score = 0
        if title_lower in t or t in title_lower:
            score += 2
        if company_lower in o or o in company_lower:
            score += 2
        if score > best_score:
            best_score = score
            best = j

    return best if best_score >= 2 else None


def enrich_jobs_with_api(jobs: list[dict], max_jobs: int = None) -> list[dict]:
    """
    Enrichit une liste de job dicts avec les données de l'API.
    Stratégie bulk : 1 appel pour récupérer toutes les offres Belgique,
    puis matching local — économise les crédits API.
    """
    if not os.environ.get("RAPIDAPI_KEY"):
        print("[LinkedIn API] RAPIDAPI_KEY non defini — enrichissement ignore")
        return jobs

    jobs_to_enrich = jobs[:max_jobs] if max_jobs else jobs
    titles = [j.get("title", "") for j in jobs_to_enrich if j.get("title") and j.get("title") != "?"]

    print(f"[LinkedIn API] Fetch bulk Belgique ({len(titles)} titres recherches)...")
    api_jobs = fetch_belgium_jobs_bulk(titles)
    print(f"[LinkedIn API] {len(api_jobs)} offres récupérées depuis l'API")

    if not api_jobs:
        for job in jobs_to_enrich:
            job["api_enriched"] = False
        return jobs

    count = 0
    for job in jobs_to_enrich:
        url = job.get("url", "")
        match = re.search(r"/jobs/view/(\d+)", url)
        job_id = match.group(1) if match else ""

        api_job = _match_job(api_jobs, job.get("title", ""), job.get("company", ""), job_id)

        if api_job:
            result = _extract_fields(api_job)
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
            job["api_enriched"] = True
            count += 1
            desc_len = len(result.get("description", ""))
            print(f"[LinkedIn API] OK : {job.get('title')} @ {job.get('company')} ({desc_len} chars)")
        else:
            job["api_enriched"] = False
            print(f"[LinkedIn API] Non matche : {job.get('title')} @ {job.get('company')}")

    print(f"[LinkedIn API] {count}/{len(jobs_to_enrich)} offre(s) enrichie(s)")
    return jobs
