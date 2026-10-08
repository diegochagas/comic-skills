# generate-comic-page — estúdio de quadrinhos por IA

Gera edições completas de quadrinhos com IA, **uma página por vez, com a sua
aprovação em cada uma**, e empacota como `.cbz`. **Toda página sai como
`.xcf` do GIMP com texto editável**: a IA desenha os balões VAZIOS e cada
balão recebe uma caixa de texto nativa do GIMP na fonte CCWildWords; capa e
editorial ganham logo/título/corpo também como camadas de texto. A IA nunca
escreve texto — acabou balão com erro de digitação.

As instruções que o agente segue estão em [SKILL.md](SKILL.md); este arquivo
é a visão geral para humanos. Os comandos rodam a partir da raiz do repo.

Os quadrinhos ficam **fora do repo**, em `~/Downloads/<projeto>/` (roteiros,
refs, renders, `.xcf`, `.cbz`). Outra raiz: variável `COMIC_PROJECTS_DIR`;
projeto guardado em outro lugar: `-p <caminho da pasta>` em qualquer script.

## Como funciona

| Papel                    | Ferramenta                                                       | Custo |
| ------------------------ | ---------------------------------------------------------------- | ----- |
| Orquestração + QC visual | **o agente** (Claude Code / Codex, skill `/generate-comic-page`) | incluso na assinatura — **sem API LLM** |
| Geração de imagem        | **Higgsfield CLI** — `gpt_image_2_5` low/2k                      | créditos do plano Plus (1000/mês); preço real: `gen_page.py ... --cost` |
| Letreiramento (toda pág.) | **OpenCV** acha os balões vazios + **GIMP 3 headless** (flatpak) cria as caixas de texto CCWildWords | zero |
| Parsing, estado, CBZ     | scripts Python locais (`scripts/`)                               | zero |

O fluxo por página:

1. **Primeira vez**: você passa uma pasta com exemplos de model sheets. O
   agente importa (`import_refs.py`), olha cada sheet e escreve no
   `charmap.json` a descrição do design de cada personagem — essa descrição
   entra em todo prompt como "trava de design", junto com as imagens.
2. O agente gera **uma** página com balões vazios (`gen_page.py`) e confere
   ele mesmo (personagens contra os sheets, nenhuma letra na arte, um balão
   por fala, beats). Depois letreira de graça: `make_layout.py` detecta os
   balões e monta o rascunho com as falas exatas do roteiro, o agente
   confere pelo overlay numerado qual fala vai em qual balão, e
   `build_xcf.py` gera `out/xcf/<ed>/page_NN.xcf` + preview. Ele te mostra
   o preview letreirado com um relatório honesto.
3. Você responde:
   - **ok** → a página é aprovada e ele pergunta se gera a próxima;
   - **mudanças específicas** → edição pontual (uma mudança por edição) ou
     nova geração com a correção; mudança só de texto/fonte/tamanho é de
     graça (mexe no layout e refaz o XCF);
   - **mais exemplos** → ele importa, atualiza o charmap e refaz a página.
4. **Capa e editorial**: primeiro você aprova a arte SEM texto, depois o
   agente desenha o layout — logo, número, preço, título, corpo do
   editorial — como camadas de texto do GIMP (aqui as fontes são de título,
   não CCWildWords). Você aprova pelo preview e recebe o `.xcf`.

A página aprovada que vai pro `.cbz` é o preview do XCF
(`work/<ed>/approved/page_NN.jpg`); se você retocar o XCF no GIMP, exporte
por cima desse JPG.

Não existe modo lote: o modelo alucina design com frequência, então nada é
gerado sem você ter visto a página anterior.

## Setup (uma vez)

```bash
npm i -g @higgsfield/cli
higgsfield auth login                 # login na conta Higgsfield (navegador)
npx skills add higgsfield-ai/skills   # skills higgsfield-* em .agents/skills/
curl -fLO https://github.com/diegochagas/gimphoto/releases/latest/download/GIMPhoto.flatpak && flatpak install --user GIMPhoto.flatpak # gera os .xcf (obrigatório)
# fonte CCWildWords instalada no sistema (o GIMP precisa enxergar: build_xcf.py --list-fonts wild)
```

Python: o `venv/` criado por `<repo>/setup.sh` (Pillow, OpenCV, numpy).

## Uso

```bash
# quadrinho novo
python3 generate-comic-page/scripts/new_project.py meu-quadrinho --title "Meu Quadrinho"
#   → preencher ~/Downloads/meu-quadrinho/project.json + PROJECT.md, roteiros em scripts_src/
python3 generate-comic-page/scripts/import_refs.py -p meu-quadrinho ~/pasta/com/model-sheets
python3 generate-comic-page/scripts/split_scripts.py -p meu-quadrinho

# no agente:
#   /generate-comic-page meu-quadrinho 1                 → próxima página da edição 1
#   /generate-comic-page meu-quadrinho 1 7               → página 7
#   /generate-comic-page meu-quadrinho 1 cover           → capa (.xcf)
#   /generate-comic-page meu-quadrinho 1 editorial       → editorial (.xcf)
#   /generate-comic-page meu-quadrinho 1 ~/mais-exemplos → importa exemplos e segue

python3 generate-comic-page/scripts/status.py                          # progresso
python3 generate-comic-page/scripts/assemble_cbz.py -p meu-quadrinho 1 # → out/*.cbz
```

## Scripts

| Script | O que faz |
| --- | --- |
| `scripts/new_project.py <nome>` | cria `~/Downloads/<nome>/` a partir de `_template/` |
| `scripts/import_refs.py -p <proj> <pasta/imagens> [--style]` | importa exemplos para `refs/`, lista os sheets que faltam no `charmap.json` |
| `scripts/split_scripts.py -p <proj> [ed...]` | roteiro por edição → `jobs/<ed>/page_NN.json` (+ `work/<ed>/state.json`, preservando estados) |
| `scripts/gen_page.py -p <proj> <ed> <pág>` | UMA geração (ou `--edit-from`, `--fix`, `--cost`, `--dry-run`) |
| `scripts/page_state.py -p <proj> next\|show\|set\|approve` | próxima página, notas, estágio, aprovação |
| `scripts/make_layout.py -p <proj> <ed> <pág> [--force]` | detecta os balões vazios (overlay numerado) e rascunha o layout com as falas exatas do roteiro |
| `scripts/build_xcf.py <layout.json>` / `--list-fonts` | layout → `.xcf` com camadas de texto nativas + preview JPG, para toda página (`gimp_layout_job.py` roda dentro do GIMP) |
| `scripts/status.py [-p <proj>]` | aprovadas / pendentes / aguardando revisão |
| `scripts/make_lettering_guide.py -p <proj> <ed>` | lista de todas as falas por página, pra quem for letreirar à mão |
| `scripts/assemble_cbz.py -p <proj> <ed>` | páginas aprovadas → `out/*.cbz` |
| `scripts/common.py` | descoberta de projetos, charmap, estado (importado pelos outros) |
| `_template/` | base de um projeto novo (`project.json`, `PROJECT.md`, `charmap.json`) |

## Projetos existentes

Projetos criados antes desta versão (ex. `megaman-nam`) funcionam sem
re-split: passe `-p <caminho da pasta>` ou mova a pasta para `~/Downloads/`.
O tipo da página (capa/editorial) é deduzido do título quando o job não tem
`kind`. Eles têm `"lettering": "ai"` no project.json (texto renderizado pela
IA, sem XCF nas páginas de história) e continuam assim; projeto novo nasce
com `"lettering": "xcf"`.
