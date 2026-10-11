# Auditoria técnica do VibeGuard

> Relatório histórico anterior às alterações. As correções e a validação posterior estão em [CORRECOES.md](CORRECOES.md).

Data: 10/10/2026, America/Bahia. Revisão de referência: `4648baf`. Os caminhos e números de linha registrados abaixo descrevem aquela revisão histórica; os links apontam para os arquivos atuais.

## Resultado

Foram identificados **38 grupos de problemas**. Há falhas que permitem aprovar código quebrado, perder conteúdo do índice Git, restaurar uma árvore incompleta e escrever fora do projeto. Os testes existentes passam, mas não cobrem esses cenários.

A análise abrangeu os módulos Python, parsers, CLI, Git/hook, snapshots/baseline, watcher, rewind, servidor HTTP, serviço do painel, HTML/CSS/JavaScript, MCP, badges, remediação, GitHub Action, workflow, configuração, documentação, empacotamento e testes. Nenhum código de produção foi modificado. As operações de Git, rewind e HTTP usadas nas reproduções ocorreram em diretórios temporários.

P1 = corrigir com prioridade por perda de dados, exposição ou aprovação indevida relevante. P2 = defeito funcional ou cobertura importante, com gatilho específico. As prioridades consideram o comportamento do produto, e não apenas a facilidade de exploração.

## Verificações realizadas

| Verificação | Resultado |
|---|---|
| `python3 -m unittest discover -s tests` | 162 testes, OK; execução registrada em 4,990 s |
| `python3 -m compileall -q vibeguard` | Sem erros de compilação Python |
| `node --check vibeguard/ui_assets/app.js` | Sintaxe estática válida |
| `node --check vibeguard/ui_assets/api.js` | Sintaxe estática válida |
| `DriftEngine('.').evaluate()` no próprio projeto | 0 achados, baseline presente |
| Reproduções adicionais | Confirmaram os casos descritos abaixo, fora da cobertura existente |

Ambiente principal: Python 3.14.4 e Node 20.18.0. Compatibilidade Python 3.8 foi investigada por inspeção e simulação explícita da ausência de uma API; não há um interpretador 3.8 instalado. Não foram feitas chamadas reais ao GitHub nem testes em IDEs externas. As reproduções do frontend usaram um harness Node para gerar e avaliar os handlers; não constituem teste completo em navegador. Os casos de erro de auditoria e concorrência foram induzidos de forma controlada.

O resultado limpo da autoauditoria não contradiz os bugs: o motor verifica somente seus invariantes implementados, e vários achados são falhas justamente nessa implementação.

## Achados

### A01 — P1: `--staged` analisa o disco, não o conteúdo que será commitado

**Local:** [vibeguard/cli.py:123](vibeguard/cli.py), [vibeguard/snapshot.py:45](vibeguard/snapshot.py), [vibeguard/drift.py:97](vibeguard/drift.py). Também afeta o serviço do painel e o MCP.

**Reprodução:** adicionar um import inexistente a `app.py`, executar `git add app.py` e remover o import somente do disco. `git show :app.py` continua contendo o import inválido. `check --staged` retornou **exit 0 e “All clear”**.

**Causa e impacto:** o Git fornece apenas nomes; o snapshot lê `filepath.read_text()`. O hook pode deixar passar erros/segredos que permanecem no índice. O inverso também pode bloquear um commit válido por alterações não staged.

**Correção:** analisar os blobs do índice e representar exclusões do índice; resolver também configuração/manifestos relevantes para esse estado.

### A02 — P1: rewind não restaura a ausência de arquivos

**Local:** [vibeguard/rewind.py:208](vibeguard/rewind.py), limpeza em `:216`.

**Reprodução:** criar checkpoint, criar `new.txt`, fazer `git add new.txt` e voltar. Resultado: **success, removed_count=0, arquivo ainda existe**. Capturar uma exclusão e recriar o arquivo depois também deixa conteúdo posterior no lugar.

