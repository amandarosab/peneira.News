import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
import time

FONTES_RSS = {
    "CULTURA E TECH": [
        {"nome": "G1 Tecnologia", "url": "https://g1.globo.com/rss/g1/tecnologia/"},
    ],
    "POLÍTICA": [
        {"nome": "Folha de S.Paulo", "url": "https://feeds.folha.uol.com.br/poder/rss091.xml"},
    ],
    "ECONOMIA": [
        {"nome": "InfoMoney", "url": "https://www.infomoney.com.br/feed/"},
    ],
    "CURIOSIDADES": [
        {"nome": "Mega Curioso", "url": "https://www.megacurioso.com.br/rss"},
    ],
}

_cache = {
    "dados": [],
    "ultima_atualizacao": 0,
}


def _formatar_para_tdah(resumo_original):
    texto_limpo = BeautifulSoup(resumo_original, "html.parser").get_text()
    if len(texto_limpo) < 20:
        return [
            "Esta matéria traz atualizações curtas e diretas.",
            "Acesse o site oficial para ler os detalhes da cobertura.",
        ]
    frases = texto_limpo.split(". ")
    bullets = []
    for frase in frases[:3]:
        if len(frase) > 5:
            bullets.append(frase.strip() + ".")
    return bullets


def _buscar_noticias():
    noticias = []
    for categoria, fontes in FONTES_RSS.items():
        for fonte in fontes:
            try:
                feed = feedparser.parse(fonte["url"])
                for entry in feed.entries[:2]:
                    data_hoje = datetime.now().strftime("%d/%m/%Y")
                    noticias.append({
                        "categoria": categoria,
                        "titulo": entry.title,
                        "link_original": entry.link,
                        "fonte": fonte["nome"],
                        "bullets": _formatar_para_tdah(entry.get("description", "")),
                        "data": data_hoje,
                        "tempo_leitura": "3 min",
                        "imagem_url": "",
                    })
            except Exception as e:
                print(f"Erro ao buscar {fonte['nome']}: {e}")
    return noticias


def _atualizar_cache():
    agora = time.time()
    if agora - _cache["ultima_atualizacao"] > 900:
        _cache["dados"] = _buscar_noticias()
        _cache["ultima_atualizacao"] = agora


def get_noticias(categoria=None):
    _atualizar_cache()
    if categoria is None:
        return _cache["dados"]
    return [n for n in _cache["dados"] if n["categoria"] == categoria]


_atualizar_cache()
NOTICIAS = _cache["dados"]
