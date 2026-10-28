-- Telefonia paginada — índices para o CDR grande (815 mil chamadas, 200 mil números em
-- produção). Correspondente declarativo em db/init/43_telefonia.sql.
--
-- Contexto: a tela telefonia.html passou a filtrar e paginar no servidor
-- (api/funcoes/telefonia_utils.py, {total, pagina, limite, itens}). Sem estes índices, cada página
-- de ERB/número fazia varredura no CDR: a contagem por CGI (nu_cgi_origem/destino) e o filtro por
-- tipo não tinham por onde entrar.
--
-- Todos btree simples (o filtro de texto continua ILIKE com % nas duas pontas, que não usa
-- índice — mas roda com LIMIT 100, e o COUNT com filtro é o custo aceito da busca).
--
-- RENUMERADA de 0021 para 0024. Este arquivo nasceu como 0021_telefonia_paginacao.sql, no mesmo
-- número de 0021_ia_progresso_orbit.sql (colisão do merge do Orbit): o runner registrava um dos
-- dois como NU_MIGRACAO = 21 e pulava o outro sem erro. Os CREATE INDEX IF NOT EXISTS abaixo já
-- eram idempotentes, então reaplicar numa base que recebeu este delta como 21 não custa nada —
-- e numa base que recebeu o outro, é aqui que os índices finalmente entram.

SET search_path TO INVESTIGACAO, PUBLIC;

-- o filtro "Voz e TuGO / só voz / só TuGO" da aba Chamadas
CREATE INDEX IF NOT EXISTS IX299_TEL_TB04_TP_REGISTRO
    ON TEL_TB04_CHAMADAS (ID_TELEFONIA, TP_REGISTRO);

-- a contagem de chamadas por ERB (qt_chamadas da página) e o filtro "só com antena":
-- o JOIN é por (telefonia, cgi), dos dois lados
CREATE INDEX IF NOT EXISTS IX300_TEL_TB04_CGI_ORIGEM
    ON TEL_TB04_CHAMADAS (ID_TELEFONIA, NU_CGI_ORIGEM)
    WHERE NU_CGI_ORIGEM IS NOT NULL AND NU_CGI_ORIGEM <> '';

CREATE INDEX IF NOT EXISTS IX301_TEL_TB04_CGI_DESTINO
    ON TEL_TB04_CHAMADAS (ID_TELEFONIA, NU_CGI_DESTINO)
    WHERE NU_CGI_DESTINO IS NOT NULL AND NU_CGI_DESTINO <> '';

-- o filtro de UF e o "só com coordenada" da aba Antenas
CREATE INDEX IF NOT EXISTS IX302_TEL_TB05_UF
    ON TEL_TB05_ERBS (ID_TELEFONIA, SG_UF);

CREATE INDEX IF NOT EXISTS IX303_TEL_TB05_COM_COORD
    ON TEL_TB05_ERBS (ID_TELEFONIA)
    WHERE VL_LATITUDE IS NOT NULL;

-- a ordem padrão (por telefone) e a busca da aba Números e IMEI
CREATE INDEX IF NOT EXISTS IX304_TEL_TB06_TELEFONE
    ON TEL_TB06_NUMEROS (ID_TELEFONIA, NU_TELEFONE);

-- o filtro de situação da aba Cadastro de linhas
CREATE INDEX IF NOT EXISTS IX305_TEL_TB03_SITUACAO
    ON TEL_TB03_LINHAS (ID_TELEFONIA, TP_SITUACAO);
