-- ===================================================================================================================
-- Marcadores livres
--
-- Tabelas: MRC_TB01_MARCADORES, MRC_TB02_APLICACOES
--
-- Os antigos achados do analista (ACH_TB01) deram lugar ao workspace de hipóteses do inquérito —
-- ver 48_hipoteses.sql e db/migracoes/0046_hipoteses.sql.
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · marcador: a etiqueta nomeada, do usuário, dentro de um inquérito
CREATE TABLE MRC_TB01_MARCADORES (
    ID_MARCADOR   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO  BIGINT NOT NULL,
    ID_USUARIO    BIGINT NOT NULL,
    NO_MARCADOR   VARCHAR(3000) NOT NULL,
    CO_COR        VARCHAR(7) NOT NULL,  -- #RRGGBB, mesma paleta do seletor de marcação
    TX_DESCRICAO  TEXT,
    NU_ORDEM      INTEGER NOT NULL DEFAULT 0,
    DT_CADASTRO   TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK202_MRC_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK203_MRC_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

-- "Dinheiro" e "dinheiro " são o mesmo marcador para quem digita — o único
-- é case-insensitive para o segundo cadastro cair no ON CONFLICT em vez de
-- criar uma etiqueta gêmea que ninguém percebe.
CREATE UNIQUE INDEX IF NOT EXISTS UX98_MRC_TB01_NOME
    ON MRC_TB01_MARCADORES (ID_INQUERITO, ID_USUARIO, lower(NO_MARCADOR));
CREATE INDEX IF NOT EXISTS IX154_MRC_TB01_INQ_USR
    ON MRC_TB01_MARCADORES (ID_INQUERITO, ID_USUARIO, NU_ORDEM);

-- 02 · aplicação: liga um marcador (de alguém) a uma marcação (do caso)
CREATE TABLE MRC_TB02_APLICACOES (
    ID_APLICACAO  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_MARCACAO   BIGINT NOT NULL,
    ID_MARCADOR   BIGINT NOT NULL,
    DT_CADASTRO   TIMESTAMP NOT NULL DEFAULT now(),
    -- CASCADE nos dois lados: desmarcar o registro (remover_marcacao) e apagar
    -- o marcador continuam sendo um DELETE simples, sem limpeza manual.
    CONSTRAINT FK204_MRC_TB02_REL_TB03 FOREIGN KEY (ID_MARCACAO)
        REFERENCES REL_TB03_MARCACOES (ID_MARCACAO) ON DELETE CASCADE,
    CONSTRAINT FK205_MRC_TB02_MRC_TB01 FOREIGN KEY (ID_MARCADOR)
        REFERENCES MRC_TB01_MARCADORES (ID_MARCADOR) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS UX98_MRC_TB02_PAR
    ON MRC_TB02_APLICACOES (ID_MARCACAO, ID_MARCADOR);
CREATE INDEX IF NOT EXISTS IX155_MRC_TB02_MARCADOR
    ON MRC_TB02_APLICACOES (ID_MARCADOR);

-- =====================================================================
-- 03 · converte as cores já usadas em marcadores nomeados
--
-- Quem já vinha usando o vermelho para "dinheiro" não pode perder isso na
-- virada: cada (inquérito, usuário, cor) distinto vira um marcador com o NOME
-- DA COR, e as marcações daquele usuário passam a apontar para ele. Marcação
-- sem cor ou sem usuário criador (as antigas, anteriores ao ID_USUARIO_CRIADOR)
-- fica sem marcador e segue pelo fallback de CO_COR.
--
-- Os nomes vêm de COR_NOMES (funcoes/relatorios_utils.py). Atenção ao #FF9800:
-- ele e o #E67E22 eram AMBOS "Laranja" lá — duas cores diferentes com a mesma
-- etiqueta no .docx. Aqui o #FF9800 vira "Âmbar" (o único case-insensitive não
-- aceitaria a repetição), e o mesmo ajuste foi feito no Python e no JS.
-- =====================================================================

INSERT INTO MRC_TB01_MARCADORES (ID_INQUERITO, ID_USUARIO, NO_MARCADOR, CO_COR, TX_DESCRICAO)
SELECT DISTINCT
       m.ID_INQUERITO,
       m.ID_USUARIO_CRIADOR,
       c.nome,
       upper(m.CO_COR),
       'Criado automaticamente a partir da cor de marcação usada antes dos marcadores nomeados.'
  FROM REL_TB03_MARCACOES m
  JOIN SEGURANCA.USR_TB01_USUARIOS u ON u.ID_USUARIO = m.ID_USUARIO_CRIADOR
  JOIN (VALUES
            ('#E74C3C', 'Vermelho'), ('#E67E22', 'Laranja'),  ('#F1C40F', 'Amarelo'),
            ('#2ECC71', 'Verde'),    ('#1ABC9C', 'Verde-água'), ('#3498DB', 'Azul'),
            ('#9B59B6', 'Roxo'),     ('#E91E63', 'Rosa'),     ('#795548', 'Marrom'),
            ('#607D8B', 'Cinza'),    ('#00BCD4', 'Ciano'),    ('#FF9800', 'Âmbar')
       ) AS c(hex, nome) ON c.hex = upper(m.CO_COR)
 WHERE m.CO_COR IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO MRC_TB02_APLICACOES (ID_MARCACAO, ID_MARCADOR)
SELECT m.ID_MARCACAO, mc.ID_MARCADOR
  FROM REL_TB03_MARCACOES m
  JOIN MRC_TB01_MARCADORES mc
    ON mc.ID_INQUERITO = m.ID_INQUERITO
   AND mc.ID_USUARIO   = m.ID_USUARIO_CRIADOR
   AND mc.CO_COR       = upper(m.CO_COR)
 WHERE m.CO_COR IS NOT NULL
ON CONFLICT DO NOTHING;

RESET search_path;
