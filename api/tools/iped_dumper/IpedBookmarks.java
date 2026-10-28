import java.io.File;
import java.io.PrintStream;
import java.util.List;
import java.util.Map;

/**
 * Le os MARCADORES do perito de um caso do IPED e imprime um JSON por linha em stdout.
 *
 * O que sao: dentro do IPED, o perito seleciona itens e os agrupa em "bookmarks" (marcadores)
 * com nome e comentario - "material de interesse", "provavel CSAM", "conversas do alvo". E o
 * resultado da ANALISE HUMANA sobre o caso, e ate agora a importacao do analyser ignorava.
 *
 * Onde ficam: <caso>/iped/bookmarks.iped (Bookmarks.STATEFILENAME), que NAO e um banco nem um
 * XML - e serializacao de objeto Java (Util.writeObject). Nao da para ler do lado Python sem
 * reimplementar o formato, entao a leitura acontece aqui, onde as classes do proprio caso
 * estao no classpath e o desserializador e o oficial.
 *
 * POR QUE ISTO E UM PROGRAMA SEPARADO DO IpedDumper: o IpedDumper foi escrito de proposito
 * para NAO tocar em nenhuma classe do iped-engine (ver o cabecalho dele - abrir um caso pela
 * API oficial dispara o carregamento de todas as tasks de processamento e trava ou explode
 * fora do ambiente completo do IPED). Aqui o iped-engine E necessario, porque so ele sabe
 * desserializar o estado. Manter os dois separados garante que uma falha aqui - versao de
 * IPED incompativel, classe faltando, arquivo corrompido - nao contamine a leitura do indice,
 * que e a parte que nao pode falhar. Do lado Python isto roda em try/except e caso sem
 * marcador simplesmente nao produz nada (ver funcoes/iped_marcadores.py).
 *
 * FORMATO DO ESTADO ENTRE VERSOES. Achado num caso real (IPED 4.2.2): o arquivo contem um
 * objeto `BitmapBookmarks`, NAO `Bookmarks` - o cast de `Bookmarks.load()` estoura com
 * ClassCastException e todos os marcadores do perito eram descartados em silencio. As duas
 * classes implementam a interface `iped.data.IBookmarks`, entao a leitura usa
 * `Util.readObject` (a porta generica do iped-utils) e a INTERFACE. Os metodos que mudaram de
 * assinatura ou de classe entre versoes (getBookmarkComment, isInReport, getLastId) sao
 * chamados por REFLEXAO, para o mesmo .class funcionar com o jar de qualquer versao do caso.
 *
 * E preciso rodar com -Djava.awt.headless=true porque o objeto serializado carrega
 * java.awt.Color e javax.swing.KeyStroke (a cor e o atalho de teclado de cada marcador).
 *
 * Saida (uma linha por tipo):
 *   {"tipo":"marcador","id":1,"nome":"...","comentario":"...","no_relatorio":true}
 *   {"tipo":"item","id":1234,"marcadores":["..."],"selecionado":true}
 * Itens sem nenhum marcador e nao selecionados nao sao emitidos.
 *
 * Uso: java -Djava.awt.headless=true -cp "<caso>/lib/*:." IpedBookmarks <pasta-do-modulo-iped> [arquivo-do-estado]
 * Sem o segundo argumento le <pasta>/bookmarks.iped; com ele, le o arquivo indicado (o Python
 * usa isso para tentar os backups de <pasta>/bkp/bookmarks.N.bkp.iped).
 */