**Causa:** `git checkout commit -- .` escreve entradas existentes na árvore; a limpeza só olha arquivos `??` antes do reset. **Correção:** restaurar a árvore e suas ausências explicitamente, preservando HEAD e metadados necessários.

### A03 — P1: checkpoint e safety backup perdem conteúdo exclusivamente staged

**Local:** [vibeguard/rewind.py:127](vibeguard/rewind.py), `:131`, `:230`.

**Reprodução:** HEAD=`base`; índice=`staged`; disco=`working`. Criar checkpoint, alterar e voltar. Resultado: disco=`working`, índice=`base`. O conteúdo `staged` não está representado pelo checkpoint nem pelo backup safety.

**Causa:** `git add -A` no índice temporário salva o disco; `git reset` restaura o índice a partir de HEAD. **Correção:** capturar e restaurar índice e working tree separadamente.

### A04 — P1: rewind sem Git escreve fora do projeto através de symlink

**Local:** [vibeguard/rewind.py:239](vibeguard/rewind.py).

**Reprodução:** checkpoint de `config.txt` regular; substituir o arquivo por link para um arquivo externo; rewind. O link permanece, e o arquivo externo é sobrescrito com o conteúdo do snapshot. Resultado **success**.

**Causa:** `shutil.copy2` segue o destino existente. **Correção:** tratar links e componentes ancestrais explicitamente, sem permitir escrita fora da raiz; incluir o tipo de entrada no snapshot.

### A05 — P1: API do painel aceita operações de escrita sem autenticação nem validação de origem

**Local:** [vibeguard/server.py:38](vibeguard/server.py), `:43`, `:148`.

**Reprodução:** POST `/api/config` com `Origin: https://untrusted.example`, sem token, retornou **HTTP 200, Access-Control-Allow-Origin: \*** e persistiu a alteração.

Os endpoints permitem trocar projeto, alterar configuração, atualizar baseline, instalar hook e executar rewind. A exposição aumenta ao usar `--host 0.0.0.0`. A aceitação no servidor foi confirmada; o comportamento de acesso por páginas externas pode depender das políticas do navegador.

**Correção:** autenticação de sessão, validação de Host/Origin e CORS restrito, especialmente nas operações mutantes.

### A06 — P1: injeção no frontend e botões de cópia quebrados

**Local:** [vibeguard/ui_assets/app.js:98](vibeguard/ui_assets/app.js), `:117`; [vibeguard/ui_assets/api.js:26](vibeguard/ui_assets/api.js).

**Reprodução funcional:** remediação normal `Re-implement 'hello'...` gera `onclick="window.copyRemedy('Re-implement%20'hello'...')"`. O handler lança **SyntaxError: missing ) after argument list**; `encodeURIComponent` não codifica apóstrofos.

**Reprodução de injeção:** um nome `x');window.auditSentinel=1;//.py` gerou handler que atribuiu o marcador ao ser avaliado. Escape HTML é decodificado pelo HTML e não protege a string JavaScript dentro do atributo. `showToast` também coloca mensagens, incluindo caminhos/erros, diretamente em `innerHTML`.

**Correção:** criar elementos/event listeners e usar `textContent`/`dataset`, sem construir handlers inline com dados do projeto.

### A07 — P1: instalação do hook sobrescreve proteção existente

**Local:** [vibeguard/git_utils.py:192](vibeguard/git_utils.py), `:201`.

**Reprodução:** criar `pre-commit` contendo `CUSTOM_PROTECTION`; instalar o hook do VibeGuard. O conteúdo anterior desaparece sem backup ou encadeamento.

**Correção:** preservar e integrar hooks existentes, ou recusar substituição sem uma escolha explícita. A reinstalação do próprio hook pode ser idempotente.

### A08 — P1: watcher perde alterações feitas durante o debounce

**Local:** [vibeguard/watcher.py:105](vibeguard/watcher.py).

