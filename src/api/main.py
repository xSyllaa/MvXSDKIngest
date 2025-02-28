"""API pour l'analyse de dépôts GitHub"""

import os
from fastapi import FastAPI, HTTPException, BackgroundTasks, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl, AnyUrl
from typing import Optional, Set, List, Dict, Any, Union
import uuid
import json
from pathlib import Path
import time

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
    url: str  # Changé de HttpUrl à str pour plus de flexibilité
    max_file_size: int = 10 * 1024 * 1024  # 10 MB par défaut
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    branch: Optional[str] = None

    # Validation personnalisée pour s'assurer que l'URL est valide
    def validate_url(self):
        if not self.url.startswith(("http://", "https://")):
            return f"https://{self.url}"
        return self.url

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

# Page d'accueil avec formulaire pour tester
@app.get("/", response_class=HTMLResponse)
async def read_root():
    """Page d'accueil de l'API d'analyse de dépôts GitHub"""
    return """
    <html>
        <head>
            <title>API d'Analyse de Dépôts GitHub</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    margin: 0;
                    padding: 20px;
                    line-height: 1.6;
                }
                .container {
                    max-width: 800px;
                    margin: 0 auto;
                }
                h1, h2 {
                    color: #333;
                }
                form {
                    background-color: #f9f9f9;
                    padding: 20px;
                    border-radius: 8px;
                    margin-bottom: 20px;
                }
                input[type="text"] {
                    width: 100%;
                    padding: 8px;
                    margin-bottom: 10px;
                }
                button {
                    background-color: #0366d6;
                    color: white;
                    border: none;
                    padding: 10px 15px;
                    border-radius: 4px;
                    cursor: pointer;
                }
                #result {
                    background-color: #f4f4f4;
                    padding: 15px;
                    border-radius: 5px;
                    white-space: pre-wrap;
                    display: none;
                    margin-top: 20px;
                }
                .spinner {
                    display: none;
                    margin-top: 20px;
                    text-align: center;
                }
                .spinner:after {
                    content: " ";
                    display: inline-block;
                    width: 20px;
                    height: 20px;
                    border-radius: 50%;
                    border: 3px solid #0366d6;
                    border-color: #0366d6 transparent #0366d6 transparent;
                    animation: spinner 1.2s linear infinite;
                }
                @keyframes spinner {
                    0% { transform: rotate(0deg); }
                    100% { transform: rotate(360deg); }
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Analyse de Dépôts GitHub</h1>
                <p>Entrez l'URL d'un dépôt GitHub pour l'analyser:</p>
                
                <form id="analyzeForm">
                    <input type="text" id="repoUrl" placeholder="https://github.com/username/repository" required>
                    <button type="submit">Analyser</button>
                </form>
                
                <div class="spinner" id="spinner"></div>
                <div id="result"></div>
                
                <h2>Documentation API</h2>
                <p>Consultez la <a href="/docs">documentation interactive</a> pour plus d'informations sur l'API.</p>
                
                <script>
                    document.getElementById('analyzeForm').addEventListener('submit', async (e) => {
                        e.preventDefault();
                        
                        const url = document.getElementById('repoUrl').value;
                        const resultDiv = document.getElementById('result');
                        const spinner = document.getElementById('spinner');
                        
                        resultDiv.style.display = 'none';
                        spinner.style.display = 'block';
                        
                        try {
                            // Appeler l'API pour lancer l'analyse
                            const response = await fetch('/analyze', {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json',
                                },
                                body: JSON.stringify({ url: url }),
                            });
                            
                            const data = await response.json();
                            const taskId = data.task_id;
                            
                            // Fonction pour vérifier l'état de l'analyse
                            const checkStatus = async () => {
                                const statusResponse = await fetch(`/results/${taskId}`);
                                const statusData = await statusResponse.json();
                                
                                if (statusData.status === 'completed') {
                                    spinner.style.display = 'none';
                                    resultDiv.textContent = JSON.stringify(statusData.results, null, 2);
                                    resultDiv.style.display = 'block';
                                } else if (statusData.status === 'failed') {
                                    spinner.style.display = 'none';
                                    resultDiv.textContent = `Erreur: ${statusData.error}`;
                                    resultDiv.style.display = 'block';
                                } else {
                                    // Si toujours en cours, vérifier à nouveau après 2 secondes
                                    setTimeout(checkStatus, 2000);
                                }
                            };
                            
                            // Démarrer la vérification de l'état
                            setTimeout(checkStatus, 2000);
                            
                        } catch (error) {
                            spinner.style.display = 'none';
                            resultDiv.textContent = `Erreur: ${error.message}`;
                            resultDiv.style.display = 'block';
                        }
                    });
                </script>
            </div>
        </body>
    </html>
    """

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
    
    # Assurez-vous que l'URL est correctement formatée
    url = repo.validate_url()
    
    # Ajouter la tâche d'analyse au background
    background_tasks.add_task(
        process_repo_analysis,
        task_id=task_id,
        url=url,
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