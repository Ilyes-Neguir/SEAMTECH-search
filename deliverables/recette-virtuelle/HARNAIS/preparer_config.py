#!/usr/bin/env python3
"""Écrit la configuration d'exécution de la recette (config/config.json du dépôt) :
le modèle vient de config/config.example.json ; chemins et secrets viennent de
l'environnement de recette (jamais du dépôt, jamais recopiés dans les preuves)."""
import json, os
from pathlib import Path

depot = Path("/home/user/SEAMTECH-search")
modele = json.loads((depot / "config" / "config.example.json").read_text(encoding="utf-8"))
modele.update({
    "root_paths": [p for p in os.environ["SEAMTECH_ROOT_PATHS"].split(":") if p],
    "database_path": "/home/user/rv/data/index.sqlite3",
    "database_url": os.environ["SEAMTECH_DATABASE_URL"],
    "host": os.environ.get("SEAMTECH_HOST", "127.0.0.1"),
    "port": int(os.environ.get("SEAMTECH_PORT", "8000")),
    "auth_token": os.environ["SEAMTECH_AUTH_TOKEN"],
    "storage_backend": "s3",
    "s3_endpoint_url": os.environ["SEAMTECH_S3_ENDPOINT_URL"],
    "s3_bucket": os.environ["SEAMTECH_S3_BUCKET"],
    "s3_access_key": os.environ["SEAMTECH_S3_ACCESS_KEY"],
    "s3_secret_key": os.environ["SEAMTECH_S3_SECRET_KEY"],
    "s3_region": os.environ.get("SEAMTECH_S3_REGION", "us-east-1"),
    "s3_force_path_style": True,
    "redis_url": os.environ["SEAMTECH_REDIS_URL"],
    "delete_local_after_upload": False,
    "enable_ocr": os.environ.get("SEAMTECH_ENABLE_OCR", "false").lower() == "true",
    "allow_network_access": False,
})
chemin = Path(os.environ.get("RECETTE_CONFIG", "/home/user/rv/config/recette.config.json"))
chemin.write_text(json.dumps(modele, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"configuration écrite : {chemin}")
print("  racines :", modele["root_paths"])
print("  stockage :", modele["storage_backend"], modele["s3_endpoint_url"], modele["s3_bucket"])
print("  OCR :", modele["enable_ocr"], "| base :", modele["database_url"].split("@")[-1])