**Reprodução controlada:** alterar `a.py` antes do debounce e colocar um stub em `b.py` durante o debounce. Relatórios: inicial limpo; mudança somente em `a.py`, limpa; nenhuma avaliação posterior de `b.py`. Auditoria completa detectou o stub.

**Causa:** atualiza `_mtimes` com ambos os arquivos, mas avalia apenas o conjunto calculado antes de dormir. **Correção:** recomputar/acumular alterações antes de avançar o estado observado.

### A09 — P1: checagens staged/diff de subprojetos podem avaliar zero arquivos

**Local:** [vibeguard/ui.py:109](vibeguard/ui.py), `:115`; [vibeguard/mcp.py:105](vibeguard/mcp.py); [vibeguard/snapshot.py:138](vibeguard/snapshot.py).

**Reprodução:** selecionar `repo/service`; alterar e stagear `service/api.py` com import inexistente. Git retorna `service/api.py`, enquanto o snapshot usa `api.py`. Painel staged retorna **0 achados**; full retorna **1**.

**Correção:** normalizar os caminhos do Git em relação à raiz efetivamente auditada. A CLI também precisa definir um comportamento consistente entre a raiz Git e `--project`.

### A10 — P1: Python 3.8 anunciado não consegue iniciar a CLI

**Local:** [vibeguard/cli.py:366](vibeguard/cli.py); [pyproject.toml:10](pyproject.toml); [setup.py:18](setup.py).

O parser usa `argparse.BooleanOptionalAction`, ausente no Python 3.8, em toda chamada a `main()`. Simulando a ausência dessa API, até `main(['--help'])` lança **AttributeError**.

**Correção:** implementar flags positivas/negativas compatíveis ou ajustar o requisito mínimo e a documentação. A simulação não substitui um teste real da matriz de versões.

### A11 — P1: funções comentadas continuam satisfazendo a baseline

**Local:** [vibeguard/parser_js.py:250](vibeguard/parser_js.py), [vibeguard/parser_go.py:95](vibeguard/parser_go.py), [vibeguard/parser_rust.py:119](vibeguard/parser_rust.py).

**Reprodução:** transformar `export function ping(x) { return x; }` em comentário `// ...` retorna **0 achados**. Reproduzido também com funções Go/Rust dentro de `/* ... */`.

**Causa:** extração de declarações usa linhas originais, incluindo comentários. **Correção:** extrair símbolos de conteúdo com comentários/strings mascarados, conservando posições.

### A12 — P1: tornar um símbolo privado não quebra o contrato para o motor

**Local:** [vibeguard/drift.py:156](vibeguard/drift.py), [vibeguard/mcp.py:126](vibeguard/mcp.py).

**Reprodução:** JS `export function ping(x)` → `function ping(x)`; Java método `public` → `private`. Ambos retornam **0 achados**.

**Causa:** símbolos atuais não precisam continuar exportados para corresponder aos da baseline. **Correção:** verificar espécie, visibilidade e contexto de exportação ao comparar contratos.

### A13 — P2: assinaturas Python incompatíveis podem parecer idênticas

**Local:** [vibeguard/analyzer.py:243](vibeguard/analyzer.py), [vibeguard/drift.py:174](vibeguard/drift.py).

**Reprodução:** `def ping(x=1)` → `def ping(x)`; `def ping(x)` → `def ping(x, /)`. Ambos retornam **0 achados**, embora chamadas antes válidas quebrem.

**Causa:** serializa somente nomes, sem defaults ou categorias de argumento. **Correção:** guardar e comparar esses atributos do contrato.

### A14 — P2: opções de secrets e ghost declarations não alteram o bloqueio

**Local:** [vibeguard/config.py:45](vibeguard/config.py), [vibeguard/drift.py:213](vibeguard/drift.py), `:242`; lógica equivalente no MCP.

