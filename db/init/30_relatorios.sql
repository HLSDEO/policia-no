-- ===================================================================================================================
-- Relatorio .docx: padroes, blocos, marcacoes e geracao
--
-- Tabelas: REL_TB01_PADROES, REL_TB02_BLOCOS, REL_TB03_MARCACOES, REL_TB04_GERACOES
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · padrão de relatório (template global do administrador)
CREATE TABLE REL_TB01_PADROES (
    ID_PADRAO           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_PADRAO           VARCHAR(3000) NOT NULL,
    TX_DESCRICAO        TEXT,
    IN_ATIVO            BOOLEAN NOT NULL DEFAULT TRUE,
    ID_USUARIO_CRIADOR  BIGINT,
    DT_CADASTRO         TIMESTAMP NOT NULL DEFAULT now()
);

-- 02 · blocos ordenados de um padrão
CREATE TABLE REL_TB02_BLOCOS (
    ID_BLOCO     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_PADRAO    BIGINT NOT NULL,
    NU_ORDEM     INTEGER NOT NULL DEFAULT 0,
    TP_BLOCO     VARCHAR(3000) NOT NULL,  -- texto | marcacoes
    NO_TITULO    VARCHAR(3000),
    TX_CONTEUDO  TEXT,  -- texto com {{variaveis}}; em 'marcacoes' é a introdução opcional
    CONSTRAINT FK140_REL_TB02_REL_TB01 FOREIGN KEY (ID_PADRAO)
        REFERENCES REL_TB01_PADROES (ID_PADRAO),
    CONSTRAINT CK03_REL_TB02_TP_BLOCO CHECK (TP_BLOCO IN ('capa', 'sumario', 'texto', 'tabela_campos', 'marcacoes', 'whatsapp',
        'contas', 'extracoes', 'chat',
        'bancario_contas', 'bancario_contrapartes', 'bancario_alertas',
        'bancario_arquivos',
        'rif_resumo', 'rif_comunicacoes', 'rif_contrapartes',
        'rif_envolvidos', 'rif_ocorrencias', 'rif_arquivos',
        'ipl_resumo', 'ipl_pecas', 'ipl_pessoas', 'ipl_achados',
        'pix_chaves',
        'nuvem_perfil', 'nuvem_dispositivos', 'nuvem_acessos', 'iped_extracoes'))
);

CREATE INDEX IX76_REL_TB02_PADRAO ON REL_TB02_BLOCOS (ID_PADRAO, NU_ORDEM);

-- 03 · marcação "marcar para o relatório", presa a um registro de origem
CREATE TABLE REL_TB03_MARCACOES (
    ID_MARCACAO         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO        BIGINT NOT NULL,
    TP_ORIGEM           VARCHAR(3000) NOT NULL,  -- mesmos códigos de ENT_TB01 (wpp_mensagem, contato_wpp, ligacao, ...)
    ID_ORIGEM           BIGINT NOT NULL,  -- PK da linha na tabela correspondente a TP_ORIGEM
    TX_COMENTARIO       TEXT,  -- opcional
    CO_COR              VARCHAR(7),  -- opcional, ex.: #E74C3C
    ID_USUARIO_CRIADOR  BIGINT,
    DT_CRIACAO          TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK141_REL_TB03_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO)
);

CREATE INDEX IX77_REL_TB03_INQUERITO ON REL_TB03_MARCACOES (ID_INQUERITO);
CREATE INDEX IX78_REL_TB03_ORIGEM ON REL_TB03_MARCACOES (TP_ORIGEM, ID_ORIGEM);
-- 3 · trava: um registro só pode estar marcado uma vez em cada inquérito
CREATE UNIQUE INDEX IF NOT EXISTS UX95_REL_TB03_REGISTRO
    ON REL_TB03_MARCACOES (ID_INQUERITO, TP_ORIGEM, ID_ORIGEM);

