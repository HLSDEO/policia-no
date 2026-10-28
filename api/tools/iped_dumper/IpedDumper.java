import org.apache.lucene.document.Document;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.IndexReader;
import org.apache.lucene.index.IndexableField;
import org.apache.lucene.store.FSDirectory;

import java.io.File;
import java.io.PrintStream;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Ferramenta de linha de comando (spike da Fase 0) que le DIRETAMENTE o indice Lucene de um
 * caso do IPED (pasta <caso>/iped/index) e imprime em stdout um JSON por linha, um por
 * documento do indice.
 *
 * Decisao tomada durante a Fase 0: NAO usar iped.data.IIPEDSource/IItemReader. Abrir um caso
 * por ali aciona Configuration.loadConfigurables(moduleDir, true) incondicionalmente, que
 * carrega TODAS as tasks de processamento configuradas no caso (Python/jep para
 * reconhecimento facial, deteccao de nudez, carteiras de hardware etc.) mesmo para leitura
 * pura - e isso trava (jep sem lib nativa) ou lanca NullPointerException em inicializadores
 * estaticos de outras tasks (ex. ExportFileTask/Messages.getExternalBundle) fora do ambiente
 * da aplicacao completa do IPED. Ver docs/extracao_java_iped.md para o relato completo.
 *
 * Em vez disso, este dumper usa Apache Lucene diretamente (o caso ja carrega os jars do
 * Lucene em lib/) para abrir o indice e enumerar os campos armazenados de cada documento -
 * sem tocar em nenhuma classe do iped-engine/iped-api, e portanto sem acionar o sistema de
 * plugins/tasks. Os nomes dos campos (id, parentId, category, hash, ...) sao os mesmos
 * definidos em iped.properties.BasicProps/ExtraProperties (strings estaveis, conferidas no
 * codigo-fonte publico do IPED) e sao lidos genericamente via Document.getFields(), sem
 * precisar declarar cada nome de antemao.
 *
 * DOIS CUIDADOS que a primeira versao nao tinha (medidos contra o caso de exemplo do repo -
 * ver docs/extracao_iped.md, secao "Campos multivalorados"):
 *
 * 1. CAMPO MULTIVALORADO. O Lucene permite o mesmo nome de campo varias vezes no mesmo
 *    documento, e o IPED usa isso a rodo: no caso de exemplo, "Regex:URL" repetia 6.932
 *    vezes, "Regex:EMAIL" 5.272, "Regex:PHONE" 5.106, "Communication:To" nos e-mails com
 *    varios destinatarios e ate "category" em 60 itens. Emitir "nome":"valor" por ocorrencia
 *    produz chave JSON repetida, e json.loads do lado Python fica so com a ULTIMA - as
 *    entidades que o IPED ja extraiu iam para o lixo, 60 itens ficavam com a categoria errada
 *    e e-mail com varios destinatarios perdia destinatario. Agora os valores sao agrupados
 *    por nome e o campo sai como ARRAY quando tem mais de um valor (campo com um valor so
 *    continua saindo como string, para nao mexer em quem ja consome).
 *
 * 2. DOCUMENTO SEM CAMPO ARMAZENADO. O indice do IPED tem, alem do documento de cada item,
 *    um documento sem nenhum campo stored (no exemplo: 2.765 de 5.530, exatamente
 *    alternados; nao sao docs deletados - o indice nao tem nenhum arquivo .liv). Emiti-los
 *    fazia o Python parsear 50% de JSON vazio a toa e, pior, "total de itens" virava maxDoc,
 *    o dobro do numero real de itens. Agora eles sao pulados aqui.
 *
 * Protocolo do stderr (o lado Python depende dele - ver iped_utils._processar_importacao_iped):
 *   PROGRESS lidos/maxDoc  - progresso da varredura de documentos; chega a 100% no fim
 *   DONE emitidos          - quantidade REAL de itens (linhas escritas em stdout)
 *
 * Uso: java -cp "lucene-*.jars:IpedDumper.jar" IpedDumper pasta-do-caso-ou-do-modulo-iped
 */
public class IpedDumper {

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("Uso: java IpedDumper <pasta-do-caso>");
            System.exit(2);
        }
        File given = new File(args[0]);
        File indexDir = resolveIndexDir(given);
        if (indexDir == null) {
            System.err.println("Indice do IPED nao encontrado a partir de: " + given.getAbsolutePath());
            System.exit(3);
        }
        System.err.println("INDEX_DIR " + indexDir.getAbsolutePath());

        PrintStream out = new PrintStream(System.out, false, "UTF-8");
        Path path = indexDir.toPath();
        try (FSDirectory dir = FSDirectory.open(path);
             IndexReader reader = DirectoryReader.open(dir)) {

            int total = reader.maxDoc();
            System.err.println("PROGRESS 0/" + total);

            int emitidos = 0;
            for (int docId = 0; docId < total; docId++) {
                Document doc = reader.document(docId);
                // documento sem campo armazenado nao descreve item nenhum - ver cuidado 2
                if (doc != null && doc.iterator().hasNext()) {
                    out.println(docToJson(docId, doc));
                    emitidos++;
                }
                if ((docId + 1) % 500 == 0) {
                    out.flush();
                    System.err.println("PROGRESS " + (docId + 1) + "/" + total);
                }
            }
            out.flush();
            System.err.println("PROGRESS " + total + "/" + total);
            System.err.println("DONE " + emitidos);
        }
    }

    /** Aceita tanto a raiz do caso (contendo iped/index) quanto o proprio modulo "iped/" quanto a pasta index/ direto. */
    private static File resolveIndexDir(File given) {
        File direct = new File(given, "index");
        if (direct.isDirectory()) {
            return direct;
        }
        File viaModule = new File(new File(given, "iped"), "index");
        if (viaModule.isDirectory()) {
            return viaModule;
        }
        if (given.getName().equals("index") && given.isDirectory()) {
            return given;
        }
        return null;
    }

    /**
     * Agrupa os campos por nome (preservando a ordem de aparicao, que no IPED e significativa:
     * timeStamp e timeEvent saem alinhados posicionalmente) e serializa. Nome com um valor so
     * vira string; com dois ou mais, vira array.
     */
    private static String docToJson(int docId, Document doc) {
        Map<String, List<String>> campos = new LinkedHashMap<String, List<String>>();
        Iterator<IndexableField> it = doc.iterator();
        while (it.hasNext()) {
            IndexableField f = it.next();
            String value = f.stringValue();
            if (value == null && f.numericValue() != null) {
                value = f.numericValue().toString();
            }
            List<String> valores = campos.get(f.name());
            if (valores == null) {
                valores = new ArrayList<String>();
                campos.put(f.name(), valores);
            }
            valores.add(value);
        }

        StringBuilder sb = new StringBuilder(256);
        sb.append('{');
        sb.append("\"luceneDocId\":").append(docId).append(',');
        sb.append("\"fields\":{");
        boolean first = true;
        for (Map.Entry<String, List<String>> e : campos.entrySet()) {
            if (!first) {
                sb.append(',');
            }
            first = false;
            sb.append(quote(e.getKey())).append(':');
            List<String> valores = e.getValue();
            if (valores.size() == 1) {
                sb.append(quote(valores.get(0)));
            } else {
                sb.append('[');
                for (int i = 0; i < valores.size(); i++) {
                    if (i > 0) {
                        sb.append(',');
                    }
                    sb.append(quote(valores.get(i)));
                }
                sb.append(']');
            }
        }
        sb.append('}');
        sb.append('}');
        return sb.toString();
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
