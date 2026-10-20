# Projeto: <nome do quadrinho>

> Criado por `generate-comic-page/scripts/new_project.py <nome>` em
> `~/Downloads/<nome>/`. Preencha este arquivo e o `project.json`, adicione
> os assets abaixo e rode
> `python3 generate-comic-page/scripts/split_scripts.py -p <nome>`.

## O que este arquivo deve conter

Tudo que é específico DESTE quadrinho e que o skill `/generate-comic-page`
precisa saber na hora de gerar e revisar páginas:

1. **Fontes** — onde vivem os roteiros originais, como atualizá-los.
2. **Estilo** — descrição do estilo visual (também deve estar no preâmbulo de
   cada roteiro), formato das páginas. Se o estilo tem um rótulo que vaza pra
   arte (ex. "NAM STYLE"), coloque em `"style_label"` no project.json.
3. **Regras de QC específicas** — o que é critério nº 1, estado/continuidade
   dos personagens. O letreiramento é sempre por PSD (`"lettering": "psd"`):
   a IA desenha balões vazios, a arte é separada em camadas (fundo,
   personagens, balões e borda de cada quadro) e cada balão vira uma caixa
   de texto do Photoshop na fonte de `"lettering_font"` (padrão
   `CCWildWords Regular`). Anote aqui
   convenções de letreiramento do quadrinho (gritos em Bold Italic,
   pensamento em Italic, tamanho fixo...).
4. **Capa e editorial** — como toda página, saem como `.psd` com
   texto editável (arte sem texto em camadas + caixas de texto). Anote aqui as fontes do logo/título/
   corpo do editorial (nomes exatos de `build_psd.py --list-fonts`), cores,
   posição do logo, e se existe um arquivo de logo pronto pra usar como
   camada de imagem. `"page_kinds"` no project.json diz quais páginas são
   capa/editorial (padrão: 1 e 2).
5. **Referências** — o que está em `refs/model-sheets/` e `refs/style/` e
   quando anexar cada coisa.

## Assets necessários na pasta do projeto

- `scripts_src/<roteiros>.md` — um arquivo por edição, com páginas marcadas
  por `=== PAGE N — TÍTULO ===` (ou ajuste `page_header_regex` no
  project.json). Cada página: STYLE, CHARACTERS, STORY BEATS + falas entre
  aspas (as falas entre aspas viram o checklist de QC automaticamente).
  Antes da primeira página: o preâmbulo com o estilo base da edição.
- `refs/model-sheets/` — exemplos/turnarounds dos personagens. Entram pelo
  `import_refs.py -p <nome> <pasta>`; dá pra mandar mais exemplos a qualquer
  momento.
- `refs/style/` — 2–4 imagens âncora do estilo (`import_refs.py --style`).
- `charmap.json` — palavra-chave do personagem → model sheets + descrição
  escrita do design (o agente preenche olhando cada sheet).