CREATE TABLE REL_TB04_GERACOES (
    ID_GERACAO       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO     BIGINT NOT NULL,
    ID_PADRAO        BIGINT NOT NULL,
    ID_USUARIO       BIGINT,
    CD_STATUS        VARCHAR(20) NOT NULL DEFAULT 'pendente',  -- pendente|processando|concluido|erro
    QT_TOTAL         INTEGER NOT NULL DEFAULT 0,  -- blocos do padrão
    QT_FEITOS        INTEGER NOT NULL DEFAULT 0,
    TX_ETAPA_ATUAL   VARCHAR(200),  -- título do bloco sendo montado
    TX_ERRO          TEXT,
    NO_ARQUIVO       VARCHAR(500),  -- nome sugerido no download
    CAMINHO_ARQUIVO  TEXT,  -- caminho do .docx em disco
    DT_INICIO        TIMESTAMP NOT NULL DEFAULT now(),
    DT_FIM           TIMESTAMP,
    CONSTRAINT FK206_REL_TB04_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK207_REL_TB04_REL_TB01 FOREIGN KEY (ID_PADRAO)
        REFERENCES REL_TB01_PADROES (ID_PADRAO) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS IX156_REL_TB04_INQUERITO ON REL_TB04_GERACOES (ID_INQUERITO);
-- Trava de concorrência por (inquérito, usuário) — e não só por inquérito: dois investigadores
-- no mesmo caso geram relatórios diferentes ao mesmo tempo e não podem se bloquear. O UniqueViolation
-- deste índice é o que vira o 409 "já há uma geração em andamento" (ver _criar_linha_geracao).
CREATE UNIQUE INDEX IF NOT EXISTS UX99_REL_TB04_ATIVA ON REL_TB04_GERACOES (ID_INQUERITO, ID_USUARIO)
    WHERE CD_STATUS IN ('pendente', 'processando');

DO $seed$
DECLARE
    v_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM REL_TB01_PADROES WHERE NO_PADRAO = 'relatório de análise - tabela simples') THEN
        RETURN;
    END IF;

    INSERT INTO REL_TB01_PADROES (NO_PADRAO, TX_DESCRICAO)
    VALUES ('relatório de análise - tabela simples',
            'Modelo de Relatório de Análise de Material Apreendido (PF). Edite os blocos, campos e variáveis conforme o caso.')
    RETURNING ID_PADRAO INTO v_id;

    INSERT INTO REL_TB02_BLOCOS (ID_PADRAO, NU_ORDEM, TP_BLOCO, NO_TITULO, TX_CONTEUDO) VALUES
    (v_id, 0, 'capa', 'RELATÓRIO DE ANÁLISE DE MATERIAL APREENDIDO',
        $b$UNIDADE POLICIAL | {{unidade_policial}}$b$),

    (v_id, 1, 'sumario', 'SUMÁRIO', NULL),

    (v_id, 2, 'tabela_campos', '1. Dados do procedimento',
        $b$Nº Procedimento (IPL/RE/NCV) | {{numero_inquerito}}
Tipo de Análise | Análise de Material Apreendido
Destinatário |
Analista |
Data | {{data_geracao}}$b$),

    (v_id, 3, 'texto', '2. Identificação do material',
        $b$Trata-se de ____________, apreendido em posse de ____________, sob o lacre ____________.$b$),

    (v_id, 4, 'texto', '3. Contexto investigativo',
        $b$____________$b$),

    (v_id, 5, 'texto', '4. Conteúdo',
        $b$Ressalte-se que os dados foram extraídos do aparelho em exame mediante a utilização do equipamento UFED (Dispositivo de Extração Forense Universal), fornecido pela empresa Cellebrite (referência mundial nessa área) e posteriormente indexados ("estruturados") por meio de ferramenta da própria Cellebrite, garantindo, assim, integridade e validade judicial para o conteúdo produzido.

Com o objetivo de garantir a manutenção da cadeia de custódia das evidências digitais examinadas, por meio da Tabela 1 é apresentado o hash do arquivo da extração física do aparelho celular. Foi utilizada a função hash SHA-2 de 256 bits.$b$),

    (v_id, 6, 'extracoes', 'Tabela 1 - Metadados dos arquivos de extração', NULL),

    (v_id, 7, 'contas', '5. Conta(s) de usuário(s)',
        $b$O aparelho celular analisado estava ativo com os dados vinculados detalhados abaixo.$b$),

    (v_id, 8, 'whatsapp', '6. WhatsApp',
        $b$Foram localizados chats no aplicativo WhatsApp, dos quais serão destacados aqueles que registraram atividades no interesse do presente apuratório.

Observação 1: As linhas das tabelas que contenham mensagens enviadas pelo investigado serão assinaladas com a cor VERDE; as mensagens enviadas pelos demais interlocutores serão assinaladas com a cor AZUL.
Observação 2: Os dados são apresentados em sua integralidade e originalidade, conforme o arquivo primário. As transcrições de áudio são realizadas ipsis litteris.
Observação 3: Poderão ser acrescentados, por conta do analista, realces e negritos em trechos selecionados.$b$),

    (v_id, 9, 'marcacoes', '7. Outros registros marcados',
        $b$Demais registros marcados para o relatório, agrupados por tipo, com indicação da extração (.ufdr) de origem.$b$),

    (v_id, 10, 'texto', '8. Conclusão', $b$____________$b$);