**Reprodução:** definir `fail_on_secret_leak=False` e `fail_on_ghost_declarations=False`. Credencial e stub continuam gerando **critical/error e blocking=True**, igual às opções ativadas.

**Correção:** definir a semântica das opções e aplicá-la às severidades/decisão de bloqueio, compartilhando a política entre interfaces.

### A15 — P2: `from unittest import mock` burla a regra de import proibido

**Local:** [vibeguard/analyzer.py:185](vibeguard/analyzer.py), [vibeguard/drift.py:319](vibeguard/drift.py).

Com `forbidden=['unittest.mock']`, `import unittest.mock` gera violação, mas `from unittest import mock` guarda apenas `unittest` e passa.

**Correção:** preservar dados suficientes de `ImportFrom` para avaliar submódulos proibidos equivalentes, sem confundir atributos com módulos.

### A16 — P2: remoção de uma sobrecarga Java passa despercebida

**Local:** [vibeguard/parser_java.py:89](vibeguard/parser_java.py), [vibeguard/drift.py:156](vibeguard/drift.py).

**Reprodução:** baseline com `App.ping()` e `App.ping(int x)`; excluir apenas a primeira. Resultado **0 achados**.

**Causa:** dicionário por nome mantém apenas uma sobrecarga. **Correção:** identificar contratos Java por nome e assinatura.

### A17 — P2: stubs comuns não são reconhecidos

**Local:** [vibeguard/analyzer.py:256](vibeguard/analyzer.py); caso de classes em `:198`; parser Java.

**Reprodução:** implementação Python substituída por docstring seguida de `pass` retorna **0 achados**, porque o corpo tem duas instruções. `raise NotImplementedError('pending')` e método Java `public void ping() {}` também passam.

**Correção:** separar docstrings do corpo efetivo e reconhecer padrões de não implementação, com tratamento para APIs abstratas/intencionais.

### A18 — P2: getter/setter Python válido é bloqueado como duplicação

**Local:** [vibeguard/analyzer.py:232](vibeguard/analyzer.py).

**Reprodução:** `@property def value(...)` com `@value.setter def value(...)` gera **duplicate_declaration/error**. Executando a classe, atribuir e ler `value=2` funciona.

**Correção:** distinguir composição de propriedades e outras declarações semanticamente válidas de redefinições acidentais.

### A19 — P2: chave genérica de credencial em dicionário não é detectada

**Local:** [vibeguard/analyzer.py:19](vibeguard/analyzer.py).

**Reprodução:** a mesma string sintética de alta entropia é detectada em `api_key = '...'`, mas não em `config = {'api_key': '...'}`.

**Causa:** regex não aceita a aspa que fecha o nome da chave antes de `:`. **Correção:** cobrir chaves entre aspas sem remover os filtros contra falsos positivos.

### A20 — P2: dependências legítimas são classificadas como alucinadas

**Local:** [vibeguard/dependency.py:163](vibeguard/dependency.py), `:199`, `:228`, `:260`, `:273`, `:299`.

Casos confirmados, agrupados aqui por subsistema:

- `requests[socks]>=2.31` e `requests>2.31` em requirements não são normalizados; `import requests` falha quando não há distribuição instalada para compensar o erro.
- Distribuição instalada `PyYAML` não valida `yaml`; o mapeamento aplicado aos manifestos não é aplicado às distribuições instaladas. Este caso foi simulado com metadata controlada.
- `protobuf` declarada registra `google.protobuf`, mas a comparação usa apenas `google`, rejeitando o import.
- Cargo `[target.'cfg(unix)'.dependencies] libc='0.2'` é ignorado e `libc` é rejeitado.
- Node `stream/promises` é rejeitado, enquanto `stream` e `node:stream/promises` passam.

**Correção:** normalizar requisitos, unificar nomes de distribuição/módulo e namespaces, percorrer dependências por target Cargo e reconhecer subpaths dos builtins Node.

