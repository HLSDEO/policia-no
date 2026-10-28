-- Nenhuma delegacia pode ficar sem unidade superior (regra de negócio: toda delegacia
-- pertence à superintendência regional do seu estado). Unidade padrão institucional:
-- SR/PF/<UF> (ex.: SR/PF/ES para a UF ES), nível 'uf' da árvore seguranca.unidades (0050).
-- Idempotente: cria a SR de cada UF em uso e amarra as delegacias que estão soltas.

SET search_path TO SEGURANCA, PUBLIC;

-- 1) UF de cada delegacia: a coluna UF ou, na falta dela, o sufixo de 2 letras do nome
--    ("FICCO/ES", "DELEGACIA-ES" -> ES). O sufixo exige separador, para não casar o fim
--    de palavras quaisquer ("...FRONTEIRA" não vira UF "RA"). Nomes são gravados em
--    maiúsculas pela API.
-- 2) unidade superior padrão SR/PF/<UF> para toda UF em uso (não duplica: NO_UNIDADE é UNIQUE).
INSERT INTO UNIDADES (NO_UNIDADE, TP_NIVEL, UF)
SELECT 'SR/PF/' || X.UF, 'uf', X.UF
FROM (
    SELECT DISTINCT COALESCE(
        NULLIF(D.UF, ''),
        (REGEXP_MATCH(D.NO_DELEGACIA, '(?:^|[-/ ])([A-Z]{2})$'))[1]
    ) AS UF
    FROM DELEGACIAS D
) X
WHERE X.UF IS NOT NULL
ON CONFLICT (NO_UNIDADE) DO NOTHING;

-- 3) amarra as delegacias soltas na SR do seu UF (coluna UF ou sufixo do nome)
UPDATE DELEGACIAS D
SET ID_UNIDADE = U.ID_UNIDADE
FROM UNIDADES U
WHERE D.ID_UNIDADE IS NULL
  AND U.TP_NIVEL = 'uf'
  AND U.NO_UNIDADE = 'SR/PF/' || COALESCE(
        NULLIF(D.UF, ''),
        (REGEXP_MATCH(D.NO_DELEGACIA, '(?:^|[-/ ])([A-Z]{2})$'))[1]
  );

-- 4) quem ganhou unidade por sufixo de nome passa a ter a UF da árvore preenchida também
UPDATE DELEGACIAS D
SET UF = U.UF
FROM UNIDADES U
WHERE D.ID_UNIDADE = U.ID_UNIDADE
  AND U.UF IS NOT NULL
  AND (D.UF IS NULL OR D.UF = '');

-- Sobrar delegacia sem unidade (nome sem sufixo de UF e UF vazia) é caso para o administrador
-- resolver na tela Administração → Delegacias: a API agora recusa criar/editar sem unidade.
RESET search_path;