END
$seed$;

DO $seed$
DECLARE
    v_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM REL_TB01_PADROES WHERE NO_PADRAO = 'Relatório de dados bancários') THEN
        RETURN;
    END IF;

    INSERT INTO REL_TB01_PADROES (NO_PADRAO, TX_DESCRICAO)
    VALUES ('Relatório de dados bancários',
            'Análise do extrato bancário afastado (SIMBA/R304): contas, contrapartes e pontos de atenção. Edite os blocos conforme o caso.')
    RETURNING ID_PADRAO INTO v_id;

    INSERT INTO REL_TB02_BLOCOS (ID_PADRAO, NU_ORDEM, TP_BLOCO, NO_TITULO, TX_CONTEUDO) VALUES
    (v_id, 0, 'capa', 'RELATÓRIO DE ANÁLISE DE DADOS BANCÁRIOS',
        $b$UNIDADE POLICIAL | {{unidade_policial}}$b$),

    (v_id, 1, 'sumario', 'SUMÁRIO', NULL),

    (v_id, 2, 'tabela_campos', '1. Dados do procedimento',
        $b$Nº Procedimento (IPL/RE/NCV) | {{numero_inquerito}}
Tipo de Análise | Análise de dados bancários (afastamento de sigilo)
Destinatário |
Analista | {{investigador}}
Data | {{data_geracao}}$b$),

    (v_id, 3, 'tabela_campos', '2. Objeto da análise',
        $b$Contas analisadas | {{bancario_contas}}
Titulares | {{bancario_titulares}}
Período | {{bancario_periodo}}
Total de créditos | {{bancario_credito}}
Total de débitos | {{bancario_debito}}
Decisão judicial |$b$),

    (v_id, 4, 'bancario_arquivos', '2.1. Metadados dos arquivos analisados',
        $b$Relação dos arquivos de extrato bancário (R304 do SIMBA) analisados, com o tamanho em bytes e os hashes (MD5, SHA-1 e SHA-256) calculados na importação, para conferência da integridade dos dados (cadeia de custódia).$b$),

    (v_id, 5, 'texto', '3. Origem e método',
        $b$Os dados a seguir foram obtidos por afastamento do sigilo bancário e entregues pelas instituições financeiras por meio do SIMBA (Sistema de Investigação de Movimentações Bancárias), relatório R304, que traz um registro por lançamento e sua contraparte.

Os valores foram totalizados de forma automática a partir dos lançamentos entregues, sem qualquer edição do conteúdo original. Cada número apresentado neste relatório pode ser reconferido no extrato de origem.

Ressalte-se que a existência de movimentação, por si só, não indica ilicitude: as tabelas e os apontamentos a seguir delimitam o que merece explicação, e não conclusão sobre a origem dos recursos.$b$),

    (v_id, 6, 'bancario_contas', '4. Contas bancárias analisadas',
        $b$Relação das contas afastadas, com o volume movimentado em cada uma no período.$b$),

    (v_id, 7, 'bancario_contrapartes', '5. Principais contrapartes',
        $b$Pessoas e empresas que mais movimentaram recursos com os titulares, somando todas as contas analisadas. "Creditou" é o que entrou nas contas dos titulares; "Recebeu" é o que saiu delas.$b$),

    (v_id, 8, 'bancario_alertas', '6. Pontos de atenção',
        $b$Apontamentos apurados automaticamente sobre os lançamentos.$b$),

    (v_id, 9, 'marcacoes', '7. Registros marcados pela equipe',
        $b$Lançamentos, contas e contrapartes marcados manualmente durante a análise.$b$),

    (v_id, 10, 'texto', '8. Conclusão',
        $b$____________$b$);
