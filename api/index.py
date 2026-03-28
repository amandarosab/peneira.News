import sys
import os
from pathlib import Path

# Adiciona a raiz do projeto ao path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app

# Vercel procura 'app' automaticamente — nome obrigatório
