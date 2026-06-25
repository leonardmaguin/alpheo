"""
Score les offres d'emploi avec Claude en deux passes :
- Passe 1 (batch) : 1 seul appel pour toutes les offres → score GO/NO-GO chiffré, 0 verbosité
- Passe 2 (individuel) : 1 appel par offre enrichie → analyse complète + estimation salaire
"""

import os
import json
import yaml

import anthropic

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "profile.yaml")
PRE_ENRICHMENT_THRESHOLD = 5  # score P1 min → déclenche enrichissement + P2
ENRICHMENT_THRESHOLD = PRE_ENRICHMENT_THRESHOLD  # alias pour compatibilité

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

from dataclasses import dataclass

@dataclass
class ScoredJob:
    id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    source: str
    salary: str
    collected_at: str
    email_date: str = ""

    score_total: int = 0
    score_role: int = 0
    score_company: int = 0
    score_location: int = 0
    hard_reject: bool = False
    reject_reason: str = ""
    strengths: str = ""
    red_flags: str = ""
    summary: str = ""
    salary_estimate: str = ""  # estimé par Claude en passe 2

    def to_dict(self) -> dict:
        return self.__dict__

    @classmethod
    def from_job_dict(cls, job: dict) -> "ScoredJob":
        obj = cls(
            id=job.get("id", ""),
            title=job.get("title", ""),
            company=job.get("company", ""),
            location=job.get("location", ""),
            description=job.get("description", ""),
            url=job.get("url", ""),
            source=job.get("source", ""),
            salary=job.get("salary", ""),
            collected_at=job.get("collected_at", ""),
            email_date=job.get("email_date", ""),
        )
        # Préserve les champs d'enrichissement API non déclarés dans le dataclass
        obj._extra = {
            k: job[k] for k in (
                "company_size", "company_industry", "seniority_level",
                "company_funding", "company_description",
                "score_p1", "scored_p2", "date_scoring_p2",
                "api_enriched", "from_cache", "recommendation", "dutch_required",
            ) if k in job
        }
        return obj

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        extra = d.pop("_extra", {})
        d.update(extra)
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_profile() -> dict:
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _parse_json_array(raw: str) -> list:
    start = raw.find("[")
    end = raw.rfind("]") + 1
    return json.loads(raw[start:end])


def _parse_json_object(raw: str) -> dict:
    start = raw.find("{")
    end = raw.rfind("}") + 1
    return json.loads(raw[start:end])


# ---------------------------------------------------------------------------
# Passe 1 — batch, compact, GO/NO-GO uniquement
# ---------------------------------------------------------------------------

PASS1_SYSTEM = """Tu es un filtre de recrutement. Évalue chaque offre selon le profil ci-dessous.

PROFIL (résumé) :
- Senior Ops & Product Builder, 14 ans XP, Bruxelles
- Cherche UNIQUEMENT en Belgique (max 1h Bruxelles) ou remote/hybride
- Rôles OK : Head of Ops, COO, GM, Chief of Staff, Head of Product, Head of IT, Director Ops, Country Manager (ops), Data/AI Lead (hands-on), IT PM, Head of Customer Success (avec équipe + ownership ops), Head of Account Management (scope stratégique/ops)
- Rôles KO : dev pur, finance, RH, sales pur, account manager individuel sans équipe, junior
- Entreprises OK : startup/scale-up tech, SaaS, marketplace, mobilité, énergie, retail tech, IA
- Entreprises KO : grand corporate, banque, pharma, immobilier
- Salaire min : 90k€ (rejeter si explicitement <80k€)

RÈGLES DE SCORE (0-10) :
- score = (role×5 + company×3 + location×2) / 10, arrondi
- role: 9-10=idéal, 7-8=acceptable, 4-6=flou/possible, 0-3=KO
- company: 9-10=startup tech claire, 7=scale-up/mid-tech, 4-6=corporate avec angle tech, 1-3=corporate/banque/pharma
- location: 10=Bruxelles/hybride, 7=Belgique <1h, 5=full-remote, 0=hors Belgique sans remote
- go=true si score>=4 ET pas de KO dur

RÈGLES KO DUR (go=false, score=0) :
- Hors Belgique sans mention remote/hybride/full-remote
- Rôle dev pur / finance / RH / sales pur
- Salaire explicitement <80k€

LOCALISATION — tu es le seul juge, pas de pré-filtre Python :
- Toute ville/commune/code postal BELGE est OK : Etterbeek, Wommelgem, Braine-l'Alleud, Bornem, Wavre, Waterloo, Leuven, Hasselt, Namur, Liège, codes postaux 1000-9999, etc.
- KO dur uniquement si la localisation est clairement hors Belgique (France, UK, Pays-Bas, Allemagne, etc.) ET aucune mention remote/hybride
- En cas de doute (ville ambiguë, localisation vide), NE PAS rejeter — score location=5

Réponds UNIQUEMENT avec un array JSON, une ligne par offre, AUCUN texte autour.
Ajoute TOUJOURS un champ "reason" (5-8 mots max) : cause du rejet ou raison principale du score élevé :
[{"id":"...","score":7,"go":true,"reason":"Head of Ops, scale-up mobilité Bruxelles"},{"id":"...","score":2,"go":false,"reason":"Rôle dev pur, hors profil"},...]"""


