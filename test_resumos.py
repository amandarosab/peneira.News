"""Script de teste para verificar qualidade dos resumos."""
import requests, feedparser, re
from bs4 import BeautifulSoup

def _limpar_html(raw):
    if not raw: return ""
    try: texto = BeautifulSoup(raw, "lxml").get_text(separator=" ")
    except: texto = raw
    texto = re.sub(r"\s+", " ", texto).strip()
    texto = re.sub(r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FAFF]+", " ", texto).strip()
    pats = [
        r"Leia mais.*$", r"Continue lendo.*$", r"Saiba mais.*$",
        r"Veja (os vídeos|mais|também).*?(g1|uol|globo)\.?",
        r"Mande para o g1.*$", r"The post .+? appeared first on .+?\.?$",
        r"Tem alguma sugestão.*$", r"Assine\b.*$",
    ]
    for p in pats:
        texto = re.sub(p, "", texto, flags=re.IGNORECASE).strip()
    return re.sub(r"\s+", " ", texto).strip()

def _palavras_titulo(titulo):
    if not titulo: return set()
    return {w.lower() for w in re.findall(r"\w+", titulo) if len(w) >= 4}

def _frase_e_relevante(frase, pt):
    if not pt: return True
    pf = {w.lower() for w in re.findall(r"\w+", frase) if len(w) >= 4}
    return len(pf & pt) >= 1

_RE_SENTENCA = re.compile(r"(?<=[.!?])\s+(?=[A-Z\u00C0-\u00DC\d\"])")
_RE_LIXO = re.compile(r"^(Por\s|Foto:|Imagem:|Cr.dito|Assine\b|Veja\s+(os|mais|tamb)|Mande\s+para|Tem\s+alguma|Acesse\s+o\s+g1|Ou.a\s+o\s+podcast)", re.I)

def fmt(texto, titulo):
    texto = _limpar_html(texto)
    if len(texto) < 30: return ["Texto insuficiente"]
    pt = _palavras_titulo(titulo)
    frases = _RE_SENTENCA.split(texto)
    bullets = []
    for f in frases:
        f = f.strip()
        if len(f) < 25: continue
        if _RE_LIXO.match(f): continue
        if not bullets and not _frase_e_relevante(f, pt): continue
        if f[-1] not in ".!?": f = f.rstrip(",;:") + "."
        if len(f) > 200: f = f[:197].rsplit(" ", 1)[0] + "..."
        bullets.append(f)
        if len(bullets) == 3: break
    return bullets or [texto[:250]]

_H = {"User-Agent": "Mozilla/5.0"}

feeds = [
    ("G1", "https://g1.globo.com/rss/g1/tecnologia/"),
    ("Folha", "https://feeds.folha.uol.com.br/poder/rss091.xml"),
    ("InfoMoney", "https://www.infomoney.com.br/feed/"),
]

for nome, url in feeds:
    print(f"\n{'='*60}")
    print(f"FONTE: {nome}")
    print(f"{'='*60}")
    try:
        resp = requests.get(url, headers=_H, timeout=10)
        feed = feedparser.parse(resp.content)
        for e in feed.entries[:2]:
            titulo = e.title
            desc = e.get("description", "") or e.get("summary", "")
            # Try og:description
            og_desc = None
            try:
                pr = requests.get(e.link, headers=_H, timeout=8)
                soup = BeautifulSoup(pr.text, "html.parser")
                og = soup.find("meta", property="og:description")
                if og and og.get("content") and len(og["content"].strip()) > 60:
                    og_desc = og["content"].strip()
            except:
                pass

            print(f"\n--- {titulo[:80]}")
            print(f"  RSS limpo: {_limpar_html(desc)[:120]}...")
            if og_desc:
                print(f"  OG desc:   {og_desc[:120]}...")
            melhor = og_desc or desc
            print(f"  BULLETS:")
            for b in fmt(melhor, titulo):
                print(f"    * {b[:150]}")
    except Exception as ex:
        print(f"  ERRO: {ex}")
