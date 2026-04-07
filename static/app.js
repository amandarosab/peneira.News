/**
 * peneira.News — Script de carregamento de matérias (paginação) e newsletter.
 * Escapa todos os dados vindos da API antes de inserir no DOM.
 */
(function () {
    'use strict';

    // --- Newsletter AJAX ---
    var newsletterForm = document.getElementById('newsletter-form');
    var newsletterMsg = document.getElementById('newsletter-msg');
    if (newsletterForm && newsletterMsg) {
        newsletterForm.addEventListener('submit', function (e) {
            e.preventDefault();
            var emailInput = document.getElementById('newsletter-email');
            var email = emailInput ? emailInput.value.trim() : '';
            if (!email) return;

            var btn = newsletterForm.querySelector('button[type="submit"]');
            if (btn) btn.disabled = true;
            newsletterMsg.textContent = 'Enviando...';

            fetch('/api/newsletter', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: email })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    newsletterMsg.textContent = data.mensagem || 'Obrigado!';
                    if (data.ok && emailInput) emailInput.value = '';
                    if (btn) btn.disabled = false;
                })
                .catch(function () {
                    newsletterMsg.textContent = 'Erro ao enviar. Tente novamente.';
                    if (btn) btn.disabled = false;
                });
        });
    }

    var btn = document.getElementById('btn-carregar-mais');
    if (!btn) return;

    /**
     * Escapa caracteres HTML para prevenir XSS.
     */
    function escapeHtml(str) {
        if (typeof str !== 'string') return '';
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    /**
     * Cria um atributo seguro para URLs — aceita apenas http(s).
     */
    function safeUrl(url) {
        if (typeof url !== 'string') return '#';
        var trimmed = url.trim();
        if (trimmed.indexOf('http://') === 0 || trimmed.indexOf('https://') === 0) {
            return escapeHtml(trimmed);
        }
        return '#';
    }

    btn.addEventListener('click', function () {
        var pagina = parseInt(btn.getAttribute('data-pagina'), 10);
        var categoria = btn.getAttribute('data-categoria') || '';
        var url = '/api/noticias?pagina=' + pagina;
        if (categoria) url += '&categoria=' + encodeURIComponent(categoria);

        btn.disabled = true;
        btn.textContent = 'Carregando...';

        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var lista = document.querySelector('.articles-list');

                data.noticias.forEach(function (n) {
                    var card = document.createElement('article');
                    card.className = 'news-card';

                    // Imagem (segura)
                    var imgContainer = document.createElement('div');
                    imgContainer.className = 'news-image-placeholder';
                    if (n.imagem_url) {
                        var imgEl = document.createElement('img');
                        imgEl.src = safeUrl(n.imagem_url);
                        imgEl.alt = escapeHtml(n.titulo);
                        imgEl.loading = 'lazy';
                        imgEl.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:inherit;';
                        imgContainer.appendChild(imgEl);
                    }

                    // Conteúdo
                    var content = document.createElement('div');
                    content.className = 'news-content';

                    // Header
                    var header = document.createElement('div');
                    header.className = 'news-header';

                    var title = document.createElement('h2');
                    title.className = 'news-title';

                    // Título clicável que leva à página de detalhe
                    var titleLink = document.createElement('a');
                    titleLink.className = 'news-title-link';
                    titleLink.href = '/noticia/' + escapeHtml(n.id || '');
                    titleLink.textContent = n.titulo || '';
                    title.appendChild(titleLink);

                    var sourceLinks = document.createElement('div');
                    sourceLinks.className = 'news-source-links';

                    var sourceCredit = document.createElement('span');
                    sourceCredit.className = 'source-credit';
                    sourceCredit.innerHTML = '<i class="fa-regular fa-newspaper"></i> Fonte: ';
                    sourceCredit.appendChild(document.createTextNode(n.fonte || ''));

                    var extLink = document.createElement('a');
                    extLink.href = safeUrl(n.link_original);
                    extLink.target = '_blank';
                    extLink.rel = 'noopener noreferrer';
                    extLink.className = 'external-link';
                    extLink.innerHTML = '<i class="fa-solid fa-link"></i> ';
                    extLink.appendChild(document.createTextNode('Acessar o site oficial'));

                    sourceLinks.appendChild(sourceCredit);
                    sourceLinks.appendChild(extLink);
                    header.appendChild(title);
                    header.appendChild(sourceLinks);

                    // Meta (data + tempo de leitura)
                    var meta = document.createElement('div');
                    meta.className = 'news-meta';

                    var dateSpan = document.createElement('span');
                    dateSpan.innerHTML = '<i class="fa-regular fa-calendar"></i> ';
                    dateSpan.appendChild(document.createTextNode(n.data || ''));

                    var timeSpan = document.createElement('span');
                    timeSpan.innerHTML = '<i class="fa-regular fa-clock"></i> ';
                    timeSpan.appendChild(document.createTextNode(n.tempo_leitura || ''));

                    meta.appendChild(dateSpan);
                    meta.appendChild(timeSpan);

                    // Montagem (sem bullets — resumo fica na página de detalhe)
                    content.appendChild(header);
                    content.appendChild(meta);
                    card.appendChild(imgContainer);
                    card.appendChild(content);
                    lista.appendChild(card);
                });

                if (data.tem_mais) {
                    btn.setAttribute('data-pagina', pagina + 1);
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-angles-down"></i> Carregar mais matérias';
                } else {
                    btn.parentElement.remove();
                }
            })
            .catch(function () {
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-solid fa-angles-down"></i> Carregar mais matérias';
            });
    });
})();
