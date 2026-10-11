# Correções das auditorias do VibeGuard

Branch: `codex/audit-fixes`. Referência anterior: `4648baf`.

Os 38 grupos de problemas de `AUDITORIA.md` receberam correções e testes de regressão. A revisão independente das alterações encontrou mais quatro casos, também reproduzidos e corrigidos. Este documento registra o resultado; o relatório original permanece como histórico dos defeitos anteriores.

## Comparação com a auditoria do Claude

| Relato do Claude | Conferência e resultado |
|---|---|
| Python 3.8 / BooleanOptionalAction | Confirmado ao construir a CLI, não ao importar o módulo. Flags substituídas por opções compatíveis. |
| Instalação sobrescreve hooks | Confirmado. Hook anterior é preservado, encadeado e restaurado na desinstalação. Mensagem após bloqueio agora é alcançável. |
| Remediação inventa declarações inválidas | Confirmado. Baseline estrutural não contém a implementação removida; patches agora oferecem orientação válida, sem fabricar uma implementação nem aprovar o contrato ausente. |
| Checkpoints quebram em worktrees | Confirmado. Raiz e diretório Git são obtidos pelo próprio Git. |
| API aceita origem externa sem autenticação | Confirmado no servidor. API exige token de sessão, Host permitido e Origin correspondente; CORS wildcard removido. |
| Badge verde após falha | Confirmado. Falha produz estado `error` e selo cinza. Erros de leitura/parser também interrompem o snapshot. |
| Rewind deixa arquivos posteriores | Confirmado. Restauração inclui ausências e preserva disco e índice separadamente. |
| Rust, overload Python, Java, defaults, dependências, URL, debounce e subprojetos | Confirmados e cobertos por regressões. |
| MCP exige Content-Length | Não confirmado: o transporte stdio oficial delimita mensagens por newline. Mantido esse transporte e corrigida a recuperação após mensagens inválidas/truncadas. [Especificação oficial](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports). |

## Cobertura das correções originais

| Achados | Alteração |
|---|---|
| A01, A09 | CLI, painel e MCP compartilham avaliação; `--staged` exporta o índice inteiro, incluindo fontes, manifests e configuração. Caminhos são relativos ao subprojeto selecionado. |
| A02–A04, A31–A33 | Checkpoints preservam bytes do disco, índice e blobs staged; restauração de ausências, Unicode, worktrees, links e arquivos `.github`/`.gitignore`. |
| A05–A07 | Autenticação HTTP e origem restrita; dados do frontend fora de handlers inline; toasts como texto; hooks existentes preservados. |
| A08 | Watcher acumula alterações ocorridas durante debounce antes de avançar seu estado. |
| A10 | CLI deixou de depender de API argparse indisponível no Python 3.8. |
| A11–A13, A16 | Comentários/literais não satisfazem contratos; comparação considera visibilidade, espécie do símbolo, async e metadados de assinatura; sobrecargas Java têm identidade própria. |
| A14–A15, A17–A20 | Flags de bloqueio respeitadas; membros importados entram nas regras; stubs, propriedades, overloads e credenciais corrigidos; dependências normalizadas. |
| A21, A37–A38 | Validação MCP usa o motor comum, valida argumentos e recupera mensagens inválidas sem perder a seguinte. |
| A22–A24 | Patches de orientação sintaticamente válidos e aplicáveis, inclusive arquivos sem newline final; falha de auditoria nunca gera selo verde. |
| A25–A27, A30 | Painel refaz avaliações e dependências atuais; operações de projeto serializadas; configuração validada antes de persistir, com defaults únicos. |
| A28–A29 | `check --json` emite JSON puro; baseline inválida produz erro explícito; gravação da baseline usa substituição atômica. |
| A34–A36 | Inputs da Action passam por ambiente, annotations escapadas e entrypoint isolado evita importar o módulo do projeto auditado. Comentário persistente só edita comentários do autor autenticado. |

## Casos adicionais encontrados durante a revisão