public class IpedBookmarks {

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("Uso: java IpedBookmarks <pasta-do-modulo-iped> [arquivo-do-estado]");
            System.exit(2);
        }
        File state;
        if (args.length >= 2) {
            state = new File(args[1]);
        } else {
            File moduleDir = resolveModuleDir(new File(args[0]));
            if (moduleDir == null) {
                System.err.println("Pasta do modulo iped nao encontrada a partir de: " + args[0]);
                System.exit(3);
            }
            state = new File(moduleDir, "bookmarks.iped");
        }
        if (!state.isFile()) {
            // caso nunca marcado pelo perito: nao e erro, e o normal
            System.err.println("SEM_MARCADORES");
            return;
        }

        // Util.readObject + interface IBookmarks: aceita Bookmarks E BitmapBookmarks (e
        // qualquer implementacao futura) sem cast para a classe concreta
        Object lido = iped.engine.util.Util.readObject(state.getAbsolutePath());
        if (!(lido instanceof iped.data.IBookmarks)) {
            System.err.println("FORMATO_DESCONHECIDO: " + lido.getClass().getName());
            System.exit(4);
        }
        iped.data.IBookmarks marcadores = (iped.data.IBookmarks) lido;
        PrintStream out = new PrintStream(System.out, false, "UTF-8");

        Map<Integer, String> nomes = marcadores.getBookmarkMap();
        for (Map.Entry<Integer, String> e : nomes.entrySet()) {
            StringBuilder sb = new StringBuilder(128);
            sb.append("{\"tipo\":\"marcador\",\"id\":").append(e.getKey());
            sb.append(",\"nome\":").append(quote(e.getValue()));
            sb.append(",\"comentario\":").append(quote(texto(marcadores, "getBookmarkComment", e.getKey())));
            sb.append(",\"no_relatorio\":").append(bool(marcadores, "isInReport", e.getKey()));
            sb.append('}');
            out.println(sb);
        }

        int ultimo = inteiro(marcadores, "getLastId", -1);
        int emitidos = 0;
        for (int id = 0; id <= ultimo; id++) {
            List<String> doItem;
            boolean selecionado;
            try {
                doItem = marcadores.getBookmarkList(id);
                selecionado = marcadores.isChecked(id);
            } catch (RuntimeException ex) {
                // id fora do bitset (estado salvo por uma versao com outro lastId): pula
                continue;
            }
            if ((doItem == null || doItem.isEmpty()) && !selecionado) {
                continue;
            }
            StringBuilder sb = new StringBuilder(128);
            sb.append("{\"tipo\":\"item\",\"id\":").append(id).append(",\"marcadores\":[");
            if (doItem != null) {
                for (int i = 0; i < doItem.size(); i++) {
                    if (i > 0) {
                        sb.append(',');
                    }
                    sb.append(quote(doItem.get(i)));
                }
            }
            sb.append("],\"selecionado\":").append(selecionado).append('}');
            out.println(sb);
            emitidos++;
            if (emitidos % 1000 == 0) {
                out.flush();
            }
        }
        out.flush();
        System.err.println("DONE " + emitidos);
    }

    /** Aceita a raiz do caso (que contem iped/) ou a propria pasta do modulo. */
    private static File resolveModuleDir(File given) {
        if (new File(given, "index").isDirectory()) {
            return given;
        }
        File viaModule = new File(given, "iped");
        if (new File(viaModule, "index").isDirectory()) {
            return viaModule;
        }
        return null;
    }

    /** Metodo opcional/inconstante entre versoes, chamado por reflexao: ausencia nao derruba. */
    private static Object invocar(Object alvo, String metodo, Object... args) {
        try {
            Class<?>[] tipos = new Class<?>[args.length];
            for (int i = 0; i < args.length; i++) {
                tipos[i] = args[i] instanceof Integer ? int.class : args[i].getClass();
            }
            java.lang.reflect.Method m = alvo.getClass().getMethod(metodo, tipos);
            return m.invoke(alvo, args);
        } catch (Throwable t) {
            return null;
        }
    }

    private static String texto(Object alvo, String metodo, Object... args) {
        Object v = invocar(alvo, metodo, args);
        return v == null ? null : String.valueOf(v);
    }

    private static boolean bool(Object alvo, String metodo, Object... args) {
        Object v = invocar(alvo, metodo, args);
        return Boolean.TRUE.equals(v);
    }

    private static int inteiro(Object alvo, String metodo, int padrao) {
        Object v = invocar(alvo, metodo);
        return v instanceof Number ? ((Number) v).intValue() : padrao;
    }

    private static String quote(String s) {
        if (s == null) {
            return "null";
        }
        StringBuilder sb = new StringBuilder(s.length() + 8);
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append('"');
        return sb.toString();
    }
}
