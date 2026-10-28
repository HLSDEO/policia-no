-- Prova compartilhada entre inquéritos: a extração deixa de ser necessariamente fruto de uma
-- importação feita no próprio inquérito e pode ser a CÓPIA de uma extração de outro inquérito
-- (menu "Compartilhar prova" do card do aparelho). Correspondente declarativo em
-- db/init/09_extracoes.sql.
--
-- Contexto (docs/compartilhar_prova.md): o mesmo celular apreendido costuma ser prova em mais de
-- um inquérito. Reimportar o .ufdr custa horas (parsing, hashes, transcrição de áudio, indexação
-- semântica) e ainda duplica dezenas de GB no disco. A cópia leva minutos e não toca no disco:
-- NO_ARQUIVO da cópia aponta para o MESMO .ufdr da origem — os anexos dos dois inquéritos são
-- lidos de lá.
--
-- As três colunas registram a procedência, que é o que a cadeia de custódia exige da cópia:
--
--   * ID_EXTRACAO_ORIGEM — de qual extração ela foi copiada. Propositalmente SEM foreign key:
--     a origem pode ser apagada depois (a cópia é independente — dados próprios, em linhas
--     próprias), e uma FK impediria esse DELETE.
--   * NU_INQUERITO_ORIGEM — o número do inquérito de onde a prova veio, em texto, para o card
--     continuar dizendo de onde ela é mesmo depois que a origem for apagada.
--   * DT_COMPARTILHAMENTO — quando a cópia foi feita (DT_UPLOAD continua sendo a data do
--     upload original, preservada na cópia).
--
-- NULL nas três = extração importada normalmente. Nenhum backfill: toda extração que já existe é
-- exatamente isso.

ALTER TABLE INVESTIGACAO.INQ_TB02_EXTRACOES
    ADD COLUMN IF NOT EXISTS ID_EXTRACAO_ORIGEM  BIGINT,
    ADD COLUMN IF NOT EXISTS NU_INQUERITO_ORIGEM VARCHAR(3000),
    ADD COLUMN IF NOT EXISTS DT_COMPARTILHAMENTO TIMESTAMP;
