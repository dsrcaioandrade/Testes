"""
Consulta situação do CPF na Receita Federal.

Estratégia (gratuita, sem API paga):
  1. Tenta POST direto sem captcha — funciona se a validação for só client-side.
  2. Se falhar, abre o navegador visível, preenche o formulário automaticamente
     e pausa para você resolver o hCaptcha manualmente. Depois captura o resultado.

Dependências:
    pip install playwright
    playwright install chromium
"""

import asyncio
import re
import sys
import urllib.parse
import urllib.request
import http.cookiejar

# ── Configuração ──────────────────────────────────────────────────────────────
FORM_URL  = 'https://servicos.receita.fazenda.gov.br/servicos/cpf/consultasituacao/consultapublica.asp'
POST_URL  = 'https://servicos.receita.fazenda.gov.br/servicos/cpf/consultasituacao/ConsultaPublicaExibir.asp'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'pt-BR,pt;q=0.9',
    'Referer': FORM_URL,
    'Origin': 'https://servicos.receita.fazenda.gov.br',
    'Content-Type': 'application/x-www-form-urlencoded',
}
# ─────────────────────────────────────────────────────────────────────────────


def _parse_result(html: str) -> dict:
    """Extrai situação cadastral do HTML de resposta."""
    html_lower = html.lower()
    result = {'raw_snippet': ''}

    # Situação
    patterns = [
        r'situa[çc][aã]o cadastral[^:]*:\s*<[^>]+>([^<]+)',
        r'<[^>]+class[^>]*situacao[^>]*>([^<]+)',
        r'(regular|irregular|suspen\w+|cancel\w+|nula|pendente\s+de\s+regulariza\w+)',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            result['situacao'] = m.group(1).strip()
            break

    # Nome
    m = re.search(r'nome[^:]*:\s*(?:<[^>]+>)?([A-ZÁÉÍÓÚÂÊÎÔÛÃÕÀÜÇ][A-Za-záéíóúâêîôûãõàüç\s]+)', html)
    if m:
        result['nome'] = m.group(1).strip()

    # Data de nascimento
    m = re.search(r'nascimento[^:]*:\s*(\d{2}/\d{2}/\d{4})', html, re.IGNORECASE)
    if m:
        result['data_nascimento'] = m.group(1)

    # Snippet bruto para debug
    for kw in ('situacao', 'regular', 'irregular', 'suspen', 'cancel', 'nula', 'pendente'):
        idx = html_lower.find(kw)
        if idx >= 0:
            result['raw_snippet'] = html[max(0, idx-50):idx+300]
            break

    return result


# ── Estratégia 1: POST direto (sem captcha) ───────────────────────────────────

def consultar_direto(cpf: str, data_nascimento: str) -> dict | None:
    """
    Tenta POST direto sem token de captcha.
    Retorna dict com resultado ou None se o servidor exigir captcha.
    """
    cpf_digits = re.sub(r'\D', '', cpf)
    dob_digits = re.sub(r'\D', '', data_nascimento)  # ddmmaaaa

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # Pega a página do formulário para estabelecer sessão/cookies
    req = urllib.request.Request(FORM_URL, headers={k: v for k, v in HEADERS.items() if k != 'Content-Type'})
    try:
        opener.open(req, timeout=15)
    except Exception as e:
        print(f'[direto] Erro ao buscar formulário: {e}')
        return None

    payload = urllib.parse.urlencode({
        'txtCPF': cpf_digits,
        'txtDataNascimento': dob_digits,
        'Enviar': 'Consultar',
        'idCheckedReCaptcha': 'false',
        'h-captcha-response': '',
    }).encode('latin-1')

    req = urllib.request.Request(POST_URL, data=payload, headers=HEADERS)
    try:
        resp = opener.open(req, timeout=15)
        html = resp.read().decode('latin-1', errors='replace')
    except Exception as e:
        print(f'[direto] Erro no POST: {e}')
        return None

    html_lower = html.lower()

    # Servidor rejeitou por captcha?
    if any(kw in html_lower for kw in ('captcha', 'anti-rob', 'robô', 'robot', 'verificação de segurança')):
        print('[direto] Servidor exige captcha — tentando modo manual...')
        return None

    # Tem resultado?
    if any(kw in html_lower for kw in ('situacao', 'regular', 'irregular', 'suspen', 'cancel', 'nula', 'pendente')):
        return _parse_result(html)

    print('[direto] Resposta inesperada — primeiros 400 chars:')
    print(html[:400])
    return None


# ── Estratégia 2: Playwright semi-automático (você resolve o captcha) ─────────

async def consultar_playwright(cpf: str, data_nascimento: str) -> dict | None:
    """
    Abre o navegador, preenche o formulário e pausa para você resolver o hCaptcha.
    Depois captura o resultado automaticamente.
    """
    from playwright.async_api import async_playwright

    cpf_digits   = re.sub(r'\D', '', cpf)
    dob_digits   = re.sub(r'\D', '', data_nascimento)  # ddmmaaaa

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,          # Navegador visível — você resolve o captcha
            slow_mo=80,
            args=['--disable-blink-features=AutomationControlled'],
        )
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='pt-BR',
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        print('Abrindo página da Receita Federal...')
        await page.goto(FORM_URL, wait_until='networkidle', timeout=30000)

        # Preenche CPF
        await page.locator('#txtCPF').click()
        await page.locator('#txtCPF').fill(cpf_digits)
        await page.wait_for_timeout(400)

        # Preenche data de nascimento
        await page.locator('#txtDataNascimento').click()
        await page.locator('#txtDataNascimento').fill(dob_digits)
        await page.keyboard.press('Tab')
        await page.wait_for_timeout(400)

        print('\n' + '='*55)
        print('  ✋ Resolva o hCaptcha no navegador e clique em "Consultar".')
        print('     Aguardando resultado...')
        print('='*55 + '\n')

        # Aguarda navegação para a página de resultado (até 2 minutos)
        try:
            await page.wait_for_url('**/ConsultaPublicaExibir.asp**', timeout=120_000)
        except Exception:
            # Tenta esperar por elemento de resultado na própria página
            try:
                await page.wait_for_selector('[id*="situacao"], [class*="situacao"], .resultado', timeout=120_000)
            except Exception:
                print('Timeout: nenhum resultado detectado em 2 minutos.')
                await browser.close()
                return None

        html = await page.content()
        await page.screenshot(path='resultado_cpf.png', full_page=True)
        print('Screenshot salvo em resultado_cpf.png')

        await browser.close()
        return _parse_result(html)


# ── Entrada principal ─────────────────────────────────────────────────────────

def main():
    cpf = input('CPF (apenas números ou formatado): ').strip() or '01579823270'
    dob = input('Data de nascimento (ddmmaaaa ou dd/mm/aaaa): ').strip() or '02121991'

    print(f'\nConsultando CPF {cpf}...\n')

    # Tentativa 1: POST direto
    resultado = consultar_direto(cpf, dob)

    # Tentativa 2: Playwright semi-automático
    if resultado is None:
        resultado = asyncio.run(consultar_playwright(cpf, dob))

    if resultado:
        print('\n✅ RESULTADO:')
        for k, v in resultado.items():
            if k != 'raw_snippet' and v:
                print(f'  {k.capitalize()}: {v}')
        if resultado.get('raw_snippet'):
            print(f'\n  [Trecho bruto]: {resultado["raw_snippet"][:200]}')
    else:
        print('\n❌ Não foi possível obter o resultado.')


if __name__ == '__main__':
    main()