def score_pass1_batch(jobs: list[dict], client: anthropic.Anthropic) -> dict[str, dict]:
    """
    Envoie toutes les offres en un seul appel Claude.
    Retourne un dict {job_id: {score, go}}.
    La détection de localisation hors-Belgique est entièrement déléguée à Claude.
    """
    results = {}

    # Découpe en batches de 50 — moins d'appels API, moins de risque de rate limit
    batch_size = 50
    all_jobs_lines = [
        f'{j["id"]} | {j.get("title","")} | {j.get("company","")} | {j.get("location","")} | {j.get("salary","")}'
        for j in jobs
    ]

    for i in range(0, len(all_jobs_lines), batch_size):
        batch_jobs = jobs[i:i + batch_size]
        batch_lines = all_jobs_lines[i:i + batch_size]
        user_msg = f"Évalue ces {len(batch_lines)} offres :\n" + "\n".join(batch_lines)
        if i > 0:
            import time as _time
            _time.sleep(3)

        try:
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=len(batch_lines) * 40 + 50,  # ~40 tokens par offre (inclut reason)
                system=PASS1_SYSTEM,
                messages=[{"role": "user", "content": user_msg}]
            )
            raw = message.content[0].text.strip()
            parsed = _parse_json_array(raw)
            for item in parsed:
                results[item["id"]] = {
                    "score": item.get("score", 0),
                    "go": item.get("go", False),
                    "reason": item.get("reason", ""),
                }
        except Exception as e:
            print(f"[Scorer/P1] Erreur batch ({i}-{i+batch_size}): {e}")
            # En cas d'erreur, marque toutes les offres du batch comme GO avec score 5
            for job in batch_jobs:
                results[job["id"]] = {"score": 5, "go": True, "error": str(e)}

    return results


# ---------------------------------------------------------------------------
# Passe 2 — individuel, analyse complète + estimation salaire
# ---------------------------------------------------------------------------