### A21 — P2: MCP aceita duplicações que a checagem normal bloqueia

**Local:** [vibeguard/mcp.py:158](vibeguard/mcp.py), `:165`.

**Reprodução:** duas definições de `ping()` retornam **valid=true, blocking_count=0, warning_count=1** em `validate_code`; a checagem normal retorna **duplicate_declaration/error**.

**Correção:** compartilhar a classificação e a política de severidade com o DriftEngine.

### A22 — P2: remediação gera código inválido para métodos/classes e perde tipos

**Local:** [vibeguard/remediation.py:195](vibeguard/remediation.py).

**Reprodução:** remover `Service.run` gera `def Service.run(self, value):`. `git apply` retorna **0**, mas a análise sintática Python lança **SyntaxError: expected '('**. Go/Rust também recebem parâmetros sem tipos, como `func StartServer(port)` e `pub fn compute_hash(data)`.

**Correção:** preservar espécie, escopo e assinatura completos; validar a sintaxe do resultado. Quando a baseline não contém informação suficiente, fornecer orientação em vez de um patch apresentado como restaurador de contratos.

### A23 — P2: arquivo sem newline final gera patch corrompido

**Local:** [vibeguard/remediation.py:228](vibeguard/remediation.py).

**Reprodução:** arquivo terminando em `x = 1` sem LF gera linha `-x = 1+x = 1` no diff. `git apply --check` retorna **128, corrupt patch**.

**Correção:** tratar a ausência de newline corretamente no unified diff e verificar aplicabilidade do patch gerado.

### A24 — P2: badge fica verde quando a auditoria falha

**Local:** [vibeguard/badge.py:261](vibeguard/badge.py); equivalente em [vibeguard/ui.py:315](vibeguard/ui.py).

**Reprodução com falha induzida:** `DriftEngine.evaluate` lança `OSError`; `get_project_state` retorna **verified / 100% Verified / 0 findings**.

**Correção:** propagar o erro ou expor estado explícito de falha. Ausência de relatório válido não deve significar verificação bem-sucedida.

### A25 — P2: painel exibe informações antigas como auditoria atual

**Local:** [vibeguard/ui.py:163](vibeguard/ui.py), `:188`, `:220`, `:314`; refresh em [vibeguard/ui_assets/app.js:246](vibeguard/ui_assets/app.js).

**Reprodução:** criar baseline limpa, executar check e depois inserir import inexistente. `get_dependencies` retorna **0 imports**, `get_prompt` conserva **0 achados** e `get_badge` conserva **verified**; auditoria nova detecta **1 achado**.

**Causa:** dependências vêm da baseline; prompt/badge reaproveitam `last_report` sem invalidar por mudanças. O botão Refresh não executa uma nova auditoria.

**Correção:** mostrar explicitamente contratos da baseline, auditar dependências atuais e invalidar/atualizar relatórios por alterações, configurações, baseline, rewind e escopo da checagem.

### A26 — P1: troca de projeto concorrente salva baseline no projeto errado

**Local:** [vibeguard/ui.py:48](vibeguard/ui.py), `:132`; servidor multithread em [vibeguard/server.py:232](vibeguard/server.py).

**Reprodução com interleaving controlado:** iniciar snapshot de A, pausar após capturá-lo, selecionar B e retomar. Resultado: **A sem baseline; baseline de B contém `only_a.py`**.

**Causa:** `update_baseline` usa `self.snapshot_engine` novamente após a captura; apenas algumas operações usam o lock. **Correção:** preservar contexto imutável por operação ou sincronizar todas as operações que dependem da seleção de projeto.

### A27 — P2: configuração inválida é persistida e quebra checks posteriores

**Local:** [vibeguard/config.py:73](vibeguard/config.py), [vibeguard/ui.py:285](vibeguard/ui.py), [vibeguard/server.py:198](vibeguard/server.py).

