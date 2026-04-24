"""Simple test script to verify img_proxy validations locally using test_client."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault('RATE_LIMIT_STORAGE_URI','memory://')
from app import app
app.testing = True
client = app.test_client()

cases = [
    ('http://127.0.0.1', 400),
    ('http://localhost', 400),
    ('https://httpbin.org/image/png', 200),
]

for url, expected in cases:
    r = client.get('/img_proxy?u=' + url)
    print(url, r.status_code, 'OK' if r.status_code==expected else 'FAIL')
