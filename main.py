import streamlit as st
import time
import data
import textwrap

# --- 1. CONFIGURACAO DA PAGINA ---
st.set_page_config(
    page_title="Peneira News",
    page_icon="🕸️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# --- 2. CARREGAR CSS ---
def load_css(file_name):
    with open(file_name, encoding="utf-8") as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

load_css("style.css")

# Initialize session state for navigation
if 'menu_selection' not in st.session_state:
    st.session_state.menu_selection = 'INICIO'

# --- 3. HEADER & NAVEGACAO ---
def render_header():
    menu_items = ["INÍCIO", "CULTURA E TECH", "POLÍTICA", "ECONOMIA", "CURIOSIDADES", "SOBRE", "CONTATO"]
    
    # Custom Header Container with Navigation
    menu_html = ''.join([f'<span class="nav-item {"active" if i == st.session_state.menu_selection else ""}">{i}</span>' for i in menu_items])
    
    st.markdown(f"""
        <div class="header-container">
            <div class="logo-section">
                <div class="logo-text">peneira<span class="logo-dot">.</span>NEWS</div>
                <div class="slogan-box">Filtramos o ruído, Você consome somente o necessário!</div>
            </div>
            <div class="nav-menu">
                {menu_html}
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    # We use empty columns to place invisible buttons over the texts to make them clickable
    cols = st.columns([1.5, 3]) 
    with cols[1]:
        nav_cols = st.columns(len(menu_items))
        for idx, item in enumerate(menu_items):
            if nav_cols[idx].button(item, key=f"nav_{item}", use_container_width=True):
                st.session_state.menu_selection = item
                st.rerun()

# --- 4. FUNCAO DE RENDERIZACAO DE CARDS ---
def render_news_card(news):
    bullets_html = ''.join([f'<li class="bullet-item">{b}</li>' for b in news['bullets']])
    
    # SVG Icons matching the mockup orange (#fd7506)
    icon_link = '<svg class="card-icon" viewBox="0 0 24 24" fill="none" stroke="#4c4b4b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>'
    icon_calendar = '<svg class="card-icon orange-icon" viewBox="0 0 24 24" fill="none" stroke="#fd7506" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>'
    icon_clock = '<svg class="card-icon orange-icon" viewBox="0 0 24 24" fill="none" stroke="#fd7506" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>'
    
    img_url = news.get("imagem_url", "")
    img_fallback = "https://images.unsplash.com/photo-1504711434969-e33886168f5c?auto=format&fit=crop&w=800&q=60"
    img_src = img_url if img_url else img_fallback
    
    img_html = f'<div class="news-image-container"><img src="{img_src}" class="news-image" alt="{news["titulo"]}"></div>'
    html = f"""
<div class="news-card">
    {img_html}
    <div class="news-content">
        <div class="news-header">
            <a href="{news['link_original']}" target="_blank" class="news-title-link">{news['titulo']}</a>
            <a href="{news['link_original']}" target="_blank" class="official-link">{icon_link} Acessar o site oficial</a>
        </div>
        <div class="context-heading">Contexto resumido:</div>
        <ul class="bullet-list">
            {bullets_html}
        </ul>
        <div class="news-footer">
            <span class="footer-item">{icon_calendar} {news['data']}</span>
            <span class="footer-item">{icon_clock} {news['tempo_leitura']}</span>
        </div>
    </div>
</div>
"""
    st.write(html, unsafe_allow_html=True)

# --- 5. FOOTER ---
def render_footer():
    st.markdown("""
        <div class="zigzag-footer">
            <div class="footer-inner">
                <div class="footer-info">
                    <div class="footer-logo">
                        <div class="logo-text">peneira<span class="logo-dot">.</span>NEWS</div>
                    </div>
                    <div class="footer-slogan">Filtramos o ruido, Voce consome somente o necessario!</div>
                    <div class="footer-copyright">
                        2025 peneira news. Todos os direitos reservados. Desenvolvido por Los Coders
                    </div>
                </div>
                <div class="newsletter-section">
                    <div class="newsletter-title">Receba direto na sua caixa de entrada</div>
                    <div class="newsletter-form">
                        <input type="text" class="newsletter-input" placeholder="Insira o seu e-mail aqui">
                        <button class="newsletter-button">INSCREVA-SE</button>
                    </div>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

# --- EXECUCAO DA PAGINA ---

render_header()

# Search Bar with SVG Icon
st.markdown("""
    <div class="search-outer">
        <div class="search-container">
            <input type="text" class="search-input-mock" placeholder="Insira o seu link externo aqui...">
            <span class="search-icon-btn">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fd7506" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                </svg>
            </span>
        </div>
    </div>
""", unsafe_allow_html=True)

# Main Content Logic
selection = st.session_state.menu_selection

if selection == "INÍCIO":
    for news in data.NOTICIAS:
        render_news_card(news)

elif selection in ["CULTURA E TECH", "POLÍTICA", "ECONOMIA", "CURIOSIDADES"]:
    # O user mudou a function em data.py para get_noticias() que recebe uppercase
    noticias = data.get_noticias(selection)
    if noticias:
        for item in noticias:
            render_news_card(item)
    else:
        st.info("Nada novo nesta categoria hoje.")

elif selection == "SOBRE":
    st.markdown("## Sobre o Peneira News")
    st.write("Criado para mentes neurodivergentes, focando na essência da notícia.")

elif selection == "CONTATO":
    st.markdown("## Contato")
    st.write("Fale conosco: contato@peneira.news")

# Space before footer
st.markdown("<br><br><br>", unsafe_allow_html=True)

render_footer()