END
$seed$;

DO $seed$
DECLARE
    v_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM REL_TB01_PADROES WHERE NO_PADRAO = 'Relatório de RIF') THEN
        RETURN;
    END IF;

    INSERT INTO REL_TB01_PADROES (NO_PADRAO, TX_DESCRICAO)
    VALUES ('Relatório de RIF',
            'Análise do Relatório de Inteligência Financeira (COAF/UIF): comunicações, contrapartes, envolvidos e ocorrências. Edite os blocos conforme o caso.')
    RETURNING ID_PADRAO INTO v_id;

    INSERT INTO REL_TB02_BLOCOS (ID_PADRAO, NU_ORDEM, TP_BLOCO, NO_TITULO, TX_CONTEUDO) VALUES
    (v_id, 0, 'capa', 'RELATÓRIO DE ANÁLISE DE RIF',
        $b$UNIDADE POLICIAL | {{unidade_policial}}$b$),

    (v_id, 1, 'sumario', 'SUMÁRIO', NULL),

    (v_id, 2, 'tabela_campos', '1. Dados do procedimento',
        $b$Nº Procedimento (IPL/RE/NCV) | {{numero_inquerito}}
Tipo de Análise | Análise de Relatório de Inteligência Financeira
Destinatário |
Analista | {{investigador}}
Data | {{data_geracao}}$b$),

    (v_id, 3, 'texto', '2. Origem dos dados',
        $b$Os dados a seguir foram obtidos de Relatório(s) de Inteligência Financeira (RIF) emitido(s) pelo Conselho de Controle de Atividades Financeiras (COAF), por meio do Sistema SISCOAF, e importado(s) para este caderno investigativo.

As informações são compostas por comunicações de operações financeiras (movimentações bancárias), pessoas a elas vinculadas (envolvidos) e os respectivos enquadramentos normativos (ocorrências). Os valores são apresentados conforme informados pelas instituições financeiras comunicantes.$b$),

    (v_id, 4, 'rif_arquivos', '2.1. Metadados dos arquivos analisados',
        $b$Relação dos arquivos que compõem o(s) RIF(s) analisado(s), com o tamanho em bytes e os hashes (MD5, SHA-1 e SHA-256) calculados na importação, para conferência da integridade dos dados (cadeia de custódia).$b$),

    (v_id, 5, 'rif_resumo', '3. Síntese do RIF',
        $b$Totais consolidados dos RIFs importados para o inquérito.$b$),

    (v_id, 6, 'rif_comunicacoes', '4. Comunicações',
        $b$Relação das comunicações de movimentações financeiras recebidas, com os valores movimentados a crédito e a débito.$b$),

    (v_id, 7, 'rif_contrapartes', '5. Contrapartes',
        $b$Pessoas e empresas que realizaram operações com os titulares das comunicações, consolidadas por CPF/CNPJ.$b$),

    (v_id, 8, 'rif_envolvidos', '6. Envolvidos',
        $b$Relação das pessoas físicas e jurídicas citadas nos RIFs, com as respectivas qualificações (PEP, servidor público).$b$),

    (v_id, 9, 'rif_ocorrencias', '7. Ocorrências',
        $b$Enquadramentos e alertas registrados nos RIFs, com a descrição dos fundamentos legais.$b$),

    (v_id, 10, 'marcacoes', '8. Registros marcados pela equipe',
        $b$Comunicações, envolvidos e contrapartes marcados manualmente durante a análise.$b$),

    (v_id, 11, 'texto', '9. Conclusão',
        $b$____________$b$);
END
$seed$;

