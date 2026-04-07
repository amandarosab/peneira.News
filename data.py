import os
import json
import time
import socket
import hashlib
import ipaddress
import logging
import tempfile
import threading
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
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
        {"nome": "Super Interessante", "url": "https://super.abril.com.br/feed"},
    ],
}

_cache = {
    "dados": [],
    "ultima_atualizacao": 0,
}
_cache_lock = threading.Lock()

_HEADERS_HTTP = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

ARQUIVO_NOTICIAS = Path(__file__).parent / "noticias_cache.json"
MAX_HISTORICO = 600
DIAS_HISTORICO = 30
ITENS_POR_FONTE = 40

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


def _calcular_tempo_leitura(descricao):
    """Estima tempo de leitura baseado no número de palavras (200 palavras/min)."""
    try:
        texto = BeautifulSoup(descricao, "lxml").get_text() if descricao else ""
    except Exception:
        texto = descricao or ""
    palavras = len(texto.split())
    return f"{max(1, round(palavras / 200))} min"


def _formatar_texto_simples(resumo_original):
    try:
        texto_limpo = BeautifulSoup(resumo_original, "lxml").get_text()
    except Exception:
        texto_limpo = resumo_original or ""
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
        soup = BeautifulSoup(resumo, 'lxml')
        img = soup.find('img')
        if img and img.get('src'):
            return img['src']
    return ''


def _is_ip_privado(hostname):
    """Verifica se o hostname resolve para um IP privado/reservado (proteção SSRF)."""
    try:
        resolved = socket.getaddrinfo(hostname, None)
        for family, _type, _proto, _canonname, sockaddr in resolved:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except (socket.gaierror, ValueError):
        return True
    return False


