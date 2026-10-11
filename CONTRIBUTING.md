# Desenvolvimento e testes

## Preparar o ambiente

Use Python 3.8 ou posterior, Git e Bash. Os testes de hooks executam scripts Bash,
e os testes de checkpoint criam repositórios Git temporários. A suíte foi
executada localmente em Linux com Python 3.14; a execução em outros sistemas não
foi validada nesta auditoria.

Instale Node.js para executar os testes de frontend. Sem Node, esses testes são
marcados como skipped. O VibeGuard não exige dependências Python de runtime nem
pytest para sua suíte. A instalação editável é opcional para usar o comando CLI:

```bash
python3 -m pip install -e .
```

## Verificar uma alteração

Execute na raiz do repositório:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q vibeguard tests
node --check vibeguard/ui_assets/api.js
node --check vibeguard/ui_assets/app.js
git diff --check
```

Para executar apenas um módulo de testes:

```bash
python3 -m unittest discover -s tests -p test_rewind_regressions.py -v
```

Ao corrigir um bug, inclua um teste que reproduza o comportamento incorreto e
verifique o resultado esperado. Use diretórios temporários para operações de
restauração, hooks e modificações de arquivos. Não atualize a baseline apenas
para fazer uma auditoria passar; alterações intencionais de contrato precisam
ser revisadas antes de atualizar a referência.

## O que a suíte cobre

| Área | Verificações |
|---|---|
| Análise e parsers | Contratos, stubs, overloads/propriedades, comentários/literais, regex, credenciais e regras |
| Dependências e configuração | Imports legítimos/inexistentes, manifests, defaults e validação antes da persistência |
| Git e rewind | Índice versus disco, worktrees, exclusões, nomes Unicode, hooks, links e colisões com arquivos ignorados |
| CLI e snapshots | Saída JSON, baseline inválida, falhas de leitura/parser e escopos staged/diff |
| Painel e HTTP | Token/origem, dados atuais, concorrência e respostas de endpoints |
| Frontend | Renderização como dados/texto, botões de cópia e token de sessão em harness Node |
| MCP | Ferramentas, validação de argumentos, contratos e recuperação de mensagens inválidas |
| Action e badges | Execução local, escaping, patches, falhas de auditoria e chamadas GitHub simuladas |

Passar na suíte confirma os cenários cobertos, sem garantir ausência de todos os
bugs. Os testes da API GitHub usam mocks; não substituem uma execução real da
Action em um PR. IDEs externas, todos os navegadores e compiladores das demais
linguagens também não são exercitados integralmente.

## Testes no GitHub

Versione `tests/` junto com o código, README, esta documentação e
`.github/workflows/tests.yml`. Os arquivos novos precisam entrar no commit;
arquivos apenas untracked não são enviados pelo push. Caches, ambientes virtuais,
artefatos de build e checkpoints locais ficam fora do repositório conforme
`.gitignore`.

O workflow `Tests` configura Python 3.8, 3.11 e 3.14, fornece Node.js e executa os
comandos de validação a cada push e pull request. Ele testa o checkout enviado.
O workflow `VibeGuard` já existente é uma auditoria de invariantes separada e usa
a Action publicada `vibe-guard/vibeguard@v1`.

As versões da matriz só devem ser consideradas validadas após a execução remota
passar. A estrutura da matriz segue a [documentação oficial do GitHub](https://docs.github.com/en/actions/tutorials/build-and-test-code/python).

Os testes permanecem no repositório fonte. O wheel instalado inclui somente o
pacote `vibeguard` e seus assets, conforme `pyproject.toml`.