DO $seed$
DECLARE
    v_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM REL_TB01_PADROES WHERE NO_PADRAO = 'relatório de análise - whatsapp') THEN
        RETURN;
    END IF;

    INSERT INTO REL_TB01_PADROES (NO_PADRAO, TX_DESCRICAO)
    VALUES ('relatório de análise - whatsapp',
            'Mesmo modelo do "relatório de análise - tabela simples" (PF), mas as conversas de WhatsApp, SMS e demais mensageiros saem como a tela de um celular (bolhas de mensagem), com os comentários do investigador depois de cada trecho de conversa, em vez de tabela. Edite os blocos, campos e variáveis conforme o caso.')
    RETURNING ID_PADRAO INTO v_id;

    INSERT INTO REL_TB02_BLOCOS (ID_PADRAO, NU_ORDEM, TP_BLOCO, NO_TITULO, TX_CONTEUDO) VALUES
    (v_id, 0, 'capa', 'RELATÓRIO DE ANÁLISE DE MATERIAL APREENDIDO',
        $b$UNIDADE POLICIAL | {{unidade_policial}}$b$),

    (v_id, 1, 'sumario', 'SUMÁRIO', NULL),

    (v_id, 2, 'tabela_campos', '1. Dados do procedimento',
        $b$Nº Procedimento (IPL/RE/NCV) | {{numero_inquerito}}
Tipo de Análise | Análise de Material Apreendido
Destinatário |
Analista |
Data | {{data_geracao}}$b$),

    (v_id, 3, 'texto', '2. Identificação do material',
        $b$Trata-se de ____________, apreendido em posse de ____________, sob o lacre ____________.$b$),

    (v_id, 4, 'texto', '3. Contexto investigativo',
        $b$____________$b$),

    (v_id, 5, 'texto', '4. Conteúdo',
        $b$Ressalte-se que os dados foram extraídos do aparelho em exame mediante a utilização do equipamento UFED (Dispositivo de Extração Forense Universal), fornecido pela empresa Cellebrite (referência mundial nessa área) e posteriormente indexados ("estruturados") por meio de ferramenta da própria Cellebrite, garantindo, assim, integridade e validade judicial para o conteúdo produzido.

Com o objetivo de garantir a manutenção da cadeia de custódia das evidências digitais examinadas, por meio da Tabela 1 é apresentado o hash do arquivo da extração física do aparelho celular. Foi utilizada a função hash SHA-2 de 256 bits.$b$),

    (v_id, 6, 'extracoes', 'Tabela 1 - Metadados dos arquivos de extração', NULL),

    (v_id, 7, 'contas', '5. Conta(s) de usuário(s)',
        $b$O aparelho celular analisado estava ativo com os dados vinculados detalhados abaixo.$b$),

    (v_id, 8, 'chat', '6. Conversas (WhatsApp, SMS e outros aplicativos de mensagens)',
        $b$Foram localizadas conversas nos aplicativos de mensagens do aparelho, dos quais serão destacadas aquelas que registraram atividades no interesse do presente apuratório, reproduzidas a seguir como aparecem na tela do celular.

Observação 1: As mensagens do investigado (dono do aparelho) aparecem à direita, em verde; as dos demais interlocutores aparecem à esquerda, em azul.
Observação 2: Os dados são apresentados em sua integralidade e originalidade, conforme o arquivo primário. As transcrições de áudio são realizadas ipsis litteris.
Observação 3: Os comentários do investigador sobre cada trecho marcado aparecem logo depois dele.$b$),

    (v_id, 9, 'marcacoes', '7. Outros registros marcados',
        $b$Demais registros marcados para o relatório, agrupados por tipo, com indicação da extração (.ufdr) de origem.$b$),

    (v_id, 10, 'texto', '8. Conclusão', $b$____________$b$);
END
$seed$;