**Reprodução:** POST `/api/config` com `{"max_file_lines":null}` retorna **200**. Check seguinte lança **TypeError: '>' not supported between instances of 'int' and 'NoneType'**.

**Correção:** validar tipos, limites, listas, regras e severidades antes de salvar; devolver erro claro e manter a configuração anterior válida.

### A28 — P2: `check --json` não produz stdout JSON válido

**Local:** [vibeguard/cli.py:167](vibeguard/cli.py), `:174`.

**Reprodução:** capturar stdout de `check --json` e chamar `json.loads`: **JSONDecodeError na primeira coluna**. Há mensagens humanas/cores antes e, em alguns casos, depois do objeto JSON.

**Correção:** emitir apenas JSON no stdout nesse modo; mensagens auxiliares no stderr. Definir saída estruturada também nos retornos antecipados sem arquivos staged/diff.

### A29 — P1: baseline corrompida desativa proteção de contratos e passa

**Local:** [vibeguard/snapshot.py:180](vibeguard/snapshot.py).

**Reprodução:** trocar baseline existente por JSON truncado. `check` retorna **exit 0**, “No baseline snapshot found” e “All clear”.

**Causa:** JSON inválido e erro de leitura viram `None`, igual a uma baseline nunca criada. **Correção:** distinguir inexistência de falha; bloquear ou exigir ação explícita para baseline inválida, com gravação atômica para reduzir truncamentos.

### A30 — P2: configuração parcial altera inesperadamente o conjunto de arquivos ignorados

**Local:** [vibeguard/config.py:28](vibeguard/config.py), `:93`.

**Reprodução:** `GuardConfig(project_name='x')` ignora `tests/*`; `GuardConfig.from_dict({})` não ignora. Criar um JSON parcial muda o comportamento padrão e pode levar a stubs/fixtures de testes bloqueando checks.

**Correção:** ter uma única origem para os valores padrão, usada na construção e na desserialização.

### A31 — P2: checkpoint quebra em linked worktrees e subdiretórios

**Local:** [vibeguard/rewind.py:123](vibeguard/rewind.py).

**Reprodução:** em linked worktree, `git add -A` com índice temporário sob `root/.git` falha com **exit 128 / Not a directory**. Em subdiretório Git, falha com **No such file or directory**.

**Causa:** `.git` pode ser arquivo, e pode estar fora da raiz solicitada. **Correção:** resolver raiz e diretório Git com Git; usar local correto para o índice temporário.

### A32 — P2: rewind não remove corretamente nomes escapados pelo Git

**Local:** [vibeguard/rewind.py:217](vibeguard/rewind.py), metadata em `:147`, árvore em `:211`.

**Reprodução:** criar `não.py` não rastreado depois do checkpoint. Rewind retorna **success, removed_count=0** e mantém o arquivo, pois usa literalmente a representação com aspas/escapes de `git status`.

**Correção:** usar saída NUL (`-z`) em status e ls-tree; não remover espaços dos nomes de arquivos com `strip()`.

### A33 — P2: snapshot sem Git ignora `.github` e `.gitignore`

**Local:** [vibeguard/rewind.py:158](vibeguard/rewind.py), `:246`.

**Reprodução:** projeto sem Git contendo `.github/config.yml`; checkpoint captura **0 arquivos**. Alterar o arquivo e voltar retorna success, mantendo a alteração.

**Causa:** `startswith('.git')` também exclui `.github`/`.gitignore` e considera ancestrais absolutos. **Correção:** comparar nomes exatos dos diretórios internos em caminhos relativos.

### A34 — P1: inputs da GitHub Action são interpretados como código shell

**Local:** [action.yml:59](action.yml), `:72`.

**Reprodução local do bloco `run`:** fornecer `baseline-file='$(touch /tmp/.../marker)'`. O Bash criou o marcador e terminou com **exit 0**. Aspas duplas não impedem command substitution quando o input é incorporado ao texto do script.

