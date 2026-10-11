# 🛡️ VibeGuard — The AI Vibe Coding Invariant & Drift Engine

[![VibeGuard Protected](vibeguard-badge.svg)](https://github.com/vibe-guard/vibeguard)

> **Catch AI drift before it lands.** Zero-dependency invariant guard, hallucinated dependency detector, ghost function tracker, and remediation guidance for Vibe Coders and AI Agents.

---

## 💡 O que é o VibeGuard e por que ele é diferente?

No desenvolvimento moderno orientado por IA (*vibe coding* com Claude Code, Cursor, Copilot, Gemini Antigravity, Aider), prompts rápidos criam novos recursos em segundos. Porém, surge um problema crítico e invisível: **AI Architectural Drift (Degradação Silenciosa)**:

1. **APIs Fantasmas & Remoção Silenciosa**: A IA reescreve um arquivo para implementar um novo endpoint e silenciosamente remove uma função ou classe existente que outro módulo dependia.
2. **Dependências Alucinadas**: A IA importa pacotes inexistentes ou esquece de adicionar ao manifesto (`package.json`, `requirements.txt`).
3. **Stubs e Gambiarras Abandonadas**: Funções com `pass`, `...`, ou `// TODO: implement later` que parecem prontas até quebrar em runtime.
4. **Vazamento Acidental de Segredos**: A IA injeta chaves de teste ou API keys hardcoded diretamente no código fonte.
5. **Inchaço Descontrolado**: Arquivos limpos de 100 linhas viram monólitos ilegíveis de 900 linhas com lógica duplicada.

O **VibeGuard** ajuda a detectar esses problemas antes do commit. Ele tira snapshots da arquitetura e contratos públicos do projeto, compara alterações selecionadas, aponta os desvios com severidade e gera orientação para corrigir os problemas. O patch de remediação não contém corpos de implementação ausentes; a correção precisa ser restaurada ou implementada e auditada novamente.

---

## ⚡ Instalação Rápida

O VibeGuard funciona **sem nenhuma dependência externa obrigatória** (apenas Python 3.8+ e a biblioteca padrão).

```bash
# Clone o repositório
git clone https://github.com/vibe-guard/vibeguard.git
cd vibeguard

# Instale via pip (modo editável ou global)
pip install -e .

# Ou execute diretamente sem instalar nada:
./bin/vibeguard --help
```

---

## 🚀 Como Usar no Fluxo de Vibe Coding

### 1. Inicializar o projeto e criar a Baseline
No início do projeto ou antes de rodar prompts pesados na IA:

```bash
vibeguard init
```
Isso criará:
- `.vibeguard.json` (regras e invariantes do seu projeto)
- `.vibeguard/baseline.json` (fotografia estrutural das suas funções, classes, módulos e exportações)

### 2. Deixe a IA codificar ("Vibe Coding")
Deixe o agente ou copiloto gerar código à vontade.

### 3. Instalar Git Hook automático (Pre-commit)
Proteja seus commits de forma automática:
```bash
vibeguard install-hook
```
Isso instala um hook no diretório de hooks configurado pelo Git, respeitando
`core.hooksPath` e worktrees. Um hook anterior é preservado e executado antes do
VibeGuard. Para desinstalar e restaurar o hook anterior, use `vibeguard uninstall-hook`.

### 4. Verificar se houve derivação ou quebras
```bash
# Verificação completa do projeto:
vibeguard check

# Verificação focada apenas nos arquivos em stage (ideal para git hook):
vibeguard check --staged

# Verificação focada apenas nos arquivos modificados nesta sessão de vibe coding:
vibeguard check --diff
```

`--staged` analisa o conteúdo do índice que será commitado, inclusive exclusões,
dependências e configuração staged. Alterações apenas no disco não mudam essa
avaliação. Se a configuração estiver ausente do índice, são usados os defaults.
`--diff` seleciona os caminhos modificados e analisa seu estado atual no disco.
Para um subprojeto, use `vibeguard check --project caminho/do/subprojeto`.

Erros de leitura, de análise ou de baseline interrompem a checagem com exit code
1. `check --json` retorna somente JSON em stdout. Avisos não bloqueiam; findings
`error` e `critical` bloqueiam.
Se a IA removeu funções públicas, alucinou pacotes ou inseriu stubs vazios, o VibeGuard falha imediatamente e mostra exatamente onde ocorreu.

### 5. Formatos de Remediação e Integração com CI/CD / IA
```bash
# Gerar prompt estruturado para devolver à IA corrigir:
vibeguard prompt
# ou
vibeguard check --prompt

# Gerar patch unificado do Git com orientação para restaurar contratos deletados:
vibeguard check --patch

# Exportar no formato padrão SARIF v2.1.0 (GitHub Code Scanning / IDEs):
vibeguard check --sarif

# Gerar anotações nativas para GitHub Actions Workflow:
vibeguard check --annotations
```

Os patches adicionam comentários de orientação com os contratos ausentes. A baseline
armazena metadados, não os corpos das implementações; aplicar o patch não restaura
o comportamento nem aprova a auditoria. Restaure o código original pelo controle
de versão ou implemente o contrato e rode a auditoria novamente.

### 6. Atualizar a baseline após aprovação
Quando você intencionalmente mudar a arquitetura ou adicionar novas funções aprovadas:
```bash
vibeguard baseline
```

### 7. Painel Web Interativo / Dashboard (`vibeguard ui`)
Prefere uma interface visual intuitiva para acompanhar tudo em tempo real em vez de apenas o terminal?

```bash
# Iniciar o painel web (abre seu navegador padrão automaticamente):
vibeguard ui

# Ou especificando porta customizada:
vibeguard ui --port 8585

# Ou através do alias dashboard:
vibeguard dashboard
```

Abra a URL completa exibida pelo launcher, incluindo `#token=...`, para iniciar a
sessão do painel. O token é removido da URL após carregar e mantido na aba; a API
exige o header `X-VibeGuard-Token` e aceita apenas a origem correspondente.

**Recursos do Painel Web:**
- **Zero Dependências Externas**: Construído 100% na biblioteca padrão do Python (`http.server`) com frontend moderno dark-mode (HTML5 + CSS + Vanilla JS, sem necessidade de build/npm).
- **Seletor de Projetos Dinâmico**: Alterne entre diretórios e repositórios diretamente pelo painel.
- **Cards Métricos**: Status de Drift, Contagem de Símbolos Protegidos, Status de Baseline e Estado do Pre-commit Hook.
- **Ações em 1 Clique**: Executar auditoria (Full, Staged ou Diff), atualizar baseline, alternar hook git, copiar orientação de correção para IA e baixar arquivo de patch `.patch`.
- **Explorador de Contratos & Símbolos**: Visualize classes, funções, complexidade ciclomática e contratos de parâmetros.
- **Auditoria de Dependências**: Identifique visualmente importações legítimas vs pacotes alucinados por IA.
- **Editor de Invariantes**: Altere regras, thresholds de linhas e flags de proteção diretamente pela interface.
- **Gerador de Badges**: Gere e copie o snippet de badge do projeto diretamente pelo botão "Copy Badge".

### 8. Badge Oficial para o README (`vibeguard badge`)
Exiba a saúde arquitetural e integridade de contratos do seu projeto com badges SVG oficiais em estilo Shields.io:

- `Protected | No findings` (verde `#00c853` quando a análise não encontra desvios; isso não representa uma verificação formal completa)
- `VibeGuard | Drift Detected` (vermelho `#e53935` quando há derivação ou regressões ativas)
- `VibeGuard | Unanchored` (âmbar `#ffa000` quando o projeto ainda não possui baseline)
- `VibeGuard | Audit Failed` (cinza quando ocorre erro de leitura ou de auditoria)

```bash
# Analisa o projeto, salva vibeguard-badge.svg e exibe o snippet Markdown pronto para o README:
vibeguard badge

# Personalizar caminho de saída ou formatos de saída:
vibeguard badge --output docs/badge.svg
vibeguard badge --markdown
vibeguard badge --html
vibeguard badge --json
```

### 9. Servidor MCP Nativo (`vibeguard mcp`)
O VibeGuard expõe um servidor **Model Context Protocol (MCP)** nativo (JSON-RPC 2.0 sobre `stdio`, zero dependências externas) para integração direta com AI IDEs e agentes (Cursor, Claude Desktop, Antigravity, Windsurf):

```bash
# Iniciar servidor MCP via stdio:
vibeguard mcp

# Exibir snippets de configuração prontos para copiar e colar:
vibeguard mcp --config-snippet

# Instalar/atualizar configuração automaticamente no Cursor (.cursor/mcp.json):
vibeguard mcp --install-cursor

# Instalar/atualizar configuração no Antigravity:
vibeguard mcp --install-antigravity
```

**Ferramentas MCP Expostas para Modelos de IA:**
- `vibeguard_check`: Executa auditoria de drift completa ou focada (`staged`, `diff`) e retorna findings estruturados.
- `vibeguard_validate_code`: Valida código proposto antes de salvar em disco (detecta contratos deletados, imports alucinados, stubs vazios e vazamento de credenciais).
- `vibeguard_get_contracts`: Retorna assinaturas e parâmetros de símbolos protegidos de arquivos ou de todo o workspace.
- `vibeguard_get_invariants`: Retorna regras arquiteturais configuradas no `.vibeguard.json`.
- `vibeguard_baseline`: Captura ou atualiza o snapshot arquitetural de baseline.
- `vibeguard_remediate`: Gera orientação e patch unificado com anotações; não restaura implementações ausentes.
- `vibeguard_checkpoint`: Captura o estado da sessão antes de alterações.
- `vibeguard_rewind`: Restaura um checkpoint específico ou volta passos no histórico.

O transporte stdio usa uma mensagem JSON por linha. `vibeguard_validate_code`
exige `file_path` e `content` e utiliza as mesmas regras do motor de auditoria.
Quando um arquivo ainda não consta da baseline, seus contratos atuais no disco
servem de referência para validar o código proposto.

---

## 🌐 Suporte Multi-Linguagem

O VibeGuard protege stacks poliglota out-of-the-box:
- **Python (`.py`)**: AST nativo completo, stdlib Python 3.8-3.14, virtualenv/pip, `pyproject.toml` (PEP 621 e Poetry).
- **JavaScript / TypeScript / React (`.js`, `.jsx`, `.ts`, `.tsx`)**: Componentes funcionais React, interfaces, types, classes, métodos públicos/privados, `package.json` e monorepos.
- **Go (`.go`)**: Packages, funções e métodos com receiver, structs, interfaces, stdlib Go e `go.mod`.
- **Rust (`.rs`)**: Crates, traits, structs, enums, impl blocks, stdlib Rust e `Cargo.toml`.
- **Java (`.java`)**: Classes, métodos, visibilidade, sobrecargas por tipos de parâmetros e stubs vazios.

Python é analisado por AST. Os demais parsers são heurísticos: o VibeGuard não
substitui compiladores, testes funcionais ou verificadores de tipos dessas linguagens.

### 10. Vibe Rewind: Máquina do Tempo para Vibe Coders (`vibeguard rewind`)
Quando programamos com IA, é comum o modelo realizar alterações destrutivas em vários arquivos simultaneamente, apagar métodos silenciosamente ou gerar arquivos novos desnecessários. O comando tradicional `git checkout` ou `git reset` é perigoso porque pode deletar arquivos sem chance de recuperação ou poluir o histórico do Git com commits de teste.

O **Vibe Rewind** cria checkpoints do estado do projeto com backup de segurança antes da restauração:

```bash
# 1. Criar um checkpoint antes de dar um prompt complexo para a IA:
vibeguard checkpoint "Antes do refactor de autenticacao"

# 2. Se a IA quebrar tudo, volte no tempo com 1 comando:
vibeguard rewind

# 3. Voltar a um checkpoint específico ou N passos atrás:
vibeguard rewind --to cp_20261010_213500_abc
vibeguard rewind --step 2

# 4. Listar histórico de checkpoints da sessão:
vibeguard checkpoints
# ou
vibeguard rewind --list

# 5. Ver o diff antes de reverter:
vibeguard rewind --diff cp_20261010_213500_abc
```

> [!TIP]
> **Backup antes de restaurar**: O `vibeguard rewind` cria um ponto de restauração safety do estado anterior para permitir recuperar os arquivos e o índice capturados.

Checkpoints novos preservam o índice Git separadamente do conteúdo no disco.
Conteúdo ignorado pelo Git não entra no backup: se ele colidir com um arquivo a
restaurar, o rewind recusa a operação para que você preserve ou mova esse conteúdo.
Checkpoints antigos continuam utilizáveis, mas não contêm o índice histórico separado.

### 11. Detector de Duplicações da IA
Modelos de IA frequentemente geram funções com nomes idênticos no mesmo arquivo ou duplicam declarações por confusão de contexto. Em linguagens como Python e JavaScript, isso sobrescreve silenciosamente a função anterior em tempo de execução sem disparar erro de sintaxe.
O VibeGuard identifica declarações duplicadas e alerta como violação de invariante (`duplicate_declaration`), impedindo bugs silenciosos.

### 12. GitHub Action Oficial para Pull Requests (`action.yml`)
Bloqueie pull requests com regressões ou código alucinado e receba um relatório no PR:

```yaml
name: VibeGuard Audit
on: [pull_request]

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: vibe-guard/vibeguard@v1
        with:
          fail-on-drift: true
          comment-pr: true
          create-patch: true
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

---

## 🧩 Regras e Invariantes Customizáveis (`.vibeguard.json`)

Você pode declarar regras estritas no `.vibeguard.json`:

```json
{
  "project_name": "meu-app",
  "max_file_lines": 400,
  "fail_on_hallucinated_imports": true,
  "fail_on_ghost_declarations": true,
  "fail_on_secret_leak": true,
  "rules": [
    {
      "id": "no-mock-in-production",
      "description": "Proibir unittest.mock em código de produção",
      "rule_type": "forbidden_import",
      "target_patterns": ["src/*.py"],
      "parameters": {
        "forbidden": ["unittest.mock", "mock"]
      },
      "severity": "error"
    }
  ]
}
```

---

## 🧪 Rodando os Testes Automatizados

Os testes ficam em `tests/` e devem ser versionados junto com o código no GitHub.
Eles fazem parte do desenvolvimento e não são incluídos no pacote wheel instalado.
Para executar a suíte a partir da raiz do repositório, tenha Python e Git disponíveis:

```bash
python3 -m unittest discover -s tests -v
```

Node.js é necessário para executar os testes de frontend; quando não está
disponível, esses testes são marcados como skipped. Não é necessário instalar
pytest nem dependências Python de runtime.

O workflow [tests.yml](.github/workflows/tests.yml) foi configurado para executar
a suíte, compilação Python e verificações de sintaxe JavaScript em pushes e pull
requests, com Python 3.8, 3.11 e 3.14. A execução remota ocorre após enviar os
arquivos ao GitHub. Consulte [CONTRIBUTING.md](CONTRIBUTING.md) para requisitos,
cobertura e verificações antes de enviar uma alteração.

---

## 🛡️ Arquitetura do VibeGuard

- **`models.py`**: Estruturas de símbolos e análises, incluindo tipos/defaults de parâmetros, retorno, identidade de sobrecargas e membros importados.
- **`config.py`**: Configuração e regras validadas antes da persistência, com defaults consistentes.
- **`analyzer.py`**: Motor estático multi-linguagem que coordena extração de símbolos, stubs, placeholders e credenciais.
- **`analyzer_utils.py`**: Métricas de entropia e complexidade usadas pelo analisador.
- **`parser_js.py`, `parser_go.py`, `parser_rust.py`, `parser_java.py`**: Parsers heurísticos específicos por linguagem.
- **`parser_utils.py`**: Máscara lexical de comentários/literais e divisão de parâmetros; mantém posições para os parsers.
- **`dependency.py`**: Verificador cruzado de integridade de dependências (Python stdlib/pip/pyproject; Node builtins/npm; Go stdlib/go.mod; Rust stdlib/Cargo.toml).
- **`git_utils.py`**: Integração com Git para hook pre-commit, inspeção de arquivos staged (`--staged`) e modificados (`--diff`).
- **`snapshot.py`**: Captura metadados estruturais, rejeita falhas de análise/baseline e salva a baseline por substituição atômica.
- **`evaluation.py`**: Avaliação compartilhada entre CLI, painel e MCP; resolve caminhos de subprojetos e exporta o índice para checagens staged.
- **`drift.py`**: Engine de comparação diferencial de invariantes arquiteturais.
- **`drift_serialization.py`**: Serialização dos relatórios de auditoria para JSON.
- **`remediation.py`**: Gera prompts de correção, SARIF, annotations escapadas e patches de orientação. Não recupera corpos de implementação ausentes da baseline.
- **`cli.py`**: Interface de linha de comando com feedback colorido e saída JSON/SARIF para CI/CD.
- **`ui.py`**: Serviço do painel, com avaliações atuais e operações de projeto serializadas para evitar trocas concorrentes de estado.
- **`server.py`**: Servidor HTTP local com roteamento REST, autenticação por token de sessão e validação de Host/Origin.
- **`mcp.py`**: Servidor Model Context Protocol (MCP) JSON-RPC 2.0 nativo sobre stdio (zero dependências) para integração com agentes de IA (Cursor, Claude Desktop, Antigravity, Windsurf).
- **`badge.py`**: Gerador oficial de badges SVG vetoriais nítidos estilo Shields.io (zero dependências).
- **`github_action.py`**: Ação GitHub nativa e bot de PR com comentário sticky inteligente e resumos de step.
- **`rewind.py`**: Captura e restaura disco/índice separadamente, preserva HEAD, cria safety e recusa colisões com conteúdo não capturado.
- **`rewind_support.py`, `rewind_cli.py`**: Tratamento seguro de caminhos/árvores Git e apresentação dos comandos de checkpoint/rewind.
- **`watcher.py`**: Monitor de arquivos que acumula alterações durante o debounce.
- **`ui_assets/`**: Interface web moderna dark-mode (HTML5, CSS3 responsivo e Vanilla JS com zero dependências externas).

Baselines antigas continuam carregáveis; tipos/defaults que elas não armazenaram
só podem ser comparados depois de uma atualização intencional da baseline.
O histórico das falhas e correções está em [AUDITORIA.md](AUDITORIA.md) e
[CORRECOES.md](CORRECOES.md).