DO $seed$
DECLARE
    v_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM REL_TB01_PADROES WHERE NO_PADRAO = 'Relatório de inquérito (ePol)') THEN
        RETURN;
    END IF;

    INSERT INTO REL_TB01_PADROES (NO_PADRAO, TX_DESCRICAO)
    VALUES ('Relatório de inquérito (ePol)',
            'Análise do inquérito policial em PDF extraído do ePol: dados do procedimento, peças que o compõem, pessoas citadas e achados (telefones, contas, valores). Edite os blocos conforme o caso.')
    RETURNING ID_PADRAO INTO v_id;

    INSERT INTO REL_TB02_BLOCOS (ID_PADRAO, NU_ORDEM, TP_BLOCO, NO_TITULO, TX_CONTEUDO) VALUES
    (v_id, 0, 'capa', 'RELATÓRIO DE ANÁLISE DE INQUÉRITO POLICIAL',
        $b$UNIDADE POLICIAL | {{unidade_policial}}$b$),

    (v_id, 1, 'sumario', 'SUMÁRIO', NULL),

    (v_id, 2, 'tabela_campos', '1. Dados do procedimento',
        $b$Nº Procedimento (IPL/RE/NCV) | {{numero_inquerito}}
Tipo de Análise | Análise de inquérito policial
Destinatário |
Analista | {{investigador}}
Data | {{data_geracao}}$b$),

    (v_id, 3, 'texto', '2. Origem dos dados',
        $b$Os dados a seguir foram obtidos do(s) autos do inquérito policial em formato PDF, exportado(s) do sistema ePol e importado(s) para este caderno investigativo.

A leitura das peças, das pessoas citadas e dos achados (telefones, contas bancárias, valores e demais identificadores) foi realizada automaticamente sobre o texto do documento, com indicação da folha de origem de cada informação, de modo que todo dado apresentado adiante pode ser conferido no documento original.$b$),

    (v_id, 4, 'ipl_resumo', '3. Síntese do inquérito',
        $b$Dados de cabeçalho do(s) inquérito(s) importado(s): unidade, autoridade, data de instauração e tipificação.$b$),

    (v_id, 5, 'ipl_pecas', '4. Peças do inquérito',
        $b$Relação das peças que compõem os autos, com a respectiva numeração de folhas.$b$),

    (v_id, 6, 'ipl_pessoas', '5. Pessoas citadas',
        $b$Pessoas mencionadas nos autos, com o papel identificado e a folha da primeira menção.$b$),

    (v_id, 7, 'ipl_achados', '6. Achados',
        $b$Telefones, contas bancárias, valores e demais identificadores localizados no texto dos autos, agrupados por tipo.$b$),

    (v_id, 8, 'marcacoes', '7. Registros marcados pela equipe',
        $b$Pessoas e achados marcados manualmente durante a análise.$b$),

    (v_id, 9, 'texto', '8. Conclusão',
        $b$____________$b$);
END
$seed$;

DO $seed$
DECLARE
    v_id      BIGINT;
    v_ordem   INTEGER;
    v_tp      VARCHAR(3000);
    v_intro   TEXT;
BEGIN
    -- (no_padrao, tp_bloco, introdução do bloco) — mesmos textos dos seeds 69/70
    FOR v_id, v_tp, v_intro IN
        SELECT p.ID_PADRAO, v.tp,
               CASE v.tp
                   WHEN 'bancario_arquivos' THEN
                       'Relação dos arquivos de extrato bancário (R304 do SIMBA) analisados, com o tamanho em bytes e os hashes (MD5, SHA-1 e SHA-256) calculados na importação, para conferência da integridade dos dados (cadeia de custódia).'
                   ELSE
                       'Relação dos arquivos que compõem o(s) RIF(s) analisado(s), com o tamanho em bytes e os hashes (MD5, SHA-1 e SHA-256) calculados na importação, para conferência da integridade dos dados (cadeia de custódia).'
               END
        FROM (VALUES ('Relatório de dados bancários', 'bancario_arquivos'),
                     ('Relatório de RIF', 'rif_arquivos')) AS v(no_padrao, tp)
        JOIN REL_TB01_PADROES p ON p.NO_PADRAO = v.no_padrao
    LOOP
        -- já existe bloco 2.1 no padrão (admin editou/criou) -> não duplicar
        IF EXISTS (SELECT 1 FROM REL_TB02_BLOCOS
                   WHERE ID_PADRAO = v_id AND NO_TITULO LIKE '2.1%') THEN
            CONTINUE;
        END IF;

        -- primeira seção "2. ..." do padrão; sem ela (template editado) pula
        SELECT b.NU_ORDEM INTO v_ordem
        FROM REL_TB02_BLOCOS b
        WHERE b.ID_PADRAO = v_id AND b.NO_TITULO ~ '^2\. '
        ORDER BY b.NU_ORDEM
        LIMIT 1;
        IF v_ordem IS NULL THEN
            CONTINUE;
        END IF;

        -- abre espaço e insere logo depois da seção "2. ..."
        UPDATE REL_TB02_BLOCOS
        SET NU_ORDEM = NU_ORDEM + 1
        WHERE ID_PADRAO = v_id AND NU_ORDEM > v_ordem;

        INSERT INTO REL_TB02_BLOCOS (ID_PADRAO, NU_ORDEM, TP_BLOCO, NO_TITULO, TX_CONTEUDO)
        VALUES (v_id, v_ordem + 1, v_tp, '2.1. Metadados dos arquivos analisados', v_intro);
    END LOOP;
END
$seed$;

RESET search_path;
