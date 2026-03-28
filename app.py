import os
import re
import json
import time
import logging
from datetime import datetime
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

app = Flask(
    __name__,
    template_folder=str(_BASE_DIR / "templates"),
    static_folder=str(_BASE_DIR / "static"),
)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(32).hex())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Headers de segurança aplicados em TODAS as respostas ---
@app.after_request
def aplicar_headers_seguranca(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "style-src 'self' https://cdnjs.cloudflare.com https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
        "img-src 'self' https: data:; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    return response

# --- Rate limiting simples (sem dependência extra) ---
_rate_limit = {}
RATE_LIMIT_MAX = 30          # máx requisições
RATE_LIMIT_WINDOW = 60       # por janela de segundos

@app.before_request
def limitar_requisicoes():
    ip = request.remote_addr
    agora = time.time()
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
        {"nome": "Mega Curioso", "url": "https://www.megacurioso.com.br/rss"},
    ],
}

# ==========================================
# 2. CACHE + PERSISTÊNCIA EM JSON
# ==========================================
ARQUIVO_NOTICIAS = Path(__file__).parent / "noticias_cache.json"
POR_PAGINA = 6  # notícias por "página" (carregamento inicial + cada clique)
MAX_HISTORICO = 200  # máx. de matérias guardadas no arquivo

cache_noticias = {
    "dados": [],
    "ultima_atualizacao": 0,
}


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
    """Grava matérias no JSON, mantendo no máximo MAX_HISTORICO."""
    try:
        with open(ARQUIVO_NOTICIAS, "w", encoding="utf-8") as f:
            json.dump(noticias[:MAX_HISTORICO], f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.error(f"Erro ao salvar histórico: {e}")


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


# ==========================================
# 4. FUNÇÕES DE EXTRAÇÃO E FORMATAÇÃO
# ==========================================
_HEADERS_HTTP = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def _formatar_texto_simples(resumo_original):
    """Fallback sem IA: divide texto em frases curtas."""
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


def formatar_para_tdah(titulo, resumo_original):
    """Usa IA se disponível, senão faz split de frases."""
    if USA_IA:
        return resumir_com_ia(titulo, resumo_original)
    return _formatar_texto_simples(resumo_original)


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
        soup = BeautifulSoup(resumo, "html.parser")
        img = soup.find("img")
        if img and img.get("src"):
            return img["src"]
    return ""


def extrair_og_image(url):
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


def _validar_url(url):
    """Verifica se é uma URL HTTP(S) válida."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


# ==========================================
# 5. MOTOR DE BUSCA DE NOTÍCIAS
# ==========================================
def buscar_noticias_automaticamente():
    noticias_peneiradas = []
    for categoria, fontes in FONTES_RSS.items():
        for fonte in fontes:
            try:
                feed = feedparser.parse(fonte["url"])
                for entry in feed.entries[:5]:
                    data_hoje = datetime.now().strftime("%d/%m/%Y")
                    descricao = entry.get("description", "") or entry.get("summary", "")

                    # Imagem: tenta RSS primeiro, depois og:image da página
                    imagem = extrair_imagem(entry)
                    if not imagem and hasattr(entry, "link"):
                        imagem = extrair_og_image(entry.link)

                    noticia = {
                        "categoria": categoria,
                        "titulo": entry.title,
                        "link_original": entry.link,
                        "fonte": fonte["nome"],
                        "bullets": formatar_para_tdah(entry.title, descricao),
                        "data": data_hoje,
                        "tempo_leitura": "3 min",
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
    pagina_num = request.args.get("pagina", 1, type=int)
    cat_slug = request.args.get("categoria", "").strip()

    dados = cache_noticias["dados"]
    if cat_slug and cat_slug in CATEGORIAS_VALIDAS:
        nome_cat = CATEGORIA_MAP[cat_slug]
        dados = [n for n in dados if n["categoria"] == nome_cat]

    inicio = pagina_num * POR_PAGINA
    fim = inicio + POR_PAGINA
    fatia = dados[inicio:fim]
    return jsonify({"noticias": fatia, "tem_mais": fim < len(dados)})


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


@app.errorhandler(429)
def too_many_requests(e):
    return "<h3>Muitas requisições. Aguarde um momento e tente novamente.</h3>", 429


if __name__ == "__main__":
    app.run(debug=True, port=5000)