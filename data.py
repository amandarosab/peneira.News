import os
import json
import time
import logging
from datetime import datetime
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

FONTES_RSS = {
    "CULTURA E TECH": [
        {"nome": "G1", "url": "https://g1.globo.com/rss/g1/tecnologia/"},
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

_HEADERS_HTTP = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

ARQUIVO_NOTICIAS = Path(__file__).parent / "noticias_cache.json"
MAX_HISTORICO = 200

# --- Detecção automática de LLM ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
USA_IA = bool(OPENAI_API_KEY)


def _resumir_com_ia(titulo, texto):
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "temperature": 0.4,
                "max_tokens": 250,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Você é um assistente que resume notícias para pessoas com TDAH. "
                            "Regras rígidas:\n"
                            "- Retorne EXATAMENTE 3 frases curtas (máx. 20 palavras cada)\n"
                            "- Use linguagem simples e direta\n"
                            "- Comece cada frase com um verbo ou dado concreto\n"
                            "- Sem introduções — vá direto ao ponto\n"
                            "- Separe cada frase por \\n"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Título: {titulo}\n\nTexto original:\n{texto[:1500]}",
                    },
                ],
            },
            timeout=15,
        )
        response.raise_for_status()
        conteudo = response.json()["choices"][0]["message"]["content"].strip()
        bullets = [b.strip().lstrip("•-– ") for b in conteudo.split("\n") if b.strip()]
        return bullets[:3] if bullets else _formatar_texto_simples(texto)
    except Exception as e:
        logger.warning(f"Erro na API OpenAI, usando fallback: {e}")
        return _formatar_texto_simples(texto)


def _formatar_texto_simples(resumo_original):
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
            bullets.append(frase.strip().rstrip(".") + ".")
    return bullets


def _formatar_para_tdah(titulo, resumo_original):
    if USA_IA:
        return _resumir_com_ia(titulo, resumo_original)
    return _formatar_texto_simples(resumo_original)


def _extrair_imagem(entry):
    """Tenta extrair a URL da imagem de um item RSS."""
    if hasattr(entry, 'media_content') and entry.media_content:
        for media in entry.media_content:
            if 'url' in media:
                return media['url']
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        for thumb in entry.media_thumbnail:
            if 'url' in thumb:
                return thumb['url']
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image'):
                return enc.get('href') or enc.get('url', '')
    resumo = entry.get('description', '') or entry.get('summary', '')
    if resumo:
        soup = BeautifulSoup(resumo, 'html.parser')
        img = soup.find('img')
        if img and img.get('src'):
            return img['src']
    return ''


def _extrair_og_image(url):
    """Fallback: busca og:image direto na página do artigo."""
    try:
        resp = requests.get(url, headers=_HEADERS_HTTP, timeout=8)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]
    except Exception:
        pass
    return ""


def _buscar_noticias():
    noticias = []
    for categoria, fontes in FONTES_RSS.items():
        for fonte in fontes:
            try:
                feed = feedparser.parse(fonte["url"])
                for entry in feed.entries[:5]:
                    data_hoje = datetime.now().strftime("%d/%m/%Y")
                    descricao = entry.get("description", "") or entry.get("summary", "")

                    imagem = _extrair_imagem(entry)
                    if not imagem and hasattr(entry, "link"):
                        imagem = _extrair_og_image(entry.link)

                    noticias.append({
                        "categoria": categoria,
                        "titulo": entry.title,
                        "link_original": entry.link,
                        "fonte": fonte["nome"],
                        "bullets": _formatar_para_tdah(entry.title, descricao),
                        "data": data_hoje,
                        "tempo_leitura": "3 min",
                        "imagem_url": imagem,
                    })
            except Exception as e:
                logger.error(f"Erro ao buscar {fonte['nome']}: {e}")
    return noticias


def _carregar_historico():
    if ARQUIVO_NOTICIAS.exists():
        try:
            with open(ARQUIVO_NOTICIAS, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _salvar_historico(noticias):
    try:
        with open(ARQUIVO_NOTICIAS, "w", encoding="utf-8") as f:
            json.dump(noticias[:MAX_HISTORICO], f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.error(f"Erro ao salvar histórico: {e}")


def _merge_noticias(novas, existentes):
    links_existentes = {n["link_original"] for n in existentes}
    unicas = [n for n in novas if n["link_original"] not in links_existentes]
    return unicas + existentes


def _atualizar_cache():
    agora = time.time()
    if agora - _cache["ultima_atualizacao"] > 900:
        novas = _buscar_noticias()
        historico = _carregar_historico()
        todas = _merge_noticias(novas, historico)
        _salvar_historico(todas)
        _cache["dados"] = todas
        _cache["ultima_atualizacao"] = agora
    elif not _cache["dados"]:
        _cache["dados"] = _carregar_historico()


def get_noticias(categoria=None):
    _atualizar_cache()
    if categoria is None:
        return _cache["dados"]
    return [n for n in _cache["dados"] if n["categoria"] == categoria]


_atualizar_cache()
NOTICIAS = _cache["dados"]
