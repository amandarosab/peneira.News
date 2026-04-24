"""
Smoke tests para CI: usa Flask test_client para validar endpoints principais.
"""
import os
import sys
from pathlib import Path

# Garantir que a raiz do projeto esteja no path quando executado a partir de scripts/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault('FLASK_ENV', 'development')
os.environ.setdefault('RATE_LIMIT_STORAGE_URI', 'memory://')

from app import app

app.testing = True
client = app.test_client()

def run():
    results = {}
    resp = client.get('/')
    results['home'] = resp.status_code

    resp = client.get('/api/csrf-token')
    results['csrf'] = resp.status_code
    token = None
    if resp.status_code == 200:
        token = resp.json.get('csrf_token')

    payload = {
        'nome':'CI Test', 'email':'ci@example.com', 'link_sugerido':'https://example.com', 'mensagem':'CI smoke test'
    }
    headers = {'Content-Type':'application/json'}
    if token:
        headers['X-CSRFToken'] = token
    resp = client.post('/api/sugestoes', json=payload, headers=headers)
    results['sugestoes'] = resp.status_code

    resp2 = client.post('/api/contato', json={'nome':'CI','sobrenome':'Test','email':'ci@example.com','telefone':'11999999999','mensagem':'CI contact'}, headers=headers)
    results['contato'] = resp2.status_code

    # test peneirar
    form = {'url_digitada': 'https://example.com'}
    if token:
        form['csrf_token'] = token
    resp3 = client.post('/peneirar', data=form)
    results['peneirar'] = resp3.status_code

    # img proxy: try httpbin image
    try:
        resp4 = client.get('/img_proxy?u=https://httpbin.org/image/png')
        results['img_proxy'] = resp4.status_code
    except Exception as e:
        results['img_proxy'] = str(e)

    print(results)

if __name__ == '__main__':
    run()
