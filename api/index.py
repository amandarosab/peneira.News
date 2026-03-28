import sys
from pathlib import Path

# Adiciona a raiz do projeto ao path para os imports funcionarem
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app

# Vercel precisa de um handler WSGI
handler = app