def _validar_url_ssrf(url):
    """Valida URL e bloqueia IPs internos."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        hostname = parsed.hostname
        if not hostname or _is_ip_privado(hostname):
            return False
        return True
    except Exception:
        return False


def _safe_get(url, **kwargs):
    """GET com proteção SSRF."""
    if not _validar_url_ssrf(url):
        logger.warning(f"SSRF bloqueado: {url}")
        return None
    kwargs.setdefault('timeout', 8)
    kwargs.setdefault('headers', _HEADERS_HTTP)
    resp = requests.get(url, **kwargs)
    resp.raise_for_status()
    return resp


def _extrair_og_image(url):
    """Fallback: busca og:image direto na página do artigo (com proteção SSRF)."""
    try:
        resp = _safe_get(url)
        if resp is None:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]
    except Exception:
        pass
    return ""


def _gerar_id(link):
    return hashlib.sha256(link.encode()).hexdigest()[:12]


_BRT = timezone(timedelta(hours=-3))


def _agora_brt():
    return datetime.now(_BRT)


def _parse_data_noticia(valor):
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor.astimezone(_BRT) if valor.tzinfo else valor.replace(tzinfo=_BRT)
    if isinstance(valor, str):
        try:
            dt = datetime.fromisoformat(valor)
            return dt.astimezone(_BRT) if dt.tzinfo else dt.replace(tzinfo=_BRT)
        except ValueError:
            pass
        try:
            return datetime.strptime(valor, "%d/%m/%Y").replace(tzinfo=_BRT)
        except ValueError:
            return None
    return None


def _extrair_datetime_publicacao(entry):
    for campo_raw in ("published", "updated"):
        raw = getattr(entry, campo_raw, "") or entry.get(campo_raw, "")
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(_BRT)
        except Exception:
            pass

    for campo_parsed in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, campo_parsed, None)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc).astimezone(_BRT)
            except Exception:
                pass

    return _agora_brt()


def _normalizar_noticia(noticia):
    noticia_norm = dict(noticia)
    link = str(noticia_norm.get("link_original", "")).strip()
    if not link:
        return None

    dt_publicacao = (
        _parse_data_noticia(noticia_norm.get("data_iso"))
        or _parse_data_noticia(noticia_norm.get("data"))
        or _agora_brt()
    )

    noticia_norm["id"] = noticia_norm.get("id") or _gerar_id(link)
    noticia_norm["data_iso"] = dt_publicacao.isoformat()
    noticia_norm["data"] = dt_publicacao.strftime("%d/%m/%Y")
    noticia_norm["bullets"] = list(noticia_norm.get("bullets") or [])
    noticia_norm["link_original"] = link
    return noticia_norm


def _ordenar_noticias(noticias):
    noticias_norm = [n for n in (_normalizar_noticia(n) for n in noticias) if n]
    return sorted(
        noticias_norm,
        key=lambda noticia: _parse_data_noticia(noticia.get("data_iso")) or _agora_brt(),
        reverse=True,
    )


def _filtrar_historico_recente(noticias, dias=DIAS_HISTORICO):
    limite = (_agora_brt() - timedelta(days=dias)).date()
    filtradas = []
    for noticia in noticias:
        noticia_norm = _normalizar_noticia(noticia)
        if not noticia_norm:
            continue
        dt_publicacao = _parse_data_noticia(noticia_norm.get("data_iso"))
        if dt_publicacao and dt_publicacao.date() >= limite:
            filtradas.append(noticia_norm)
    return _ordenar_noticias(filtradas)


def _buscar_noticias():
    noticias = []
    for categoria, fontes in FONTES_RSS.items():
        for fonte in fontes:
            try:
                # Usa requests com timeout para não bloquear indefinidamente
                resp = requests.get(fonte["url"], headers=_HEADERS_HTTP, timeout=10)
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
                for entry in feed.entries[:ITENS_POR_FONTE]:
                    data_publicacao = _extrair_datetime_publicacao(entry)
                    descricao = entry.get("description", "") or entry.get("summary", "")

                    imagem = _extrair_imagem(entry)
                    if not imagem and hasattr(entry, "link"):
                        imagem = _extrair_og_image(entry.link)

                    noticias.append({
                        "id": _gerar_id(entry.link),
                        "categoria": categoria,
                        "titulo": entry.title,
                        "link_original": entry.link,
                        "fonte": fonte["nome"],
                        "bullets": _formatar_para_tdah(entry.title, descricao),
                        "data": data_publicacao.strftime("%d/%m/%Y"),
                        "data_iso": data_publicacao.isoformat(),
                        "tempo_leitura": _calcular_tempo_leitura(descricao),
                        "imagem_url": imagem,
                    })
            except Exception as e:
                logger.error(f"Erro ao buscar {fonte['nome']}: {e}")
    return _filtrar_historico_recente(noticias)


def _carregar_historico():
    if ARQUIVO_NOTICIAS.exists():
        try:
            with open(ARQUIVO_NOTICIAS, "r", encoding="utf-8") as f:
                return _filtrar_historico_recente(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _salvar_historico(noticias):
    """Grava matérias de forma atômica para evitar corrupção."""
    noticias_filtradas = _filtrar_historico_recente(noticias)[:MAX_HISTORICO]
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(ARQUIVO_NOTICIAS.parent), suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(noticias_filtradas, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(ARQUIVO_NOTICIAS))
    except OSError as e:
        logger.error(f"Erro ao salvar histórico: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _merge_noticias(novas, existentes):
    mescladas = {}
    for noticia in existentes:
        noticia_norm = _normalizar_noticia(noticia)
        if noticia_norm:
            mescladas[noticia_norm["link_original"]] = noticia_norm
    for noticia in novas:
        noticia_norm = _normalizar_noticia(noticia)
        if noticia_norm:
            mescladas[noticia_norm["link_original"]] = noticia_norm
    return _ordenar_noticias(list(mescladas.values()))


def _atualizar_cache():
    with _cache_lock:
        agora = time.time()
        if agora - _cache["ultima_atualizacao"] > 900:
            novas = _buscar_noticias()
            historico = _carregar_historico()
            todas = _merge_noticias(novas, historico)
            _salvar_historico(todas)
            _cache["dados"] = _filtrar_historico_recente(todas)
            _cache["ultima_atualizacao"] = agora
        elif not _cache["dados"]:
            _cache["dados"] = _filtrar_historico_recente(_carregar_historico())


def get_noticias(categoria=None):
    _atualizar_cache()
    if categoria is None:
        return _cache["dados"]
    return [n for n in _cache["dados"] if n["categoria"] == categoria]


def get_noticias_lazy():
    """Retorna as notícias em cache sem disparar atualização de rede (pra uso imediato)."""
    if not _cache["dados"]:
        _cache["dados"] = _carregar_historico()
    return _cache["dados"]


# NOTICIAS é resolvido sob demanda (.get_noticias()) em vez de ser carregado no import
class _LazyNoticias:
    """Proxy lazy: acessar data.NOTICIAS chama get_noticias() automaticamente."""
    def __iter__(self):
        return iter(get_noticias())
    def __len__(self):
        return len(get_noticias())
    def __getitem__(self, item):
        return get_noticias()[item]
    def __bool__(self):
        return bool(get_noticias())


NOTICIAS = _LazyNoticias()