def build_pass2_prompt(job: dict, profile: dict) -> str:
    strong_roles = ", ".join(profile["target_roles"]["strong_match"])
    acceptable_roles = ", ".join(profile["target_roles"]["acceptable"])

    return f"""Tu es un expert en recrutement senior. Analyse en détail cette offre selon le profil ci-dessous.

## PROFIL
- Senior Ops & Product Builder, 14 ans XP, École Centrale Paris
- Localisation : Bruxelles. Cherche en Belgique (max 1h) ou remote/hybride.
- Rôles idéaux : {strong_roles}
- Rôles acceptables : {acceptable_roles}
- Rôles KO : dev pur, finance, RH, sales pur, account manager individuel sans équipe, junior
- Entreprises cibles : startups/scale-ups tech 20-300 pers., SaaS, marketplace, e-commerce, mobilité, énergie, retail tech, IA
- Salaire min : {profile["compensation"]["min_gross_annual_eur"]}€ brut/an
- Compétences : Agentic AI (Claude/MCP), Ops management, SQL/DBT, process automation, product, ERP
- Red flags : {", ".join(profile["red_flags"][:4])}

## OFFRE
- Titre : {job.get("title", "")}
- Entreprise : {job.get("company", "")}
- Localisation : {job.get("location", "")}
- Salaire affiché : {job.get("salary") or "Non mentionné"}
- Secteur entreprise : {job.get("company_industry") or "Inconnu"}
- Taille entreprise : {job.get("company_size") or "Inconnue"}
- Séniorité : {job.get("seniority_level") or "Non précisée"}
- Description :
{job.get("description", "")}

## INSTRUCTIONS — réponds UNIQUEMENT en JSON valide :

{{
  "hard_reject": false,
  "reject_reason": "",
  "score_role": 7,
  "score_company": 6,
  "score_location": 8,
  "score_total": 7,
  "recommendation": "GO",
  "dutch_required": "",
  "salary_estimate": "90-110k€ brut/an (estimé d'après le secteur et la séniorité)",
  "company_size": "~80 personnes",
  "company_funding": "Série B",
  "company_description": "2-3 phrases : ce que fait la société, son marché, son produit ou service principal.",
  "strengths": "Point fort 1. Point fort 2. Point fort 3.",
  "red_flags": "Red flag éventuel.",
  "summary": "2-3 phrases concrètes : pourquoi le candidat devrait ou non postuler, ce qui manque pour décider."
}}

Règles de scoring :
- hard_reject = true si : hors Belgique SANS remote, rôle KO, salaire explicite <80k€
- score_role : 10=rôle idéal, 7-8=acceptable, 4-6=flou, 0-3=KO
- score_company : 10=startup tech mission claire, 7=scale-up/mid-tech, 4=corporate angle tech, 1-3=grand corporate/banque/pharma
- score_location : 10=Bruxelles/hybride, 7=Belgique <1h, 5=remote, 0=hors Belgique sans remote
- score_total : (role×0.5 + company×0.3 + location×0.2), arrondi
- recommendation : "GO" si score_total >= 6 et hard_reject=false, "NO GO" sinon
- dutch_required : "mandatory" si le néerlandais est explicitement requis, "preferred" si mentionné comme un atout, "" si pas mentionné
- salary_estimate : si non mentionné, estime d'après secteur/taille/séniorité/localisation. Format "X-Yk€ brut/an"
- company_size : estime d'après la description (ex: "~50 personnes", "200-500", "1000+"). "" si vraiment impossible à estimer.
- company_funding : infère depuis la description (ex: "Série A", "Série B", "Bootstrapped", "Corporate / filiale", "Scale-up cotée", "Inconnu")
- company_description : 2-3 phrases claires sur l'activité, le marché et le produit/service. Priorité à ce qui aide à évaluer la fit culturelle et sectorielle.
"""


