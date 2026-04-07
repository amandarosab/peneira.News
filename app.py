import os
import re
import json
import time
import socket
import hashlib
import ipaddress
import logging
import tempfile
import threading
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, abort, jsonify
from markupsafe import escape

# ==========================================
# 0. CONFIGURAÇÃO & SEGURANÇA
# ==========================================
_BASE_DIR = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(
    __name__,
    template_folder=str(_BASE_DIR / "templates"),
    static_folder=str(_BASE_DIR / "static"),
)
_secret = os.environ.get('SECRET_KEY', '').strip()
if not _secret:
    logger.warning("SECRET_KEY não definida! Usando chave aleatória (inseguro em produção).")
    _secret = os.urandom(32).hex()
app.config['SECRET_KEY'] = _secret

# --- Headers de segurança aplicados em TODAS as respostas ---
@app.after_request
def aplicar_headers_seguranca(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "style-src 'self' https://cdnjs.cloudflare.com https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
        "img-src 'self' https: data:; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    return response

# --- Rate limiting simples (sem dependência extra) ---
_rate_limit = {}
RATE_LIMIT_MAX = 30          # máx requisições
RATE_LIMIT_WINDOW = 60       # por janela de segundos
_RATE_LIMIT_CLEANUP_INTERVAL = 300  # limpa entradas expiradas a cada 5 min
_rate_limit_last_cleanup = 0

def _obter_ip_real():
    """Obtém IP real do cliente, mesmo atrás de proxy reverso (Vercel/Cloudflare)."""
    forwarded = request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
    if forwarded:
        return forwarded
    return request.remote_addr or '0.0.0.0'

def _limpar_rate_limit_expirados():
    """Remove entradas expiradas para evitar memory exhaustion."""
    global _rate_limit_last_cleanup
    agora = time.time()
    if agora - _rate_limit_last_cleanup < _RATE_LIMIT_CLEANUP_INTERVAL:
        return
    _rate_limit_last_cleanup = agora
    expirados = [ip for ip, reg in _rate_limit.items() if agora - reg['inicio'] > RATE_LIMIT_WINDOW]
    for ip in expirados:
        del _rate_limit[ip]

@app.before_request
def limitar_requisicoes():
    _limpar_rate_limit_expirados()
    ip = _obter_ip_real()
    agora = time.time()
    with _rate_limit_lock:
        registro = _rate_limit.get(ip, {"contagem": 0, "inicio": agora})
        if agora - registro["inicio"] > RATE_LIMIT_WINDOW:
            registro = {"contagem": 0, "inicio": agora}
        registro["contagem"] += 1
        _rate_limit[ip] = registro
        if registro["contagem"] > RATE_LIMIT_MAX:
            abort(429)

# ==========================================
# 1. CONFIGURAÇÃO DAS FONTES REAIS (RSS)
# ==========================================
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

# ==========================================
# 2. CACHE + PERSISTÊNCIA EM JSON
# ==========================================
# Vercel tem filesystem read-only, usa /tmp para gravar
_IS_VERCEL = os.environ.get("VERCEL", "") == "1"
if _IS_VERCEL:
    ARQUIVO_NOTICIAS = Path("/tmp/noticias_cache.json")
else:
    ARQUIVO_NOTICIAS = _BASE_DIR / "noticias_cache.json"
POR_PAGINA = 6  # notícias por "página" (carregamento inicial + cada clique)
MAX_HISTORICO = 600  # máx. de matérias guardadas (~1 mês de histórico)

cache_noticias = {
    "dados": [],
    "ultima_atualizacao": 0,
}
_cache_lock = threading.Lock()
_rate_limit_lock = threading.Lock()


def _carregar_historico():
    """Lê o arquivo JSON com matérias acumuladas."""
    if ARQUIVO_NOTICIAS.exists():
        try:
            with open(ARQUIVO_NOTICIAS, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _salvar_historico(noticias):
    """Grava matérias no JSON de forma atômica para evitar corrupção."""
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(ARQUIVO_NOTICIAS.parent), suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(noticias[:MAX_HISTORICO], f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(ARQUIVO_NOTICIAS))
    except OSError as e:
        logger.error(f"Erro ao salvar histórico: {e}")
        # Limpa arquivo temporário caso o rename falhe
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _merge_noticias(novas, existentes):
    """Mescla novas matérias com as já salvas, sem duplicatas (por link)."""
    links_existentes = {n["link_original"] for n in existentes}
    unicas = [n for n in novas if n["link_original"] not in links_existentes]
    return unicas + existentes  # novas no topo

# ==========================================
# 3. DETECÇÃO AUTOMÁTICA DE LLM (GPT-4o-mini)
# ==========================================
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
USA_IA = bool(OPENAI_API_KEY)

if USA_IA:
    logger.info("API Key da OpenAI detectada — resumos com IA ativados (GPT-4o-mini).")
else:
    logger.info("Sem API Key da OpenAI — usando resumo por processamento de texto.")


def resumir_com_ia(titulo, texto):
    """Chama GPT-4o-mini para gerar bullets otimizados para TDAH."""
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
                            "- NUNCA traduza nomes próprios (pessoas, empresas, marcas, lugares). "
                            "Mantenha-os exatamente como no texto original\n"
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
        return bullets[:3] if bullets else _formatar_texto_simples(texto, titulo)
    except Exception as e:
        logger.warning(f"Erro na API OpenAI, usando fallback: {e}")
        return _formatar_texto_simples(texto, titulo)


# ==========================================
# 4. FUNÇÕES DE EXTRAÇÃO E FORMATAÇÃO
# ==========================================
_HEADERS_HTTP = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def _calcular_tempo_leitura(descricao):
    """Estima tempo de leitura a partir do número de palavras (200 palavras/min)."""
    try:
        texto = BeautifulSoup(descricao, "lxml").get_text() if descricao else ""
    except Exception:
        texto = descricao or ""
    palavras = len(texto.split())
    minutos = max(1, round(palavras / 200))
    return f"{minutos} min"


def _limpar_html(raw):
    """Remove HTML e normaliza espaços em branco."""
    if not raw:
        return ""
    try:
        texto = BeautifulSoup(raw, "lxml").get_text(separator=" ")
    except Exception:
        texto = raw
    # colapsa espaços
    texto = re.sub(r"\s+", " ", texto).strip()
    # remove TODOS os emojis/símbolos Unicode (range amplo)
    texto = re.sub(
        r"[\U0001F000-\U0001FFFF\U00002600-\U000027BF\U0000FE00-\U0000FE0F\U0000200D]+",
        "", texto
    ).strip()
    # remove variation selectors isolados que sobram (️)
    texto = re.sub(r"\uFE0F", "", texto)
    # remove chamadas de navegação/CTA comuns em portais BR
    _LIXO_PATTERNS = [
        r"Leia mais.*$",
        r"Continue lendo.*$",
        r"Saiba mais.*$",
        r"Clique aqui.*$",
        r"Veja (os vídeos|mais|também).*?(g1|uol|globo)\.?",
        r"Mande para o g1.*$",
        r"The post .+? appeared first on .+?\.?$",
        r"Tem alguma sugestão.*$",
        r"Assine\b.*$",
        r"Entenda\s+embate.*$",
    ]
    for pat in _LIXO_PATTERNS:
        texto = re.sub(pat, "", texto, flags=re.IGNORECASE).strip()
    # normaliza pontuação estranha: ". ️." → "."
    texto = re.sub(r"\.\s*\.", ".", texto)
    # colapsa espaços restantes
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def _extrair_descricao_completa(entry):
    """Extrai o texto mais rico possível do item RSS."""
    candidatos = []
    if hasattr(entry, "content") and entry.content:
        for c in entry.content:
            val = c.get("value", "")
            if val:
                candidatos.append(val)
    if hasattr(entry, "summary_detail"):
        val = getattr(entry.summary_detail, "value", "") or ""
        if val:
            candidatos.append(val)
    for campo in ("description", "summary"):
        val = entry.get(campo, "")
        if val:
            candidatos.append(val)
    melhor = max(candidatos, key=lambda t: len(_limpar_html(t)), default="")
    return melhor


def _palavras_titulo(titulo):
    """Extrai palavras significativas do título (>=4 chars) para comparação."""
    if not titulo:
        return set()
    return {
        w.lower() for w in re.findall(r"\w+", titulo)
        if len(w) >= 4
    }


def _frase_e_relevante(frase, palavras_titulo):
    """Verifica se a frase é relevante para o artigo (compartilha palavras com o título)."""
    if not palavras_titulo:
        return True
    palavras_frase = {w.lower() for w in re.findall(r"\w+", frase) if len(w) >= 4}
    overlap = palavras_frase & palavras_titulo
    return len(overlap) >= 1


# ---- Regex para quebrar texto em frases ----
_RE_SENTENCA = re.compile(
    r"(?<=[.!?…])\s+(?=[A-ZÁÀÃÂÉÊÍÓÔÕÚÇ\d\"])"
)

# Padrões de frases que são lixo (navegação, créditos, CTAs)
_RE_LIXO_FRASE = re.compile(
    r"^("
    r"Por\s|Foto:|Imagem:|Crédito|Assine\b|Veja\s+(os\s+vídeos|mais|também)"
    r"|Mande\s+para|Tem\s+alguma\s+sugestão"
    r"|Acesse\s+o\s+g1|Ouça\s+o\s+podcast"
    r")",
    re.IGNORECASE,
)


def _formatar_texto_simples(texto_limpo, titulo=""):
    """Fallback sem IA: gera até 3 bullets contextuais a partir do texto."""
    texto_limpo = _limpar_html(texto_limpo) if texto_limpo else ""

    if len(texto_limpo) < 30:
        titulo_limpo = _limpar_html(titulo) if titulo else "esta matéria"
        return [
            f"{titulo_limpo.rstrip('.')}.",
            "Acesse a matéria completa no site oficial para todos os detalhes.",
        ]

    palavras_tit = _palavras_titulo(titulo)
    frases = _RE_SENTENCA.split(texto_limpo)

    bullets = []
    for frase in frases:
        frase = frase.strip()
        if len(frase) < 25:
            continue
        if _RE_LIXO_FRASE.match(frase):
            continue
        # Se é a primeira frase e não tem nenhuma relação com o título,
        # provavelmente é um headline de matéria relacionada — pula
        if not bullets and not _frase_e_relevante(frase, palavras_tit):
            continue
        # normaliza pontuação
        if frase[-1] not in ".!?…":
            frase = frase.rstrip(",;:") + "."
        # Frases muito longas: tenta cortar numa vírgula/ponto-e-vírgula natural
        if len(frase) > 200:
            # Procura o último separador natural antes de 200 chars
            corte = frase[:200]
            for sep in ["; ", ", que ", ", segundo ", ", de acordo ", ", após ", ", mas ", ", e "]:
                pos = corte.rfind(sep)
                if pos > 80:  # garante que não fique curto demais
                    frase = corte[:pos].rstrip(",;: ") + "."
                    break
            else:
                # Sem separador natural — corta na última palavra completa
                frase = corte.rsplit(" ", 1)[0].rstrip(",;: ") + "."
        bullets.append(frase)
        if len(bullets) == 3:
            break

    if not bullets:
        # Pega o primeiro trecho significativo e corta em frase
        trecho = texto_limpo[:300]
        for sep in [". ", "; ", ", "]:
            pos = trecho.find(sep, 40)
            if pos > 0:
                trecho = trecho[:pos + 1]
                break
        else:
            trecho = trecho.rsplit(" ", 1)[0].rstrip(",;: ") + "."
        bullets.append(trecho.strip())

    return bullets


def _buscar_pagina(url):
    """Faz GET seguro na página do artigo e retorna o soup, ou None."""
    try:
        resp = _safe_request_get(url)
        if resp is None:
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return None


def _extrair_og_image_do_soup(soup):
    """Extrai og:image de um soup já parseado."""
    if soup is None:
        return ""
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return og["content"]
    return ""


def _fix_mojibake(text):
    """Corrige encoding latin1→utf8 (ex: 'potÃªncia' → 'potência')."""
    if not text:
        return text
    try:
        fixed = text.encode("latin-1").decode("utf-8")
        # Só usa se o resultado tem menos '?' e '�' que o original
        if fixed.count("�") <= text.count("�"):
            return fixed
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return text


def _enriquecer_texto(soup, texto_rss):
    """Combina og:description (limpo) + texto RSS para gerar o texto mais rico possível."""
    texto_limpo = _limpar_html(texto_rss)

    if soup is None:
        return texto_rss

    og_text = ""
    # og:description costuma ser o melhor resumo — escrito pelo editor
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        og_text = _fix_mojibake(og["content"].strip())

    meta_text = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        meta_text = _fix_mojibake(meta["content"].strip())

    melhor_meta = og_text if len(og_text) >= len(meta_text) else meta_text

    if melhor_meta and len(melhor_meta) > 60:
        # Garante que og:description termina com pontuação
        if melhor_meta[-1] not in ".!?…":
            melhor_meta = melhor_meta.rstrip(",;: ") + "."
        # Combina: og:description no início + RSS depois, para ter mais frases
        if len(texto_limpo) > 80 and melhor_meta not in texto_limpo:
            return melhor_meta + " " + texto_limpo
        return melhor_meta if len(melhor_meta) >= len(texto_limpo) else texto_rss

    # Se já temos bastante texto do RSS, usa ele
    if len(texto_limpo) >= 150:
        return texto_rss

    # Último recurso: primeiros <p> do artigo
    article = soup.find("article") or soup.find(
        "div", class_=re.compile(r"(content|article|post|entry|materia|text)", re.I)
    )
    if article:
        paragrafos = article.find_all("p", limit=5)
        texto_pagina = " ".join(
            p.get_text(strip=True)
            for p in paragrafos
            if len(p.get_text(strip=True)) > 30
        )
        if len(texto_pagina) > len(texto_limpo):
            return texto_pagina

    return texto_rss


def formatar_para_tdah(titulo, resumo_original):
    """Usa IA se disponível, senão faz split de frases."""
    if USA_IA:
        return resumir_com_ia(titulo, resumo_original)
    return _formatar_texto_simples(resumo_original, titulo)


def extrair_imagem(entry):
    """Tenta extrair imagem do RSS (media:content, enclosure, HTML)."""
    if hasattr(entry, "media_content") and entry.media_content:
        for media in entry.media_content:
            if "url" in media:
                return media["url"]
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        for thumb in entry.media_thumbnail:
            if "url" in thumb:
                return thumb["url"]
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image"):
                return enc.get("href") or enc.get("url", "")
    resumo = entry.get("description", "") or entry.get("summary", "")
    if resumo:
        soup = BeautifulSoup(resumo, "lxml")
        img = soup.find("img")
        if img and img.get("src"):
            return img["src"]
    return ""


def _is_ip_privado(hostname):
    """Verifica se o hostname resolve para um IP privado/reservado (proteção SSRF)."""
    try:
        resolved = socket.getaddrinfo(hostname, None)
        for family, _type, _proto, _canonname, sockaddr in resolved:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except (socket.gaierror, ValueError):
        return True  # se não resolver, bloqueia por segurança
    return False


def _validar_url(url):
    """Verifica se é uma URL HTTP(S) válida e não aponta para rede interna."""
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


def _safe_request_get(url, **kwargs):
    """Faz GET com proteção contra SSRF (bloqueia IPs privados/internos)."""
    if not _validar_url(url):
        logger.warning(f"SSRF bloqueado: URL rejeitada -> {url}")
        return None
    kwargs.setdefault('timeout', 8)
    kwargs.setdefault('headers', _HEADERS_HTTP)
    resp = requests.get(url, **kwargs)
    resp.raise_for_status()
    return resp


def extrair_og_image(url):
    """Fallback: busca og:image direto na página do artigo (com proteção SSRF)."""
    soup = _buscar_pagina(url)
    return _extrair_og_image_do_soup(soup)


def _gerar_id(link):
    """Gera um ID curto e estável a partir da URL da notícia."""
    return hashlib.sha256(link.encode()).hexdigest()[:12]


# Fuso horário de Brasília (UTC-3)
_BRT = timezone(timedelta(hours=-3))

# Padrões comuns de data em RSS para parse manual
_RE_DATA_RSS = re.compile(
    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
    re.IGNORECASE,
)
_MESES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _extrair_data_publicacao(entry):
    """Extrai a data de publicação respeitando timezone → converte pra BRT."""
    # 1) Tenta parsear a string raw com offset (mais preciso)
    for campo_raw in ("published", "updated"):
        raw = getattr(entry, campo_raw, "") or entry.get(campo_raw, "")
        if not raw:
            continue
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(raw)  # respeita offset do RSS
            dt_brt = dt.astimezone(_BRT)
            return dt_brt.strftime("%d/%m/%Y")
        except Exception:
            pass
        # Fallback: regex pra extrair dia/mês/ano da string
        m = _RE_DATA_RSS.search(raw)
        if m:
            dia, mes_nome, ano = m.groups()
            mes = _MESES.get(mes_nome[:3].lower())
            if mes:
                return f"{int(dia):02d}/{mes:02d}/{ano}"

    # 2) Fallback absoluto: published_parsed em UTC → BRT
    for campo_parsed in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, campo_parsed, None)
        if parsed:
            try:
                dt_utc = datetime(*parsed[:6], tzinfo=timezone.utc)
                dt_brt = dt_utc.astimezone(_BRT)
                return dt_brt.strftime("%d/%m/%Y")
            except Exception:
                pass

    return datetime.now(_BRT).strftime("%d/%m/%Y")


# ==========================================
# 5. MOTOR DE BUSCA DE NOTÍCIAS
# ==========================================
def buscar_noticias_automaticamente():
    noticias_peneiradas = []
    for categoria, fontes in FONTES_RSS.items():
        for fonte in fontes:
            try:
                # Usa requests com timeout para não bloquear o worker indefinidamente
                resp = requests.get(fonte["url"], headers=_HEADERS_HTTP, timeout=10)
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
                for entry in feed.entries[:5]:
                    # Extrai a data real de publicação do RSS
                    data_pub = _extrair_data_publicacao(entry)

                    # Extrai o texto mais rico possível do RSS
                    descricao = _extrair_descricao_completa(entry)

                    # Busca a página do artigo uma única vez (reusada para imagem + texto)
                    soup_pagina = _buscar_pagina(entry.link) if hasattr(entry, "link") else None

                    # Imagem: tenta RSS primeiro, depois og:image da página
                    imagem = extrair_imagem(entry)
                    if not imagem:
                        imagem = _extrair_og_image_do_soup(soup_pagina)

                    # Enriquece texto se RSS trouxe pouco conteúdo
                    descricao = _enriquecer_texto(soup_pagina, descricao)

                    noticia = {
                        "id": _gerar_id(entry.link),
                        "categoria": categoria,
                        "titulo": entry.title,
                        "link_original": entry.link,
                        "fonte": fonte["nome"],
                        "bullets": formatar_para_tdah(entry.title, descricao),
                        "data": data_pub,
                        "tempo_leitura": _calcular_tempo_leitura(descricao),
                        "imagem_url": imagem,
                    }
                    noticias_peneiradas.append(noticia)
            except Exception as e:
                logger.error(f"Erro ao buscar {fonte['nome']}: {e}")
    return noticias_peneiradas


# ==========================================
# 6. ROTAS DO SITE (FLASK)
# ==========================================
CATEGORIAS_VALIDAS = {"cultura-e-tech", "politica", "economia", "curiosidades"}
PAGINAS_VALIDAS = {"sobre", "contato"}

CATEGORIA_MAP = {
    "cultura-e-tech": "CULTURA E TECH",
    "politica": "POLÍTICA",
    "economia": "ECONOMIA",
    "curiosidades": "CURIOSIDADES",
}


def _atualizar_cache():
    global cache_noticias
    with _cache_lock:
        tempo_atual = time.time()
        if tempo_atual - cache_noticias["ultima_atualizacao"] > 900:
            logger.info("Buscando novas notícias nos jornais...")
            novas = buscar_noticias_automaticamente()
            historico = _carregar_historico()
            todas = _merge_noticias(novas, historico)
            _salvar_historico(todas)
            cache_noticias["dados"] = todas
            cache_noticias["ultima_atualizacao"] = tempo_atual
        elif not cache_noticias["dados"]:
            cache_noticias["dados"] = _carregar_historico()


@app.route("/")
def home():
    _atualizar_cache()
    return render_template("index.html", noticias=cache_noticias["dados"][:POR_PAGINA], pagina="inicio", tem_mais=len(cache_noticias["dados"]) > POR_PAGINA)


@app.route("/categoria/<slug>")
def categoria(slug):
    if slug not in CATEGORIAS_VALIDAS:
        abort(404)
    _atualizar_cache()
    nome_categoria = CATEGORIA_MAP[slug]
    filtradas = [n for n in cache_noticias["dados"] if n["categoria"] == nome_categoria]
    return render_template("index.html", noticias=filtradas[:POR_PAGINA], pagina=slug, tem_mais=len(filtradas) > POR_PAGINA)


@app.route("/api/noticias")
def api_noticias():
    """Endpoint de paginação — retorna próximas matérias em JSON."""
    _atualizar_cache()
    pagina_num = max(0, request.args.get("pagina", 1, type=int))
    cat_slug = request.args.get("categoria", "").strip()

    dados = cache_noticias["dados"]
    if cat_slug and cat_slug in CATEGORIAS_VALIDAS:
        nome_cat = CATEGORIA_MAP[cat_slug]
        dados = [n for n in dados if n["categoria"] == nome_cat]

    inicio = pagina_num * POR_PAGINA
    fim = inicio + POR_PAGINA
    fatia = dados[inicio:fim]
    return jsonify({"noticias": fatia, "tem_mais": fim < len(dados)})


@app.route("/noticia/<noticia_id>")
def detalhe_noticia(noticia_id):
    """Página de detalhe com resumo completo da matéria."""
    if not re.fullmatch(r'[a-f0-9]{12}', noticia_id):
        abort(404)
    _atualizar_cache()
    noticia = next((n for n in cache_noticias["dados"] if n.get("id") == noticia_id), None)
    if not noticia:
        abort(404)
    return render_template("noticia.html", noticia=noticia, pagina="detalhe")


@app.route("/sobre")
def sobre():
    return render_template("index.html", noticias=[], pagina="sobre")


@app.route("/contato")
def contato():
    return render_template("index.html", noticias=[], pagina="contato")


@app.route("/peneirar", methods=["POST"])
def peneirar_link():
    """Rota para quando o usuário cola um link na barra de busca."""
    link_recebido = request.form.get("url_digitada", "").strip()

    # Validação de segurança: só aceita URLs HTTP(S)
    if not link_recebido or not _validar_url(link_recebido):
        return render_template(
            "index.html",
            noticias=cache_noticias["dados"],
            erro="Por favor, insira um link válido (ex: https://exemplo.com/materia).",
        )

    link_escapado = escape(link_recebido)
    return render_template(
        "index.html",
        noticias=cache_noticias["dados"],
        peneirado={
            "url": link_escapado,
            "mensagem": "Aqui entraria a integração com a IA para resumir este link exato!"
            if not USA_IA
            else "Funcionalidade de resumo por IA em desenvolvimento.",
        },
    )


# ==========================================
# 7. NEWSLETTER
# ==========================================
_SUBSCRIBERS_FILE = (
    Path("/tmp/subscribers.json") if _IS_VERCEL
    else _BASE_DIR / "subscribers.json"
)


def _carregar_subscribers():
    if _SUBSCRIBERS_FILE.exists():
        try:
            with open(_SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _salvar_subscribers(lista):
    try:
        fd, tmp = tempfile.mkstemp(dir=str(_SUBSCRIBERS_FILE.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(_SUBSCRIBERS_FILE))
    except OSError as e:
        logger.error(f"Erro ao salvar subscribers: {e}")
        try:
            os.unlink(tmp)
        except Exception:
            pass


_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


@app.route("/api/newsletter", methods=["POST"])
def newsletter():
    """Registra e-mail do visitante (stub — integrar com Resend/Mailchimp futuramente)."""
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    if not email or not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "mensagem": "E-mail inválido."}), 400
    subs = _carregar_subscribers()
    if email not in subs:
        subs.append(email)
        _salvar_subscribers(subs)
        logger.info(f"Novo subscriber: {email}")
    return jsonify({"ok": True, "mensagem": "Obrigado! Você será notificado das novidades."})


@app.errorhandler(429)
def too_many_requests(e):
    return "<h3>Muitas requisições. Aguarde um momento e tente novamente.</h3>", 429


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", port=5000)