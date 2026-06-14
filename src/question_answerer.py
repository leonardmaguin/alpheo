"""
Répond aux questions de candidature pour une offre donnée en utilisant Claude Sonnet.
Lit les questions depuis le Sheets, génère les réponses, écrit en retour.
"""

import os
import anthropic
import yaml

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "profile.yaml")
PROFILE_MEMO_PATH = os.path.join(os.path.dirname(__file__), "..", "profile_memo.md")


def load_profile() -> dict:
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_profile_memo() -> str:
    if os.path.exists(PROFILE_MEMO_PATH):
        with open(PROFILE_MEMO_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def build_answer_prompt(job: dict, active_questions: list[tuple[int, str]], profile: dict, memo: str) -> str:
    """
    active_questions: list of (original_index, question_text) for non-empty questions only.
    """
    strong_roles = ", ".join(profile["target_roles"]["strong_match"])
    skills_high = ", ".join(profile["skills_valued"]["high"])

    if memo:
        profile_section = f"""## PROFIL DU CANDIDAT
{memo}"""
    else:
        profile_section = f"""## PROFIL DU CANDIDAT
- Titre : Senior Ops & Product Builder, 14 ans d'expérience
- École Centrale Paris
- Localisation : Bruxelles, Belgique
- Rôles idéaux : {strong_roles}
- Compétences clés : {skills_high}
- Langues : Français (natif), Anglais (C2), Allemand (B1), Portugais (B1)"""

    questions_block = "\n".join(
        f"Question {i+1}: {q}" for i, q in active_questions
    )

    json_template = "{\n" + ",\n".join(
        f'  "response_{i+1}": "Ta réponse à la question {i+1}"'
        for i, _ in active_questions
    ) + "\n}"

    return f"""Tu es un expert en recrutement senior qui aide un candidat à rédiger des réponses de candidature percutantes.

{profile_section}

## CONTEXTE DE L'OFFRE
- Titre du poste : {job.get("title", "")}
- Entreprise : {job.get("company", "")}
- Localisation : {job.get("location", "")}
- Salaire affiché : {job.get("salary") or "Non mentionné"}
- Secteur : {job.get("company_industry") or "Non précisé"}
- Taille entreprise : {job.get("company_size") or "Non précisée"}
- Séniorité : {job.get("seniority_level") or "Non précisée"}
- Analyse P2 : {job.get("summary") or "Non disponible"}
- Points forts identifiés : {job.get("strengths") or "Non disponible"}
- Description entreprise : {job.get("company_description") or "Non disponible"}
- Description du poste :
{job.get("description", "Non disponible")}

## QUESTIONS DE CANDIDATURE À RÉPONDRE
{questions_block}

## INSTRUCTIONS
Pour chaque question, rédige une réponse de candidature professionnelle et convaincante :
- Réponds en utilisant la même langue que la question (français si français, anglais si anglais)
- Sois concis mais impactant : 3-5 phrases maximum par question
- Appuie-toi sur les expériences concrètes du profil et les éléments de l'offre pour personnaliser chaque réponse
- Cite des chiffres et faits réels tirés du profil (ex : "16M€ de CA", "NPS 95", "60 FTE") quand c'est pertinent
- Montre la valeur ajoutée concrète que le candidat apporterait à CE poste dans CETTE entreprise
- Évite les formules génériques — chaque réponse doit être spécifique et mémorable
- Utilise la première personne ("J'ai...", "Je...", "I have...", "I...")

Réponds UNIQUEMENT en JSON valide avec exactement {len(active_questions)} clé(s) :
{json_template}"""


def answer_questions_for_job(job: dict, questions: list[str]) -> dict[str, str]:
    """
    Génère des réponses pour les questions de candidature.
    questions: list of 3 values (index 0/1/2), may contain empty strings.
    Retourne un dict {response_1, response_2, response_3} — empty string for skipped questions.
    """
    import json

    active_questions = [(i, q) for i, q in enumerate(questions) if q and q.strip()]
    if not active_questions:
        print("[Questions] Aucune question trouvée pour cette offre.")
        return {"response_1": "", "response_2": "", "response_3": ""}

    profile = load_profile()
    memo = load_profile_memo()
    if memo:
        print("[Questions] profile_memo.md chargé.")
    else:
        print("[Questions] profile_memo.md absent — utilisation du profil minimal.")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = build_answer_prompt(job, active_questions, profile, memo)

    print(f"[Questions] Appel Claude Sonnet pour {len(active_questions)} question(s) — {job.get('title')} @ {job.get('company')}")
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    start = raw.find("{")
    end = raw.rfind("}") + 1
    result = json.loads(raw[start:end])

    responses = {"response_1": "", "response_2": "", "response_3": ""}
    for rank, (orig_idx, _) in enumerate(active_questions):
        responses[f"response_{orig_idx + 1}"] = result.get(f"response_{rank + 1}", "")

    return responses