def score_pass2_single(job: dict, profile: dict, client: anthropic.Anthropic) -> ScoredJob:
    """Score complet avec description — 1 appel par offre."""
    scored = ScoredJob.from_job_dict(job)

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": build_pass2_prompt(job, profile)}]
        )
        raw = message.content[0].text.strip()
        if not raw:
            raise ValueError("Réponse vide de Claude")
        result = _parse_json_object(raw)

        scored.hard_reject = result.get("hard_reject", False)
        scored.reject_reason = result.get("reject_reason", "")
        scored.score_role = result.get("score_role", 0)
        scored.score_company = result.get("score_company", 0)
        scored.score_location = result.get("score_location", 0)
        scored.score_total = result.get("score_total", 0)
        scored.salary_estimate = result.get("salary_estimate", "")
        scored.strengths = result.get("strengths", "")
        scored.red_flags = result.get("red_flags", "")
        scored.summary = result.get("summary", "")
        scored._extra["recommendation"] = result.get("recommendation", "")
        scored._extra["dutch_required"] = result.get("dutch_required", "")
        # Taille, funding, description entreprise : Claude écrase l'API si elle répond
        if result.get("company_size"):
            scored._extra["company_size"] = result["company_size"]
        if result.get("company_funding"):
            scored._extra["company_funding"] = result["company_funding"]
        if result.get("company_description"):
            scored._extra["company_description"] = result["company_description"]

    except Exception as e:
        print(f"[Scorer/P2] Erreur pour '{job.get('title')}': {e}")
        scored.score_total = job.get("score_total", 0)  # conserve le score P1
        scored.reject_reason = f"Erreur scoring P2: {e}"
        scored._extra["p2_failed"] = True  # signal : ne pas écrire cette ligne comme scorée P2

    return scored


# ---------------------------------------------------------------------------
# Fonctions publiques appelées par main.py
# ---------------------------------------------------------------------------

def score_pass1(jobs: list[dict], verbose: bool = True) -> list[ScoredJob]:
    """
    Passe 1 batch : 1 appel Claude pour toutes les offres.
    Retourne toutes les offres avec score P1, triées par score décroissant.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    results = score_pass1_batch(jobs, client)

    scored_list = []
    rejected = 0
    for job in jobs:
        scored = ScoredJob.from_job_dict(job)
        r = results.get(job["id"], {"score": 0, "go": False})
        scored.score_total = r.get("score", 0)
        scored.hard_reject = not r.get("go", False)
        if scored.hard_reject:
            # Préfère la raison du pré-filtre localisation (déjà dans r), sinon celle de Claude
            scored.reject_reason = r.get("reject_reason") or r.get("reason") or "Score P1 trop bas"
        else:
            scored.reject_reason = ""
            scored._extra["p1_reason"] = r.get("reason", "")
        scored_list.append(scored)

        if verbose:
            if scored.hard_reject:
                print(f"[P1] NO  {scored.score_total}/10 — {job.get('title')} @ {job.get('company')} ({scored.reject_reason[:60]})")
            else:
                print(f"[P1] GO  {scored.score_total}/10 — {job.get('title')} @ {job.get('company')}")
        rejected += scored.hard_reject

    go_count = len(scored_list) - rejected
    print(f"\n[P1] {go_count} GO, {rejected} NO — {len(jobs)} offres en 1 appel Claude")

    scored_list.sort(key=lambda x: (not x.hard_reject, x.score_total), reverse=True)
    return scored_list


def score_pass2(jobs: list[dict], verbose: bool = True) -> list[ScoredJob]:
    """
    Passe 2 individuelle : 1 appel par offre avec description complète.
    Retourne les offres rescorées.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    profile = load_profile()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    results: list[ScoredJob] = []
    lock = threading.Lock()

    def _score(job):
        scored = score_pass2_single(job, profile, client)
        with lock:
            results.append(scored)
            if verbose:
                status = "REJET" if scored.hard_reject else f"{scored.score_total}/10"
                print(f"[P2] {status} — {job.get('title')} @ {job.get('company')}")

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_score, job) for job in jobs]
        for f in as_completed(futures):
            f.result()

    accepted = sum(1 for j in results if not j.hard_reject and j.score_total >= ENRICHMENT_THRESHOLD)
    print(f"\n[P2] {accepted} offres shortlistées sur {len(jobs)} scorées")
    results.sort(key=lambda x: (not x.hard_reject, x.score_total), reverse=True)
    return results


# Alias conservé pour compatibilité avec main.py (utilisé en mode --test)
def score_all_jobs(jobs: list[dict], verbose: bool = True, pass2: bool = False, **_) -> list[ScoredJob]:
    if pass2:
        return score_pass2(jobs, verbose=verbose)
    return score_pass1(jobs, verbose=verbose)
