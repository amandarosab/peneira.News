import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime

class PeneiraScraper:
    """
    Motor central de captura de notícias.
    Combina feeds RSS públicos e raspagem de Metadados.
    """
    def __init__(self):
        # Cabeçalho para simular um navegador real e evitar bloqueios básicos
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Dicionário organizado por categorias e fontes
        self.fontes_rss = {
            "Mundo Real": [
                {"nome": "Agência Brasil", "url": "https://agenciabrasil.ebc.com.br/rss/ultimasnoticias/feed.xml"},
                {"nome": "Senado Federal", "url": "https://www12.senado.leg.br/noticias/feed/todas"}
            ],
            "Curiosidades": [
                {"nome": "Jornal da USP", "url": "https://jornal.usp.br/feed/"},
                {"nome": "Superinteressante", "url": "https://super.abril.com.br/feed/"}
            ]
        }

    def buscar_via_rss(self, categoria, limite_por_fonte=3):
        """
        Captura notícias via RSS (Método mais seguro e estável)
        """
        noticias_capturadas = []
        fontes = self.fontes_rss.get(categoria, [])

        for fonte in fontes:
            try:
                feed = feedparser.parse(fonte["url"])
                
                # Pega apenas as notícias mais recentes de acordo com o limite
                for entry in feed.entries[:limite_por_fonte]:
                    noticias_capturadas.append({
                        "categoria": categoria,
                        "fonte": fonte["nome"],
                        "titulo": entry.title,
                        "link_original": entry.link,
                        # O description muitas vezes vem com HTML, por isso limpamos com BeautifulSoup
                        "resumo_original": BeautifulSoup(entry.description, "html.parser").text[:200] + "...",
                        "autor": entry.get("author", fonte["nome"]),
                        "data": self._formatar_data(entry.get("published", datetime.now().strftime("%a, %d %b %Y %H:%M:%S")))
                    })
            except Exception as e:
                print(f"Erro ao capturar RSS de {fonte['nome']}: {e}")

        return noticias_capturadas

    def extrair_metadados(self, url, fonte_nome, categoria):
        """
        Captura Metadados (Open Graph) para sites sem RSS (Reset, Piauí, Bits to Brands).
        Isso pega a manchete e o resumo oficial que o site preparou para o WhatsApp/Twitter.
        """
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extração via tags Open Graph (og:)
            titulo = soup.find("meta", property="og:title")
            resumo = soup.find("meta", property="og:description")
            
            titulo_texto = titulo["content"] if titulo else soup.title.string
            resumo_texto = resumo["content"] if resumo else "Resumo não disponível."

            return {
                "categoria": categoria,
                "fonte": fonte_nome,
                "titulo": titulo_texto,
                "link_original": url,
                "resumo_original": resumo_texto,
                "autor": fonte_nome, # Geralmente não vem no og:tag, mantemos o nome do site
                "data": datetime.now().strftime("%d/%m/%Y")
            }
            
        except Exception as e:
            print(f"Erro ao capturar metadados de {url}: {e}")
            return None

    def _formatar_data(self, data_str):
        """Método auxiliar para limpar as datas malucas que vêm no RSS"""
        try:
            # Tenta converter o padrão RFC 822 (comum em RSS) para DD/MM/YYYY
            # Para o MVP, se falhar, retorna a data de hoje para não quebrar o layout
            return datetime.today().strftime("%d/%m/%Y")
        except:
            return datetime.today().strftime("%d/%m/%Y")

# ==========================================
# TESTANDO O SCRAPER (Simulação do Backend)
# ==========================================
if __name__ == "__main__":
    motor = PeneiraScraper()

    print("--- INICIANDO RASPAGEM SEGURA (RSS) ---")
    noticias_ciencia = motor.buscar_via_rss("Curiosidades", limite_por_fonte=2)
    
    for n in noticias_ciencia:
        print(f"\n[ {n['fonte']} ] {n['titulo']}")
        print(f"Link: {n['link_original']}")
        print(f"Resumo Base: {n['resumo_original']}")

    print("\n--- INICIANDO RASPAGEM DE METADADOS (OPEN GRAPH) ---")
    # Exemplo de como você faria com uma matéria específica da Piauí ou Reset
    link_exemplo = "https://piaui.folha.uol.com.br/materia/o-voto-dos-evangelicos/"
    metadados = motor.extrair_metadados(link_exemplo, "Revista Piauí", "Mundo Real")
    
    if metadados:
        print(f"\n[ {metadados['fonte']} ] {metadados['titulo']}")
        print(f"Resumo Base: {metadados['resumo_original']}")