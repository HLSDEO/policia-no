-- ===================================================================================================================
-- Agendador interno de tarefas periódicas (o passe do resumo noturno, hoje)
--
-- Tabelas: AGD_TB01_EXECUCOES
--
-- O passe da madrugada dependia de um agendador do HOST (systemd no Ubuntu, Agendador de Tarefas
-- no Windows), instalado à mão em cada servidor — quem só rodava `docker compose up -d` ficava sem
-- resumo nenhum e sem aviso. Agora quem dispara é uma thread dentro da própria API
-- (api/funcoes/agendador.py), igual em Windows e em Linux, sem nada para instalar no host.
--
-- Esta tabela é o LEDGER dessa thread, e resolve os dois problemas de um agendador embutido:
--
-- 1. **Só um roda.** Com API_WORKERS > 1 (e com duas instalações apontando para o mesmo banco) há
--    várias threads acordando às 02:00. A vaga do dia é a PRIMARY KEY (TP_TAREFA, DT_REFERENCIA):
--    quem consegue inserir a linha roda, os outros veem o conflito e desistem. O guard é o banco,
--    não um SELECT antes do INSERT — mesmo raciocínio do UX100_JOB_TB01_ATIVO em db/init/41_jobs.sql.
-- 2. **Não roda duas vezes, e não deixa de rodar.** DT_REFERENCIA é a DATA (no fuso configurado) a
--    que a execução se refere, não o instante em que ela aconteceu: servidor desligado às 02:00 e
--    ligado às 08:00 roda ao ligar, ainda como a execução daquele dia — o "Persistent=true" do
--    systemd, sem systemd.
--
-- DT_ULTIMA_ATIVIDADE é o batimento: execução em 'processando' que parou de bater (processo morto)
-- pode ser retomada por outro worker. Ver _reivindicar em api/funcoes/agendador.py.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE AGD_TB01_EXECUCOES (
    -- tarefa agendada; hoje só 'resumo_noturno' (a tabela nasce genérica de propósito: a próxima
    -- tarefa periódica entra como uma linha nova, sem mudança de schema)
    TP_TAREFA            VARCHAR(30)  NOT NULL,
    -- dia a que a execução se refere, no fuso configurado (RESUMO_NOTURNO_TZ) — calculado em
    -- Python, NUNCA por now(): o container roda em UTC e a madrugada é a do usuário
    DT_REFERENCIA        DATE         NOT NULL,
    CD_STATUS            VARCHAR(20)  NOT NULL DEFAULT 'processando',  -- processando|concluido|erro
    -- quem disparou: a thread da API, o CLI (agendador do host / execução manual) ou a estreia
    -- (primeira subida desta versão, que marca o dia como feito sem rodar nada — ver o módulo)
    NO_ORIGEM            VARCHAR(20)  NOT NULL DEFAULT 'agendador',    -- agendador|cli|estreia
    NO_WORKER            VARCHAR(100),   -- host:pid de quem pegou a vaga (diagnóstico)
    QT_PROCESSADOS       INTEGER      NOT NULL DEFAULT 0,   -- inquéritos com resumo gerado
    QT_ERROS             INTEGER      NOT NULL DEFAULT 0,
    TX_ERRO              TEXT,
    DT_INICIO            TIMESTAMP    NOT NULL DEFAULT now(),
    DT_ULTIMA_ATIVIDADE  TIMESTAMP    NOT NULL DEFAULT now(),  -- batimento; retomada de órfã usa
    DT_FIM               TIMESTAMP,
    PRIMARY KEY (TP_TAREFA, DT_REFERENCIA),
    CONSTRAINT CK22_AGD_TB01_STATUS CHECK (CD_STATUS IN ('processando', 'concluido', 'erro'))
);

RESET search_path;