**Correção:** usar as variáveis `INPUT_*` já declaradas no ambiente, expandidas como dados e entre aspas; token pelo ambiente. A reprodução não executou a Action no GitHub.

### A35 — P2: annotations não escapam nomes de arquivo ou mensagens

**Local:** [vibeguard/remediation.py:136](vibeguard/remediation.py).

**Reprodução:** caminho contendo newline e `::notice::` produz uma nova linha de comando no stdout, em vez de uma única annotation. Separadores também podem quebrar as propriedades.

**Correção:** escapar caracteres especiais de conteúdo e propriedades das workflow commands antes de gerar a saída.

### A36 — P1: módulo do projeto auditado substitui o código da Action

**Local:** [action.yml:57](action.yml), `:71`; workflow em [.github/workflows/vibeguard.yml](.github/workflows/vibeguard.yml).

**Reprodução local:** com `PYTHONPATH` apontando primeiro para a Action, criar [vibeguard/cli.py](vibeguard/cli.py) no diretório de trabalho. `python3 -m vibeguard.cli ci` executou **WORKSPACE_SHADOW_MODULE_EXECUTED**, exit 0, em lugar do auditor.

**Causa:** `python -m` dá prioridade ao diretório corrente. No workflow deste projeto, código alterado no PR pode substituir a própria auditoria, com acesso às variáveis entregues à etapa.

**Correção:** executar entrada confiável da Action com caminho de imports controlado e passar explicitamente a raiz auditada. A solução deve respeitar a versão mínima de Python escolhida.

### A37 — P2: MCP não valida argumentos obrigatórios de ferramentas

**Local:** [vibeguard/mcp.py:198](vibeguard/mcp.py), `:304`.

**Reprodução:** chamar `validate_code` para arquivo novo sem `content`, embora seja obrigatório no schema anunciado, retorna **valid=true / isError=false**. O conteúdo ausente vira string vazia.

**Correção:** validar campos obrigatórios e tipos antes de executar handlers, diferenciando ausência de conteúdo vazio intencional.

### A38 — P2: mensagem malformada impede processamento das próximas mensagens MCP

**Local:** [vibeguard/mcp.py:340](vibeguard/mcp.py).

**Reprodução:** entrada `{"broken"}\n` seguida de um ping JSON-RPC válido retorna somente Parse error no EOF e perde a resposta ao ping. Num stream aberto, o erro fica pendente.

**Causa:** falha de decode começando com `{`/`[` é sempre considerada mensagem incompleta; o buffer não é recuperado. **Correção:** tratar a delimitação de mensagens e isolar linhas inválidas, sem perder mensagens subsequentes.

## Observação adicional sobre comentários de PR

[vibeguard/github_action.py:237](vibeguard/github_action.py) seleciona qualquer comentário com o marcador sticky, sem verificar autoria. Um mock confirmou PATCH em comentário de terceiro. Isso pode escolher o comentário errado; o resultado de permissões no GitHub não foi verificado, portanto não se afirma um HTTP 403 garantido nem exploração externa confirmada.

## Ordem sugerida de correção

1. Integridade de dados: A02, A03, A04, A07, A26 e A31.
2. Fronteiras de execução/escrita: A05, A06, A34 e A36.
3. Aprovações incorretas: A01, A08, A09, A11, A12, A21, A24 e A29.
4. Compatibilidade e funcionalidade: demais casos, incluindo assinaturas, propriedades, imports, remediação e saída JSON.

Cada correção deve começar por um teste de regressão do caso correspondente. Para rewind, incluir conteúdo do índice, arquivos ausentes, links, Unicode, worktrees e projetos sem Git. Para auditoria, testar equivalência entre CLI, UI e MCP e distinguir relatórios parciais de uma aprovação de todo o projeto.

Esta revisão identifica defeitos reproduzidos e limites específicos; não certifica ausência de outros bugs, compatibilidade com todas as versões/linguagens nem execução real de todas as integrações externas.