- Uma configuração apenas no disco podia desativar a auditoria do índice. A configuração staged agora vem exclusivamente do índice; se ausente, usa defaults.
- Regex JavaScript com aspas podia esconder funções seguintes. Máscara lexical passou a distinguir regex, comentários e divisão.
- MCP deixou de proteger arquivos novos ausentes da baseline. Restaurado o fallback aos contratos atuais de cada arquivo.
- Rewind podia sobrescrever arquivos ignorados ausentes do safety. Um preflight recusa colisões não capturadas, incluindo links ancestrais e arquivos ignorados recém-stageados, antes de alterar disco/índice.
- Exceções de leitura/parser eram descartadas pelo snapshot. Agora interrompem a avaliação; metadados de símbolo inválidos também são rejeitados.

## Evidências de validação

- A suíte cresceu de 162 para **266 testes**; os resultados da rodada final estão registrados abaixo.
- Testes novos foram executados contra as falhas antes das correções e repetidos depois, incluindo os casos da revisão.
- `python3 -m compileall -q vibeguard tests`: sem erros.
- `ast.parse(..., feature_version=(3,8))`: módulos de produção aceitos pela gramática 3.8; CLI testada com ausência simulada de `BooleanOptionalAction`.
- `node --check` para `app.js` e `api.js`: sem erros. Harness Node verifica cópia de caminhos/apóstrofos, toasts e envio/remoção do token; fixtures adicionais verificam regex/divisão.
- `git diff --check`: limpo.
- Wheel construído em cópia temporária, com novos módulos e assets presentes.
- Painel aberto em navegador sobre projeto temporário: carregamento autenticado, remoção do token da URL e acesso após reload verificados, sem erros/warnings de console observados.
- Restaurações destrutivas, hooks, concorrência e requisições HTTP foram testados em projetos temporários.

## Limites e decisões

Os parsers de JavaScript/TypeScript, Java, Go e Rust continuam heurísticos; essas correções não equivalem a compilar integralmente cada linguagem. Baselines antigas continuam carregáveis, mas não permitem comparar tipos/defaults que nunca armazenaram. Checkpoints antigos não têm o índice histórico separado; esse conteúdo não pode ser reconstruído retrospectivamente. Submódulos permanecem repositórios separados.

Patches de remediação orientam a restauração quando não existe implementação salva; o problema permanece bloqueante até ser realmente corrigido. Checkpoints novos armazenam mais dados para permitir recuperação fiel. Colisões com conteúdo ignorado não salvo são recusadas e exigem mover/preservar esse conteúdo primeiro.

Não há runtime Python 3.8 instalado neste ambiente; a suíte executou em Python 3.14. Não houve execução real da Action no GitHub nem integração em IDEs externas. Se o token GitHub não expõe identidade em `/user`, o comentário usa criação em vez de editar comentários de autoria incerta.

A baseline do próprio projeto não foi regenerada para ocultar mudanças de contrato. Alterações permanecem revisáveis no checkout, sem commit, push ou publicação.

## Documentação e preparação dos testes para o GitHub

O README foi complementado com semântica staged/diff, preservação de hooks,
ferramentas MCP, suporte Java, módulos novos e limites dos parsers/checkpoints.
`CONTRIBUTING.md` descreve requisitos, cobertura e comandos de validação.
`.github/workflows/tests.yml` configura execução em pushes/PRs para Python 3.8,
3.11 e 3.14 com Node.js disponível para os testes de frontend.

Nova execução local: **266 testes, OK**, em 13,727 s, registrada em
`/tmp/vibeguard-final-tests.log`. `compileall`, `node --check` para os dois
assets JS, `git diff --check`, gramática Python 3.8 para os 27 módulos de
produção e a autoauditoria (`check --json`: zero achados) também passaram. O
badge versionado foi regenerado para exibir `Protected | No findings`, sem a
promessa indevida de verificação completa. Um wheel construído anteriormente
com `tests/` presente no fonte confirmou que os testes não entram no pacote
instalado. Eles devem permanecer versionados no GitHub.

Uma resposta HTTP de erro aberta pela Action gerava `ResourceWarning`; foi
acrescentada uma asserção de fechamento, verificada primeiro em falha e depois
em sucesso junto da suíte completa.

Ainda é necessário enviar as alterações para o GitHub para que a matriz de CI
execute de fato em Python 3.8, 3.11 e 3.14; a validação de gramática e a suíte
local em Python 3.14 não substituem essas execuções remotas.

A matriz remota foi configurada, mas ainda não executada; este trabalho não
enviou commits ou arquivos ao GitHub.
