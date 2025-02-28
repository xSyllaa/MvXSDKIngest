"""API pour l'analyse de dépôts GitHub"""

import os
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from typing import Optional, Set, List, Dict, Any
import uuid
import json
from pathlib import Path

from sdkingest.repository_ingest import ingest_async

app = FastAPI(title="GitHub Repo Analysis API")

# Configuration CORS pour permettre les appels depuis votre frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Remplacez par l'URL de votre frontend en production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dossier pour stocker les résultats temporaires
RESULTS_DIR = Path("./results")
os.makedirs(RESULTS_DIR, exist_ok=True)

class RepoRequest(BaseModel):
    """Modèle de données pour la requête d'analyse de dépôt"""
    url: HttpUrl
    max_file_size: int = 10 * 1024 * 1024  # 10 MB par défaut
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    branch: Optional[str] = None

class RepoResponse(BaseModel):
    """Modèle de données pour la réponse d'analyse de dépôt"""
    task_id: str
    status: str = "processing"

class RepoResult(BaseModel):
    """Modèle de données pour les résultats d'analyse de dépôt"""
    summary: str
    tree: str
    content: str

# Stockage en mémoire des tâches (en production, utilisez une base de données)
tasks: Dict[str, Dict[str, Any]] = {}

@app.post("/analyze", response_model=RepoResponse)
async def analyze_repo(repo: RepoRequest, background_tasks: BackgroundTasks):
    """
    Endpoint pour analyser un dépôt GitHub.
    Retourne un ID de tâche que le client peut utiliser pour récupérer les résultats.
    """
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status": "processing"}
    
    # Conversion des listes en ensembles pour les patterns
    include_set = set(repo.include_patterns) if repo.include_patterns else None
    exclude_set = set(repo.exclude_patterns) if repo.exclude_patterns else None
    
    # Ajouter la tâche d'analyse au background
    background_tasks.add_task(
        process_repo_analysis,
        task_id=task_id,
        url=str(repo.url),
        max_file_size=repo.max_file_size,
        include_patterns=include_set,
        exclude_patterns=exclude_set,
        branch=repo.branch
    )
    
    return RepoResponse(task_id=task_id)

async def process_repo_analysis(
    task_id: str,
    url: str,
    max_file_size: int,
    include_patterns: Optional[Set[str]],
    exclude_patterns: Optional[Set[str]],
    branch: Optional[str]
):
    """Processus d'analyse en arrière-plan"""
    try:
        # Appel à la fonction d'ingestion
        summary, tree, content = await ingest_async(
            source=url,
            max_file_size=max_file_size,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            branch=branch
        )
        
        # Stocker les résultats
        result_file = RESULTS_DIR / f"{task_id}.json"
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump({
                "summary": summary,
                "tree": tree,
                "content": content
            }, f)
        
        tasks[task_id] = {
            "status": "completed",
            "file": str(result_file)
        }
    except Exception as e:
        tasks[task_id] = {
            "status": "failed",
            "error": str(e)
        }

@app.get("/results/{task_id}", response_model=Dict[str, Any])
async def get_results(task_id: str):
    """
    Endpoint pour récupérer les résultats de l'analyse.
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Tâche non trouvée")
    
    task_info = tasks[task_id]
    
    if task_info["status"] == "processing":
        return {"status": "processing"}
    
    if task_info["status"] == "failed":
        return {
            "status": "failed",
            "error": task_info.get("error", "Une erreur s'est produite")
        }
    
    # Si l'analyse est terminée, récupérer les résultats du fichier
    result_file = Path(task_info["file"])
    if not result_file.exists():
        raise HTTPException(status_code=404, detail="Fichier de résultats non trouvé")
    
    with open(result_file, "r", encoding="utf-8") as f:
        results = json.load(f)
    
    return {
        "status": "completed",
        "results": results
    }

@app.delete("/results/{task_id}")
async def delete_results(task_id: str):
    """
    Endpoint pour supprimer les résultats d'une analyse.
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Tâche non trouvée")
    
    task_info = tasks[task_id]
    
    if task_info["status"] == "completed" and "file" in task_info:
        result_file = Path(task_info["file"])
        if result_file.exists():
            result_file.unlink()
    
    del tasks[task_id]
    return {"message": "Résultats supprimés avec succès"} 