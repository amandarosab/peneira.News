from flask import Flask, render_template, request
import feedparser
from bs4 import BeautifulSoup
import time
from datetime import datetime

app = Flask(__name__)

# ==========================================
# 1. CONFIGURAÇÃO DAS FONTES REAIS (RSS)
# ==========================================
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
    ]
}

# ==========================================
# 2. CACHE PARA NÃO DERRUBAR O SERVIDOR
# ==========================================
# Evita que o site faça o download das notícias toda vez que alguém der F5
cache_noticias = {
    "dados": [],
    "ultima_atualizacao": 0
}

# ==========================================
# 3. FUNÇÕES DE AUTOMAÇÃO E IA (SIMULADA)
# ==========================================
def formatar_para_tdah(resumo_original):
    """
    Aqui é onde a IA entraria no futuro. 
    Por enquanto, criamos um algoritmo que pega o texto do jornal 
    e quebra em bullet points para caber no seu layout perfeito.
    """
    # Limpa tags HTML que vêm sujeiras dos jornais
    texto_limpo = BeautifulSoup(resumo_original, "html.parser").get_text()
    
    if len(texto_limpo) < 20:
        return ["Esta matéria traz atualizações curtas e diretas.", "Acesse o site oficial para ler os detalhes da cobertura."]

    # Divide o texto em frases para virarem bullets
    frases = texto_limpo.split('. ')
    bullets = []
    for frase in frases[:3]: # Pega no máximo 3 bullets
        if len(frase) > 5:
            bullets.append(frase.strip() + ".")
            
    return bullets

def buscar_noticias_automaticamente():
    """Motor que varre os sites dos jornais e monta o seu feed"""
    noticias_peneiradas = []
    
    for categoria, fontes in FONTES_RSS.items():
        for fonte in fontes:
            try:
                # Conecta no site do jornal
                feed = feedparser.parse(fonte["url"])
                
                # Pega as 2 matérias mais recentes daquela fonte
                for entry in feed.entries[:2]:
                    # Processa a data
                    data_hoje = datetime.now().strftime("%d/%m/%Y")
                    
                    # Cria o formato exigido pelo seu Frontend
                    noticia = {
                        "categoria": categoria,
                        "titulo": entry.title,
                        "link_original": entry.link,
                        "fonte": fonte["nome"],
                        "bullets": formatar_para_tdah(entry.description),
                        "data": data_hoje,
                        "tempo_leitura": "3 min" # Simulado
                    }
                    noticias_peneiradas.append(noticia)
            except Exception as e:
                print(f"Erro ao buscar {fonte['nome']}: {e}")
                
    return noticias_peneiradas

# ==========================================
# 4. ROTAS DO SITE (FLASK)
# ==========================================
@app.route('/')
def home():
    global cache_noticias
    
    # Atualiza as notícias a cada 15 minutos (900 segundos) para ser rápido
    tempo_atual = time.time()
    if tempo_atual - cache_noticias["ultima_atualizacao"] > 900:
        print("Buscando novas notícias nos jornais...")
        cache_noticias["dados"] = buscar_noticias_automaticamente()
        cache_noticias["ultima_atualizacao"] = tempo_atual

    # Envia as notícias reais para o seu HTML
    return render_template('index.html', noticias=cache_noticias["dados"])

@app.route('/peneirar', methods=['POST'])
def peneirar_link():
    """Rota para quando o usuário cola o link na barra de busca"""
    link_recebido = request.form.get('url_digitada')
    return f"<h3>Sucesso!</h3><p>O link recebido para peneirar foi: <a href='{link_recebido}'>{link_recebido}</a></p><p>Aqui entraria a integração com a IA para resumir este link exato!</p>"

if __name__ == '__main__':
    app.run(debug=True, port=5000)